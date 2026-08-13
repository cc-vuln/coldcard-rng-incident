#!/usr/bin/env python3
"""Move a rejected registration out of the live registry, so nobody has to.

`agent_guard.py` refuses a run whose registry changes break the rules, and it
deliberately reverts nothing: what an injected run tried to do is the
evidence. The cost of that was paid by a person. A single unlistable host in
`sources.toml` leaves the registry invalid, so `just audit` and `just test`
stay red and the scheduled publish stays stopped, until somebody reads the
rejection and edits the file by hand. On 7 Aug 2026 that was one OpenSats
article, and it stopped the tree for the rest of the day.

Quarantine is the missing third option between reverting and leaving it. The
offending block is moved, verbatim, into `quarantine/registry-YYYY-MM.toml`
with the reason and the run that produced it. Nothing is deleted, the evidence
is preserved and greppable, the registry is valid again, and the host is still
not allowlisted, so the poll never fetches it.

Two properties make this safe to run unattended:

- **only what this run added.** A block is quarantined only if its id is
  absent from the run's `before` registry. An agent cannot use this to
  evict a long-standing source, because a pre-existing id is never eligible;
  a problem naming one is reported and left alone, which is a real human
  matter and cannot have been caused by the run
- **it only removes.** There is no path here that adds a host, relaxes a rule
  or edits a surviving block. The strictest outcome it can produce is a
  smaller registry

What it is not is an approval mechanism. A quarantined source stays out until
somebody adds the host to `scripts/registry_hosts.toml` and moves the block
back, which is still a human edit, twice over. The difference is that nothing
is blocked while they get round to it.

Zero dependencies: stdlib only, Python 3.11+ for tomllib.

Usage:
    quarantine_registry.py [--registry PATH] [--before PATH]
                           [--run-id ID] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from migrate_registry import refresh_if_installed

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "sources.toml"
CHECK = Path(__file__).resolve().parent / "check_registry.py"

TABLES = ("source", "x_post", "nostr_post", "x_watch")
# Every check_registry problem is reported as "<id>: what is wrong with it",
# under a summary line and bulleted, so the bullet is optional here.
PROBLEM_RE = re.compile(r"^(?:-\s+)?([A-Za-z0-9][A-Za-z0-9._-]*): (.+)$")
HEADER_RE = re.compile(r"^\[\[([a-z_]+)\]\]\s*$")


def run_check(registry: Path, before: Path | None) -> tuple[int, str]:
    cmd = [sys.executable, str(CHECK), "--registry", str(registry)]
    if before is not None:
        cmd += ["--before", str(before)]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return done.returncode, (done.stderr or "") + (done.stdout or "")


def offenders(output: str) -> dict[str, list[str]]:
    """id -> the reasons named for it."""
    found: dict[str, list[str]] = {}
    for line in output.splitlines():
        m = PROBLEM_RE.match(line.strip())
        if m:
            found.setdefault(m.group(1), []).append(m.group(2))
    return found


def registry_ids(path: Path) -> set[str]:
    if not path or not path.exists():
        return set()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {e["id"] for table in TABLES for e in data.get(table, [])
            if isinstance(e, dict) and e.get("id")}


def block_spans(text: str) -> list[tuple[str, int, int, int]]:
    """(table, index-within-table, start-line, end-line) for every block.

    end-line is exclusive and stops before the next table header, with
    trailing blank lines left behind so the file does not collapse.
    """
    lines = text.splitlines(keepends=True)
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = HEADER_RE.match(line.rstrip("\n"))
        if m:
            starts.append((m.group(1), i))

    spans = []
    seen: dict[str, int] = {}
    for n, (table, start) in enumerate(starts):
        end = starts[n + 1][1] if n + 1 < len(starts) else len(lines)
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        idx = seen.get(table, 0)
        seen[table] = idx + 1
        spans.append((table, idx, start, end))
    return spans


def extract(text: str, ids: set[str]) -> tuple[str, dict[str, str]]:
    """Remove each named block, returning the new text and what was taken."""
    data = tomllib.loads(text)
    lines = text.splitlines(keepends=True)
    taken: dict[str, str] = {}
    drop: list[tuple[int, int]] = []

    for table, idx, start, end in block_spans(text):
        entries = data.get(table, [])
        if idx >= len(entries):
            continue
        ident = entries[idx].get("id")
        if ident in ids:
            taken[ident] = "".join(lines[start:end])
            drop.append((start, end))

    for start, end in sorted(drop, reverse=True):
        del lines[start:end]
    return "".join(lines), taken


def quarantine_path(registry: Path, now: datetime) -> Path:
    """Beside the registry it came out of, not beside this script: that is
    what keeps a test's sandboxed registry from writing into the real tree."""
    return registry.resolve().parent / "quarantine" / f"registry-{now:%Y-%m}.toml"


def append_quarantine(path: Path, blocks: dict[str, list[str]],
                      texts: dict[str, str], run_id: str,
                      now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Registrations moved out of sources.toml by\n"
            "# scripts/quarantine_registry.py, verbatim, newest last.\n"
            "#\n"
            "# A block is here because it broke a registry rule, most often by\n"
            "# naming a host that scripts/registry_hosts.toml does not list.\n"
            "# Nothing here is polled, fetched or published: this file is a\n"
            "# record, not a registry, and no tool reads it back.\n"
            "#\n"
            "# To restore one: add the host to scripts/registry_hosts.toml,\n"
            "# move the block back into sources.toml, and say why in the\n"
            "# commit. That is still a human edit, twice over. Until then\n"
            "# nothing is blocked and nothing is fetched.\n",
            encoding="utf-8")

    parts = [""]
    for ident, body in texts.items():
        parts.append(f"# quarantined {now:%Y%m%dT%H%M%SZ}"
                     f"{f' from run {run_id}' if run_id else ''}")
        for reason in blocks[ident]:
            parts.append(f"#   {reason}")
        parts.append(body.rstrip("\n"))
        parts.append("")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(parts) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", type=Path, default=REGISTRY)
    ap.add_argument("--before", type=Path,
                    help="the run's before-registry; only ids absent from it "
                         "are eligible")
    ap.add_argument("--run-id", default="", help="recorded with each block")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.registry.exists():
        print(f"quarantine-registry: no registry at {args.registry}",
              file=sys.stderr)
        return 1

    code, output = run_check(args.registry, args.before)
    if code == 0:
        print("quarantine-registry: the registry passes; nothing to do")
        return 0

    named = offenders(output)
    if not named:
        print("quarantine-registry: the registry was rejected but no block was "
              "named, so there is nothing safe to move:\n" + output.strip(),
              file=sys.stderr)
        return 1

    present = registry_ids(args.registry)
    if not args.before:
        # Without a baseline there is no way to tell what this run added from
        # what has been registered for weeks, and everything would look
        # eligible. Report and stop: an audit is not a licence to remove.
        print("quarantine-registry: no --before baseline, so nothing is "
              "eligible to move. The registry needs a person:", file=sys.stderr)
        for ident, reasons in sorted(named.items()):
            for reason in reasons:
                print(f"  - {ident}: {reason}", file=sys.stderr)
        return 1

    was_there = registry_ids(args.before)
    eligible = {i for i in named if i in present and i not in was_there}
    kept = {i: named[i] for i in named if i not in eligible}

    for ident, reasons in kept.items():
        where = "already in the registry before this run" \
            if ident in was_there else "not a block in the registry"
        print(f"quarantine-registry: leaving {ident} alone ({where}):",
              file=sys.stderr)
        for reason in reasons:
            print(f"  - {reason}", file=sys.stderr)

    if not eligible:
        print("quarantine-registry: nothing this run added is eligible; the "
              "registry still fails and needs a person", file=sys.stderr)
        return 1

    text = args.registry.read_text(encoding="utf-8")
    new_text, taken = extract(text, eligible)
    missing = eligible - set(taken)
    if missing:
        print(f"quarantine-registry: could not locate {', '.join(sorted(missing))} "
              f"in the file; leaving the registry alone", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    dest = quarantine_path(args.registry, now)
    if args.dry_run:
        print(f"quarantine-registry: would move {', '.join(sorted(taken))} "
              f"to {dest}")
        return 0

    args.registry.write_text(new_text, encoding="utf-8")
    append_quarantine(dest, named, taken, args.run_id, now)

    code, output = run_check(args.registry, args.before)
    if code != 0:
        print("quarantine-registry: the registry still fails after "
              "quarantining; a person needs to look:\n" + output.strip(),
              file=sys.stderr)
        return 1

    try:
        refresh_if_installed(args.registry)
    except (OSError, ValueError) as exc:
        print("quarantine-registry: live registry passes but its discoverable "
              f"projection could not be refreshed: {exc}", file=sys.stderr)
        return 1

    print(f"quarantine-registry: moved {len(taken)} rejected registration(s) "
          f"to {dest}; the registry passes again")
    for ident in sorted(taken):
        print(f"  - {ident}: {named[ident][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
