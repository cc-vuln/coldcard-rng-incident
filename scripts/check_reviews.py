#!/usr/bin/env python3
"""Refuse to publish while any detected difference is unreviewed.

Every diff under archive/diffs must carry a classification in
revision-reviews.toml before the site can be built or deployed. The public
changes page renders a placeholder ("This detected difference has not yet
been reviewed for capture noise.") for anything unlisted; that placeholder is
an internal state, not something a reader should ever see on the live site.

Run by `just audit`, so every build and deploy recipe inherits the gate.
Exits non-zero and names the offenders. The remedy is to run the review
agent (or classify by hand), never to delete the diff: the archive is
append-only.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    reviews = tomllib.loads((ROOT / "revision-reviews.toml").read_text())
    classified = {
        (r["source"], r["timestamp"])
        for r in reviews.get("revision", [])
        if r.get("status") != "unreviewed"
    }

    outstanding = sorted(
        (p.parent.name, p.stem)
        for p in (ROOT / "archive" / "diffs").glob("*/*.diff")
        if (p.parent.name, p.stem) not in classified
    )

    if outstanding:
        print(f"review check failed: {len(outstanding)} detected "
              f"difference(s) lack a classification in revision-reviews.toml:",
              file=sys.stderr)
        for source, ts in outstanding[:15]:
            print(f"  - {source} {ts}", file=sys.stderr)
        if len(outstanding) > 15:
            print(f"  ... and {len(outstanding) - 15} more", file=sys.stderr)
        print("\nUnreviewed differences render as placeholders on the public "
              "changes page and must not ship. Run the review agent "
              "(sudo systemctl start archive-review.service) or classify "
              "them by hand; never delete the underlying diffs.",
              file=sys.stderr)
        return 1

    print(f"review check ok: every detected difference is classified "
          f"({len(classified)} revisions on file)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
