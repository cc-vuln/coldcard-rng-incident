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
4. every assessed DISCOVERY.md line still contains the candidate it came from
5. requested first captures name sources this run actually registered

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
    # Registers community threads and records verdicts.
    "intake": ("sources.toml", "DISCOVERY.md", ".work/"),
    # Rechecks unverified claims across the editorial pages.
    "sweep": ("site/src/pages/", "sources.toml", "BACKLOG.md", ".work/"),
    # Read-only triage. Records a verdict, registers nothing.
    "xtriage": ("DISCOVERY.md", ".work/"),
}

# Roles whose remit includes registering sources, and therefore asking for a
# first capture. The driver performs the capture itself, after this gate.
REGISTERING_ROLES = {"intake", "sweep"}

# The two files other tooling in this repository legitimately writes while an
# agent is running: `ingest-x.py` and `ingest_nostr.py` register into the
# registry, and the discovery scripts append to the queue and prune entries
# from it once their thread is registered. This is one working tree with live
# timers in it, so "did this file change" cannot tell an agent's edit from a
# neighbour's.
#
# Failing on that would be wrong twice over: it fails runs that did nothing
# wrong, and a gate that cries wolf is a gate somebody switches off. So a
# change here that is outside the role's remit is reported and not fatal,
# and the content rules below carry the weight instead. Those run on every
# role: whoever wrote a registry block, it must name an allowlisted host and
# the pinned query, and whoever moved a queue line, the candidate text must
# survive. What an out-of-remit review agent could get away with is
# registering a thread that already passes every registry rule, which is
# visible in the next `git diff` and is not a capability worth the noise.
SHARED_PATHS = frozenset({"sources.toml", "DISCOVERY.md"})

# Files kept in full before the run, so the scan sees added lines rather than
# whole files. Everything else is covered by its hash alone.
SNAPSHOT: tuple[str, ...] = (
    "sources.toml", "DISCOVERY.md", "revision-reviews.toml", "BACKLOG.md",
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
        if not in_remit(rel, role) and rel not in SHARED_PATHS:
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


def discovery_integrity(run_dir: Path) -> list[str]:
    """Every assessed candidate must still carry the line it came from.

    A verdict is appended to the candidate line, so an assessed line begins
    with the pending line it replaced. Without this an injected run could
    rewrite a candidate on its way past: relabel someone's thread, or point
    the permalink somewhere else, and the queue would carry the change as
    though a person had made it.
    """
    before = run_dir / "before" / "DISCOVERY.md"
    after = ROOT / "DISCOVERY.md"
    if not before.exists() or not after.exists():
        return []

    def sections(text: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        current = ""
        for line in text.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                out.setdefault(current, [])
            elif line.startswith("- ") and current:
                out[current].append(line)
        return out

    # Everything that is not Pending counts as settled, on both sides. Reading
    # the sections asymmetrically is a bug this check shipped with: "Link
    # review, held for a human decision" was counted as settled in the new
    # file and not in the old, so every line already sitting there looked like
    # a verdict invented during the run, and a clean intake was rejected with
    # eleven false positives.
    def settled(parsed: dict[str, list[str]]) -> list[str]:
        return [line for name, lines in parsed.items()
                if name != "Pending" for line in lines]

    old, new = sections(read_text(before)), sections(read_text(after))
    old_pending = old.get("Pending", [])
    still_pending = set(new.get("Pending", []))
    assessed = settled(new)
    old_assessed = set(settled(old))
    registry_text = read_text(ROOT / "sources.toml")

    problems = []
    for line in old_pending:
        if line in still_pending:
            continue
        if any(entry == line or entry.startswith(line + " ") for entry in assessed):
            continue
        # The discovery scripts prune a pending line once its thread reaches
        # the registry by any route, and they run on their own timer. A line
        # whose URL is now registered left the queue the ordinary way.
        url = re.search(r"\((https?://[^)]+)\)", line)
        if url and url.group(1) in registry_text:
            continue
        problems.append(
            f"DISCOVERY.md: a pending candidate left the queue without its "
            f"text surviving into an assessed line: {line[:90]}")
    for entry in assessed:
        if entry in old_assessed:
            continue
        if any(entry == line or entry.startswith(line + " ")
               for line in old_pending):
            continue
        problems.append(
            f"DISCOVERY.md: an assessed line does not match any candidate "
            f"that was pending: {entry[:90]}")
    return problems


def approve_captures(role: str, run_dir: Path) -> tuple[list[str], list[str]]:
    """Which requested first captures the driver may actually run.

    The agent no longer calls capture.py. It writes source ids here, and only
    ids this run registered, in a registry that passed check_registry.py, are
    handed back to the driver. A poisoned block is therefore rejected before
    anything fetches it, rather than after.
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
    old_ids: set[str] = set()
    if before_registry.exists():
        old = tomllib.loads(read_text(before_registry))
        old_ids = {b["id"] for table in ("source", "x_post", "nostr_post")
                   for b in old.get(table, []) if "id" in b}
    new = tomllib.loads(read_text(ROOT / "sources.toml"))
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


def do_before(role: str, run_dir: Path) -> int:
    (run_dir / "before").mkdir(parents=True, exist_ok=True)
    state = {"role": role, "files": manifest()}
    (run_dir / "manifest.json").write_text(json.dumps(state))
    for rel in SNAPSHOT:
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
    for rel in changed:
        if in_remit(rel, role):
            continue
        if rel in SHARED_PATHS:
            shared_notes.append(rel)
            continue
        if rel not in after_files:
            problems.append(f"{rel}: deleted, and outside the {role} remit")
        elif rel not in before_files:
            problems.append(f"{rel}: created, and outside the {role} remit")
        else:
            problems.append(f"{rel}: modified, and outside the {role} remit")

    problems += scan_added(role, run_dir, changed)

    if "sources.toml" in changed:
        registry = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("check_registry.py")),
             "--registry", str(ROOT / "sources.toml"),
             "--before", str(run_dir / "before" / "sources.toml")],
            cwd=ROOT, capture_output=True, text=True)
        if registry.returncode != 0:
            problems.append("the registry changes were rejected:\n    " +
                            "\n    ".join(registry.stderr.strip().splitlines()))

    if "DISCOVERY.md" in changed:
        problems += discovery_integrity(run_dir)

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
    for rel in shared_notes:
        print(f"agent-guard: note, {rel} also changed during this run and is "
              f"outside the {role} remit. It is shared with the discovery and "
              f"ingest tooling, so this is usually a neighbour rather than the "
              f"agent; its content rules passed either way. Check `git diff "
              f"-- {rel}` if the run looks odd.")
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
