#!/usr/bin/env python3
"""Build, verify and install the one-time structured discovery migration.

The default is a read-only migration rehearsal. ``--write`` installs the
validated store once. Every legacy input byte is retained in the migration
bundle; every legacy bullet becomes a provenance-bearing observation; and
every transition encoded in a bullet is replayed chronologically while its
original file, line number and queue rank remain exact provenance.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discovery_store import (  # noqa: E402
    MAX_EVENTS_PER_TRANSACTION,
    DiscoveryStore,
    _event,
    atomic_bytes,
    atomic_text,
    candidate_key,
    digest,
    normalize_stamp,
    pretty_json,
    read_json,
    schema_files,
    sha256_bytes,
    stamp_now,
    url_identity,
    validate_store,
)

ROOT = Path(__file__).resolve().parent.parent
HEADING_RE = re.compile(r"^## (.+)$")
URL_RE = re.compile(r"\((https?://[^)]+)\)")
STAMP = r"(\d{8}T\d{6}Z)"
REGISTERED_RE = re.compile(
    rf"(?:->|,)\s+registered as ([A-Za-z0-9][A-Za-z0-9._-]*).*?"
    rf"\({STAMP}\)(?=;| ->|$)")
ALREADY_RE = re.compile(
    rf"-> already registered as ([A-Za-z0-9][A-Za-z0-9._-]*).*?"
    rf"\({STAMP}\)(?=;| ->|$)")
DISMISSED_RE = re.compile(
    rf"-> dismissed:\s*(.*?) \({STAMP}\)(?=;| ->|$)")
RETRY_RE = re.compile(
    rf"-> Pending:\s*(.*?) \({STAMP}\)(?=;| ->|$)")
SOURCE_DATE_RE = re.compile(r"^- (\d{4})-(\d{2})-(\d{2}) ")

SECTION_STATES = {
    "Pending": "pending",
    "Deferred": "deferred",
    "Assessed": "assessed",
    "Link review, held for a human decision": "human-review",
}

REGISTRY_TABLES = ("source", "x_post", "nostr_post")


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=root,
        text=True, capture_output=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _source_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"source-reference input is not a regular file: {path}")
    with path.open("rb") as handle:
        value = tomllib.load(handle)
    return {
        row["id"]
        for table in REGISTRY_TABLES
        for row in value.get(table, [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }


def _source_reference_resolution(root: Path, events: list[dict]) -> dict:
    """Record where every migration-era registration id resolved at cutover.

    Keep the ids, not just counts.  The migration validator can then derive
    the referenced set independently from the immutable baseline events and
    prove that this inventory is a complete, disjoint partition of it.
    """
    referenced = sorted({
        event["payload"]["source_id"]
        for event in events
        if event["type"] == "verdict"
        and isinstance(event["payload"].get("source_id"), str)
    })
    live = _source_ids(root / "sources.toml")
    quarantined: set[str] = set()
    quarantine = root / "quarantine"
    if quarantine.exists():
        if quarantine.is_symlink() or not quarantine.is_dir():
            raise ValueError("quarantine source-reference path is unsafe")
        for path in sorted(quarantine.glob("registry-????-??.toml")):
            quarantined.update(_source_ids(path))
    via_quarantine = sorted(
        value for value in referenced if value not in live and value in quarantined)
    unresolved = sorted(
        value for value in referenced if value not in live and value not in quarantined)
    return {
        "referenced": referenced,
        "live": sorted(value for value in referenced if value in live),
        "quarantined": via_quarantine,
        "unresolved": unresolved,
    }


def legacy_entries(path: Path, default_section: str | None = None, *,
                   root: Path | None = None) -> list[dict]:
    """Return every legacy bullet with exact path, line number and text."""
    if not path.is_file() or path.is_symlink():
        return []
    section = default_section
    rows: list[dict] = []
    text = path.read_bytes().decode("utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        if match := HEADING_RE.fullmatch(line):
            section = match.group(1)
        elif line.startswith("- "):
            stored_path = (path.resolve().relative_to(root.resolve()).as_posix()
                           if root is not None else path.as_posix())
            rows.append({
                "path": stored_path,
                "line_number": number,
                "section": section or "Assessed",
                "line": line,
            })
    return rows


def read_all(root: Path) -> tuple[list[dict], list[dict]]:
    """Read the live queue and all pre-cutover rotated verdict files."""
    queue = root / "DISCOVERY.md"
    if queue.is_symlink() or not queue.is_file():
        raise ValueError("legacy DISCOVERY.md is missing or is not a regular file")
    paths = [queue]
    rotated = root / "discovery"
    if rotated.exists():
        if rotated.is_symlink() or not rotated.is_dir():
            raise ValueError("legacy discovery path is not a regular directory")
        paths.extend(sorted(rotated.glob("assessed-????-??.md")))
    rows: list[dict] = []
    sources: list[dict] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"legacy discovery input is not a regular file: {path}")
        relative = path.relative_to(root).as_posix()
        found = legacy_entries(
            path, "Assessed" if path != queue else None, root=root)
        raw = path.read_bytes()
        copy_path = "discovery/migration-v1/legacy/" + relative
        rows.extend(found)
        sources.append({
            "path": relative,
            "copy": copy_path,
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "entries": len(found),
            "raw": raw,
        })
    return rows, sources


def line_actions(line: str) -> list[dict]:
    """Return every encoded retry/verdict transition in textual order."""
    actions: list[tuple[int, dict]] = []
    for match in RETRY_RE.finditer(line):
        actions.append((match.start(), {
            "action": "retry",
            "reason": match.group(1).strip(),
            "at": match.group(2),
        }))
    for match in DISMISSED_RE.finditer(line):
        actions.append((match.start(), {
            "action": "dismissed",
            "reason": match.group(1).strip(),
            "at": match.group(2),
        }))
    for match in ALREADY_RE.finditer(line):
        actions.append((match.start(), {
            "action": "already-registered",
            "source_id": match.group(1),
            "reason": f"already registered as {match.group(1)}",
            "at": match.group(2),
        }))
    for match in REGISTERED_RE.finditer(line):
        actions.append((match.start(), {
            "action": "registered",
            "source_id": match.group(1),
            "reason": f"registered as {match.group(1)}",
            "at": match.group(2),
        }))
    actions.sort(key=lambda item: item[0])
    return [value for _position, value in actions]


def terminal_action_text(line: str) -> bool:
    """Compatibility helper used by migration-focused tests."""
    return bool(line_actions(line))


def terminal_action(parsed: dict) -> tuple[str, dict] | None:
    """Compatibility helper returning the final encoded transition."""
    actions = parsed.get("actions") or line_actions(parsed["line"])
    if not actions:
        return None
    value = dict(actions[-1])
    kind = "retry" if value.pop("action") == "retry" else "verdict"
    if kind == "verdict":
        value["verdict"] = actions[-1]["action"]
    return kind, value


def parsed_line(row: dict, queue_rank: int = 0) -> dict:
    """Translate one legacy occurrence without discarding its original text."""
    line = row["line"]
    url_match = URL_RE.search(line)
    if url_match:
        url = url_match.group(1)
        platform, native_id = url_identity(url)
    else:
        native_id = hashlib.sha256(line.encode("utf-8")).hexdigest()[:20]
        platform = "legacy"
        url = f"urn:coldcard-discovery:legacy:{native_id}"
    identity = candidate_key(platform, native_id)
    actions = line_actions(line)
    section_state = SECTION_STATES.get(row["section"], "human-review")
    if section_state == "assessed":
        initial_state = "pending" if actions else "human-review"
    else:
        initial_state = section_state
    date = SOURCE_DATE_RE.match(line)
    if actions:
        event_at = actions[0]["at"]
        time_basis = "verdict-stamp"
    elif date:
        event_at = "".join(date.groups()) + "T000000Z"
        time_basis = "source-display-date"
    else:
        event_at = "19700101T000000Z"
        time_basis = "unknown-placeholder"
    observation = {
        "url": url,
        "legacy_line": line,
        "legacy_path": row["path"],
        "legacy_line_number": row["line_number"],
        "legacy_section": row["section"],
        "legacy_event_time_basis": time_basis,
        "legacy_queue_rank": queue_rank,
    }
    return {
        "identity": identity,
        "url": url,
        "state": initial_state,
        "event_at": event_at,
        "actions": actions,
        "observation": observation,
        "line": line,
        "path": row["path"],
        "line_number": row["line_number"],
        "section": row["section"],
    }


def _migration_events(store: DiscoveryStore, rows: list[dict]) \
        -> tuple[list[dict], dict, list[dict]]:
    events: list[dict] = []
    working: dict[str, dict] = {}
    repairs: dict[str, list[dict]] = {
        "assessed_retry_only_reopened_pending": [],
        "assessed_without_transition_held_for_human_review": [],
        "missing_url_given_stable_legacy_identity": [],
        "multi_transition_lines_preserved": [],
        "verdict_supersessions_made_explicit": [],
        "verdict_reopened_by_later_retry": [],
    }
    occurrence_semantics: dict[tuple[str, int], dict] = {}
    ordinal = 0

    def add(event: dict) -> None:
        nonlocal ordinal
        ordinal += 1
        identity = event["candidate"]
        current = copy.deepcopy(working.get(identity))
        working[identity] = store._apply_event(current, event, ordinal)
        events.append(event)

    planned: list[tuple[str, int, int, str, dict, dict]] = []
    for queue_rank, row in enumerate(rows, 1):
        parsed = parsed_line(row, queue_rank)
        actions = parsed["actions"]
        reference = {
            "path": parsed["path"],
            "line_number": parsed["line_number"],
            "candidate": parsed["identity"],
        }
        if not URL_RE.search(parsed["line"]):
            repairs["missing_url_given_stable_legacy_identity"].append(reference)
        if parsed["section"] == "Assessed" and not actions:
            repairs["assessed_without_transition_held_for_human_review"].append(reference)
        if len(actions) > 1:
            repairs["multi_transition_lines_preserved"].append({
                **reference,
                "transitions": [action["action"] for action in actions],
            })
        if parsed["section"] == "Assessed" and actions \
                and actions[-1]["action"] == "retry":
            repairs["assessed_retry_only_reopened_pending"].append(reference)

        occurrence_semantics[(parsed["path"], parsed["line_number"])] = {
            "path": parsed["path"],
            "line_number": parsed["line_number"],
            "queue_rank": queue_rank,
            "section": parsed["section"],
            "identity": parsed["identity"],
            "url": parsed["url"],
            "initial_state": parsed["state"],
            "event_at": parsed["event_at"],
            "time_basis": parsed["observation"]["legacy_event_time_basis"],
            "observation_event_id": None,
            "actions": [
                {**action, "event_ids": []} for action in actions
            ],
        }

        # Source position is an audit coordinate, not chronology. Rotated
        # verdicts can refer to the same native object as a later live-queue
        # row, so file iteration order must not make an older verdict final.
        planned.append((parsed["event_at"], queue_rank, 0,
                        "observation", parsed, reference))
        for action_number, action in enumerate(actions, 1):
            planned.append((action["at"], queue_rank, action_number,
                            "action", {**parsed, "action": action}, reference))

    for _at, _rank, transition_number, kind, parsed, reference in sorted(planned):
        occurrence = occurrence_semantics[
            (parsed["path"], parsed["line_number"])]
        if kind == "observation":
            event = store._observation_event(
                parsed["observation"], parsed["state"], parsed["event_at"],
                strict_identity=False,
            )
            add(event)
            occurrence["observation_event_id"] = event["event_id"]
            continue
        action = parsed["action"]
        bound_event_ids: list[str] = []
        if action["action"] == "retry":
            current_verdict = working[parsed["identity"]].get("verdict")
            if current_verdict is not None:
                reopen = _event(
                    "state", parsed["identity"], action["at"], {
                        "state": "pending",
                        "reason": "later legacy retry reopened pending",
                        "supersedes": current_verdict["event_id"],
                    })
                repairs["verdict_reopened_by_later_retry"].append({
                    **reference,
                    "supersedes": current_verdict["event_id"],
                    "reopen": reopen["event_id"],
                })
                add(reopen)
                bound_event_ids.append(reopen["event_id"])
            retry = store._retry_event(
                parsed["identity"], action["reason"], action["at"])
            add(retry)
            bound_event_ids.append(retry["event_id"])
            occurrence["actions"][transition_number - 1]["event_ids"] = \
                bound_event_ids
            continue
        current = working[parsed["identity"]].get("verdict")
        supersedes = current.get("event_id") if current else None
        event = store._verdict_event(
            parsed["identity"], action["action"], action["reason"],
            action["at"], source_id=action.get("source_id"),
            supersedes=supersedes,
        )
        if supersedes:
            repairs["verdict_supersessions_made_explicit"].append({
                **reference,
                "supersedes": supersedes,
                "replacement": event["event_id"],
            })
        add(event)
        bound_event_ids.append(event["event_id"])
        occurrence["actions"][transition_number - 1]["event_ids"] = \
            bound_event_ids
    semantics = sorted(
        occurrence_semantics.values(), key=lambda row: row["queue_rank"])
    if any(not row["observation_event_id"] or any(
            not action["event_ids"] for action in row["actions"])
           for row in semantics):
        raise ValueError("migration did not bind every legacy occurrence action")
    return events, repairs, semantics


def _copy_legacy_sources(dest: Path, sources: list[dict]) -> list[dict]:
    public: list[dict] = []
    for source in sources:
        path = dest / source["copy"]
        atomic_bytes(path, source["raw"], mode=0o644)
        public.append({key: source[key] for key in
                       ("path", "copy", "bytes", "sha256", "entries")})
    return public


def _prepare_empty_destination(dest: Path) -> None:
    if dest.exists():
        if dest.is_symlink() or not dest.is_dir():
            raise ValueError(f"migration destination is not a directory: {dest}")
        if any(dest.iterdir()):
            raise ValueError(f"migration destination is not empty: {dest}")
    else:
        dest.mkdir(parents=True, mode=0o755)


def build(root: Path, dest: Path, *, source_commit: str | None = None,
          created_at: str | None = None,
          reference_root: Path | None = None) -> dict:
    """Build and validate a complete store at an empty destination."""
    root, dest = Path(root), Path(dest)
    _prepare_empty_destination(dest)
    rows, held_sources = read_all(root)
    public_sources = _copy_legacy_sources(dest, held_sources)
    for relative, text in schema_files().items():
        atomic_text(dest / relative, text, mode=0o644)

    store = DiscoveryStore(dest, bootstrap=True)
    events, repairs, occurrence_rows = _migration_events(store, rows)
    occurrence_value = {"schema": 1, "occurrences": occurrence_rows}
    occurrence_raw = pretty_json(occurrence_value).encode("utf-8")
    occurrence_copy = (
        "discovery/migration-v1/occurrence-semantics.json")
    atomic_bytes(dest / occurrence_copy, occurrence_raw, mode=0o644)
    baseline = DiscoveryStore.project([{"events": events}])
    baseline_rows = sorted(baseline.values(), key=DiscoveryStore._sort_key)
    states = Counter(candidate["state"] for candidate in baseline_rows)
    platforms = Counter(candidate["platform"] for candidate in baseline_rows)
    created = normalize_stamp(created_at or stamp_now())
    descriptor = {
        "schema": 1,
        # This names the repository head visible at cutover. Exact input
        # identity comes from the byte counts and hashes below; a dirty live
        # queue is not falsely described as matching HEAD.
        "source_head_at_cutover": source_commit or _git_head(root),
        "created_at": created,
        "source_files": public_sources,
        "legacy_entries": len(rows),
        "legacy_lines_sha256": digest([row["line"] for row in rows]),
        "migration_semantic_root": digest(baseline_rows),
        "states": dict(sorted(states.items())),
        "platforms": dict(sorted(platforms.items())),
        "repairs": repairs,
        "occurrence_semantics": {
            "copy": occurrence_copy,
            "bytes": len(occurrence_raw),
            "sha256": sha256_bytes(occurrence_raw),
            "entries": len(occurrence_rows),
        },
        "source_references": _source_reference_resolution(
            Path(reference_root) if reference_root is not None else root,
            events),
    }
    bundle_root = digest(descriptor)
    transaction_ids: list[str] = []
    for offset in range(0, len(events), MAX_EVENTS_PER_TRANSACTION):
        chunk = events[offset:offset + MAX_EVENTS_PER_TRANSACTION]
        number = offset // MAX_EVENTS_PER_TRANSACTION + 1
        transaction = store.commit_events(
            chunk,
            kind="migration-v1",
            at=max(event["at"] for event in chunk),
            operation_id=f"migration-v1:{bundle_root}:{number:04d}",
        )
        transaction_ids.append(transaction["transaction_id"])
    if not transaction_ids:
        raise ValueError("legacy discovery record has no candidate entries")

    candidates = store.list_candidates()
    if candidates != baseline_rows:
        raise ValueError("committed migration differs from its anchored baseline")
    manifest = {
        **descriptor,
        "migration_transactions": transaction_ids,
        "migration_bundle_root": bundle_root,
    }
    atomic_text(store.marker, pretty_json(manifest), mode=0o644)
    store.render_all()
    validate_store(dest)
    return manifest


def _legacy_discovery_is_replaceable(path: Path) -> bool:
    if not path.exists():
        return True
    if path.is_symlink() or not path.is_dir():
        return False
    for held in path.iterdir():
        if held.is_symlink() or not held.is_file() \
                or not re.fullmatch(r"assessed-\d{4}-\d{2}\.md", held.name):
            return False
    return True


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _durable_replace(source: Path, destination: Path) -> None:
    """Rename and persist removal/insertion in both parent directories."""
    source_parent, destination_parent = source.parent, destination.parent
    os.replace(source, destination)
    _fsync_directory(destination_parent)
    if source_parent != destination_parent:
        _fsync_directory(source_parent)


def _open_work_directory(root: Path) -> int:
    """Open/create .work without following a substituted directory."""
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        created = False
        try:
            os.mkdir(".work", 0o775, dir_fd=root_fd)
            created = True
        except FileExistsError:
            pass
        if created:
            os.fsync(root_fd)
        return os.open(
            ".work", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd)
    finally:
        os.close(root_fd)


def _private_work_directory(root: Path, name: str) -> Path:
    """Create/open one operator-only child of .work without symlink traversal."""
    if "/" in name or name in {"", ".", ".."}:
        raise ValueError("invalid private work directory name")
    work_fd = _open_work_directory(root)
    child_fd: int | None = None
    try:
        created = False
        try:
            os.mkdir(name, 0o700, dir_fd=work_fd)
            created = True
        except FileExistsError:
            pass
        if created:
            os.fsync(work_fd)
        child_fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=work_fd)
        os.fchmod(child_fd, 0o700)
    finally:
        if child_fd is not None:
            os.close(child_fd)
        os.close(work_fd)
    return root / ".work" / name


@contextmanager
def cutover_locks(root: Path):
    """Exclude pre-cutover and structured writers during installation.

    Old discovery/intake processes serialize on
    ``.work/agent-discovery-intake/intake.lock``; all guarded agent roles use
    ``.work/agent-runs.lock``; the replacement store uses its discovery lock.
    The first two acquisitions are nonblocking, so an old process holding the
    intake lock while waiting for agent-run cannot deadlock the installer.
    """
    root = Path(root)
    work_fd: int | None = None
    agent_fd: int | None = None
    intake_dir_fd: int | None = None
    intake_fd: int | None = None
    try:
        work_fd = _open_work_directory(root)
        agent_fd = os.open(
            "agent-runs.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600, dir_fd=work_fd)
        try:
            fcntl.flock(agent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "an agent run is active; structured cutover refused") from exc

        intake_created = False
        try:
            os.mkdir("agent-discovery-intake", 0o775, dir_fd=work_fd)
            intake_created = True
        except FileExistsError:
            pass
        if intake_created:
            os.fsync(work_fd)
        intake_dir_fd = os.open(
            "agent-discovery-intake",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=work_fd)
        intake_fd = os.open(
            "intake.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600, dir_fd=intake_dir_fd)
        try:
            fcntl.flock(intake_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "a pre-cutover discovery writer is active; cutover refused") from exc

        with DiscoveryStore(root).locked():
            yield
    finally:
        for fd in (intake_fd, intake_dir_fd, agent_fd, work_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _install_journal(root: Path) -> Path:
    return root / ".work" / "discovery-migration-install.json"


def _clear_install_journal(path: Path) -> None:
    path.unlink(missing_ok=True)
    if path.parent.is_dir():
        _fsync_directory(path.parent)


def _journal_backup(root: Path, value: object) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("discovery migration install journal has an invalid backup")
    relative = Path(value)
    expected = Path(".work/discovery-migration-backups")
    if relative.is_absolute() or ".." in relative.parts \
            or relative.parent != expected:
        raise ValueError("discovery migration install journal escaped its backup area")
    return root / relative


def recover_install(root: Path, *, lock_held: bool = False) -> bool:
    """Finish or roll back an interrupted activation described by its journal."""
    root = Path(root)
    store = DiscoveryStore(root)
    with store._lock_context(lock_held):
        journal_path = _install_journal(root)
        if not journal_path.exists() and not journal_path.is_symlink():
            return False
        journal = read_json(
            journal_path, max_bytes=16 * 1024, canonical=True)
        if set(journal) != {"schema", "backup"} or journal["schema"] != 1:
            raise ValueError("unsupported discovery migration install journal")
        backup = _journal_backup(root, journal["backup"])
        live = root / "discovery"
        if migration_is_installed(root):
            # The directory activation was atomic. A crash may only have left
            # the small root index stale, so rebuild projections before the
            # complete validation and then retire the journal.
            store.render_all(lock_held=True)
            validate_store(root, lock_held=True)
            _clear_install_journal(journal_path)
            return True
        if live.exists() or live.is_symlink():
            if not _legacy_discovery_is_replaceable(live):
                raise ValueError(
                    "interrupted discovery activation left an unexpected live tree")
            if backup is not None and backup.exists():
                raise ValueError(
                    "interrupted discovery activation has both live and backup trees")
        elif backup is not None:
            if backup.is_symlink() or not backup.is_dir():
                raise ValueError(
                    "interrupted discovery activation lost its legacy backup")
            _durable_replace(backup, live)
        # With no prior discovery directory there is nothing to restore. The
        # staged build remains ignored and a fresh rehearsal may safely start.
        _clear_install_journal(journal_path)
        return True


def verify_live_inputs_match_bundle(root: Path, built: Path) -> None:
    """Prove the staged bundle covers the complete live legacy namespace."""
    manifest_path = built / "discovery/migration-v1/manifest.json"
    manifest = read_json(
        manifest_path, max_bytes=2 * 1024 * 1024, canonical=True)
    expected = manifest.get("source_files")
    if not isinstance(expected, list):
        raise ValueError("staged migration has no source-file inventory")
    queue = root / "DISCOVERY.md"
    if queue.is_symlink() or not queue.is_file():
        raise ValueError("live legacy DISCOVERY.md is unsafe or missing")
    live_paths = [queue]
    legacy_dir = root / "discovery"
    if legacy_dir.exists() or legacy_dir.is_symlink():
        if not _legacy_discovery_is_replaceable(legacy_dir):
            raise ValueError("live legacy discovery namespace is unexpected")
        live_paths.extend(sorted(legacy_dir.iterdir()))
    actual_names = [path.relative_to(root).as_posix() for path in live_paths]
    expected_names = [row.get("path") for row in expected
                      if isinstance(row, dict)]
    if actual_names != expected_names:
        raise ValueError(
            "staged migration does not cover every live legacy source: "
            f"live={actual_names}, staged={expected_names}")
    for path, row in zip(live_paths, expected):
        raw = path.read_bytes()
        entries = sum(
            line.startswith(b"- ") for line in raw.splitlines())
        if len(raw) != row.get("bytes") \
                or sha256_bytes(raw) != row.get("sha256") \
                or entries != row.get("entries"):
            raise ValueError(
                f"live legacy source changed after staging: {row.get('path')}")


def replace_generated(root: Path, built: Path, *,
                      lock_held: bool = False) -> None:
    """Atomically activate a validated build; preserve any legacy dir backup."""
    root, built = Path(root), Path(built)
    validate_store(built)
    source = built / "discovery"
    if source.is_symlink() or not source.is_dir():
        raise ValueError("built discovery directory is missing")
    store = DiscoveryStore(root)
    with store._lock_context(lock_held):
        if migration_is_installed(root):
            raise ValueError("structured discovery migration is already installed")
        live = root / "discovery"
        if not _legacy_discovery_is_replaceable(live):
            raise ValueError("live discovery directory contains unexpected files")
        verify_live_inputs_match_bundle(root, built)
        backup: Path | None = None
        if live.exists():
            backup_dir = _private_work_directory(
                root, "discovery-migration-backups")
            backup = backup_dir / f"legacy-{stamp_now()}-{os.getpid()}"
        journal_path = _install_journal(root)
        if journal_path.exists() or journal_path.is_symlink():
            raise ValueError(
                "an earlier discovery migration install journal needs recovery")
        atomic_text(journal_path, pretty_json({
            "schema": 1,
            "backup": (backup.relative_to(root).as_posix()
                       if backup is not None else None),
        }), mode=0o600)
        try:
            if backup is not None:
                _durable_replace(live, backup)
            _durable_replace(source, live)
            atomic_text(root / "DISCOVERY.md",
                        (built / "DISCOVERY.md").read_text(encoding="utf-8"),
                        mode=0o640)
        except Exception:
            # os.replace() may have succeeded even when the following parent
            # fsync raised.  The live migration marker is therefore the
            # authoritative activation boundary, not a Python flag set after
            # _durable_replace() returns.  Preserve the journal in that case;
            # recover_install() will regenerate the root index and validate
            # the activated store on the next run.
            if migration_is_installed(root):
                raise
            if backup is not None and not live.exists():
                _durable_replace(backup, live)
            _clear_install_journal(journal_path)
            raise
        _clear_install_journal(journal_path)


@contextmanager
def legacy_revision_root(repository: Path, revision: str):
    """Materialise only legacy queue inputs from one explicit git revision."""
    names = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", revision, "--",
         "DISCOVERY.md", "discovery"],
        cwd=repository, check=True, text=True, capture_output=True,
    ).stdout.splitlines()
    selected = [name for name in names if name == "DISCOVERY.md" or
                re.fullmatch(r"discovery/assessed-\d{4}-\d{2}\.md", name)]
    if "DISCOVERY.md" not in selected:
        raise ValueError(f"{revision} has no tracked DISCOVERY.md")
    with tempfile.TemporaryDirectory(prefix="discovery-legacy-") as raw:
        checkout = Path(raw)
        for name in selected:
            value = subprocess.run(
                ["git", "show", f"{revision}:{name}"], cwd=repository,
                check=True, capture_output=True,
            ).stdout
            path = checkout / name
            atomic_bytes(path, value, mode=0o644)
        yield checkout


def migration_is_installed(root: Path) -> bool:
    return (Path(root) / "discovery" / "migration-v1" /
            "manifest.json").is_file()


def _new_stage(root: Path, output: Path | None) -> Path:
    if output is not None:
        dest = output.resolve()
        repository = root.resolve()
        if dest == repository or repository in dest.parents:
            raise ValueError(
                "explicit migration output must be outside the repository")
        _prepare_empty_destination(dest)
        return dest
    work_fd = _open_work_directory(root)
    os.close(work_fd)
    work = root / ".work"
    return Path(tempfile.mkdtemp(prefix="discovery-migration-check-", dir=work))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--output", type=Path,
                        help="empty rehearsal destination; omitted uses .work")
    parser.add_argument(
        "--legacy-revision",
        help="rehearse from legacy inputs at an explicit git revision",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.write and args.legacy_revision:
            raise ValueError("--write cannot install a historical revision")
        if args.write and args.output:
            raise ValueError("--write chooses an internal atomic staging path")
        if args.write:
            # Bridge both generations of writer lock across the complete
            # snapshot and activation. Recovery remains first, so a hard stop
            # after moving the legacy directory can be repaired on rerun.
            with cutover_locks(root):
                recover_install(root, lock_held=True)
                if migration_is_installed(root):
                    print(
                        "migrate-discovery: migration is already installed; refusing",
                        file=sys.stderr)
                    return 2
                stage = _new_stage(root, None)
                manifest = build(root, stage)
                if migration_is_installed(root):
                    raise ValueError("migration was installed by another writer")
                replace_generated(root, stage, lock_held=True)
                result = validate_store(root, lock_held=True)
        else:
            journal_path = _install_journal(root)
            if journal_path.exists() or journal_path.is_symlink():
                raise ValueError(
                    "an interrupted discovery install needs recovery; "
                    "rerun with --write")
            if migration_is_installed(root) and not args.legacy_revision:
                result = validate_store(root)
                manifest = result["migration"]
                print(
                    "migrate-discovery: structured store valid: "
                    f"{result['candidates']} candidates; baseline semantic root "
                    f"{manifest['migration_semantic_root']}"
                )
                return 0
            stage = _new_stage(root, args.output)
            if args.legacy_revision:
                with legacy_revision_root(root, args.legacy_revision) as legacy_root:
                    manifest = build(
                        legacy_root, stage, source_commit=args.legacy_revision,
                        reference_root=root)
            else:
                live_store = DiscoveryStore(root)
                with live_store.locked():
                    if migration_is_installed(root):
                        raise ValueError("migration was installed by another writer")
                    manifest = build(root, stage)
            result = validate_store(stage)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"migrate-discovery: {exc}", file=sys.stderr)
        return 1

    action = "installed" if args.write else "rehearsed"
    print(
        f"migrate-discovery: {action} {manifest['legacy_entries']} legacy "
        f"entries as {result['candidates']} candidates in "
        f"{result['transactions']} immutable transactions; semantic root "
        f"{manifest['migration_semantic_root']}"
    )
    if not args.write:
        print(f"migrate-discovery: rehearsal retained at {stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
