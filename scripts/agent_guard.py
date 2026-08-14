#!/usr/bin/env python3
"""Check what an unattended agent run actually did, and fail if it overreached.

The agents in this repository read text that strangers wrote: captured diffs,
community threads, hydrated post bodies. A prompt telling the model to treat
that text as data is worth having and is not a control, because the whole
point of an injection is that the model stops following the prompt. This gate
assumes the injection worked and asks a different question: is what is on disk
now something this agent was allowed to produce?

It runs either side of the agent, as the operator account rather than the
agent account:

    agent_guard.py before --role intake --run-dir .work/agent-guard/<TS>
    <the agent runs, deprivileged, via run-agent.sh>
    agent_guard.py after  --role intake --run-dir .work/agent-guard/<TS>

`before` records a hash of every file git can see, and keeps a copy of the
handful the role is allowed to write. `after` recomputes and enforces:

1. nothing outside the role's remit changed, in either direction
2. no secret value, key shape or operator needle appears in what was added
3. the registry changes are in shape (scripts/check_registry.py)
4. intake verdict data covers exactly the protected packet and an intake
   agent did not edit any part of the discovery record directly
5. requested first captures name sources this run actually registered
6. the append-only files only gained lines: their before-text is a verbatim
   prefix of their after-text

A failure exits 1 and names the offender. It changes nothing: the edits stay
on disk, which is where a human can read them, and which already blocks
publication because publish-scheduled.sh skips a tree that is dirty outside
archive/. Reverting would destroy the most interesting evidence there is,
namely what the injection tried to do.

`archive/` is deliberately outside the manifest. The capture timer writes
there throughout an agent run, so it carries no signal about the agent; the
agent is kept out of it by file ownership and by never being the process that
calls capture.py. See docs/design/agent-sandbox.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What each role may write. A path ending in "/" is a prefix; anything else is
# an exact repository-relative path. Keep these as tight as the prompt: they
# are the machine-readable half of what the standing instructions promise.
ROLES: dict[str, tuple[str, ...]] = {
    # Classifies captured diffs. Appends classifications, drafts normalizer
    # proposals, and nothing else.
    "review": ("revision-reviews.toml", ".work/normalizer-proposals/"),
    # Registers community threads and writes a verdict-data outbox. The queue
    # itself is an operator-side deterministic write after this gate.
    "intake": ("sources.toml", ".work/"),
    # Rechecks unverified claims across the editorial pages.
    "sweep": ("site/src/pages/", "BACKLOG.md", ".work/"),
    # Registers queued X posts as [[x_post]] blocks and records verdicts.
    # Replaced the read-only xtriage role on 8 Aug 2026, when X promotion
    # was automated; its first captures are ingests, not polls.
    "xintake": ("sources.toml", ".work/"),
    # Drafts correction proposals from the claim sweep's state-changed
    # flags. Propose-only: corrections.toml and the site pages are
    # deliberately outside the remit, so a run that edits them fails here,
    # and the deterministic applier (scripts/apply_corrections.py) is the
    # only writer. The APPEND_ONLY prefix check below covers
    # corrections.toml regardless, keyed on the file rather than the role.
    "corrections": (".work/correction-proposals/",),
    # Syncs published prose with the record from the staleness packet:
    # dated refresh, section prose sync against routed revisions, link
    # additions, capture-grounded marker promotions. The narrower remit on
    # index.astro and response/legal.astro (dated refresh and link addition
    # only, never reframed) is a prompt-level discipline, deliberately not
    # encoded here: a path list cannot tell a refresh from a reframe, so the
    # control is the post-run gate chain in scripts/agent-site-sync.sh
    # (check-claims plus a full gated build), which rejects the run when the
    # edits do not build clean.
    "sync": ("site/src/pages/", ".work/"),
}

# Roles whose remit includes registering sources, and therefore asking for a
# first capture. The driver performs the capture itself, after this gate.
REGISTERING_ROLES = {"intake", "xintake"}

# Other operator-side tooling legitimately writes the registry and structured
# discovery store while most agent roles are running. This is one working tree
# with live timers in it, so "did this file change" cannot tell an agent's edit
# from a neighbour's.
#
# Failing on that would be wrong twice over: it fails runs that did nothing
# wrong, and a gate that cries wolf is a gate somebody switches off. So a
# change here that is outside the role's remit is reported and not fatal,
# and the content rules below carry the weight instead. Those run on every
# role: whoever wrote a registry block, it must name an allowlisted host and
# the pinned query, and a discovery change must leave the immutable transaction
# chain and every generated projection valid. What an out-of-remit review agent
# could get away with is
# registering a thread that already passes every registry rule, which is
# visible in the next `git diff` and is not a capability worth the noise.
SHARED_PATHS = frozenset({"sources.toml"})


def is_discovery_path(rel: str) -> bool:
    """Whether *rel* is canonical or generated discovery state."""
    return rel == "DISCOVERY.md" or rel.startswith("discovery/")


def is_shared_path(rel: str) -> bool:
    return rel in SHARED_PATHS or is_discovery_path(rel)

# Files kept in full before the run, so the scan sees added lines rather than
# whole files. Everything else is covered by its hash alone.
SNAPSHOT: tuple[str, ...] = (
    "sources.toml", "revision-reviews.toml", "BACKLOG.md",
)

# Append-only by project rule: revision-reviews.toml and corrections.toml are
# additive classification and correction logs, and archive/index.jsonl is the
# append-only poll record. Until now the "only append" half of that rule lived
# in the agent prompts, which docs/design/agent-sandbox.md is explicit is not
# a control: the secret scan below reads added lines, so a run that rewrote
# an existing entry and added nothing would pass it. The prefix check in
# append_only_integrity is the deterministic half.
#
# archive/index.jsonl is defence in depth: archive/ writes are outside every
# role's remit and owned by the capture runner, so a run cannot reach the file
# without already failing the remit check. But the check costs one comparison
# and the index is the file the change record is rebuilt from. corrections.toml
# is in no current role's remit either; the check keys on the file, not the
# role, so it holds the day a role gains it.
APPEND_ONLY: tuple[str, ...] = (
    "revision-reviews.toml", "corrections.toml", "archive/index.jsonl",
)

# .env names whose values are public by design and appear across the site.
PUBLIC_ENV = {"SITE_URL"}

SECRET_SHAPES = (
    (re.compile(r"\bnsec1[023456789acdefghjklmnpqrstuvwxyz]{58}\b"),
     "a nostr secret key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key block"),
    (re.compile(r"\bAAAA[A-Za-z0-9%]{40,}\b"), "an X bearer token"),
    # No generic "40 opaque characters" rule for the Cloudflare token: a git
    # SHA is 40 characters too, and the sweep agent cites pinned commits by
    # hash all day. The literal value read from .env catches that token
    # exactly, which is better than a shape that cries wolf.
    (re.compile(r"(?m)^\s*/home/"), "a filesystem path under /home"),
    (re.compile(r"\bssh://|\bssh -|StrictHostKeyChecking"), "an SSH invocation"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_visible() -> list[str]:
    """Repository-relative paths git would consider, minus the archive.

    Tracked files plus untracked files that are not ignored: the set a commit
    could carry. archive/ is excluded because the capture timer writes there
    concurrently, so a change under it says nothing about the agent.
    """
    seen: set[str] = set()
    for args in (["git", "ls-files", "-z"],
                 ["git", "ls-files", "-z", "--others", "--exclude-standard"]):
        result = subprocess.run(args, cwd=ROOT, capture_output=True, check=True)
        seen.update(p for p in result.stdout.decode().split("\0") if p)
    return sorted(p for p in seen if not p.startswith("archive/"))


def manifest() -> dict[str, str]:
    out = {}
    for rel in git_visible():
        path = ROOT / rel
        try:
            if path.is_file() and not path.is_symlink():
                out[rel] = sha256(path)
        except OSError:
            continue
    return out


def in_remit(rel: str, role: str) -> bool:
    for allowed in ROLES[role]:
        if allowed.endswith("/"):
            if rel.startswith(allowed):
                return True
        elif rel == allowed:
            return True
    return False


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def added_lines(before: Path, after: Path) -> list[str]:
    """Lines present in `after` that `before` did not have.

    A set difference rather than a diff: the question is what text is now in
    the file that was not there before, wherever it ended up. Reordering is
    not interesting here, and the registry's own ordering is checked by
    check_registry.py.
    """
    old = set(read_text(before).splitlines()) if before.exists() else set()
    return [line for line in read_text(after).splitlines()
            if line.strip() and line not in old]


def env_secrets() -> tuple[list[tuple[str, str]], str | None]:
    """Literal secret values from .env, to search for verbatim.

    Values of PUBLIC_* names and SITE_URL are published deliberately and
    appear all over the site; searching for them would fail every run.
    """
    env = ROOT / ".env"
    if not env.exists():
        return [], (".env is not readable, so the gate cannot search for "
                    "literal secret values. A gate running without its list "
                    "is not a gate")
    secrets = []
    for line in read_text(env).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name.startswith("PUBLIC_") or name in PUBLIC_ENV:
            continue
        if len(value) < 12 or value.lower() in {"true", "false"}:
            continue
        secrets.append((name, value))
    return secrets, None


def private_needles() -> list[str]:
    path = ROOT / "site/tools/private-tokens.json"
    if not path.exists():
        return []
    try:
        return [entry["needle"] for entry in json.loads(read_text(path))
                if entry.get("needle")]
    except (ValueError, TypeError, KeyError):
        return []


def scan_added(role: str, run_dir: Path, changed: list[str]) -> list[str]:
    """Look for anything that must never appear in agent-written text."""
    problems: list[str] = []
    secrets, env_problem = env_secrets()
    if env_problem:
        problems.append(env_problem)
    needles = private_needles()

    for rel in changed:
        if not in_remit(rel, role) and not is_shared_path(rel):
            continue  # already reported as out of remit; do not scan twice
        path = ROOT / rel
        if not path.is_file():
            continue
        before = run_dir / "before" / rel
        if rel in SNAPSHOT and before.exists():
            text = "\n".join(added_lines(before, path))
        else:
            text = read_text(path)
        if not text:
            continue
        for name, value in secrets:
            if value in text:
                problems.append(f"{rel}: contains the literal value of {name}")
        for needle in needles:
            if needle.lower() in text.lower():
                problems.append(f"{rel}: contains an operator needle from "
                                f"site/tools/private-tokens.json")
                break
        for pattern, what in SECRET_SHAPES:
            match = pattern.search(text)
            if match:
                problems.append(f"{rel}: added text looks like {what} "
                                f"({match.group(0)[:24]}...)")
    return problems


def structured_discovery_integrity() -> list[str]:
    """Ask the store owner to validate canonical and generated discovery.

    The guard deliberately does not duplicate transaction-chain or rendering
    rules. A discovery writer may run concurrently with non-intake roles, but
    its output is shared only when the store's single read-only validator says
    the immutable chain, candidate projections, state, views, index and
    migration bundle all agree.
    """
    try:
        from discovery_store import validate_store

        validate_store(ROOT)
    except Exception as exc:  # validation failures are evidence, not crashes
        return [f"structured discovery validation failed: {exc}"]
    return []


def append_only_integrity(run_dir: Path) -> list[str]:
    """The append-only files must gain content, never lose or change it.

    A pure append leaves the before-text as a verbatim prefix of the
    after-text. Anything else (a rewritten entry, a truncated file, a
    reordering) means history was edited, which is the one thing the
    corrections and review logs exist to make visible.
    """
    problems = []
    for rel in APPEND_ONLY:
        before = run_dir / "before" / rel
        if not before.exists():
            continue  # the file did not exist when the run started
        old = before.read_bytes()
        try:
            new = (ROOT / rel).read_bytes()
        except OSError:
            problems.append(
                f"{rel}: existed before the run and is unreadable or gone "
                f"now. It is append-only")
            continue
        if not new.startswith(old):
            problems.append(
                f"{rel}: existing content changed during the run. It is "
                f"append-only: new entries go at the end, and nothing already "
                f"there is rewritten or moved")
    return problems


def approve_x_captures(wanted: list[str], before_registry: Path,
                       new: dict) -> tuple[list[str], list[str]]:
    """The xintake role asks in post permalinks, not source ids.

    A request is approved when it is the exact `url` of an [[x_post]] block
    this run added to the registry. Anything else (a bare id, a URL that was
    already registered, a URL no block carries, anything not an x.com
    permalink) is refused, so the driver ingests only what this run's
    validated registry delta names. The block's host is already pinned to
    x.com by check_registry.py, which runs before this list is acted on.
    """
    old_urls: set[str] = set()
    if before_registry.exists():
        old = tomllib.loads(read_text(before_registry))
        old_urls = {str(b.get("url", ""))
                    for b in old.get("x_post", []) if isinstance(b, dict)}
    new_urls = {str(b.get("url", ""))
                for b in new.get("x_post", []) if isinstance(b, dict)}

    approved, problems = [], []
    for url in wanted:
        if not url.startswith("https://x.com/"):
            problems.append(f"first capture requested for {url}, which is "
                            f"not an x.com post permalink")
        elif url in new_urls and url not in old_urls:
            approved.append(url)
        elif url in new_urls:
            problems.append(f"first capture requested for {url}, which this "
                            f"run did not register")
        else:
            problems.append(f"first capture requested for {url}, which is "
                            f"not in the registry")
    return approved, problems


def approve_captures(role: str, run_dir: Path) -> tuple[list[str], list[str]]:
    """Which requested first captures the driver may actually run.

    The agent no longer calls capture.py. It writes source ids here (post
    URLs for the xintake role), and only ones this run registered, in a
    registry that passed check_registry.py, are handed back to the driver. A
    poisoned block is therefore rejected before anything fetches it, rather
    than after.
    """
    requests = run_dir / "capture-requests.txt"
    if not requests.exists():
        return [], []
    wanted = [line.strip() for line in read_text(requests).splitlines()
              if line.strip() and not line.startswith("#")]
    if not wanted:
        return [], []
    if role not in REGISTERING_ROLES:
        return [], [f"{len(wanted)} first capture(s) requested, but the "
                    f"{role} role registers nothing"]

    before_registry = run_dir / "before" / "sources.toml"
    new = tomllib.loads(read_text(ROOT / "sources.toml"))
    if role == "xintake":
        return approve_x_captures(wanted, before_registry, new)

    old_ids: set[str] = set()
    if before_registry.exists():
        old = tomllib.loads(read_text(before_registry))
        old_ids = {b["id"] for table in ("source", "x_post", "nostr_post")
                   for b in old.get(table, []) if "id" in b}
    new_ids = {b["id"] for table in ("source", "x_post", "nostr_post")
               for b in new.get(table, []) if "id" in b}

    approved, problems = [], []
    for ident in wanted:
        if ident not in new_ids:
            problems.append(f"first capture requested for {ident}, which is "
                            f"not in the registry")
        elif ident in old_ids:
            problems.append(f"first capture requested for {ident}, which this "
                            f"run did not register")
        else:
            approved.append(ident)
    return approved, problems


def validate_intake_outbox(role: str, run_dir: Path) -> list[str]:
    """Validate the protected decision data for an intake role.

    Completeness is intentional: a missing body is represented by ``retry``;
    absence is never interpreted as a decision. Registry relationships are
    checked against both snapshots so a model cannot claim it registered an
    old source or cite a newly invented id as already present.
    """
    if role not in REGISTERING_ROLES:
        return []
    outbox = run_dir / "intake-verdicts.jsonl"
    try:
        import intake_verdicts
        verdicts = intake_verdicts.validate_paths(
            packet_path=run_dir / "intake-packet.json",
            outbox_path=run_dir / "intake-verdicts.jsonl",
            before_registry_path=run_dir / "before" / "sources.toml",
            after_registry_path=ROOT / "sources.toml")
    except (OSError, ValueError) as exc:
        return [f"intake verdict outbox was rejected: {exc}"]
    text = read_text(outbox)
    problems = []
    secrets, _env_problem = env_secrets()  # scan_added reports missing .env
    for name, value in secrets:
        if value in text:
            problems.append(
                f"intake-verdicts.jsonl: contains the literal value of {name}")
    for needle in private_needles():
        if needle.lower() in text.lower():
            problems.append(
                "intake-verdicts.jsonl: contains an operator needle from "
                "site/tools/private-tokens.json")
            break
    scan_values = [text, *(row["reason"] for row in verdicts)]
    for pattern, what in SECRET_SHAPES:
        match = next((found for value in scan_values
                      if (found := pattern.search(value))), None)
        if match:
            problems.append(
                f"intake-verdicts.jsonl: text looks like {what} "
                f"({match.group(0)[:24]}...)")
    return problems


def do_before(role: str, run_dir: Path) -> int:
    (run_dir / "before").mkdir(parents=True, exist_ok=True)
    state = {"role": role, "files": manifest()}
    (run_dir / "manifest.json").write_text(json.dumps(state))
    for rel in sorted(set(SNAPSHOT) | set(APPEND_ONLY)):
        src = ROOT / rel
        if src.exists():
            dst = run_dir / "before" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    print(f"agent-guard: {len(state['files'])} file(s) recorded for the "
          f"{role} run")
    return 0


def do_after(role: str, run_dir: Path) -> int:
    state_path = run_dir / "manifest.json"
    if not state_path.exists():
        print(f"agent-guard: no manifest at {state_path}; the before pass did "
              f"not run, so this run cannot be checked", file=sys.stderr)
        return 1
    state = json.loads(read_text(state_path))
    if state.get("role") != role:
        print(f"agent-guard: manifest is for the {state.get('role')} role, "
              f"not {role}", file=sys.stderr)
        return 1

    before_files: dict[str, str] = state["files"]
    after_files = manifest()
    changed = sorted(
        rel for rel in set(before_files) | set(after_files)
        if before_files.get(rel) != after_files.get(rel))

    problems = []
    shared_notes = []
    discovery_changed = [rel for rel in changed if is_discovery_path(rel)]
    for rel in changed:
        # Intake roles never edit canonical transactions or their generated
        # projections. Their driver binds decisions to candidate heads, so a
        # neighbouring lane may legitimately advance discovery during the
        # deprivileged phase; the operator-side applier then stale-rejects the
        # whole outbox if any selected head changed.
        if is_discovery_path(rel) and role in REGISTERING_ROLES:
            shared_notes.append(rel)
            continue
        if in_remit(rel, role):
            continue
        if is_shared_path(rel):
            shared_notes.append(rel)
            continue
        if rel not in after_files:
            problems.append(f"{rel}: deleted, and outside the {role} remit")
        elif rel not in before_files:
            problems.append(f"{rel}: created, and outside the {role} remit")
        else:
            problems.append(f"{rel}: modified, and outside the {role} remit")

    problems += scan_added(role, run_dir, changed)

    # Runs on every role, whatever `changed` holds: the check is cheap, an
    # untouched file passes it trivially, and archive/index.jsonl never
    # appears in `changed` because archive/ is outside the manifest.
    problems += append_only_integrity(run_dir)

    if "sources.toml" in changed:
        registry = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("check_registry.py")),
             "--registry", str(ROOT / "sources.toml"),
             "--before", str(run_dir / "before" / "sources.toml")],
            cwd=ROOT, capture_output=True, text=True)
        if registry.returncode != 0:
            problems.append("the registry changes were rejected:\n    " +
                            "\n    ".join(registry.stderr.strip().splitlines()))

    if discovery_changed:
        # The sandbox account cannot write discovery/. A valid change during
        # an intake run is therefore a neighbouring operator-side lane, which
        # the packet's candidate heads handle optimistically. Invalid changes
        # still fail here; selected-head changes fail later, all-or-nothing,
        # in the verdict applier.
        problems += structured_discovery_integrity()

    problems += validate_intake_outbox(role, run_dir)

    approved, capture_problems = approve_captures(role, run_dir)
    problems += capture_problems

    if problems:
        print(f"agent-guard: the {role} run is REJECTED "
              f"({len(problems)} problem(s)):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(f"\nThe agent's edits have been left exactly as they are, in the "
              f"working tree and in {run_dir}. Nothing was reverted: what an "
              f"injected run tried to do is the evidence. Read the diff before "
              f"committing or reverting anything, and note that a dirty tree "
              f"outside archive/ already stops the scheduled publish.",
              file=sys.stderr)
        return 1

    (run_dir / "approved-captures.txt").write_text(
        "".join(f"{ident}\n" for ident in approved))
    touched = [rel for rel in changed if in_remit(rel, role)]
    print(f"agent-guard: the {role} run is in remit: {len(touched)} file(s) "
          f"changed, {len(approved)} first capture(s) approved")
    for rel in (path for path in shared_notes if not is_discovery_path(path)):
        print(f"agent-guard: note, {rel} also changed during this run and is "
              f"outside the {role} remit. It is shared with operator-side "
              f"discovery or ingest tooling, so this is usually a neighbour "
              f"rather than the agent; its deterministic validation passed. "
              f"Check `git diff "
              f"-- {rel}` if the run looks odd.")
    shared_discovery = [path for path in shared_notes
                        if is_discovery_path(path)]
    if shared_discovery:
        print(f"agent-guard: note, {len(shared_discovery)} structured discovery "
              f"path(s) also changed outside the {role} remit. The canonical "
              "chain and every generated projection validated, so this is "
              "accepted as a concurrent operator-side update. Check `git "
              "diff -- DISCOVERY.md discovery/` if the run looks odd.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument("--role", required=True, choices=sorted(ROLES))
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir if args.run_dir.is_absolute() else ROOT / args.run_dir
    if args.phase == "before":
        return do_before(args.role, run_dir)
    return do_after(args.role, run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
