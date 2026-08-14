#!/usr/bin/env python3
"""Apply one guard-approved intake verdict batch to the discovery store.

The packet binds each candidate's immutable event head; the rendered queue line
is presentation and is never used to decide which record changes. The command
takes ``.work/locks/discovery.lock`` only for its final validation and one
transactional ``DiscoveryStore.apply_actions`` call. A concurrent discovery
change therefore causes the complete stale batch to fail rather than requiring
an hour-long lock across hydration and the agent run.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import discovery_store
import intake_verdicts

ROOT = Path(__file__).resolve().parent.parent
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def apply(*, root: Path, packet_path: Path, verdict_path: Path,
          before_registry_path: Path, after_registry_path: Path,
          approval_path: Path, lock_held: bool,
          operation_id: str) -> tuple[int, int]:
    store = discovery_store.DiscoveryStore(root)
    if not lock_held:
        with store.locked():
            return apply(
                root=root, packet_path=packet_path, verdict_path=verdict_path,
                before_registry_path=before_registry_path,
                after_registry_path=after_registry_path,
                approval_path=approval_path, lock_held=True,
                operation_id=operation_id)
    if not approval_path.is_file():
        raise intake_verdicts.VerdictError(
            "guard approval marker approved-captures.txt is missing")
    if not isinstance(operation_id, str) \
            or not OPERATION_ID_RE.fullmatch(operation_id):
        raise intake_verdicts.VerdictError("invalid intake operation id")

    packet = intake_verdicts.load_packet(packet_path)
    intake_verdicts.require_structured_packet(packet)
    verdicts = intake_verdicts.validate_paths(
        packet_path=packet_path, outbox_path=verdict_path,
        before_registry_path=before_registry_path,
        after_registry_path=after_registry_path)

    if not store.marker.is_file():
        raise intake_verdicts.VerdictError(
            "structured discovery migration marker is missing")
    try:
        discovery_store.validate_store(root, lock_held=True)
    except (OSError, TypeError, ValueError) as exc:
        raise intake_verdicts.VerdictError(str(exc)) from exc

    packet_candidates = {
        candidate["candidate_id"]: candidate
        for candidate in packet["candidates"]
    }
    current = {candidate["identity"]: candidate
               for candidate in store.list_candidates(lock_held=True)}
    actions: list[dict] = []
    stale_error: str | None = None
    for verdict in verdicts:  # validate_paths has restored packet order.
        identity = verdict["candidate_id"]
        expected_head = packet_candidates[identity]["candidate_head"]
        candidate = current.get(identity)
        if candidate is None:
            stale_error = stale_error or \
                f"packet candidate no longer exists: {identity}"
        elif candidate.get("state") != "pending":
            stale_error = stale_error or \
                f"packet candidate is no longer pending: {identity}"
        elif candidate.get("head") != expected_head:
            stale_error = stale_error or \
                f"packet candidate head changed: {identity}"
        actions.append({
            "candidate_id": identity,
            "expected_head": expected_head,
            "action": verdict["action"],
            "reason": verdict["reason"],
            "at": verdict["at"],
            **({"source_id": verdict["source_id"]}
               if "source_id" in verdict else {}),
        })

    # A prior invocation may have durably committed the transaction and then
    # stopped before the driver advanced. Let the store compare exact content
    # for that same operation id; every other stale packet still fails before
    # a write.
    if stale_error and not any(
            tx["operation_id"] == operation_id
            for tx in store.load_transactions()):
        raise intake_verdicts.VerdictError(stale_error)

    try:
        store.apply_actions(
            actions, lock_held=True, operation_id=operation_id)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise intake_verdicts.VerdictError(str(exc)) from exc
    terminal = sum(row["action"] != "retry" for row in verdicts)
    return terminal, len(verdicts) - terminal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--registry", type=Path,
                        help="registry after the guarded run (default ROOT/sources.toml)")
    parser.add_argument("--operation-id", required=True,
                        help="stable guarded agent run id")
    parser.add_argument("--lock-held", action="store_true",
                        help="caller already holds .work/locks/discovery.lock")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    run_dir = args.run_dir if args.run_dir.is_absolute() else root / args.run_dir
    registry_arg = args.registry or Path("sources.toml")
    registry = (registry_arg if registry_arg.is_absolute()
                else root / registry_arg)
    try:
        terminal, retries = apply(
            root=root,
            packet_path=run_dir / "intake-packet.json",
            verdict_path=run_dir / "intake-verdicts.jsonl",
            before_registry_path=run_dir / "before" / "sources.toml",
            after_registry_path=registry,
            approval_path=run_dir / "approved-captures.txt",
            lock_held=args.lock_held,
            operation_id=args.operation_id)
    except (OSError, ValueError) as exc:
        print(f"apply-intake-verdicts: {exc}", file=sys.stderr)
        return 1
    print(f"applied {terminal} terminal intake verdict(s); "
          f"left {retries} candidate(s) pending for retry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
