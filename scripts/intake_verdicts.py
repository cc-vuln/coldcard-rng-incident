#!/usr/bin/env python3
"""Validate an intake-agent verdict outbox against its protected run packet.

The agent writes one JSON object per candidate to
``.work/intake-verdicts.jsonl``. The driver copies that file and the exact
packet into the operator-owned guard run directory before validation. This
module is shared by the guard and deterministic applier; it never writes the
queue and has no dependency on a future structured discovery store.
"""
from __future__ import annotations

import json
import re
import tomllib
import datetime as dt
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ACTIONS = frozenset({"retry", "registered", "dismissed", "already-registered"})
TERMINAL_ACTIONS = ACTIONS - {"retry"}
STAMP_RE = re.compile(r"^20[0-9]{6}T[0-9]{6}Z$")
SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
REQUIRED_FIELDS = frozenset({
    "schema_version", "candidate_id", "action", "reason", "at",
})
OPTIONAL_FIELDS = frozenset({"source_id"})


class VerdictError(ValueError):
    """The outbox is incomplete, ambiguous, or inconsistent with the run."""


def _load_json(path: Path, subject: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VerdictError(f"cannot read {subject} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise VerdictError(f"{subject} {path} is not valid JSON: {exc}") from exc


def load_packet(path: Path) -> dict[str, Any]:
    packet = _load_json(path, "intake packet")
    if not isinstance(packet, dict) or packet.get("schema_version") != 1:
        raise VerdictError("intake packet has an unsupported schema")
    candidates = packet.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise VerdictError("intake packet has no candidate records")
    if len(candidates) > 15:
        raise VerdictError("intake packet exceeds the 15-candidate run limit")
    seen: set[str] = set()
    for position, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict):
            raise VerdictError(f"intake packet candidate {position} is not an object")
        ident = candidate.get("candidate_id")
        line = candidate.get("queue_line")
        if not isinstance(ident, str) or not ident or ident in seen:
            raise VerdictError(f"intake packet candidate {position} has an invalid id")
        if not isinstance(line, str) or not line.startswith("- "):
            raise VerdictError(f"intake packet candidate {ident} has no queue line")
        seen.add(ident)
    return packet


def load_outbox(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise VerdictError("verdict outbox is not a regular non-symlink file")
    if path.stat().st_size > 65536:
        raise VerdictError("verdict outbox exceeds 65536 bytes")
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise VerdictError(f"cannot read verdict outbox {path}: {exc}") from exc
    if not raw_lines:
        raise VerdictError("verdict outbox is empty")
    if len(raw_lines) > 15:
        raise VerdictError("verdict outbox exceeds 15 lines")
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(raw_lines, 1):
        if not raw.strip():
            raise VerdictError(f"verdict outbox line {number} is blank")
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VerdictError(f"verdict outbox line {number} is invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise VerdictError(f"verdict outbox line {number} is not an object")
        rows.append(row)
    return rows


def load_registry(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except OSError as exc:
        raise VerdictError(f"cannot read registry {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise VerdictError(f"registry {path} is invalid TOML: {exc}") from exc
    if not isinstance(value, dict):
        raise VerdictError(f"registry {path} is not an object")
    return value


def registry_ids(registry: dict[str, Any]) -> set[str]:
    return {
        str(row["id"])
        for table in ("source", "x_post", "nostr_post")
        for row in registry.get(table, [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }


def registry_by_id(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["id"]): row
        for table in ("source", "x_post", "nostr_post")
        for row in registry.get(table, [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }


def validate(*, packet: dict[str, Any], verdicts: list[dict[str, Any]],
             before_registry: dict[str, Any], after_registry: dict[str, Any]
             ) -> list[dict[str, Any]]:
    """Return normalized verdicts or raise before any queue write.

    A terminal verdict must exist for every packet candidate except an
    explicit ``retry``. Registrations must name a source introduced by this
    run; already-registered verdicts must name a source that existed before
    the run. Dismissals and retries cannot smuggle a source relationship.
    """
    candidates = {row["candidate_id"]: row for row in packet["candidates"]}
    before_ids = registry_ids(before_registry)
    after_ids = registry_ids(after_registry)
    after_by_id = registry_by_id(after_registry)
    added_ids = after_ids - before_ids
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    registered_sources: set[str] = set()

    for number, raw in enumerate(verdicts, 1):
        fields = set(raw)
        missing = REQUIRED_FIELDS - fields
        extra = fields - REQUIRED_FIELDS - OPTIONAL_FIELDS
        if missing:
            raise VerdictError(
                f"verdict line {number} lacks: {', '.join(sorted(missing))}")
        if extra:
            raise VerdictError(
                f"verdict line {number} has unsupported field(s): "
                f"{', '.join(sorted(extra))}")
        if raw["schema_version"] != SCHEMA_VERSION:
            raise VerdictError(f"verdict line {number} has an unsupported schema")
        ident = raw["candidate_id"]
        if not isinstance(ident, str) or ident not in candidates:
            raise VerdictError(f"verdict line {number} names a candidate outside the packet")
        if ident in seen:
            raise VerdictError(f"candidate {ident} has more than one verdict")
        seen.add(ident)
        action = raw["action"]
        if action not in ACTIONS:
            raise VerdictError(f"candidate {ident} has unknown action {action!r}")
        reason = raw["reason"]
        if not isinstance(reason, str) or not reason.strip() or "\n" in reason \
                or "\r" in reason or len(reason.strip()) > 500:
            raise VerdictError(
                f"candidate {ident} needs a one-line reason of 1-500 characters")
        at = raw["at"]
        if not isinstance(at, str) or not STAMP_RE.fullmatch(at):
            raise VerdictError(f"candidate {ident} has an invalid UTC stamp")
        try:
            dt.datetime.strptime(at, "%Y%m%dT%H%M%SZ")
        except ValueError as exc:
            raise VerdictError(f"candidate {ident} has an impossible UTC stamp") from exc
        if "—" in reason:
            raise VerdictError(f"candidate {ident} reason contains an em-dash")
        source_id = raw.get("source_id")
        if source_id is not None and (not isinstance(source_id, str)
                                      or not SOURCE_ID_RE.fullmatch(source_id)):
            raise VerdictError(f"candidate {ident} has an invalid source_id")

        exact_ids = set(candidates[ident].get("registry_exact_match", {})
                        .get("source_ids", []))
        if action == "registered":
            if not source_id:
                raise VerdictError(f"registered candidate {ident} lacks source_id")
            if source_id not in added_ids:
                raise VerdictError(
                    f"registered candidate {ident} does not name a source added by this run")
            if exact_ids:
                raise VerdictError(
                    f"registered candidate {ident} already has an exact registry match")
            try:
                from build_intake_packet import canonical_external_key
                _platform, registered_key = canonical_external_key(
                    str(after_by_id[source_id].get("url", "")))
            except (KeyError, ValueError) as exc:
                raise VerdictError(
                    f"registered candidate {ident} names a source without its permalink") from exc
            if registered_key != candidates[ident].get("external_key"):
                raise VerdictError(
                    f"registered candidate {ident} names a different native object")
            if source_id in registered_sources:
                raise VerdictError(
                    f"registered source_id {source_id} is used by more than one verdict")
            registered_sources.add(source_id)
        elif action == "already-registered":
            if not source_id:
                raise VerdictError(
                    f"already-registered candidate {ident} lacks source_id")
            if source_id not in before_ids:
                raise VerdictError(
                    f"already-registered candidate {ident} does not name a pre-run source")
            if source_id not in exact_ids:
                raise VerdictError(
                    f"already-registered candidate {ident} is not an exact packet match")
        elif source_id is not None:
            raise VerdictError(f"{action} candidate {ident} must not name source_id")

        normalized.append({
            "schema_version": SCHEMA_VERSION,
            "candidate_id": ident,
            "action": action,
            "reason": reason.strip(),
            "at": at,
            **({"source_id": source_id} if source_id is not None else {}),
        })

    missing_ids = set(candidates) - seen
    if missing_ids:
        raise VerdictError(
            "verdict outbox is incomplete; missing: " + ", ".join(sorted(missing_ids)))
    by_id = {row["candidate_id"]: row for row in normalized}
    return [by_id[candidate["candidate_id"]]
            for candidate in packet["candidates"]]


def validate_paths(*, packet_path: Path, outbox_path: Path,
                   before_registry_path: Path,
                   after_registry_path: Path) -> list[dict[str, Any]]:
    return validate(
        packet=load_packet(packet_path),
        verdicts=load_outbox(outbox_path),
        before_registry=load_registry(before_registry_path),
        after_registry=load_registry(after_registry_path),
    )
