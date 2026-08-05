#!/usr/bin/env python3
"""List diff paths that do not yet have a completed review."""
from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


def unreviewed(diff_root: Path, review_file: Path) -> list[Path]:
    data = tomllib.loads(review_file.read_text())
    classified = {
        (entry["source"], entry["timestamp"])
        for entry in data.get("revision", [])
        if entry.get("status") != "unreviewed"
    }
    pending = [
        path for path in diff_root.glob("*/*.diff")
        if (path.parent.name, path.stem) not in classified
    ]
    return sorted(pending, key=lambda path: (path.stem, path.parent.name))


def bounded(paths: list[Path], limit: int, max_bytes: int) -> list[Path]:
    """Take an oldest-first batch, always allowing one oversized diff."""
    selected: list[Path] = []
    size = 0
    for path in paths[:limit]:
        candidate_size = path.stat().st_size
        if selected and size + candidate_size > max_bytes:
            break
        selected.append(path)
        size += candidate_size
    return selected


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff-root", type=Path, default=root / "archive/diffs")
    parser.add_argument("--reviews", type=Path,
                        default=root / "revision-reviews.toml")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--max-bytes", type=int, default=120_000)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be at least 1")
    paths = bounded(unreviewed(args.diff_root, args.reviews),
                    args.limit, args.max_bytes)
    for path in paths:
        try:
            print(path.relative_to(root))
        except ValueError:
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
