#!/usr/bin/env python3
"""Split ``sources.toml`` into a discoverable, equivalence-checked registry.

The converter never edits the legacy file.  It first writes a complete tree in
a sibling staging directory, verifies parsed meaning and an exact byte-for-byte
reconstruction of the legacy text, then swaps the staged directory into place.
Use ``--dry-run`` (the default) to exercise the conversion without retaining
anything, ``--check`` to verify an existing tree, and ``--write`` to install a
new tree. Use ``--refresh`` after a transitional writer changes
``sources.toml``: it stages and verifies a fresh projection before atomically
replacing the old shards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import registry_store


ROOT = Path(__file__).resolve().parent.parent
SCHEMA = registry_store.MANIFEST_SCHEMA
HEADER = re.compile(
    r"^[ \t]*\[\[(source|x_post|nostr_post|x_watch)\]\][ \t]*(?:#.*)?(?:\r?\n|$)"
)

README_TEMPLATE = ROOT / "registry" / "README.md"


class MigrationError(ValueError):
    """The legacy input or generated sharded tree failed an invariant."""


@dataclass(frozen=True)
class Fragment:
    order: int
    table: str
    key: str
    text: str

    @property
    def body(self) -> str:
        return f"# registry-order: {self.order}\n{self.text}"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _advance_string_state(line: str, state: str | None) -> str | None:
    """Track TOML multiline strings so header-looking body lines are ignored.

    Top-level array headers have to occupy their own line. A small lexical
    scanner is sufficient here, but it must understand both multiline string
    forms because assessed notes are commonly triple-quoted.
    """
    index = 0
    length = len(line)
    while index < length:
        if state is not None:
            end = line.find(state, index)
            if end < 0:
                return state
            if state == '"""':
                # A delimiter preceded by an odd number of backslashes is data.
                slashes = 0
                cursor = end - 1
                while cursor >= 0 and line[cursor] == "\\":
                    slashes += 1
                    cursor -= 1
                if slashes % 2:
                    index = end + 1
                    continue
            state = None
            index = end + 3
            continue

        char = line[index]
        if char == "#":
            return None
        if line.startswith('"""', index) or line.startswith("'''", index):
            state = line[index:index + 3]
            index += 3
            continue
        if char in {'"', "'"}:
            quote = char
            index += 1
            while index < length:
                if quote == '"' and line[index] == "\\":
                    index += 2
                    continue
                if line[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        index += 1
    return state


def _headers(text: str) -> list[tuple[int, int, str]]:
    """Return (fragment start, table-header start, table) in source order.

    A contiguous blank/comment prelude belongs to the following record. This
    keeps section headings such as ``# tier 2`` beside the first record in that
    section instead of stranded at the end of the preceding record's file.
    """
    headers: list[tuple[int, int, str]] = []
    state: str | None = None
    offset = 0
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    for index, line in enumerate(lines):
        if state is None:
            match = HEADER.fullmatch(line)
            if match:
                prelude = index
                while prelude > 0:
                    prior = lines[prelude - 1]
                    if prior.strip() and not prior.lstrip().startswith("#"):
                        break
                    prelude -= 1
                headers.append((offsets[prelude], offsets[index], match.group(1)))
        state = _advance_string_state(line, state)
    return headers


def split_legacy(text: str) -> tuple[str, list[Fragment], dict[str, Any]]:
    """Split exact source substrings and prove they match parsed TOML records."""
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MigrationError(f"legacy sources.toml does not parse: {exc}") from exc
    try:
        registry_store.validate_registry(parsed)
    except registry_store.RegistryError as exc:
        raise MigrationError(str(exc)) from exc
    if not parsed or next(iter(parsed)) != "meta":
        raise MigrationError("legacy registry must begin with [meta]")

    headers = _headers(text)
    expected_count = sum(len(parsed.get(table, [])) for table in registry_store.TABLE_DIRECTORIES)
    if len(headers) != expected_count:
        raise MigrationError(
            f"found {len(headers)} source block headers but parsed {expected_count} records"
        )
    if not headers:
        raise MigrationError("legacy registry contains no record blocks")

    meta_text = text[:headers[0][0]]
    try:
        meta_parsed = tomllib.loads(meta_text)
    except tomllib.TOMLDecodeError as exc:
        raise MigrationError(f"[meta] preamble does not parse independently: {exc}") from exc
    if meta_parsed != {"meta": parsed["meta"]}:
        raise MigrationError("text before the first record is not exactly the [meta] table")

    table_offsets = {table: 0 for table in registry_store.TABLE_DIRECTORIES}
    fragments: list[Fragment] = []
    for position, (start, _header_start, table) in enumerate(headers, start=1):
        end = headers[position][0] if position < len(headers) else len(text)
        raw = text[start:end]
        try:
            one = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            raise MigrationError(
                f"[[{table}]] block {position} does not parse alone: {exc}"
            ) from exc
        if list(one) != [table] or not isinstance(one[table], list) or len(one[table]) != 1:
            raise MigrationError(f"block {position} must contain exactly one [[{table}]] table")
        record = one[table][0]
        expected = parsed[table][table_offsets[table]]
        if not registry_store.semantic_equal(record, expected):
            raise MigrationError(f"[[{table}]] block {position} changed while splitting")
        table_offsets[table] += 1
        key = registry_store.stable_key(table, record)
        fragments.append(Fragment(position, table, key, raw))

    if meta_text + "".join(fragment.text for fragment in fragments) != text:
        raise MigrationError("split fragments do not reconstruct the legacy bytes")
    return meta_text, fragments, parsed


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    # These are tracked catalogue records, not runtime secrets. Keep them
    # readable for repository tooling while leaving writes with the operator.
    os.chmod(path, 0o644)


def _manifest(legacy_path: Path, parsed: dict[str, Any], registry_dir: Path,
              fragments: list[Fragment]) -> dict[str, Any]:
    legacy_bytes = legacy_path.read_bytes()
    entries = []
    for fragment in fragments:
        path = registry_store.fragment_path(registry_dir, fragment.table, fragment.key)
        relative = path.relative_to(registry_dir).as_posix()
        entries.append({
            "order": fragment.order,
            "table": fragment.table,
            "key": fragment.key,
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return {
        "schema": SCHEMA,
        "legacy": {
            "path": legacy_path.name,
            "bytes": len(legacy_bytes),
            "sha256": sha256_bytes(legacy_bytes),
        },
        "semantic_sha256": registry_store.semantic_sha256(parsed),
        "counts": {
            table: len(parsed.get(table, []))
            for table in registry_store.TABLE_DIRECTORIES
        },
        "meta": {
            "path": "meta.toml",
            "bytes": (registry_dir / "meta.toml").stat().st_size,
            "sha256": sha256_file(registry_dir / "meta.toml"),
        },
        "fragments": entries,
    }


def build_tree(legacy_path: Path, registry_dir: Path) -> dict[str, Any]:
    """Build and verify a new tree in an empty staging directory."""
    if registry_dir.exists() and any(registry_dir.iterdir()):
        raise MigrationError(f"staging directory is not empty: {registry_dir}")
    registry_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(registry_dir, 0o755)
    text = legacy_path.read_text(encoding="utf-8")
    meta_text, fragments, parsed = split_legacy(text)

    try:
        readme = README_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise MigrationError(
            f"cannot read registry README template {README_TEMPLATE}: {exc}"
        ) from exc
    _write_text(registry_dir / "README.md", readme)
    _write_text(registry_dir / "meta.toml", meta_text)
    for dirname in registry_store.TABLE_DIRECTORIES.values():
        directory = registry_dir / dirname
        directory.mkdir()
        os.chmod(directory, 0o755)
    for fragment in fragments:
        path = registry_store.fragment_path(registry_dir, fragment.table, fragment.key)
        _write_text(path, fragment.body)

    manifest = _manifest(legacy_path, parsed, registry_dir, fragments)
    _write_text(
        registry_dir / "manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    verify_tree(legacy_path, registry_dir)
    return manifest


def _strip_order(text: str, path: Path) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or not registry_store.ORDER_LINE.fullmatch(lines[0]):
        raise MigrationError(f"{path} has no parseable registry-order marker")
    return "".join(lines[1:])


def verify_tree(legacy_path: Path, registry_dir: Path) -> dict[str, Any]:
    """Verify hashes, ordering, exact source text and parsed equivalence."""
    try:
        legacy_text = legacy_path.read_text(encoding="utf-8")
        _meta_text, expected_fragments, legacy = split_legacy(legacy_text)
        sharded = registry_store.load_shards(registry_dir)
    except (OSError, registry_store.RegistryError) as exc:
        raise MigrationError(str(exc)) from exc
    if not registry_store.semantic_equal(legacy, sharded):
        raise MigrationError("sharded registry is not order-sensitive semantic equivalent")

    manifest_path = registry_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read registry manifest {manifest_path}: {exc}") from exc
    expected_manifest = _manifest(legacy_path, legacy, registry_dir, expected_fragments)
    if manifest != expected_manifest:
        raise MigrationError("registry manifest does not match the legacy file and fragments")

    reconstructed = (registry_dir / "meta.toml").read_text(encoding="utf-8")
    for entry in sorted(manifest["fragments"], key=lambda item: item["order"]):
        path = registry_dir / entry["path"]
        try:
            path.resolve().relative_to(registry_dir.resolve())
        except ValueError as exc:
            raise MigrationError(
                f"manifest fragment escapes registry tree: {entry['path']}"
            ) from exc
        reconstructed += _strip_order(path.read_text(encoding="utf-8"), path)
    if reconstructed != legacy_text:
        raise MigrationError(
            "meta and ordered fragments do not reconstruct sources.toml byte for byte"
        )
    return manifest


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_tree(staged: Path, target: Path) -> None:
    """Swap a verified sibling tree into place, rolling back on failure."""
    if staged.parent != target.parent:
        raise MigrationError("staged and target registry trees must be siblings")
    if target.is_symlink():
        raise MigrationError(f"refusing to replace symlinked registry tree: {target}")

    backup = target.parent / f".{target.name}.backup-{uuid.uuid4().hex}"
    had_target = target.exists()
    if had_target:
        os.replace(target, backup)
    try:
        os.replace(staged, target)
        _fsync_directory(target.parent)
    except BaseException:
        if had_target and backup.exists() and not target.exists():
            os.replace(backup, target)
            _fsync_directory(target.parent)
        raise
    if backup.exists():
        shutil.rmtree(backup)
        _fsync_directory(target.parent)


def _support_only(path: Path) -> bool:
    return path.is_dir() and {child.name for child in path.iterdir()} <= {"README.md"}


def write_tree(legacy_path: Path, target: Path) -> dict[str, Any]:
    """Install a generated tree without overwriting divergent shard edits."""
    if (target / "meta.toml").exists():
        try:
            return verify_tree(legacy_path, target)
        except MigrationError as exc:
            raise MigrationError(
                f"existing sharded registry is not equivalent; refusing to replace it: {exc}"
            ) from exc
    if target.exists() and not _support_only(target):
        raise MigrationError(
            f"refusing to replace non-empty support directory {target}; expected README.md only"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
    try:
        manifest = build_tree(legacy_path, staged)
        replace_tree(staged, target)
        return verify_tree(legacy_path, target)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def refresh_tree(legacy_path: Path, target: Path) -> dict[str, Any]:
    """Atomically replace stale shards with a verified current projection.

    The old tree remains in place while the sibling staging tree is built.
    ``replace_tree`` swaps it only after byte reconstruction, semantic
    equivalence and manifest verification all pass. Until that final rename,
    :func:`registry_store.load` sees the old manifest mismatch and reads the
    current legacy file, so stale shards cannot become authoritative.
    """
    if target.is_symlink():
        raise MigrationError(f"refusing to replace symlinked registry tree: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
    try:
        build_tree(legacy_path, staged)
        # The input may be written by an unattended lane. Bind the completed
        # staging tree to the legacy file once more immediately before swap.
        verify_tree(legacy_path, staged)
        replace_tree(staged, target)
        manifest = verify_tree(legacy_path, target)
        status = registry_store.registry_status(
            registry_dir=target, legacy_path=legacy_path
        )
        if not status.current:
            raise MigrationError(
                f"refreshed registry did not become current: {status.reason}"
            )
        return manifest
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def refresh_if_installed(legacy_path: Path,
                         registry_dir: Path | None = None) -> bool:
    """Refresh an installed projection after a legacy registry write.

    Fixtures and pre-migration clones have no manifest and need no extra
    files, so callers can use this unconditionally. A present manifest means
    the projection is part of the repository contract: failure propagates and
    stops the writer's enclosing command instead of leaving a stale index that
    only the next audit would discover.
    """
    legacy_path = Path(legacy_path).resolve()
    target = (Path(registry_dir).resolve() if registry_dir is not None
              else legacy_path.parent / "registry")
    if not (target / "manifest.json").is_file():
        return False
    refresh_tree(legacy_path, target)
    return True


def _summary(mode: str, manifest: dict[str, Any]) -> None:
    counts = manifest["counts"]
    count_text = ", ".join(f"{table}={count}" for table, count in counts.items())
    print(
        f"registry migration {mode} ok: {sum(counts.values())} records "
        f"({count_text}); legacy sha256 {manifest['legacy']['sha256']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--registry", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--write", action="store_true")
    modes.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    legacy = (args.legacy or root / "sources.toml").resolve()
    registry = (args.registry or root / "registry").resolve()
    try:
        if args.check:
            manifest = verify_tree(legacy, registry)
            _summary("check", manifest)
        elif args.write:
            manifest = write_tree(legacy, registry)
            _summary("write", manifest)
        elif args.refresh:
            manifest = refresh_tree(legacy, registry)
            _summary("refresh", manifest)
        else:
            with tempfile.TemporaryDirectory(prefix="registry-migration-") as raw:
                manifest = build_tree(legacy, Path(raw) / "registry")
            _summary("dry-run", manifest)
            print(f"would replace {registry}; no repository files written")
    except (MigrationError, registry_store.RegistryError, OSError) as exc:
        print(f"registry migration failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
