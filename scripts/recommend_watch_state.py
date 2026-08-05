#!/usr/bin/env python3
"""Rank active sources that may no longer justify recurring polling."""
from __future__ import annotations

import argparse
import collections
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--minimum-polls", type=int, default=6)
    args = parser.parse_args()

    registry = tomllib.loads((ROOT / "sources.toml").read_text())
    reviews = tomllib.loads((ROOT / "revision-reviews.toml").read_text())
    status: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for entry in reviews.get("revision", []):
        status[entry["source"]][entry["status"]] += 1

    polls: collections.Counter[str] = collections.Counter()
    bytes_read: collections.Counter[str] = collections.Counter()
    changed: collections.Counter[str] = collections.Counter()
    with (ROOT / "archive/index.jsonl").open() as handle:
        for line in handle:
            event = json.loads(line)
            source = event.get("id")
            if not isinstance(source, str):
                continue
            polls[source] += 1
            bytes_read[source] += int(event.get("bytes", 0) or 0)
            if event.get("event") in ("changed", "first"):
                changed[source] += 1

    rows = []
    for source in registry.get("source", []):
        sid = source["id"]
        if source.get("watch", "active") != "active" or source.get("gone"):
            continue
        if source.get("tier") != 3 or polls[sid] < args.minimum_polls:
            continue
        reviews_for_source = status[sid]
        content = reviews_for_source["source-content"]
        noise = reviews_for_source["capture-noise"]
        # This is a recommendation queue, never an automatic verdict. Prefer
        # sources with no observed editorial movement and high polling cost.
        if content:
            continue
        rows.append((polls[sid], bytes_read[sid], noise, changed[sid], sid,
                     source.get("kind", ""), source.get("org", "")))

    print("source\tpolls\tmegabytes\tchanged\tnoise_reviews\tkind\torg")
    for poll_count, byte_count, noise, changes, sid, kind, org in sorted(
        rows, reverse=True
    )[:args.limit]:
        print(f"{sid}\t{poll_count}\t{byte_count / 1_000_000:.1f}\t"
              f"{changes}\t{noise}\t{kind}\t{org}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
