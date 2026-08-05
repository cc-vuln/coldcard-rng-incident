#!/usr/bin/env python3
"""Rank captured sources by review yield and avoidable noise.

This is a read-only operational report. It uses the additive review ledger and
does not infer that an unreviewed difference is noise.
"""
from __future__ import annotations

import argparse
import collections
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    data = tomllib.loads((ROOT / "revision-reviews.toml").read_text())
    counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for review in data.get("revision", []):
        counts[review["source"]][review["status"]] += 1

    rows = []
    totals: collections.Counter[str] = collections.Counter()
    for source, statuses in counts.items():
        totals.update(statuses)
        reviewed = sum(statuses.values())
        noise = statuses["capture-noise"]
        content = statuses["source-content"]
        rows.append((noise, noise / reviewed, reviewed, content, source))

    print("source\treviewed\tcontent\tnoise\tnoise_pct")
    for noise, ratio, reviewed, content, source in sorted(rows, reverse=True)[:args.limit]:
        print(f"{source}\t{reviewed}\t{content}\t{noise}\t{ratio:.0%}")
    reviewed = sum(totals.values())
    ratio = totals["capture-noise"] / reviewed if reviewed else 0
    print(f"\nTOTAL\t{reviewed}\t{totals['source-content']}\t"
          f"{totals['capture-noise']}\t{ratio:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
