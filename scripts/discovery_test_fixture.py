"""Build a minimal, fully validated discovery store for focused tests.

The production migration has its own exhaustive tests. Consumer fixtures need
only a small immutable baseline with the same marker, schemas, transaction
chain and generated projections, so they construct those facts directly.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import discovery_store


def install_store(root: Path, rows: list[dict]) -> discovery_store.DiscoveryStore:
    """Install one migration baseline from explicit candidate facts.

    Each row needs ``line``, ``url`` and ``at``. Optional ``state`` defaults to
    pending; ``verdict`` and ``retry`` describe a subsequent migration event.
    """
    store = discovery_store.DiscoveryStore(root, bootstrap=True)
    lines = [row["line"] for row in rows]
    source_text = ["# Discovery fixture", ""]
    prepared = []
    for row in rows:
        section = ("Assessed" if row.get("verdict")
                   else row.get("state", "pending").title())
        source_text.extend([f"## {section}", "", row["line"], ""])
        prepared.append((row, section, len(source_text) - 1))
    ordered_events: list[tuple[str, int, int, int, dict]] = []
    occurrence_rows: list[dict] = []
    for number, (row, section, line_number) in enumerate(prepared, 1):
        semantic = discovery_store.audit_legacy_line_semantics(
            row["line"], section, number, "DISCOVERY.md", line_number)
        observation_state = semantic["initial_state"]
        observation = {
            "url": row["url"],
            "display_line": row["line"].split(" -> ", 1)[0],
            "legacy_line": row["line"],
            "legacy_candidate_line": row["line"].split(" -> ", 1)[0],
            "legacy_path": "DISCOVERY.md",
            "legacy_line_number": line_number,
            "legacy_section": section,
            "legacy_event_time_basis": semantic["time_basis"],
            "legacy_queue_rank": number,
        }
        observation_event = store._observation_event(
            observation, observation_state, semantic["event_at"],
            strict_identity=True)
        ordered_events.append((
            semantic["event_at"], number, 0, 0, observation_event))
        identity = discovery_store.candidate_key(
            *discovery_store.url_identity(row["url"]))
        bound_actions = []
        for transition, action in enumerate(semantic["actions"], 1):
            if action["action"] == "retry":
                action_event = store._retry_event(
                    identity, action["reason"], action["at"])
            else:
                action_event = store._verdict_event(
                    identity, action["action"], action["reason"], action["at"],
                    source_id=action.get("source_id"))
            ordered_events.append((
                action["at"], number, transition, 0, action_event))
            bound_actions.append({**action, "event_ids": [action_event["event_id"]]})
        occurrence_rows.append({
            **semantic,
            "observation_event_id": observation_event["event_id"],
            "actions": bound_actions,
        })

    events = [row[-1] for row in sorted(ordered_events)]
    source_rel = "discovery/migration-v1/legacy/DISCOVERY.md"
    source_raw = ("\n".join(source_text).rstrip() + "\n").encode("utf-8")
    discovery_store.atomic_text(root / source_rel,
                                source_raw.decode("utf-8"), mode=0o644)
    candidates = sorted(
        discovery_store.DiscoveryStore.project([{"events": events}]).values(),
        key=discovery_store.DiscoveryStore._sort_key)
    states = Counter(candidate["state"] for candidate in candidates)
    platforms = Counter(candidate["platform"] for candidate in candidates)
    referenced = sorted({
        event["payload"]["source_id"]
        for event in events
        if event["type"] == "verdict" and event["payload"].get("source_id")
    })
    occurrence_value = {"schema": 1, "occurrences": occurrence_rows}
    occurrence_raw = discovery_store.pretty_json(occurrence_value).encode("utf-8")
    occurrence_copy = "discovery/migration-v1/occurrence-semantics.json"
    discovery_store.atomic_bytes(
        root / occurrence_copy, occurrence_raw, mode=0o644)
    descriptor = {
        "schema": 1,
        "created_at": "20260814T000000Z",
        "source_head_at_cutover": "fixture",
        "source_files": [{
            "path": "DISCOVERY.md",
            "copy": source_rel,
            "bytes": len(source_raw),
            "sha256": discovery_store.sha256_bytes(source_raw),
            "entries": len(lines),
        }],
        "legacy_entries": len(lines),
        "legacy_lines_sha256": discovery_store.digest(lines),
        "migration_semantic_root": discovery_store.digest(candidates),
        "states": dict(sorted(states.items())),
        "platforms": dict(sorted(platforms.items())),
        "repairs": {
            "assessed_retry_only_reopened_pending": [],
            "assessed_without_transition_held_for_human_review": [],
            "missing_url_given_stable_legacy_identity": [],
            "multi_transition_lines_preserved": [],
            "verdict_supersessions_made_explicit": [],
            "verdict_reopened_by_later_retry": [],
        },
        "occurrence_semantics": {
            "copy": occurrence_copy,
            "bytes": len(occurrence_raw),
            "sha256": discovery_store.sha256_bytes(occurrence_raw),
            "entries": len(occurrence_rows),
        },
        "source_references": {
            "referenced": referenced,
            "live": [],
            "quarantined": [],
            "unresolved": referenced,
        },
    }
    bundle_root = discovery_store.digest(descriptor)
    transaction = store.commit_events(
        events, kind="migration-v1",
        at=max(event["at"] for event in events),
        operation_id=f"migration-v1:{bundle_root}:0001")
    self_check = store.list_candidates()
    if self_check != candidates:
        raise AssertionError("fixture projection changed while committing")
    manifest = {
        **descriptor,
        "migration_transactions": [transaction["transaction_id"]],
        "migration_bundle_root": bundle_root,
    }
    for rel, text in discovery_store.schema_files().items():
        discovery_store.atomic_text(root / rel, text, mode=0o644)
    discovery_store.atomic_text(
        store.marker,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        mode=0o644)
    store.render_all()
    discovery_store.validate_store(root)
    return store
