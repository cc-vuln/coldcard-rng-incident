#!/usr/bin/env python3
"""Classify unreviewed diffs proven equal by current canonical rules."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import capture
from list_unreviewed_diffs import unreviewed

ROOT = Path(__file__).resolve().parent.parent
HEADER = re.compile(r"^--- ([^@]+)@(\d{8}T\d{6}Z)$")


def canonical_equivalent(path: Path, sources: dict[str, dict]) -> tuple[bool, list[str]]:
    lines = path.read_text().splitlines()
    if not lines:
        return False, []
    match = HEADER.fullmatch(lines[0])
    if not match or match.group(1) != path.parent.name:
        return False, []
    source_id, previous_ts = match.groups()
    source = sources.get(source_id)
    if source is None:
        return False, []
    normalizers = capture.source_normalizers(source)
    if not normalizers:
        return False, []
    previous = ROOT / "archive/snapshots" / source_id / f"{previous_ts}.txt"
    current = ROOT / "archive/snapshots" / source_id / f"{path.stem}.txt"
    if not previous.exists() or not current.exists():
        return False, []
    comparison_source = {**source, "normalizers": normalizers}
    before = capture.canonical_text(previous.read_text(), comparison_source)
    after = capture.canonical_text(current.read_text(), comparison_source)
    return before == after, normalizers


def reddit_structural_summary(path: Path) -> str:
    lines = path.read_text().splitlines()
    added = sum(line.startswith("+comment: ") for line in lines)
    removed = sum(line.startswith("-comment: ") for line in lines)
    if added or removed:
        parts = []
        if added:
            parts.append(f"{added} additional comment record(s)")
        if removed:
            parts.append(f"{removed} previously held comment record(s) omitted")
        return (
            "Reddit served " + " and ".join(parts)
            + "; the diff preserves their text and any edits to existing records."
        )
    return (
        "Fields within an existing Reddit post or comment changed; the diff "
        "preserves the exact served text."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    registry = capture.load_sources()
    sources = {source["id"]: source for source in registry.get("source", [])}
    review_file = ROOT / "revision-reviews.toml"
    proven = []
    structural = []
    for path in unreviewed(ROOT / "archive/diffs", review_file):
        equivalent, normalizers = canonical_equivalent(path, sources)
        if equivalent:
            proven.append((path, normalizers))
            continue
        source = sources.get(path.parent.name, {})
        if source.get("capture") == "reddit-json" and source.get("tier") == 3:
            structural.append((path, reddit_structural_summary(path)))

    for path, normalizers in proven:
        print(f"{path.parent.name} {path.stem}: {', '.join(normalizers)}")
    for path, _summary in structural:
        print(f"{path.parent.name} {path.stem}: tier3 reddit structure")
    if args.apply and (proven or structural):
        with review_file.open("a", encoding="utf-8") as handle:
            for path, normalizers in proven:
                rules = ", ".join(normalizers)
                handle.write(
                    "\n[[revision]]\n"
                    f'source = "{path.parent.name}"\n'
                    f'timestamp = "{path.stem}"\n'
                    'status = "capture-noise"\n'
                    "summary = \"Only text excluded by the tested canonical "
                    f"comparison rules changed ({rules}); the tracked source "
                    "content is identical.\"\n"
                    'classifier = "canonical-equivalence"\n'
                )
            for path, summary in structural:
                escaped = summary.replace("\\", "\\\\").replace('"', '\\"')
                handle.write(
                    "\n[[revision]]\n"
                    f'source = "{path.parent.name}"\n'
                    f'timestamp = "{path.stem}"\n'
                    'status = "source-content"\n'
                    f'summary = "{escaped}"\n'
                    'classifier = "reddit-structure"\n'
                )
        print(f"classified {len(proven)} canonical-equivalent and "
              f"{len(structural)} Tier 3 Reddit difference(s)")
    elif not args.apply:
        print(f"{len(proven)} canonical-equivalent and "
              f"{len(structural)} Tier 3 Reddit difference(s), dry run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
