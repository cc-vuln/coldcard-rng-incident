#!/usr/bin/env python3
"""Apply one guard-approved intake verdict outbox to legacy DISCOVERY.md.

The intake driver already holds ``.work/agent-discovery-intake/intake.lock``
for the whole run. This command therefore does not acquire the lock again; it
validates the protected packet and outbox in full, then performs one durable
atomic replacement through ``discovery_common.atomic_text``. A failed
validation writes nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import discovery_common as common
import intake_verdicts

ROOT = Path(__file__).resolve().parent.parent


def verdict_suffix(row: dict) -> str:
    action = row["action"]
    stamp = row["at"]
    if action == "registered":
        return f"-> registered as {row['source_id']} ({stamp})"
    if action == "already-registered":
        return f"-> already registered as {row['source_id']} ({stamp})"
    if action == "dismissed":
        return f"-> dismissed: {row['reason']} ({stamp})"
    raise intake_verdicts.VerdictError(
        f"retry action has no assessed-line suffix: {row['candidate_id']}")


def apply(*, intake_path: Path, packet_path: Path, verdict_path: Path,
          before_registry_path: Path, after_registry_path: Path,
          approval_path: Path) -> tuple[int, int]:
    if not approval_path.is_file():
        raise intake_verdicts.VerdictError(
            "guard approval marker approved-captures.txt is missing")
    verdicts = intake_verdicts.validate_paths(
        packet_path=packet_path, outbox_path=verdict_path,
        before_registry_path=before_registry_path,
        after_registry_path=after_registry_path)
    packet = intake_verdicts.load_packet(packet_path)
    candidates = {row["candidate_id"]: row for row in packet["candidates"]}

    try:
        original = intake_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise intake_verdicts.VerdictError(
            f"cannot read intake queue {intake_path}: {exc}") from exc
    sections = common.split_sections(original)
    pending = common.section(sections, common.PENDING_H)
    assessed = common.section(sections, common.ASSESSED_H)

    pending_counts: dict[str, int] = {}
    for line in pending:
        pending_counts[line] = pending_counts.get(line, 0) + 1
    for ident, candidate in candidates.items():
        count = pending_counts.get(candidate["queue_line"], 0)
        if count != 1:
            raise intake_verdicts.VerdictError(
                f"packet candidate {ident} appears {count} times in Pending; expected once")

    terminal = [row for row in verdicts if row["action"] != "retry"]
    terminal_lines = {
        candidates[row["candidate_id"]]["queue_line"] for row in terminal
    }
    new_pending = [line for line in pending if line not in terminal_lines]
    new_assessed = list(assessed)
    for row in verdicts:  # Preserve packet/outbox order deterministically.
        if row["action"] != "retry":
            line = candidates[row["candidate_id"]]["queue_line"]
            new_assessed.append(f"{line} {verdict_suffix(row)}")

    rebuilt = []
    found_pending = found_assessed = False
    for heading, lines in sections:
        if heading == common.PENDING_H:
            rebuilt.append((heading, new_pending))
            found_pending = True
        elif heading == common.ASSESSED_H:
            rebuilt.append((heading, new_assessed))
            found_assessed = True
        else:
            rebuilt.append((heading, lines))
    if not found_pending or not found_assessed:
        raise intake_verdicts.VerdictError(
            "intake queue lacks the Pending or Assessed section")

    updated = common.join_sections(rebuilt)
    if updated != original:
        common.atomic_text(intake_path, updated)
    return len(terminal), len(verdicts) - len(terminal)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--intake", type=Path, default=ROOT / "DISCOVERY.md")
    parser.add_argument("--registry", type=Path, default=ROOT / "sources.toml")
    args = parser.parse_args(argv)
    run_dir = args.run_dir if args.run_dir.is_absolute() else ROOT / args.run_dir
    try:
        terminal, retries = apply(
            intake_path=args.intake,
            packet_path=run_dir / "intake-packet.json",
            verdict_path=run_dir / "intake-verdicts.jsonl",
            before_registry_path=run_dir / "before" / "sources.toml",
            after_registry_path=args.registry,
            approval_path=run_dir / "approved-captures.txt")
    except (OSError, ValueError) as exc:
        print(f"apply-intake-verdicts: {exc}", file=sys.stderr)
        return 1
    print(f"applied {terminal} terminal intake verdict(s); "
          f"left {retries} candidate(s) pending for retry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
