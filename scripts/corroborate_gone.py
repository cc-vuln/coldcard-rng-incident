#!/usr/bin/env python3
"""Corroborate a DNS failure streak before anything is recorded gone.

`capture.py diagnose` reports a `dns-unresolved` streak when this host's
resolver fails a source's name. That proves nothing about the source: on
4 Aug 2026 the capture host's filtering resolver started refusing
coldcardwatch.com while the site stayed up, the record called a live source
gone, and the project had to publish a correction (corrections.toml,
6 Aug 2026). Since 8 Aug 2026 the convention in AGENTS.md is that `gone` may
be set for a name-resolution failure only when an independent check agrees.

This tool is that check. For every source whose diagnosis is
`dns-unresolved` with a streak at or above the threshold it re-resolves the
name two ways that do not touch the host resolver: DNS-over-HTTPS JSON
queries against both dns.google and cloudflare-dns.com, for the A and NS
records. Three outcomes:

- **confirmed-gone** — this host's getaddrinfo also fails AND both public
  resolvers return NXDOMAIN or no-data for the name. With --yes the source
  block in sources.toml gains `gone = true`, `gone_since`, `gone_status`
  and a `gone_note` carrying the corroboration transcript, and an alert is
  emitted. The edit is quarantine_registry-style surgery: one block, its
  neighbours byte-identical.
- **reachable-elsewhere** — any public resolver answers. The source is
  live; this collector's resolver is the problem. `gone` is never set; an
  alert of kind failure-streak asks a person to look at the resolver.
- **inconclusive** — a DoH query errored or the resolvers disagree.
  Nothing is written and nothing is alerted; the streak already surfaces in
  `just status`.

Sources already marked `gone` are excluded by diagnose itself; sources with
`watch = "frozen"` are excluded here as well, because a frozen source's
last failure sits in the append-only index forever and is settled, not
outstanding.

The default mode is a dry run: decisions and transcripts are printed and
nothing changes. `--yes` applies. The exit status is 0 for every outcome
short of a usage error, because this runs from a timer and a corroboration
pass must never break the line. The alert is best-effort: alert.py being
absent or failing never fails the run.

Zero dependencies: stdlib only, Python 3.11+ for tomllib.

Usage:
    corroborate_gone.py [--yes] [--min-streak N] [--registry PATH]
                        [--capture PATH] [--timeout SECONDS]
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from quarantine_registry import block_spans

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "sources.toml"
CAPTURE = Path(__file__).resolve().parent / "capture.py"
ALERT = Path(__file__).resolve().parent / "alert.py"
VENV_PY = ROOT / ".venv" / "bin" / "python"

TABLES = ("source", "x_post", "nostr_post")

DOH_SERVERS = (
    {"name": "dns.google", "url": "https://dns.google/resolve"},
    {"name": "cloudflare-dns.com", "url": "https://cloudflare-dns.com/dns-query"},
)
QTYPES = ("A", "NS")

DNS_STATUS = {
    0: "NOERROR",
    1: "FORMERR",
    2: "SERVFAIL",
    3: "NXDOMAIN",
    4: "NOTIMP",
    5: "REFUSED",
}

OUTCOMES = ("confirmed-gone", "reachable-elsewhere", "inconclusive")


# -- the evidence -------------------------------------------------------------


def run_diagnose(capture: Path) -> list[dict]:
    """`capture.py diagnose --json` as a list of rows. Never raises: a
    non-zero exit or the non-JSON "nothing failing" output both mean there
    is nothing to corroborate."""
    done = subprocess.run(
        [sys.executable, str(capture), "diagnose", "--json"],
        cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
    if done.returncode != 0:
        print(f"corroborate-gone: diagnose exited {done.returncode}; "
              "nothing to corroborate")
        return []
    try:
        rows = json.loads(done.stdout)
    except json.JSONDecodeError:
        # "every source's most recent poll succeeded" and friends.
        return []
    return rows if isinstance(rows, list) else []


def load_registry(registry: Path) -> dict[str, dict]:
    data = tomllib.loads(registry.read_text(encoding="utf-8"))
    entries: dict[str, dict] = {}
    for table in TABLES:
        for entry in data.get(table, []):
            if isinstance(entry, dict) and entry.get("id"):
                entries[entry["id"]] = entry
    return entries


def candidates(rows: list[dict], registry: dict[str, dict],
               min_streak: int) -> list[dict]:
    """Rows eligible for corroboration: dns-unresolved, streak at or over
    the threshold, still registered, not already gone, not frozen."""
    picked = []
    for row in rows:
        if row.get("diagnosis") != "dns-unresolved":
            continue
        if (row.get("streak") or 0) < min_streak:
            continue
        entry = registry.get(row.get("id") or "")
        if entry is None:
            continue
        if entry.get("gone") or entry.get("watch") == "frozen":
            continue
        picked.append(row)
    return picked


def local_resolve(host: str) -> dict:
    """One getaddrinfo from this host, for the transcript. This is the same
    resolver whose failure started the streak, so it is evidence about the
    host, never about the source."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return {"ok": True,
                "addresses": sorted({i[4][0] for i in infos}),
                "error": None}
    except OSError as exc:
        return {"ok": False, "addresses": [], "error": str(exc)}


def doh_query(host: str, server: dict, timeout: int = 15) -> dict:
    """A and NS for `host` from one DoH JSON endpoint. Never raises: a
    failed query is recorded as evidence of an inconclusive pass, not an
    exception."""
    result: dict = {"server": server["name"], "queries": {}}
    for qtype in QTYPES:
        url = server["url"] + "?" + urllib.parse.urlencode(
            {"name": host, "type": qtype})
        req = urllib.request.Request(url, headers={
            "accept": "application/dns-json",
            "user-agent": "coldcard-archive corroborate_gone.py",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            answers = [a.get("data", "") for a in data.get("Answer", [])]
            result["queries"][qtype] = {
                "status": data.get("Status"), "answers": answers}
        except Exception as exc:
            result["queries"][qtype] = {
                "error": f"{type(exc).__name__}: {exc}"[:150]}
    return result


# -- the decision -------------------------------------------------------------


def verdict(resolver: dict) -> str:
    """One resolver's answer for the name: 'answers', 'absent' or 'error'.

    Any answer record on either query means the name exists somewhere; an
    errored query with no answer anywhere means the resolver cannot be
    counted either way; NXDOMAIN or NOERROR without records on every query
    means this resolver says the name is not there."""
    saw_answer = saw_error = saw_absent = False
    for q in (resolver.get("queries") or {}).values():
        if q.get("answers"):
            saw_answer = True
        elif q.get("error") or q.get("status") not in (0, 3):
            saw_error = True
        else:
            saw_absent = True
    if saw_answer:
        return "answers"
    if saw_error or not saw_absent:
        return "error"
    return "absent"


def classify(local: dict, resolvers: list[dict]) -> str:
    """The outcome for one candidate, from the evidence only."""
    verdicts = [verdict(r) for r in resolvers]
    if "answers" in verdicts:
        return "reachable-elsewhere"
    if verdicts and all(v == "absent" for v in verdicts) and not local["ok"]:
        return "confirmed-gone"
    return "inconclusive"


# -- the transcript -----------------------------------------------------------


def describe_query(q: dict) -> str:
    if q.get("error"):
        return f"query failed ({q['error']})"
    status = DNS_STATUS.get(q.get("status"), f"status {q.get('status')}")
    if q.get("answers"):
        return f"{status}: " + ", ".join(q["answers"])
    return f"{status}, no answer records"


def transcript(row: dict, host: str, local: dict, resolvers: list[dict],
               now: datetime) -> str:
    lines = [
        f"candidate {row['id']}: host {host}, "
        f"dns-unresolved x{row['streak']} since {row.get('failing_since')} "
        f"(last good {row.get('last_good')})",
        f"  this host, getaddrinfo: "
        + (", ".join(local["addresses"]) if local["ok"]
           else f"failed ({local['error']})"),
    ]
    for r in resolvers:
        parts = "; ".join(
            f"{qt} {describe_query(r['queries'][qt])}"
            for qt in QTYPES if qt in r["queries"])
        lines.append(f"  {r['server']}: {parts}")
    lines.append(f"  corroborated at {now:%Y-%m-%dT%H:%M:%SZ}")
    return "\n".join(lines)


def gone_note(row: dict, host: str, local: dict, resolvers: list[dict],
              now: datetime) -> str:
    body = transcript(row, host, local, resolvers, now)
    return f"""\
Recorded gone by scripts/corroborate_gone.py on {now:%-d %B %Y}.

This collector's resolver failed the name on {row['streak']} consecutive
polls. A failure of this host's resolver alone does not establish that a
source is gone (corrections.toml, 6 Aug 2026), so before recording it the
name was re-resolved through two independent public DNS-over-HTTPS
resolvers, each asked for the A and NS records:

{body}

Both independent resolvers agree the name does not resolve, and this host's
resolver fails it too, which is consistent with the origin withdrawing the
name rather than with a block on this collector. The captures held remain
the record of what the source said while it was reachable."""


def gone_status(resolvers: list[dict]) -> str:
    statuses = [r["queries"]["A"].get("status") for r in resolvers
                if "A" in r.get("queries", {})]
    if statuses and all(s == 3 for s in statuses):
        return "NXDOMAIN"
    return "no-data"


# -- the registry edit ---------------------------------------------------------


def apply_gone(text: str, ident: str, since: str, status: str,
               note: str) -> str:
    """Insert the gone_* fields into `ident`'s block, after its url line.

    The quarantine_registry style: locate the block by table position, touch
    only those lines, leave every neighbour byte-identical."""
    data = tomllib.loads(text)
    lines = text.splitlines(keepends=True)
    for table, idx, start, end in block_spans(text):
        entries = data.get(table, [])
        if idx >= len(entries):
            continue
        entry = entries[idx]
        if not isinstance(entry, dict) or entry.get("id") != ident:
            continue
        if entry.get("gone"):
            raise ValueError(f"{ident!r} is already marked gone")
        url_line = next(
            (i for i in range(start + 1, end)
             if re.match(r"\s*url\s*=", lines[i])),
            None)
        if url_line is None:
            raise ValueError(f"{ident!r}: no url line found in its block")
        note_lines = note.rstrip("\n").splitlines()
        if any('"""' in l for l in note_lines):
            raise ValueError(f"{ident!r}: gone_note body cannot be quoted")
        new = [
            "gone = true\n",
            f'gone_since = "{since}"\n',
            f'gone_status = "{status}"\n',
            'gone_note = """\n',
            *(l + "\n" for l in note_lines),
            '"""\n',
        ]
        lines[url_line + 1:url_line + 1] = new
        edited = "".join(lines)
        tomllib.loads(edited)  # a broken edit must never reach the file
        return edited
    raise ValueError(f"{ident!r}: not found in the registry")


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
                    help="apply the registry edits and emit the alerts; "
                         "without it this only prints decisions")
    ap.add_argument("--min-streak", type=int, default=4,
                    help="dns-unresolved streak at or over which a source "
                         "is corroborated (default 4)")
    ap.add_argument("--registry", type=Path, default=REGISTRY)
    ap.add_argument("--capture", type=Path, default=CAPTURE,
                    help="the capture.py to ask for diagnose --json")
    ap.add_argument("--timeout", type=int, default=15,
                    help="per-query DoH timeout in seconds")
    args = ap.parse_args()

    rows = run_diagnose(args.capture)
    registry = load_registry(args.registry)
    picked = candidates(rows, registry, args.min_streak)

    if not picked:
        print(f"corroborate-gone: no source with a dns-unresolved streak "
              f">= {args.min_streak} needs corroboration")
        return 0

    mode = "apply" if args.yes else "dry-run"
    print(f"corroborate-gone ({mode}): {len(picked)} candidate(s) with a "
          f"dns-unresolved streak >= {args.min_streak}")

    now = datetime.now(timezone.utc)
    text = args.registry.read_text(encoding="utf-8")
    edited = text
    confirmed: list[str] = []

    for row in picked:
        entry = registry[row["id"]]
        host = urllib.parse.urlparse(entry["url"]).hostname
        if not host:
            print(f"\n{row['id']}: no hostname in {entry['url']}; skipped")
            continue
        local = local_resolve(host)
        resolvers = [doh_query(host, s, timeout=args.timeout)
                     for s in DOH_SERVERS]
        outcome = classify(local, resolvers)
        print(f"\n{row['id']}: {outcome}")
        print(transcript(row, host, local, resolvers, now))

        if outcome == "confirmed-gone":
            since = f"{now:%Y%m%dT%H%M%SZ}"
            status = gone_status(resolvers)
            note = gone_note(row, host, local, resolvers, now)
            if args.yes:
                edited = apply_gone(edited, row["id"], since, status, note)
                confirmed.append(row["id"])
                emit_alert("emit", "--kind", "gone-set",
                           "--severity", "warning",
                           "--key", f"gone-{row['id']}",
                           "--summary",
                           f"{row['id']} recorded gone: {host} does not "
                           f"resolve on dns.google or cloudflare-dns.com "
                           f"(streak {row['streak']})")
            else:
                print(f"  would set gone = true, gone_since = \"{since}\", "
                      f"gone_status = \"{status}\" and the transcript above "
                      f"as gone_note")
        elif outcome == "reachable-elsewhere":
            if args.yes:
                emit_alert("emit", "--kind", "failure-streak",
                           "--summary",
                           f"{row['id']}: unreachable from this collector, "
                           f"resolves publicly ({host}; streak "
                           f"{row['streak']})")
            else:
                print("  would alert: unreachable from this collector, "
                      "resolves publicly; gone is NOT set")

    if confirmed:
        args.registry.write_text(edited, encoding="utf-8")
        print(f"\ncorroborate-gone: recorded gone: {', '.join(confirmed)}")
    elif args.yes:
        print("\ncorroborate-gone: nothing was confirmed gone; "
              "the registry is unchanged")
    else:
        print("\ncorroborate-gone: dry-run; nothing changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
