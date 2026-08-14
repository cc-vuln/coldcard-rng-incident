#!/usr/bin/env python3
"""The operator-summary half of `just status`: what is waiting on a person.

`capture.py status` says what is tracked and when each source last moved.
This says what needs a decision: structured discovery queues, quarantined
registrations, host proposals the intake agent declined for an unlisted host,
sources on a failure streak, recorded first-capture failures, and detected
differences the review gate has not classified yet.

Each section reads small files or reuses the tool that owns the rule
(`capture.py diagnose --json` for streaks, `check_reviews.unreviewed` for the
gate's count), so nothing here can drift from what the gates enforce. A
missing optional operator queue means nothing to report. The activated
discovery store is different: a missing or invalid canonical record is shown
as a failure and makes this command non-zero. Stdlib only.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import registry_store

ROOT = Path(__file__).resolve().parent.parent

# Same tables quarantine_registry.py knows how to move.
TABLES = ("source", "x_post", "nostr_post", "x_watch")


def discovery_queue() -> bool:
    """Report actionable discovery states after validating the whole store."""
    print("== structured discovery ==")
    try:
        from discovery_store import DiscoveryStore, validate_store

        store = DiscoveryStore(ROOT)
        with store.locked():
            validate_store(ROOT, lock_held=True)
            candidates = store.list_candidates(lock_held=True)
    except Exception as exc:
        print(f"INVALID: {exc}\n")
        return False

    counts = Counter(str(row.get("state", "unknown")) for row in candidates)
    pending_x = sum(1 for row in candidates
                    if row.get("state") == "pending"
                    and row.get("platform") == "x")
    pending_community = counts.get("pending", 0) - pending_x
    print(f"{len(candidates)} candidate(s); canonical chain and generated "
          "projections validate:")
    print(f"  pending:      {counts.get('pending', 0)} "
          f"({pending_community} community, {pending_x} X)")
    print(f"  deferred:     {counts.get('deferred', 0)}")
    print(f"  human review: {counts.get('human-review', 0)}")
    print(f"  assessed:     {counts.get('assessed', 0)}\n")
    return True


def host_of(url: str) -> str:
    return urlparse(url).hostname or "(no host)"


def quarantine() -> None:
    print("== quarantined registrations ==")
    files = sorted((ROOT / "quarantine").glob("registry-*.toml"))
    if not files:
        print("none\n")
        return
    total, hosts = 0, Counter()
    for path in files:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            # A quarantine file is verbatim evidence, hand-restorable; a
            # broken one is reported, not hidden, and never fatal here.
            print(f"  {path.name}: could not parse ({exc})")
            continue
        for table in TABLES:
            for entry in data.get(table, []):
                if isinstance(entry, dict) and entry.get("id"):
                    total += 1
                    hosts[host_of(str(entry.get("url", "")))] += 1
    print(f"{total} registration(s) in {len(files)} file(s), "
          f"{len(hosts)} unique host(s):")
    for host, n in hosts.most_common():
        print(f"  {host} ({n})")
    print()


def host_proposals() -> None:
    print("== pending host proposals (.work/host-proposals.txt) ==")
    path = ROOT / ".work" / "host-proposals.txt"
    if not path.exists():
        print("none\n")
        return
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    if not lines:
        print("none\n")
        return
    # The vetter leaves accepted lines in place as the record of the
    # proposal; a host already in registry_hosts.toml is done, not waiting.
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_registry
    admitted = check_registry.allowed_hosts()
    hosts = Counter()
    done = 0
    malformed = 0
    for line in lines:
        fields = line.split("\t")
        if len(fields) == 4 and fields[1].strip():
            if fields[1].strip() in admitted:
                done += 1
            else:
                hosts[fields[1].strip()] += 1
        else:
            malformed += 1
    if not hosts:
        print(f"none waiting ({done} line(s) name already-admitted hosts)\n")
        return
    print(f"{len(lines)} proposal(s), {len(hosts)} unique host(s) waiting:")
    for host, n in hosts.most_common():
        print(f"  {host} ({n})")
    if done:
        print(f"  ({done} line(s) name already-admitted hosts; kept as the "
              f"record of the proposal)")
    if malformed:
        print(f"  ({malformed} line(s) not in the tab-separated shape; "
              f"read the file itself)")
    print()


def streaks(minimum: int = 2) -> None:
    print(f"== failure streaks (>= {minimum}) ==")
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "capture.py"),
         "diagnose", "--json"],
        cwd=ROOT, capture_output=True, text=True)
    rows = []
    if done.returncode == 0:
        try:
            rows = json.loads(done.stdout)
        except ValueError:
            # diagnose prints prose, not JSON, when nothing is failing.
            rows = []
    else:
        print(f"  diagnose failed (exit {done.returncode}): "
              f"{done.stderr.strip()[:120]}")
    rows = [r for r in rows if r.get("streak", 0) >= minimum]
    if not rows:
        print("none\n")
        return
    print(f"{len(rows)} source(s):")
    for r in rows:
        print(f"  {r['id']:<44} {r['diagnosis']:<22} "
              f"x{r['streak']} since {r.get('failing_since')}")
    print()


def capture_failures() -> None:
    print("== recorded capture failures (.work/capture-failures.txt) ==")
    path = ROOT / ".work" / "capture-failures.txt"
    if not path.exists():
        print("none\n")
        return
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    if not lines:
        print("none\n")
        return
    print(f"{len(lines)} recorded, most recent last:")
    for line in lines[-10:]:
        print(f"  {line}")
    if len(lines) > 10:
        print(f"  ... and {len(lines) - 10} earlier")
    print()


def correction_proposals() -> None:
    print("== correction proposals (.work/correction-proposals/) ==")
    queue = ROOT / ".work" / "correction-proposals"
    pending = sorted(queue.glob("*.md")) if queue.is_dir() else []
    if not pending:
        print("none\n")
        return
    advice = []
    ready = 0
    for path in pending:
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        if re.search(r"^\s*-?\s*status:\s*advice-only", head,
                     re.IGNORECASE | re.MULTILINE):
            advice.append(path.name)
        else:
            ready += 1
    print(f"{len(pending)} pending: {ready} awaiting apply-corrections, "
          f"{len(advice)} advice-only (never machine-applied; they are "
          f"decisions for a person):")
    for name in advice[:10]:
        print(f"  advice-only: {name}")
    if len(advice) > 10:
        print(f"  ... and {len(advice) - 10} more")
    print()


def uncaptured() -> None:
    print("== registered social posts with no capture ==")
    # check_registry owns the rule; import it rather than restate it.
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_registry
    missing = check_registry.uncaptured_posts(registry_store.load(ROOT))
    if not missing:
        print("none\n")
        return
    print(f"{len(missing)} registered but never captured (a run died between "
          f"registration and first capture; `just ingest-x <url>` repairs an "
          f"X post):")
    for ident in missing[:10]:
        print(f"  {ident}")
    if len(missing) > 10:
        print(f"  ... and {len(missing) - 10} more")
    print()


def unreviewed_diffs() -> None:
    print("== unreviewed detected differences ==")
    # check_reviews owns the gate's rule; import it rather than restate it.
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_reviews
    outstanding, _ = check_reviews.unreviewed(ROOT)
    if not outstanding:
        print("none\n")
        return
    print(f"{len(outstanding)} unreviewed (the review gate refuses a "
          f"publish while any remain):")
    for source, ts in outstanding[:10]:
        print(f"  {source} {ts}")
    if len(outstanding) > 10:
        print(f"  ... and {len(outstanding) - 10} more")
    print()


def main() -> int:
    discovery_ok = discovery_queue()
    quarantine()
    host_proposals()
    streaks()
    capture_failures()
    uncaptured()
    correction_proposals()
    unreviewed_diffs()
    return 0 if discovery_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
