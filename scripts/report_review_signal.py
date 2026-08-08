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

    # Human-override measurement. A correction keeps the entry it corrects
    # and appends one carrying classifier = "human", so the count of entries
    # a human later corrected, per status, is the measured error rate of the
    # review layer. The denominator is every non-human entry, tagged or not:
    # the agent's entries predate the classifier field.
    revisions = data.get("revision", [])
    human_keys = {(r["source"], r["timestamp"]) for r in revisions
                  if r.get("classifier") == "human"}
    base = [r for r in revisions if r.get("classifier") != "human"]
    corrected = [r for r in base
                 if (r["source"], r["timestamp"]) in human_keys]
    by_status: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for entry in base:
        by_status[entry["status"]]["reviewed"] += 1
    for entry in corrected:
        by_status[entry["status"]]["corrected"] += 1

    print(f"\nhuman overrides (classifier = \"human\"): {len(human_keys)} "
          "corrected entr(ies)")
    print("status\treviewed\tcorrected\toverride_pct")
    for status in sorted(by_status):
        reviewed_n = by_status[status]["reviewed"]
        corrected_n = by_status[status]["corrected"]
        ratio = corrected_n / reviewed_n if reviewed_n else 0
        print(f"{status}\t{reviewed_n}\t{corrected_n}\t{ratio:.1%}")
    reviewed_n = len(base)
    ratio = len(corrected) / reviewed_n if reviewed_n else 0
    print(f"TOTAL\t{reviewed_n}\t{len(corrected)}\t{ratio:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
