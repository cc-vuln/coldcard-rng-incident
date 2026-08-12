#!/usr/bin/env python3
"""Commit guard-passed pipeline output, deterministically, as the operator.

Until 8 Aug 2026 the churn this repository accumulates between human sessions
— poll captures, review classifications, registrations, discovery verdicts,
generated site indexes — sat uncommitted until a person swept it up, and while
it sat there publish-scheduled.sh refused to deploy: its clean-tree guard reads
any uncommitted state as "not a reconstructible release", so the public site fell
behind the record for exactly as long as the record was busiest. On 8 Aug 2026
the operator directed the pipeline to run unattended, with human review
retroactive rather than a gate. This script is the commit step of that
directive. It is deliberately not an LLM: the judgement calls were made when
the staging list and the gates below were written down, and what runs every
hour is a checklist.

What it will commit is a fixed allowlist — the pipeline's own outputs and the
project's tracked documentation — staged by name, never `git add -A` on the
tree. `.work/`, `.env` and everything gitignored are unreachable from here, so
a rejected agent run's evidence, an operator's kill switch, and secrets cannot
be swept into a commit by this script no matter what state the tree is in.

Every precondition prints its reason when it blocks, and a block exits 1:

  1. HEAD is on main. Publishing automation never runs from a feature branch.
  2. `.no-publish` is absent. It is the operator's kill switch, untracked on
     purpose, and the commit timer respects it for the same reason the publish
     timer does: unattended steps stop when somebody is working.
  3. `just test` passes.
  4. `just audit-core` passes: the audit WITHOUT the review gate.
     Unreviewed diffs make the tree unpublishable (publish-scheduled has its
     own skip for them), not uncommittable, and with polls finding changes
     every 30 minutes against a two-hourly review pass, gating commits on
     classification would stall the committer most of the day. capture.py
     still exits 21 when the poll holds the writer lock, which is contention
     rather than a finding, so 21s are retried across a typical poll window.
  5. No unresolved agent-guard run since the last commit. agent_guard.py
     writes approved-captures.txt only when a run passes; a run directory
     without one was rejected, is still in flight, or died mid-run. All three
     block: a rejected run's edits are evidence a person has to read, and
     committing them would launder an injection into the record.
  6. The site build lock is free, and is then HELD across staging and the
     commit. A publish build must read one stable HEAD for its version stamp.
  7. The archive writer lock is free, and is then HELD across staging and the
     commit. Staging archive/ while a poll is mid-write could commit a
     snapshot without its index.jsonl line, which is the change record this
     repository exists to get right.

The commit message is assembled from the staged diff alone — change class,
counts of snapshot captures, registrations, classifications and corrections —
and linted before use: no Co-Authored-By trailer, no "generated with", none
of the operator needles in site/tools/private-tokens.json. This project
publishes pseudonymously and its commit stream is a provenance channel;
AGENTS.md's no-attribution rule applies to automation exactly as it applies
to people, so a message that fails the lint refuses the commit rather than
shipping.

Modes: with no argument or --dry-run every check runs and the staged set and
message are printed, but nothing is committed; a temporary index is used so
even the real index is untouched. --yes commits. Exit codes: 0 committed or
nothing to do, 1 a precondition blocked, 2 usage.

Installed as record-commit.timer (see the .example units beside this file).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from archive_lock import ArchiveLockBusy, archive_lock  # noqa: E402

# The whole of what this script may stage. Paths, not patterns: the list is
# the machine-readable half of "guard-passed pipeline output", and anything
# not named here — .work/, .env, an operator's scratch file — is unreachable.
# site/src/data/ lives inside site/src/ and is named anyway because it is the
# generated-index path. The scheduled publisher regenerates that index under
# the build lock before its build; the next ordinary commit tick must also be
# able to carry any index produced by a manual build.
STAGE_PATHS: tuple[str, ...] = (
    "archive/",
    "revision-reviews.toml",
    "sources.toml",
    "DISCOVERY.md",
    "discovery/",
    "quarantine/",
    "corrections.toml",
    "site/src/",
    "site/src/data/",
    "CHANGELOG.md",
    "AGENTS.md",
    "docs/",
    "BACKLOG.md",
    "justfile",
    "scripts/",
)

# Message lint. The strings are matched case-insensitively against the whole
# assembled message, subject and body. site/tools/private-tokens.json adds
# the operator's own needles, read the same way agent_guard.py reads them.
FORBIDDEN_MESSAGE_STRINGS: tuple[str, ...] = (
    "co-authored-by",
    "generated with",
)

# capture.py's exit code for writer-lock contention. `just audit` takes a
# shared lock, so a poll mid-run turns the audit gate into a 21; that is a
# reason to retry, not a reason to stay blocked.
LOCK_BUSY_EXIT = 21
BUILD_LOCK_PATH = Path("/tmp/cc-build.lock")
# A tier poll can hold the lock for a quarter of an hour now that browser
# captures and the chain monitors share the tick (observed 9 Aug 2026); the
# retry span covers that. Skipping a tick costs nothing.
AUDIT_RETRY_WAITS_SECONDS = (120, 180, 240, 300)

GUARD_RUNS = Path(".work/agent-guard")
RUN_ID_RE = re.compile(r"^(\d{8}T\d{6}Z)-")


class BuildLockBusy(RuntimeError):
    """Raised when a site build or publish owns the shared build lock."""


@contextlib.contextmanager
def build_lock(path: Path = BUILD_LOCK_PATH) -> Iterator[None]:
    """Hold the same non-blocking lock as every Astro build.

    The committer changes HEAD. A build reads HEAD near the end when it writes
    ``version.json``, so committing during route generation can stamp a commit
    different from the one whose files the earlier routes read. The publisher
    skips when this lock is busy; the committer follows the same rule and lets
    its next hourly tick retry.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise BuildLockBusy(str(path)) from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def git(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, env=env, check=False,
    )


def current_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def last_commit_epoch(root: Path) -> int | None:
    """Commit time of HEAD, or None on a repository with no commits yet."""
    result = git(root, "log", "-1", "--format=%ct")
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return int(result.stdout.strip())


def run_id_epoch(name: str) -> int | None:
    match = RUN_ID_RE.match(name)
    if not match:
        return None
    stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ")
    return int(stamp.replace(tzinfo=timezone.utc).timestamp())


def unresolved_guard_runs(root: Path) -> list[str]:
    """Guard run directories newer than HEAD that never recorded a pass.

    agent_guard.py's after pass writes approved-captures.txt only on success,
    so its absence names a run that was rejected, is still running, or died
    between passes. A rejected run leaves its edits in the tree on purpose —
    what an injection tried is the evidence — and a committer that swept them
    in would be the injection's last mile. "Since the last commit" scopes the
    check: older unresolved runs were already sitting in the tree when a
    person made that commit, so they have been seen.
    """
    runs_dir = root / GUARD_RUNS
    if not runs_dir.is_dir():
        return []
    since = last_commit_epoch(root)
    unresolved = []
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / "approved-captures.txt").exists():
            continue
        started = run_id_epoch(entry.name)
        # A run whose id predates HEAD still blocks if the directory has been
        # touched since: a slow run that started before the commit and is
        # still going has no verdict yet either way. Both comparisons are
        # >=: a run stamped or touched in the commit's own second cannot be
        # ordered against it, and the safe side of ambiguous is blocked.
        marker = started if started is not None else int(entry.stat().st_mtime)
        touched = int(entry.stat().st_mtime)
        if since is None or marker >= since or touched >= since:
            unresolved.append(entry.name)
    return unresolved


def _staging_env(index_file: Path | None) -> dict | None:
    """The environment git staging runs under.

    With an index file, GIT_INDEX_FILE redirects every index operation at a
    throwaway file, which is how --dry-run stages exactly what --yes would
    without touching the real index. Without one, git inherits the
    environment as-is.
    """
    if index_file is None:
        return None
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = str(index_file)
    return env


def _prepare_temp_index(root: Path, env: dict) -> None:
    """Seed a throwaway index from HEAD so --dry-run can stage without
    touching the real one."""
    if last_commit_epoch(root) is not None:
        git(root, "read-tree", "HEAD", env=env)
    else:
        git(root, "read-tree", "--empty", env=env)


def stage(root: Path, index_file: Path | None = None) -> list[tuple[str, str]]:
    """Stage the allowlist and return the staged (status, path) pairs.

    `-A` scoped to the named paths, so deletions inside them are staged too.
    Missing paths are filtered out first: a fresh clone has no quarantine/
    and git would fail the whole add for one absent name.
    """
    env = _staging_env(index_file)
    existing = [p for p in STAGE_PATHS if (root / p.rstrip("/")).exists()]
    if existing:
        result = git(root, "add", "-A", "--", *existing, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"git add failed: {result.stderr.strip()}")
    return staged_changes(root, index_file=index_file)


def staged_changes(root: Path, index_file: Path | None = None) -> list[tuple[str, str]]:
    result = git(root, "diff", "--cached", "--name-status", "-z",
                 env=_staging_env(index_file))
    out = []
    fields = [f for f in result.stdout.split("\0") if f]
    # name-status -z emits status\0path\0 (and for renames, old\0new\0).
    i = 0
    while i < len(fields):
        status = fields[i]
        i += 1
        if status.startswith(("R", "C")):
            i += 1  # skip the old name; the new one follows
        if i < len(fields):
            out.append((status[0], fields[i]))
            i += 1
    return out


@dataclass
class Summary:
    """What a staged set contains, in the units the commit message reports."""

    snapshots: int = 0          # new (source, timestamp) captures in archive/snapshots
    social_captures: int = 0    # new capture dirs under archive/x and archive/nostr
    registrations: int = 0      # new [[source]]/[[x_post]]/[[nostr_post]] blocks
    classifications: int = 0    # new [[revision]] entries
    corrections: int = 0        # new [[correction]] entries
    rotations: int = 0          # new discovery/assessed-*.md files
    quarantines: int = 0        # new quarantine/*.toml files
    areas: dict[str, int] = field(default_factory=dict)  # top area -> files
    statuses: list[tuple[str, str]] = field(default_factory=list)


def _area_of(path: str) -> str:
    if path.startswith("archive/"):
        return "archive/"
    if path.startswith("site/"):
        return "site/"
    if "/" in path:
        return path.split("/", 1)[0] + "/"
    return path


def summarize(root: Path, staged: list[tuple[str, str]],
              index_file: Path | None = None) -> Summary:
    summary = Summary(statuses=staged)
    snapshot_keys = set()
    social_keys = set()
    for status, path in staged:
        summary.areas[_area_of(path)] = summary.areas.get(_area_of(path), 0) + 1
        if status != "A":
            continue
        snap = re.match(r"archive/snapshots/([^/]+)/([^/.]+)\.", path)
        if snap:
            snapshot_keys.add(snap.groups())
            continue
        social = re.match(r"archive/(?:x|nostr)/([^/]+/[^/]+)/", path)
        if social:
            social_keys.add(social.group(1))
            continue
        if path.startswith("discovery/"):
            summary.rotations += 1
        elif path.startswith("quarantine/"):
            summary.quarantines += 1
    summary.snapshots = len(snapshot_keys)
    summary.social_captures = len(social_keys)

    def added_blocks(tracked: str, table: str) -> int:
        if not any(p == tracked for _, p in staged):
            return 0
        diff = git(root, "diff", "--cached", "-U0", "--", tracked,
                   env=_staging_env(index_file))
        return sum(1 for line in diff.stdout.splitlines()
                   if line.startswith("+[[") and line[1:].startswith(f"[[{table}]]"))

    summary.registrations = sum(
        added_blocks("sources.toml", table)
        for table in ("source", "x_post", "nostr_post"))
    summary.classifications = added_blocks("revision-reviews.toml", "revision")
    summary.corrections = added_blocks("corrections.toml", "correction")
    return summary


# Which staged paths make a commit agent work rather than record churn or a
# site change. These are the files the four roles are allowed to write (plus
# the queue rotations and quarantine moves their drivers produce), so their
# presence without any archive/ change means the churn came from an agent run.
AGENT_PATHS = (
    "sources.toml", "revision-reviews.toml", "DISCOVERY.md", "BACKLOG.md",
    "corrections.toml", "discovery/", "quarantine/",
)


def classify(staged: list[tuple[str, str]]) -> str:
    """The message prefix, by dominant change class.

    Precedence follows how the project's own history reads: archive churn
    dominates whatever rides with it ("record: ... captures, registrations and
    classifications"); agent-remit files without archive churn are agent work;
    what is left is either the site or the tooling behind it ("automation:",
    the prefix the 8 Aug policy commits used).
    """
    paths = [p for _, p in staged]
    if any(p.startswith("archive/") for p in paths):
        return "record"
    if any(any(p == a or p.startswith(a) for a in AGENT_PATHS) for p in paths):
        return "agents"
    if any(p.startswith("site/") for p in paths):
        return "site"
    return "automation"


def build_message(summary: Summary) -> str:
    prefix = classify(summary.statuses)
    parts = []
    if summary.snapshots:
        parts.append(f"{summary.snapshots} new snapshot capture(s)")
    if summary.social_captures:
        parts.append(f"{summary.social_captures} new social capture(s)")
    if summary.registrations:
        parts.append(f"{summary.registrations} registration(s)")
    if summary.classifications:
        parts.append(f"{summary.classifications} classification(s)")
    if summary.corrections:
        parts.append(f"{summary.corrections} correction(s)")
    if summary.rotations:
        parts.append(f"{summary.rotations} rotated discovery file(s)")
    if summary.quarantines:
        parts.append(f"{summary.quarantines} quarantined registration file(s)")
    if not parts:
        total = len(summary.statuses)
        areas = ", ".join(sorted(summary.areas))
        parts.append(f"{total} file(s) across {areas}")
    subject = f"{prefix}: " + ", ".join(parts)

    body_lines = [
        "Unattended pipeline output committed under the 8 Aug 2026 directive:",
        "guard-passed agent work, capture churn and generated indexes, staged",
        "from the fixed allowlist in scripts/record_commit.py. Review is",
        "retroactive.",
        "",
    ]
    for area in sorted(summary.areas):
        body_lines.append(f"{area}: {summary.areas[area]} file(s)")
    return subject + "\n\n" + "\n".join(body_lines) + "\n"


def private_needles(root: Path) -> list[str]:
    path = root / "site" / "tools" / "private-tokens.json"
    if not path.exists():
        return []
    try:
        return [entry["needle"] for entry in json.loads(path.read_text())
                if entry.get("needle")]
    except (ValueError, TypeError, KeyError):
        return []


def lint_message(root: Path, message: str) -> list[str]:
    """What the no-attribution rule forbids in a commit message, found or not.

    The lint runs on the assembled message even though this template contains
    none of it: the template is code and code drifts, and the rule exists
    because four commits carrying a tool trailer had to be stripped from
    pushed history on 7 Aug 2026. A check that can only fail after an edit
    it did not review is still the check that catches the edit.
    """
    problems = []
    lowered = message.lower()
    for forbidden in FORBIDDEN_MESSAGE_STRINGS:
        if forbidden in lowered:
            problems.append(f"message contains '{forbidden}'")
    for needle in private_needles(root):
        if needle.lower() in lowered:
            problems.append("message contains an operator needle from "
                            "site/tools/private-tokens.json")
    return problems


def run_just(root: Path, recipe: str) -> int:
    return subprocess.run(["just", recipe], cwd=root, check=False).returncode


def audit_with_retry(root: Path, out) -> bool:
    """`just audit-core`, retrying through writer-lock contention.

    The 30-minute poll holds the exclusive writer lock for minutes at a time
    and the audit's shared lock fails non-blocking behind it, so a bare 21 is
    the common case, not a finding. Retries spanning a typical poll separate
    "the poll was running" from a gate that genuinely fails; still locked
    after that is treated as blocked and the next timer tick tries again.
    """
    rc = run_just(root, "audit-core")
    for wait in AUDIT_RETRY_WAITS_SECONDS:
        if rc != LOCK_BUSY_EXIT:
            break
        print(f"record-commit: audit hit the writer lock (exit 21), "
              f"retrying in {wait}s", file=out)
        time.sleep(wait)
        rc = run_just(root, "audit-core")
    return rc == 0


class Blocked(RuntimeError):
    """A precondition failed; the message is the reason, printed verbatim."""


def check_preconditions(root: Path, out=sys.stdout) -> None:
    branch = current_branch(root)
    if branch != "main":
        raise Blocked(f"HEAD is on {branch or 'a detached commit'}, not main")
    print("precondition: on main", file=out)

    kill = root / ".no-publish"
    if kill.exists():
        try:
            reason = kill.read_text().strip()
        except OSError:
            reason = ""
        raise Blocked(f".no-publish is present{': ' + reason if reason else ''}")
    print("precondition: no .no-publish", file=out)

    if run_just(root, "test") != 0:
        raise Blocked("`just test` fails; the pipeline's output is not "
                      "committed on top of a red tree")
    print("precondition: just test passes", file=out)

    if not audit_with_retry(root, out):
        raise Blocked("`just audit-core` fails (writer-lock 21s were "
                      "retried through a poll window; it still fails)")
    print("precondition: just audit-core passes (no review gate)", file=out)

    unresolved = unresolved_guard_runs(root)
    if unresolved:
        raise Blocked(
            f"{len(unresolved)} agent-guard run(s) since the last commit have "
            "no pass verdict (rejected, in flight, or died mid-run); their "
            "edits are evidence and stay uncommitted until a person reads "
            f"them: {', '.join(unresolved)}")
    print("precondition: every agent-guard run since the last commit passed",
          file=out)


def commit_staged(root: Path, message: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "commit", "-F", "-"],
        input=message, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git commit failed: {result.stderr.strip()}")
    return git(root, "rev-parse", "--short", "HEAD").stdout.strip()


def push_committed(root: Path) -> None:
    """Push after every commit, not only after a deploy.

    Until 12 Aug 2026 the only push followed a successful publish, so routine
    publish skips held the record's off-machine copy hostage to the deploy
    gates: over 10-12 Aug two days of commits sat unpushed while publishes
    skipped. The pushed history is the only off-machine copy until the backup
    item lands, and /cite/ points readers at it. HEAD is known to be on main
    (precondition 1). A failed push changes nothing here: the next commit
    tick retries, and publish-scheduled still pushes after a deploy as
    before. Never fails the tick: the commit is the durable thing.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "push"],
            capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"record-commit: push could not run ({exc}); the next tick "
              "retries", file=sys.stderr)
        return
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        print(f"record-commit: push failed "
              f"({detail[-1] if detail else 'no output'}); the next tick "
              "retries", file=sys.stderr)
        return
    print("record-commit: pushed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="report everything, commit nothing (the default)")
    mode.add_argument("--yes", action="store_true",
                      help="actually commit")
    args = parser.parse_args(argv)
    dry_run = not args.yes

    try:
        check_preconditions(ROOT)
    except Blocked as exc:
        print(f"record-commit: blocked, {exc}", file=sys.stderr)
        return 1

    if dry_run:
        # A temporary index makes the dry run exact — same staged set, same
        # message — without touching the index the operator may be using.
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index"
            _prepare_temp_index(ROOT, _staging_env(index))
            staged = stage(ROOT, index_file=index)
            if not staged:
                print("record-commit: nothing to commit")
                return 0
            summary = summarize(ROOT, staged, index_file=index)
            message = build_message(summary)
            print(f"record-commit: would stage {len(staged)} path(s):")
            for status, path in staged:
                print(f"  {status} {path}")
            problems = lint_message(ROOT, message)
            if problems:
                print("record-commit: the assembled message FAILS the "
                      "no-attribution lint:", file=sys.stderr)
                for problem in problems:
                    print(f"  - {problem}", file=sys.stderr)
                return 1
            print("record-commit: would commit with message:\n---")
            print(message, end="")
            print("---\nrecord-commit: dry run, nothing committed")
            return 0

    # --yes. Lock order is build, then archive. publish-scheduled holds the
    # build lock while it audits; taking the archive lock first here would let
    # the two processes wait on each other. Both acquisitions are non-blocking,
    # so routine contention simply defers this hourly tick.
    try:
        with build_lock():
            with archive_lock("record-commit"):
                staged = stage(ROOT)
                if not staged:
                    print("record-commit: nothing to commit")
                    return 0
                message = build_message(summarize(ROOT, staged))
                problems = lint_message(ROOT, message)
                if problems:
                    # The staged changes stay staged: the next tick re-derives
                    # them, and an operator looking at the tree sees what was
                    # about to ship.
                    print("record-commit: blocked, the assembled message fails "
                          "the no-attribution lint:", file=sys.stderr)
                    for problem in problems:
                        print(f"  - {problem}", file=sys.stderr)
                    return 1
                short = commit_staged(ROOT, message)
    except BuildLockBusy as exc:
        print(f"record-commit: blocked, a build holds {exc}; "
              "the next tick retries", file=sys.stderr)
        return 1
    except ArchiveLockBusy as exc:
        print(f"record-commit: blocked, the archive writer lock is held "
              f"({exc}); the next tick retries", file=sys.stderr)
        return 1
    print(f"record-commit: committed {short} — {message.splitlines()[0]}")
    push_committed(ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
