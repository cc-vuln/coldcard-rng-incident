#!/usr/bin/env python3
"""Render compact, bounded evidence packets for archive diff review."""
from __future__ import annotations

import argparse
from pathlib import Path

import registry_store

ROOT = Path(__file__).resolve().parent.parent


def changed_lines(path: Path, limit: int) -> tuple[list[str], bool]:
    selected = []
    for line in path.read_text().splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            selected.append(line)
    return selected[:limit], len(selected) > limit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--lines-per-diff", type=int, default=120)
    args = parser.parse_args()
    registry = registry_store.load(ROOT)
    sources = {source["id"]: source for source in registry.get("source", [])}
    for raw_path in args.paths:
        path = ROOT / raw_path
        source = sources.get(path.parent.name, {})
        lines, truncated = changed_lines(path, args.lines_per_diff)
        print(f"### {path.parent.name} {path.stem}")
        print(f"Title: {source.get('title', '')}")
        context = source.get("note") or source.get("why") or ""
        if context:
            print("Context: " + " ".join(str(context).split()))
        # No markdown fence around the diff lines. A fence is closed by text
        # anybody can write, and these lines are exactly that: whatever the
        # source published. The whole packet file is wrapped once by
        # render_agent_prompt.py in a marker unique to the run, which is the
        # boundary that content cannot guess its way out of.
        print("Changed lines:")
        print("\n".join(lines) if lines else "(no added or removed text lines)")
        print(f"Packet truncated: {'yes' if truncated else 'no'}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
