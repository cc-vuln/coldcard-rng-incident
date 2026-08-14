#!/usr/bin/env python3
"""Validate discovery history and refresh its generated Markdown views.

Before the structured-store cutover this command moved old verdict lines from
``DISCOVERY.md`` into monthly Markdown files. Canonical history now lives in
the event ledger and is never rotated. Monthly assessed pages, the working
queue pages and ``DISCOVERY.md`` are deterministic projections and may be
regenerated at any time.

The old ``--keep-days`` and ``--today`` options remain accepted so installed
services and operator habits do not fail during cutover. They no longer select
or remove history. ``--dry-run`` validates the migration and canonical
projections without rewriting presentation files.
"""

import argparse
import sys
from datetime import date

import discovery_common as dc
from discovery_store import DiscoveryStore, validate_migration


def rotate(today: date | None = None, keep_days: int = 31,
           dry_run: bool = False) -> dict[str, str]:
    """Compatibility entry point: validate, then optionally render all views."""
    # Retained only for callers of the former rotation API. History retention
    # is now unconditional, so age cannot affect the result.
    del today, keep_days
    validate_migration(dc.ROOT)
    if dry_run:
        return {}
    return DiscoveryStore(dc.ROOT).render_all()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep-days", type=int, default=31,
        help="deprecated compatibility option; structured history is retained",
    )
    parser.add_argument(
        "--today", type=date.fromisoformat, default=None,
        help="deprecated compatibility option, YYYY-MM-DD",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate without regenerating Markdown presentation",
    )
    args = parser.parse_args()

    try:
        written = rotate(args.today, args.keep_days, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        print(f"rotate-discovery: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print("rotate-discovery: structured history valid; no files written")
    else:
        print("rotate-discovery: structured history valid; refreshed "
              f"{len(written)} generated view(s); no history rotated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
