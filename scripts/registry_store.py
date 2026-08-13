#!/usr/bin/env python3
"""Read the source registry without exposing its physical layout to callers.

The registry is split into one record per TOML file for discovery. During the
transition, ``sources.toml`` remains the write surface: :func:`load` selects
the sharded projection only when its manifest proves that every fragment still
matches the current legacy bytes, and otherwise falls back to that file.

This module is deliberately stdlib-only.  It validates the stable key before
using it as a filename and validates every fragment again while loading, so a
registry record cannot escape its table directory by choosing a path-shaped
id.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent.parent

TABLE_DIRECTORIES: dict[str, str] = {
    "source": "sources",
    "x_post": "x-posts",
    "nostr_post": "nostr-posts",
    "x_watch": "x-watches",
}
KEY_FIELDS: dict[str, str] = {
    "source": "id",
    "x_post": "id",
    "nostr_post": "id",
    "x_watch": "handle",
}
STABLE_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
ORDER_LINE = re.compile(r"# registry-order: ([1-9][0-9]*)\r?\n?$")
MANIFEST_SCHEMA = 1


class RegistryError(ValueError):
    """The registry is malformed or its on-disk layout is unsafe."""


@dataclass(frozen=True)
class RegistryStatus:
    """Whether shards are a complete, current projection of ``sources.toml``."""

    current: bool
    reason: str
    registry_dir: Path
    legacy_path: Path
    legacy_sha256: str | None = None


@dataclass(frozen=True)
class _ProjectionCheck:
    """One projection check and the exact parsed observations it verified."""

    status: RegistryStatus
    shards: dict[str, Any] | None = None
    legacy: dict[str, Any] | None = None


def validate_stable_key(value: object, *, subject: str = "stable key") -> str:
    """Return a safe registry key, or raise before it reaches a path."""
    if not isinstance(value, str) or not STABLE_KEY.fullmatch(value):
        raise RegistryError(
            f"{subject} must match {STABLE_KEY.pattern}, got {value!r}"
        )
    return value


def stable_key(table: str, record: Mapping[str, Any]) -> str:
    """Return and validate the filename key for one registry record."""
    try:
        field = KEY_FIELDS[table]
    except KeyError as exc:
        raise RegistryError(f"unknown registry table {table!r}") from exc
    return validate_stable_key(record.get(field), subject=f"[[{table}]].{field}")


def _require_contained(path: Path, directory: Path) -> None:
    """Reject symlink and traversal paths before opening a fragment."""
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError as exc:
        raise RegistryError(f"registry fragment escapes {directory}: {path}") from exc
    if path.parent != directory:
        raise RegistryError(f"registry fragment is not directly inside {directory}: {path}")
    if path.is_symlink():
        raise RegistryError(f"registry fragment may not be a symlink: {path}")


def fragment_path(registry_dir: Path, table: str, key: str) -> Path:
    """Construct a contained canonical path for a record fragment."""
    key = validate_stable_key(key, subject=f"[[{table}]] filename key")
    try:
        directory = registry_dir / TABLE_DIRECTORIES[table]
    except KeyError as exc:
        raise RegistryError(f"unknown registry table {table!r}") from exc
    path = directory / f"{key}.toml"
    _require_contained(path, directory)
    return path


def validate_registry(registry: Mapping[str, Any]) -> None:
    """Validate the layout-independent shape and stable keys."""
    allowed = {"meta", *TABLE_DIRECTORIES}
    unexpected = list(dict.fromkeys(registry.keys() - allowed))
    if unexpected:
        raise RegistryError(
            "unsupported top-level registry table(s): " + ", ".join(unexpected)
        )
    if not isinstance(registry.get("meta"), dict):
        raise RegistryError("registry must contain one [meta] table")

    for table in TABLE_DIRECTORIES:
        records = registry.get(table, [])
        if not isinstance(records, list):
            raise RegistryError(f"[[{table}]] must be an array of tables")
        seen: set[str] = set()
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise RegistryError(f"[[{table}]] record {index} is not a table")
            key = stable_key(table, record)
            if key in seen:
                raise RegistryError(f"duplicate [[{table}]] stable key {key!r}")
            seen.add(key)


def _parse_legacy(text: str, path: Path) -> dict[str, Any]:
    try:
        registry = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"legacy registry {path} does not parse: {exc}") from exc
    validate_registry(registry)
    return registry


def load_legacy(path: Path) -> dict[str, Any]:
    """Load and validate one legacy monolithic registry."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"cannot read legacy registry {path}: {exc}") from exc
    return _parse_legacy(text, path)


def _parse_order(text: str, path: Path) -> int:
    first = text.splitlines(keepends=True)[:1]
    match = ORDER_LINE.fullmatch(first[0] if first else "")
    if not match:
        raise RegistryError(
            f"{path} must begin with '# registry-order: N' on its own line"
        )
    return int(match.group(1))


def _parse_fragment(text: str, path: Path, table: str) -> tuple[int, str, dict[str, Any]]:
    """Parse one already-read fragment so verification never reopens it."""
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"registry fragment {path} does not parse: {exc}") from exc
    filename_key = validate_stable_key(path.stem, subject=f"fragment filename {path.name}")
    order = _parse_order(text, path)
    if list(parsed) != [table]:
        raise RegistryError(f"{path} must contain only one [[{table}]] table")
    records = parsed[table]
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise RegistryError(f"{path} must contain exactly one [[{table}]] table")
    record = records[0]
    key = stable_key(table, record)
    if key != filename_key:
        raise RegistryError(
            f"{path}: filename key {filename_key!r} does not match record key {key!r}"
        )
    return order, key, record


def _load_fragment(path: Path, table: str) -> tuple[int, str, dict[str, Any]]:
    directory = path.parent
    _require_contained(path, directory)
    if path.suffix != ".toml":
        raise RegistryError(f"unexpected non-TOML registry fragment: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"cannot read registry fragment {path}: {exc}") from exc
    return _parse_fragment(text, path, table)


def load_shards(registry_dir: Path) -> dict[str, Any]:
    """Load a complete sharded registry in its original global table order."""
    registry_dir = Path(registry_dir)
    meta_path = registry_dir / "meta.toml"
    if meta_path.is_symlink():
        raise RegistryError(f"registry metadata may not be a symlink: {meta_path}")
    try:
        meta_data = tomllib.loads(meta_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError(f"cannot read registry metadata {meta_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"registry metadata {meta_path} does not parse: {exc}") from exc
    if list(meta_data) != ["meta"] or not isinstance(meta_data["meta"], dict):
        raise RegistryError(f"{meta_path} must contain only one [meta] table")

    root_toml = sorted(p.name for p in registry_dir.glob("*.toml") if p.name != "meta.toml")
    if root_toml:
        raise RegistryError(
            f"unexpected TOML file(s) beside meta.toml: {', '.join(root_toml)}"
        )

    fragments: list[tuple[int, str, str, dict[str, Any]]] = []
    for table, dirname in TABLE_DIRECTORIES.items():
        directory = registry_dir / dirname
        if directory.is_symlink():
            raise RegistryError(f"registry table directory may not be a symlink: {directory}")
        if not directory.is_dir():
            raise RegistryError(f"missing registry table directory: {directory}")
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix != ".toml":
                raise RegistryError(f"unexpected entry in registry table directory: {path}")
            order, key, record = _load_fragment(path, table)
            fragments.append((order, table, key, record))

    orders = [fragment[0] for fragment in fragments]
    if len(orders) != len(set(orders)):
        duplicates = sorted(order for order in set(orders) if orders.count(order) > 1)
        raise RegistryError(
            "duplicate registry-order value(s): " + ", ".join(map(str, duplicates))
        )
    expected = list(range(1, len(fragments) + 1))
    if sorted(orders) != expected:
        raise RegistryError(
            f"registry-order values must be contiguous 1..{len(fragments)}; "
            f"found {sorted(orders)!r}"
        )

    registry: dict[str, Any] = {"meta": meta_data["meta"]}
    for _order, table, _key, record in sorted(fragments, key=lambda item: item[0]):
        registry.setdefault(table, []).append(record)
    validate_registry(registry)
    return registry


def _locations(location: Path) -> tuple[Path, Path]:
    """Return (sharded directory, legacy path) for a root-like location."""
    if location.name == "registry":
        return location, location.parent / "sources.toml"
    return location / "registry", location / "sources.toml"


def _read_bytes(path: Path, *, subject: str) -> bytes:
    """Read one file observation with a consistent project-facing error."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RegistryError(f"cannot read {subject} {path}: {exc}") from exc


def _resolve_locations(location: Path | str, registry_dir: Path | None,
                       legacy_path: Path | None) -> tuple[Path, Path]:
    path = Path(location)
    if registry_dir is not None or legacy_path is not None:
        if registry_dir is None or legacy_path is None:
            raise RegistryError(
                "registry_dir and legacy_path must be supplied together"
            )
        return Path(registry_dir), Path(legacy_path)
    if path.is_file() or path.suffix == ".toml":
        return path.parent / "registry", path
    return _locations(path)


def _check_projection(
    location: Path | str = ROOT,
    *,
    legacy_sha256: str | None = None,
    registry_dir: Path | None = None,
    legacy_path: Path | None = None,
    observed_legacy: bytes | None = None,
) -> _ProjectionCheck:
    """Verify and parse a projection from one read of every held file.

    The returned shard mapping is assembled from the same bytes whose sizes
    and hashes were checked against the manifest.  Callers must not reopen the
    fragments after this succeeds: doing so would introduce a time-of-check /
    time-of-use gap during an atomic projection refresh.
    """
    sharded, legacy = _resolve_locations(location, registry_dir, legacy_path)

    def fail(reason: str, actual_sha: str | None = None,
             *, legacy_registry: dict[str, Any] | None = None) -> _ProjectionCheck:
        return _ProjectionCheck(
            RegistryStatus(False, reason, sharded, legacy, actual_sha),
            legacy=legacy_registry,
        )

    manifest_path = sharded / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return fail("manifest missing or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"manifest unreadable: {exc}")
    manifest_keys = {
        "schema", "legacy", "semantic_sha256", "counts", "meta", "fragments"
    }
    if (not isinstance(manifest, dict) or set(manifest) != manifest_keys
            or manifest.get("schema") != MANIFEST_SCHEMA):
        return fail("manifest schema is unsupported")

    recorded_legacy = manifest.get("legacy")
    if (not isinstance(recorded_legacy, dict)
            or set(recorded_legacy) != {"path", "bytes", "sha256"}):
        return fail("manifest has no legacy identity")
    if recorded_legacy.get("path") != legacy.name:
        return fail("manifest names a different legacy file")
    if observed_legacy is None:
        try:
            observed_legacy = _read_bytes(legacy, subject="legacy registry")
        except RegistryError as exc:
            return fail(str(exc))
    actual_legacy = hashlib.sha256(observed_legacy).hexdigest()
    if legacy_sha256 is not None and legacy_sha256 != actual_legacy:
        return fail("legacy registry changed during currentness check", actual_legacy)
    if recorded_legacy.get("sha256") != actual_legacy:
        return fail("legacy sources.toml is newer or different", actual_legacy)
    legacy_bytes = len(observed_legacy)
    if recorded_legacy.get("bytes") != legacy_bytes:
        return fail("legacy sources.toml byte count differs", actual_legacy)

    meta = manifest.get("meta")
    fragments = manifest.get("fragments")
    if (not isinstance(meta, dict) or set(meta) != {"path", "bytes", "sha256"}
            or meta.get("path") != "meta.toml" or not isinstance(fragments, list)):
        return fail("manifest file list is malformed", actual_legacy)

    # Validate every declared identity before letting it select a path.
    declared: list[tuple[dict[str, Any], str, str, int]] = []
    seen_paths = {"meta.toml"}
    for entry in fragments:
        required = {"order", "table", "key", "path", "bytes", "sha256"}
        if (not isinstance(entry, dict) or set(entry) != required
                or not isinstance(entry.get("path"), str)):
            return fail("manifest file entry is malformed", actual_legacy)
        relative = Path(entry["path"])
        if (relative.is_absolute() or ".." in relative.parts
                or entry["path"] in seen_paths):
            return fail("manifest path is unsafe or duplicated", actual_legacy)
        table, key, order = entry.get("table"), entry.get("key"), entry.get("order")
        if table not in TABLE_DIRECTORIES or not isinstance(order, int) or order < 1:
            return fail("manifest fragment identity is malformed", actual_legacy)
        try:
            key = validate_stable_key(key, subject="manifest fragment key")
        except RegistryError as exc:
            return fail(str(exc), actual_legacy)
        expected_path = f"{TABLE_DIRECTORIES[table]}/{key}.toml"
        if entry["path"] != expected_path:
            return fail("manifest fragment path disagrees with identity", actual_legacy)
        seen_paths.add(entry["path"])
        declared.append((entry, table, key, order))
    expected_orders = list(range(1, len(fragments) + 1))
    if [item[3] for item in declared] != expected_orders:
        return fail("manifest fragment order is not contiguous", actual_legacy)

    # A manifest cannot hide extra fragments. This retains load_shards()'s
    # complete-tree check without reopening any declared path.
    root_toml = sorted(
        path.name for path in sharded.glob("*.toml") if path.name != "meta.toml"
    )
    if root_toml:
        return fail(
            f"unexpected TOML file(s) beside meta.toml: {', '.join(root_toml)}",
            actual_legacy,
        )
    actual_fragment_paths: set[str] = set()
    for _table, dirname in TABLE_DIRECTORIES.items():
        directory = sharded / dirname
        if directory.is_symlink():
            return fail(f"registry table directory may not be a symlink: {directory}",
                        actual_legacy)
        if not directory.is_dir():
            return fail(f"missing registry table directory: {directory}", actual_legacy)
        try:
            children = list(directory.iterdir())
        except OSError as exc:
            return fail(f"cannot list registry table directory {directory}: {exc}",
                        actual_legacy)
        for held in children:
            if held.is_symlink() or not held.is_file() or held.suffix != ".toml":
                return fail(f"unexpected entry in registry table directory: {held}",
                            actual_legacy)
            actual_fragment_paths.add(f"{dirname}/{held.name}")
    declared_paths = {entry[0]["path"] for entry in declared}
    if actual_fragment_paths != declared_paths:
        return fail("manifest fragment list differs from the registry tree", actual_legacy)

    reconstruction = hashlib.sha256()
    reconstructed_bytes = 0
    parsed_fragments: list[tuple[int, str, str, dict[str, Any]]] = []
    entries: list[tuple[dict[str, Any], str | None, str | None, int | None]] = [
        (meta, None, None, None), *declared
    ]
    try:
        sharded_resolved = sharded.resolve()
    except (OSError, RuntimeError) as exc:
        return fail(f"registry directory is unavailable: {exc}", actual_legacy)
    meta_record: dict[str, Any] | None = None
    for entry, table, key, order in entries:
        held = sharded / entry["path"]
        try:
            held.resolve().relative_to(sharded_resolved)
        except (OSError, RuntimeError, ValueError) as exc:
            return fail(f"manifest file unavailable: {exc}", actual_legacy)
        if held.is_symlink():
            return fail(f"manifest file differs: {entry['path']}", actual_legacy)
        try:
            payload = _read_bytes(held, subject="registry file")
        except RegistryError as exc:
            return fail(f"manifest file unavailable: {exc}", actual_legacy)
        if (held.is_symlink() or len(payload) != entry.get("bytes")
                or hashlib.sha256(payload).hexdigest() != entry.get("sha256")):
            return fail(f"manifest file differs: {entry['path']}", actual_legacy)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            return fail(f"manifest file unreadable: {exc}", actual_legacy)

        if table is None:
            try:
                meta_data = tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                return fail(f"registry metadata {held} does not parse: {exc}", actual_legacy)
            if list(meta_data) != ["meta"] or not isinstance(meta_data["meta"], dict):
                return fail(f"{held} must contain only one [meta] table", actual_legacy)
            meta_record = meta_data["meta"]
            reconstruction.update(payload)
            reconstructed_bytes += len(payload)
            continue

        try:
            held_order, held_key, record = _parse_fragment(text, held, table)
        except RegistryError as exc:
            return fail(f"manifest fragment is invalid: {exc}", actual_legacy)
        if (held_order, held_key) != (order, key):
            return fail("manifest fragment identity differs", actual_legacy)
        first_newline = payload.find(b"\n")
        if first_newline < 0:
            return fail(f"manifest fragment is invalid: {held} has no order line",
                        actual_legacy)
        body = payload[first_newline + 1:]
        reconstruction.update(body)
        reconstructed_bytes += len(body)
        parsed_fragments.append((order, table, key, record))

    if (reconstructed_bytes != legacy_bytes
            or reconstruction.hexdigest() != actual_legacy):
        return fail("ordered shards do not reconstruct sources.toml", actual_legacy)
    if meta_record is None:  # Defensive: the manifest always supplies this entry.
        return fail("registry metadata is missing", actual_legacy)

    sharded_registry: dict[str, Any] = {"meta": meta_record}
    for _order, table, _key, record in parsed_fragments:
        sharded_registry.setdefault(table, []).append(record)
    try:
        validate_registry(sharded_registry)
    except RegistryError as exc:
        return fail(f"shards are invalid: {exc}", actual_legacy)
    if manifest.get("semantic_sha256") != semantic_sha256(sharded_registry):
        return fail("shard semantic hash differs", actual_legacy)
    expected_counts = {
        table: len(sharded_registry.get(table, [])) for table in TABLE_DIRECTORIES
    }
    if manifest.get("counts") != expected_counts:
        return fail("shard counts differ", actual_legacy)
    try:
        legacy_text = observed_legacy.decode("utf-8")
        legacy_registry = _parse_legacy(legacy_text, legacy)
    except UnicodeDecodeError as exc:
        return fail(f"legacy registry {legacy} is not UTF-8: {exc}", actual_legacy)
    except RegistryError as exc:
        return fail(f"legacy registry is invalid: {exc}", actual_legacy)
    if not semantic_equal(legacy_registry, sharded_registry):
        return fail("shards differ semantically from sources.toml", actual_legacy,
                    legacy_registry=legacy_registry)
    return _ProjectionCheck(
        RegistryStatus(
            True, "manifest and shards match legacy", sharded, legacy, actual_legacy
        ),
        shards=sharded_registry,
        legacy=legacy_registry,
    )


def registry_status(location: Path | str = ROOT, *, legacy_sha256: str | None = None,
                    registry_dir: Path | None = None,
                    legacy_path: Path | None = None) -> RegistryStatus:
    """Report whether shards exactly project the current legacy registry.

    A manifest is the activation marker, not merely ``meta.toml``. The legacy
    byte hash is checked first, then every manifest path, byte count and hash,
    and finally the parsed sharded tree's semantic hash. A stale, partial or
    tampered tree is therefore never selected by :func:`load`.

    ``legacy_sha256`` may be supplied by a caller that already observed the
    legacy file. The current bytes are still read, and a changed observation
    makes the projection non-current even when its size and meaning are equal.
    """
    return _check_projection(
        location,
        legacy_sha256=legacy_sha256,
        registry_dir=registry_dir,
        legacy_path=legacy_path,
    ).status


def shards_current(location: Path | str = ROOT) -> bool:
    """Small boolean API for callers that need only transition currentness."""
    return registry_status(location).current


def load(location: Path | str = ROOT) -> dict[str, Any]:
    """Load shards only when their manifest matches the current legacy file.

    ``location`` may be a repository root, a ``registry`` directory, or a
    direct path to a legacy TOML file. Direct legacy paths remain direct for
    small fixtures. During the transition, a new append to ``sources.toml``
    immediately makes shards stale and this function reads the legacy file
    until an explicit migration refresh succeeds.
    """
    path = Path(location)
    if path.is_file() or path.suffix == ".toml":
        return load_legacy(path)
    _, legacy = _locations(path)
    try:
        legacy_bytes = _read_bytes(legacy, subject="legacy registry")
        legacy_text = legacy_bytes.decode("utf-8")
    except RegistryError:
        raise
    except UnicodeDecodeError as exc:
        raise RegistryError(f"cannot read legacy registry {legacy}: {exc}") from exc
    legacy_sha = hashlib.sha256(legacy_bytes).hexdigest()
    check = _check_projection(
        path,
        legacy_sha256=legacy_sha,
        observed_legacy=legacy_bytes,
    )
    # A transitional writer may append while either the current or fallback
    # path is being checked. Re-read before every return: if the observation
    # moved, the newer legacy bytes win even when projection verification
    # failed part-way through an atomic refresh.
    try:
        after = _read_bytes(legacy, subject="legacy registry during recheck")
    except RegistryError as exc:
        raise RegistryError(f"cannot recheck legacy registry {legacy}: {exc}") from exc
    if after != legacy_bytes:
        try:
            return _parse_legacy(after.decode("utf-8"), legacy)
        except UnicodeDecodeError as exc:
            raise RegistryError(f"legacy registry {legacy} is not UTF-8: {exc}") from exc
    if check.status.current:
        if check.shards is None:  # Defensive: current always carries its observation.
            raise RegistryError("current registry check returned no parsed shards")
        return check.shards
    if check.legacy is not None:
        return check.legacy
    return _parse_legacy(legacy_text, legacy)


def semantic_equal(left: Any, right: Any) -> bool:
    """Compare values while treating mapping and list order as significant."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return list(left) == list(right) and all(
            semantic_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            semantic_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def _semantic_value(value: Any) -> Any:
    """Encode TOML values without discarding mapping order or scalar types."""
    if isinstance(value, dict):
        return ["map", [[key, _semantic_value(item)] for key, item in value.items()]]
    if isinstance(value, list):
        return ["list", [_semantic_value(item) for item in value]]
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return [type(value).__name__, value.isoformat()]
    return [type(value).__name__, value]


def semantic_sha256(registry: Mapping[str, Any]) -> str:
    """Hash parsed registry meaning, including mapping and list order."""
    payload = json.dumps(
        _semantic_value(dict(registry)),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
