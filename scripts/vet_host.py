#!/usr/bin/env python3
"""Vet an intake agent's host proposals and admit the sound ones.

Until 8 Aug 2026, adding a host to `scripts/registry_hosts.toml` was a human
edit, twice over (the registry file and `scripts/agent_egress_hosts.toml`).
The cost of that was paid in latency: a candidate the intake agent would have
registered, minus its host, waited in `.work/host-proposals.txt` for a person.
The replacement keeps the gate but moves the mechanical half here, driver-side
on the operator account: the intake agent still only *proposes* (one
tab-separated line per declined candidate), and this tool decides whether a
proposal is sound by checks that do not depend on anyone's judgement:

- **name hygiene.** Not an IP literal, not a localhost or private-style
  domain, not a single label, not a known URL shortener. These are rejected
  out of hand; nothing downstream can rescue them.
- **DNS agreement.** The host must resolve via this machine's getaddrinfo
  AND both public DoH resolvers (dns.google and cloudflare-dns.com, the
  same pair corroborate_gone.py corroborates disappearances through).
  Agreement that the name does not exist is a rejection; disagreement or a
  resolver error is inconclusive, not a rejection, because this host's own
  resolver has been wrong before (corrections.toml, 6 Aug 2026).
- **public addresses only.** Any address literal returned by the local or
  public resolvers must be globally routable. A private, loopback, link-local,
  reserved or otherwise non-global answer rejects the host before HTTP.
- **redirect shape.** `https://<host>/` is fetched without following
  redirects. A 3xx to a www/subdomain variant of the same domain is normal
  and the admitted host is the redirect target. A 3xx to a different domain
  is a rejection ("redirect to <host>"): the proposal said one host and the
  network says another, which is exactly what a person should look at.
- **robots.txt.** Fetched once from the (normalised) host. AGENTS.md:
  "Do not add a source that forbids it in robots.txt without checking
  first." If the site's robots.txt forbids our user-agent the candidate
  path, the host is rejected with the reason. 404 means no policy; 401/403
  means disallow-all by convention; a 5xx or a fetch error is inconclusive.

On a pass, and only on a pass, the host is appended to the `admitted` group
of `scripts/registry_hosts.toml` and of `scripts/agent_egress_hosts.toml`.
The group is deliberately separate from the hand-filed semantic groups: the
checks above can say a host is sound, but not whether it is press, research
or industry, and filing it is a one-line human edit with the evidence in the
alert. The edit is quarantine_registry-style surgery: the group's entries
stay alphabetical, commented entries keep their comments, every neighbour is
byte-identical, and the result is re-parsed with tomllib before it is
written. Each admission emits a `host-admission` alert, so every host this
tool adds is auditable after the fact.

On a rejection the reason is recorded in `.work/host-vetting.json` and the
host is not re-vetted for 30 days; the proposal line stays in
`.work/host-proposals.txt` for a person, and `just status` surfaces it.
Nothing about a rejection touches either TOML file. An inconclusive host is
left entirely alone so a later run retries it.

The default mode is a dry run: verdicts and check transcripts are printed
and nothing changes. `--yes` applies. The exit status is 0 for every outcome
short of a usage error, because this runs at the tail of the intake drivers
and a vetting pass must never break the line. The alert is best-effort:
alert.py being absent or failing never fails the run.

Zero dependencies: stdlib only, Python 3.11+ for tomllib.

Usage:
    vet_host.py [--yes] [--proposals PATH] [--registry-hosts PATH]
                [--egress-hosts PATH] [--state PATH] [--timeout SECONDS]
"""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from capture import UA
from agent_proxy import connect_public
from check_registry import allowed_hosts
from corroborate_gone import DOH_SERVERS, doh_query, local_resolve
from corroborate_gone import verdict as resolver_verdict

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
PROPOSALS = ROOT / ".work" / "host-proposals.txt"
STATE = ROOT / ".work" / "host-vetting.json"
REGISTRY_HOSTS = HERE / "registry_hosts.toml"
EGRESS_HOSTS = HERE / "agent_egress_hosts.toml"
ALERT = HERE / "alert.py"
VENV_PY = ROOT / ".venv" / "bin" / "python"

TIMEOUT = 15
RETRY_DAYS = 30

# The group admitted hosts land in, in both TOML files. Separate from the
# hand-filed semantic groups on purpose: these checks can establish that a
# host is sound, not what it is.
GROUP = "admitted"
GROUP_COMMENT = {
    "registry": (
        "# Admitted by scripts/vet_host.py from intake host proposals\n"
        "# (.work/host-proposals.txt) after DNS, robots.txt and redirect\n"
        "# vetting. Re-filing one of these into a semantic group above is a\n"
        "# human edit."
    ),
    "egress": (
        "# Admitted by scripts/vet_host.py alongside registry_hosts.toml, so\n"
        "# the proxy's two allowlist files record the same admissions."
    ),
}

# URL shorteners: the record names the page a link lands on, never the
# redirector, whose target can change after admission. Extend as needed.
SHORTENERS = {
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "buff.ly",
    "rebrand.ly", "shorturl.at", "tiny.cc", "cutt.ly", "rb.gy", "t.ly",
}

HOST_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")

LOCAL_SUFFIXES = (
    ".localhost", ".local", ".lan", ".internal", ".home", ".corp",
    ".test", ".invalid",
)

OUTCOMES = ("admit", "reject", "inconclusive")


# -- proposals and state --------------------------------------------------------


def parse_proposals(text: str) -> tuple[list[dict], list[str]]:
    """The queue the intake agents append to: tab-separated, four fields,
    `<candidate id-or-url>\thost\t<reason>\t<UTC stamp>`. Blank lines and
    comments are skipped; a malformed line is reported, never fatal."""
    proposals, problems = [], []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 4 or not all(p.strip() for p in parts[:2]):
            problems.append(f"line {lineno}: not four tab-separated fields")
            continue
        candidate, host, reason, stamp = (p.strip() for p in parts)
        proposals.append({
            "candidate": candidate, "host": host.lower(), "reason": reason,
            "stamp": stamp, "line": lineno})
    return proposals, problems


def by_host(proposals: list[dict]) -> list[tuple[str, list[dict]]]:
    """One vetting decision per host, first-seen order. Several candidates
    may share a host; the first proposal's reason speaks for the group."""
    groups: dict[str, list[dict]] = {}
    for p in proposals:
        groups.setdefault(p["host"], []).append(p)
    return list(groups.items())


def load_state(path: Path) -> dict | None:
    """None on a corrupt file: refusing to vet beats re-vetting hosts a
    previous run already rejected."""
    if not path.exists():
        return {"version": 1, "hosts": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"vet-hosts: {path} does not parse ({exc}); refusing to run "
              "over a corrupt state file")
        return None
    if not isinstance(state, dict) or not isinstance(
            state.get("hosts"), dict):
        print(f"vet-hosts: {path} is not a vetting state file; refusing "
              "to run")
        return None
    return state


def save_state(path: Path, state: dict) -> None:
    """Atomic: a state file torn mid-write must never read as 'no host has
    ever been rejected'."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def rejected_recently(state: dict, host: str, now: datetime) -> str | None:
    """The recorded rejection reason if this host was rejected inside the
    retry window, else None."""
    entry = state["hosts"].get(host)
    if not entry or entry.get("status") != "rejected":
        return None
    try:
        when = datetime.strptime(entry["when"], "%Y%m%dT%H%M%SZ")
        when = when.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None
    if now - when < timedelta(days=RETRY_DAYS):
        return entry.get("reason") or "rejected earlier"
    return None


# -- the checks ------------------------------------------------------------------


def name_rejection(host: str) -> str | None:
    """The no-network checks. A reason string, or None if the name is one a
    public registry could name."""
    try:
        ipaddress.ip_address(host)
        return "an IP literal, not a hostname"
    except ValueError:
        pass
    if not HOST_RE.match(host):
        return f"not a valid bare hostname ({host!r})"
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return "a localhost or private-style domain"
    if "." not in host:
        return "a single-label name, not a public hostname"
    if host in SHORTENERS:
        return ("a URL shortener; the record names the page a link lands "
                "on, not the redirector")
    return None


def fetch(url: str, timeout: int) -> dict:
    """One request, redirects NOT followed: the redirect itself is evidence.
    Returns {status, location, body}; transport errors raise."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    class PublicHTTPSConnection(http.client.HTTPSConnection):
        """HTTPSConnection pinned to one globally routable DNS answer."""
        def connect(self):
            self.sock = connect_public((self.host, self.port), self.timeout)
            if self._tunnel_host:
                self._tunnel()
            server_hostname = self._tunnel_host or self.host
            self.sock = self._context.wrap_socket(
                self.sock, server_hostname=server_hostname
            )

    class PublicHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(
                PublicHTTPSConnection, req,
                context=getattr(self, "_context", None),
            )

    # Ignore process proxy variables here. Vetting must connect to the exact
    # public address it checked, not hand an untrusted proposal to a proxy.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect, PublicHTTPSHandler()
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return {"status": resp.status, "location": None,
                    "body": resp.read(1_000_000)}
    except urllib.error.HTTPError as exc:
        body = b"" if 300 <= exc.code < 400 else exc.read(500_000)
        return {"status": exc.code,
                "location": exc.headers.get("Location"), "body": body}


def same_domain(a: str, b: str) -> bool:
    """A www/subdomain variant of the same name in either direction."""
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def non_global_answers(local: dict, resolvers: list[dict]) -> list[str]:
    """Non-global IP literals present in local or public resolver answers."""
    values = list(local.get("addresses") or [])
    for resolver in resolvers:
        query = (resolver.get("queries") or {}).get("A") or {}
        values.extend(query.get("answers") or [])
    unsafe = set()
    for value in values:
        try:
            address = ipaddress.ip_address(str(value).strip())
        except ValueError:
            # An A response may include a CNAME before its address.
            continue
        if not address.is_global:
            unsafe.add(str(address))
    return sorted(unsafe)


def vet(host: str, candidate_url: str | None = None, timeout: int = TIMEOUT,
        resolve=None, doh=None, fetcher=None) -> dict:
    """The whole checklist for one host. Network seams are injectable so the
    tests never touch a socket: resolve(host) -> local_resolve dict,
    doh(host, server) -> doh_query dict, fetcher(url, timeout) -> fetch
    dict. Returns {outcome, host, reason, checks}: `host` is the normalised
    form (a same-domain redirect target), `reason` is set on reject and
    inconclusive, `checks` is the transcript."""
    resolve = resolve or local_resolve
    doh = doh or (lambda h, s: doh_query(h, s, timeout=timeout))
    fetcher = fetcher or fetch
    checks: list[str] = []

    def done(outcome: str, reason: str | None = None,
             normalised: str | None = None) -> dict:
        return {"outcome": outcome, "host": normalised or host,
                "reason": reason, "checks": checks}

    # 1. name hygiene, no network
    reason = name_rejection(host)
    if reason:
        checks.append(f"name: {reason}")
        return done("reject", reason)
    checks.append("name: a valid public hostname")

    # 2. DNS: this host's getaddrinfo and both DoH resolvers must agree the
    # name exists, and every address literal they return must be global.
    def dns_gate(name: str) -> tuple[str, str | None]:
        local = resolve(name)
        resolvers = [doh(name, s) for s in DOH_SERVERS]
        verdicts = [resolver_verdict(r) for r in resolvers]
        local_txt = (", ".join(local["addresses"]) if local["ok"]
                     else f"failed ({local['error']})")
        checks.append(f"dns {name}: getaddrinfo {local_txt}; " + "; ".join(
            f"{r['server']} {resolver_verdict(r)}" for r in resolvers))
        unsafe = non_global_answers(local, resolvers)
        if unsafe:
            return "reject", (
                "resolves to non-global address(es): " + ", ".join(unsafe)
            )
        if local["ok"] and all(v == "answers" for v in verdicts):
            return "ok", None
        if not local["ok"] and all(v == "absent" for v in verdicts):
            return "reject", (
                "does not resolve: this host's resolver and both public DoH "
                "resolvers agree the name is absent"
            )
        return "inconclusive", (
            "dns: resolvers disagree or errored; left for a later run"
        )

    dns_outcome, dns_reason = dns_gate(host)
    if dns_outcome != "ok":
        return done(dns_outcome, dns_reason)

    # 3. redirect shape at the root, redirects not followed
    try:
        root = fetcher(f"https://{host}/", timeout)
    except Exception as exc:
        checks.append(f"redirect: fetch failed ({type(exc).__name__}: {exc})")
        return done("inconclusive", f"root fetch failed ({exc})")
    status, location = root["status"], root.get("location")
    normalised = host
    if 200 <= status < 300:
        checks.append(f"redirect: https://{host}/ -> {status}")
    elif 300 <= status < 400 and location:
        target = urllib.parse.urljoin(f"https://{host}/", location)
        thost = (urllib.parse.urlparse(target).hostname or "").lower()
        checks.append(f"redirect: https://{host}/ -> {status} {target}")
        if not thost:
            return done("inconclusive", f"redirect status {status} without "
                        "a resolvable Location host")
        if same_domain(host, thost):
            normalised = thost
            if normalised != host:
                checks.append(f"redirect: normalised to {normalised}")
                dns_outcome, dns_reason = dns_gate(normalised)
                if dns_outcome != "ok":
                    return done(dns_outcome, dns_reason)
        else:
            return done("reject", f"redirect to {thost}")
    else:
        checks.append(f"redirect: https://{host}/ -> status {status}")
        return done("inconclusive", f"root fetch returned status {status}; "
                    "left for a later run")

    # 4. robots.txt on the normalised host, checked against the candidate's
    # own path where the proposal carried a URL
    robots_url = f"https://{normalised}/robots.txt"
    try:
        robots = fetcher(robots_url, timeout)
    except Exception as exc:
        checks.append(f"robots: fetch failed ({type(exc).__name__}: {exc})")
        return done("inconclusive", f"robots.txt fetch failed ({exc})")
    rstatus = robots["status"]
    if rstatus == 404 or 300 <= rstatus < 400:
        checks.append(f"robots: {robots_url} -> {rstatus}; no policy, "
                      "allowed by convention")
    elif rstatus in (401, 403):
        checks.append(f"robots: {robots_url} -> {rstatus}")
        return done("reject", f"robots.txt refused with status {rstatus}, "
                    "which is disallow-all by convention")
    elif rstatus >= 500:
        checks.append(f"robots: {robots_url} -> {rstatus}")
        return done("inconclusive", f"robots.txt returned status {rstatus}; "
                    "left for a later run")
    else:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(robots["body"].decode("utf-8", "replace").splitlines())
        page = candidate_url or f"https://{normalised}/"
        if not page.startswith("http"):
            page = f"https://{normalised}/"
        if not parser.can_fetch(UA, page):
            checks.append(f"robots: {robots_url} forbids our user-agent "
                          f"at {page}")
            return done("reject", f"robots.txt forbids crawling {page} "
                        "with this project's user-agent")
        checks.append(f"robots: {robots_url} allows our user-agent at "
                      f"{page}")

    return done("admit", normalised=normalised)


# -- the TOML surgery -------------------------------------------------------------


def insert_host(text: str, group: str, host: str, comment: str) -> str:
    """Insert `host` into `[hosts]`' `group` array, alphabetically.

    The quarantine_registry style, adapted to a two-level file: locate the
    group's array by line, insert one `  "host",` line before the first
    entry that sorts after it (skipping back over that entry's own comment
    lines so a comment stays with the entry it describes), or append the
    whole group at the end when it does not exist yet. Every other byte is
    untouched, and the result is re-parsed with tomllib before it is
    returned: a broken edit must never reach the file."""
    data = tomllib.loads(text)
    existing = {h for g in data.get("hosts", {}).values() for h in g}
    if host in existing:
        raise ValueError(f"{host!r} is already listed")

    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines)
                  if re.match(rf"^{re.escape(group)}\s*=\s*\[\s*$",
                              l.rstrip("\n"))), None)
    if start is None:
        edited = text
        if edited and not edited.endswith("\n"):
            edited += "\n"
        edited += (f"\n{comment}\n{group} = [\n  \"{host}\",\n]\n")
    else:
        end = next(i for i in range(start + 1, len(lines))
                   if lines[i].rstrip("\n").strip() == "]")
        at = end  # default: append before the closing bracket
        for i in range(start + 1, end):
            m = re.match(r'^\s*"([^"]+)",\s*$', lines[i].rstrip("\n"))
            if m and m.group(1) > host:
                at = i
                while at > start + 1 and lines[at - 1].lstrip().startswith("#"):
                    at -= 1
                break
        lines[at:at] = [f'  "{host}",\n']
        edited = "".join(lines)

    parsed = tomllib.loads(edited)
    if host not in parsed.get("hosts", {}).get(group, []):
        raise ValueError(f"edited text does not list {host!r} under {group}")
    return edited


# -- the alert ------------------------------------------------------------------


def emit_alert(*argv: str) -> None:
    """Best effort: alert.py may not exist yet, and its failure is not this
    tool's failure."""
    if not ALERT.exists():
        print("  (scripts/alert.py not present; alert skipped)")
        return
    python = str(VENV_PY) if VENV_PY.exists() else sys.executable
    try:
        done = subprocess.run([python, str(ALERT), *argv], cwd=ROOT,
                              capture_output=True, text=True, timeout=30,
                              check=False)
        if done.returncode != 0:
            print(f"  (alert exited {done.returncode}: "
                  f"{(done.stderr or done.stdout).strip()[:150]})")
    except Exception as exc:
        print(f"  (alert failed: {exc})")


# -- main -----------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true",
                    help="apply the TOML edits, record state and emit the "
                         "alerts; without it this only prints verdicts")
    ap.add_argument("--proposals", type=Path, default=PROPOSALS)
    ap.add_argument("--registry-hosts", type=Path, default=REGISTRY_HOSTS)
    ap.add_argument("--egress-hosts", type=Path, default=EGRESS_HOSTS)
    ap.add_argument("--state", type=Path, default=STATE)
    ap.add_argument("--timeout", type=int, default=TIMEOUT,
                    help="per-request timeout in seconds")
    args = ap.parse_args()

    if not args.proposals.exists():
        print(f"vet-hosts: no {args.proposals}; nothing proposed")
        return 0
    proposals, problems = parse_proposals(
        args.proposals.read_text(encoding="utf-8"))
    for problem in problems:
        print(f"vet-hosts: skipped malformed proposal, {problem}")
    if not proposals:
        print("vet-hosts: no proposals to vet")
        return 0
    state = load_state(args.state)
    if state is None:
        return 0
    registered = allowed_hosts(args.registry_hosts)

    now = datetime.now(timezone.utc)
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    mode = "apply" if args.yes else "dry-run"
    print(f"vet-hosts ({mode}): {len(proposals)} proposal(s), "
          f"{len(by_host(proposals))} distinct host(s)")

    registry_text = args.registry_hosts.read_text(encoding="utf-8")
    egress_text = args.egress_hosts.read_text(encoding="utf-8")
    admitted: list[str] = []

    for host, props in by_host(proposals):
        reason = props[0]["reason"]
        print(f"\n{host} (proposal: {reason}; {len(props)} candidate(s))")
        if host in registered:
            print("  already in registry_hosts.toml; nothing to vet")
            state["hosts"][host] = {
                "status": "already-listed", "reason": reason, "when": stamp}
            continue
        prior = rejected_recently(state, host, now)
        if prior is not None:
            print(f"  rejected within {RETRY_DAYS} days ({prior}); left "
                  "for a human")
            continue

        candidate = props[0]["candidate"]
        result = vet(host, candidate_url=candidate, timeout=args.timeout)
        for line in result["checks"]:
            print(f"  {line}")
        outcome, normalised = result["outcome"], result["host"]

        if outcome == "admit" and normalised in registered:
            print(f"  normalised to {normalised}, which is already "
                  "registered; nothing to admit")
            state["hosts"][host] = {
                "status": "already-listed", "reason": reason, "when": stamp}
            continue

        if outcome == "admit":
            print(f"  verdict: ADMIT {normalised}")
            if args.yes:
                registry_text = insert_host(
                    registry_text, GROUP, normalised, GROUP_COMMENT["registry"])
                egress_text = insert_host(
                    egress_text, GROUP, normalised, GROUP_COMMENT["egress"])
                registered.add(normalised)
                admitted.append(normalised)
                state["hosts"][host] = {
                    "status": "admitted", "host": normalised,
                    "reason": reason, "when": stamp}
                emit_alert("emit", "--kind", "host-admission",
                           "--severity", "warning",
                           "--key", f"host-{normalised}-{now:%Y%m%d}",
                           "--summary",
                           f"admitted host {normalised} to "
                           "registry_hosts.toml and agent_egress_hosts.toml "
                           f"(proposal: {reason})")
            else:
                print(f"  would admit {normalised} to registry_hosts.toml "
                      "and agent_egress_hosts.toml and emit a "
                      "host-admission alert")
        elif outcome == "reject":
            print(f"  verdict: REJECT ({result['reason']})")
            if args.yes:
                state["hosts"][host] = {
                    "status": "rejected", "reason": result["reason"],
                    "when": stamp}
            else:
                print(f"  would record the rejection and leave the host "
                      f"unvetted for {RETRY_DAYS} days; the proposal line "
                      "stays in .work/host-proposals.txt")
        else:
            print(f"  verdict: INCONCLUSIVE ({result['reason']}); nothing "
                  "recorded, a later run retries")

    if args.yes:
        if admitted:
            args.registry_hosts.write_text(registry_text, encoding="utf-8")
            args.egress_hosts.write_text(egress_text, encoding="utf-8")
        save_state(args.state, state)
        print(f"\nvet-hosts: admitted: {', '.join(admitted)}"
              if admitted else
              "\nvet-hosts: nothing admitted; the TOML files are unchanged")
    else:
        print("\nvet-hosts: dry-run; nothing changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
