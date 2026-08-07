#!/usr/bin/env python3
"""Rotate old DISCOVERY.md verdicts into discovery/assessed-YYYY-MM.md.

DISCOVERY.md is the live intake queue: pending candidates, and the intake
agent's verdict on each assessed one. The verdict half only grows, and the
file is shared with five discovery lanes and two agent prompts, all of
which parse it section by section. Rotation keeps the queue recent: a
verdict from the last --keep-days days stays, an older one moves, verbatim
and one line per entry, into discovery/assessed-YYYY-MM.md, filed by the
month of its verdict stamp.

The destination is a top-level discovery/ directory, not archive/. The
deposit gate refuses an unclassified archive tree and the manifest
describes every archive tree as captures; rotated verdicts are neither.
They are project-created records of the same kind as DISCOVERY.md itself,
so they live beside it and ride the deposit's ordinary inclusion path.

Three properties matter more than the move:

- DISCOVERY.md stays the single writable queue. It is replaced atomically
  under the intake lock the lanes and the intake agent already take
  (discovery_common), never edited in place, and never read until the lock
  is held, so a rotation cannot interleave with an intake run
- Nothing is rewritten. A rotated line is byte-identical to the line that
  sat in the queue, and a month file is append-only: re-running after a
  restore re-adds nothing already there
- A verdict without a UTC stamp never rotates. Agent verdicts carry
  (YYYYMMDDTHHMMSSZ); a hand-dismissed line without one has no assessment
  date to file under, so it stays in the queue until a human adds one

One upstream behaviour changes deliberately: update_intake() no longer
sees rotated URLs as already present, so a lane that loses its seen state
can re-queue an old dismissed thread. The lanes keep SEEN_KEEP ids each
and that seen state is the real dedupe; the queue scan was a backstop.

Zero dependencies: stdlib only, plus discovery_common for the lock and the
atomic write.
"""

import argparse
import re
import sys
from datetime import date

import discovery_common as dc

# The last stamp on the line wins: a corrected verdict carries two, and the
# correction date is the assessment date.
STAMP = re.compile(r"\((\d{4})(\d{2})(\d{2})T\d{6}Z\)")

MONTH_HEADER = """\
# Discovery intake verdicts — {month}

Assessed entries rotated out of DISCOVERY.md by `just rotate-discovery`,
verbatim, one line per entry. The queue keeps recent verdicts; older ones
live here, filed by verdict month.
"""


def verdict_date(line: str) -> date | None:
    """The date of a line's last verdict stamp, or None when it has none."""
    stamps = STAMP.findall(line)
    if not stamps:
        return None
    year, month, day = stamps[-1]
    return date(int(year), int(month), int(day))


def plan(text: str, today: date, keep_days: int) -> tuple[dict[str, list[str]], str]:
    """Split the intake text into lines to rotate and the rewritten queue.

    Returns ({month: [rotated lines]}, new DISCOVERY.md body). Lines that
    are not queue entries (blank separators) are kept with the queue; the
    rewritten tail collapses the blank runs a removal leaves behind.
    """
    if "\n## Assessed" not in text:
        raise SystemExit("rotate-discovery: DISCOVERY.md has no ## Assessed section")
    head, tail = text.split("## Assessed", 1)

    # The Assessed section ends at the next heading: the live queue carries
    # a third section ("Link review, held for a human decision") whose lines
    # are not verdicts and are preserved verbatim, blank runs included.
    tail_lines = tail.splitlines()
    end = next((i for i, line in enumerate(tail_lines)
                if line.startswith("## ")), len(tail_lines))
    assessed, rest = tail_lines[:end], tail_lines[end:]

    kept: list[str] = []
    moved: dict[str, list[str]] = {}
    for line in assessed:
        stamp = verdict_date(line) if line.startswith("- ") else None
        if line.startswith("- ") and stamp is not None \
                and (today - stamp).days > keep_days:
            moved.setdefault(stamp.strftime("%Y-%m"), []).append(line)
        else:
            kept.append(line)

    tidied: list[str] = []
    for line in kept:
        if not line.strip() and (not tidied or not tidied[-1].strip()):
            continue
        tidied.append(line)
    while tidied and not tidied[-1].strip():
        tidied.pop()

    out = head.rstrip() + "\n\n## Assessed\n"
    if tidied:
        out += "\n" + "\n".join(tidied) + "\n"
    if rest:
        out += "\n" + "\n".join(rest).rstrip() + "\n"
    return moved, out


def write_month_files(moved: dict[str, list[str]]) -> None:
    """Append rotated lines to their month files, creating files as needed.

    A line already present is skipped, so a rotate-restore-rotate sequence
    cannot duplicate one.
    """
    dest_dir = dc.ROOT / "discovery"
    dest_dir.mkdir(exist_ok=True)
    for month, lines in sorted(moved.items()):
        path = dest_dir / f"assessed-{month}.md"
        body = (path.read_text(encoding="utf-8") if path.exists()
                else MONTH_HEADER.format(month=month))
        present = set(body.splitlines())
        add = [line for line in lines if line not in present]
        if add:
            dc.atomic_text(path, body.rstrip() + "\n\n" + "\n".join(add) + "\n")


def rotate(today: date, keep_days: int, dry_run: bool = False) -> dict[str, list[str]]:
    """Move assessed lines older than keep_days out of the intake queue."""
    if not dc.INTAKE.exists():
        return {}
    if dry_run:
        return plan(dc.INTAKE.read_text(encoding="utf-8"), today, keep_days)[0]

    dc.INTAKE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with dc.INTAKE_LOCK.open("a+") as lock_handle:
        dc.acquire_intake_lock(lock_handle)
        # Read inside the lock: an intake run appends verdicts under the
        # same lock, and a plan made from a pre-lock read could drop one.
        moved, out = plan(dc.INTAKE.read_text(encoding="utf-8"), today, keep_days)
        if not moved:
            return {}
        write_month_files(moved)
        dc.atomic_text(dc.INTAKE, out)
        return moved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep-days", type=int, default=31,
                        help="keep verdicts this many days old or newer "
                             "(default 31)")
    parser.add_argument("--today", type=date.fromisoformat, default=None,
                        help="override today's date, YYYY-MM-DD (testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would move without writing")
    args = parser.parse_args()

    today = args.today or date.today()
    try:
        moved = rotate(today, args.keep_days, dry_run=args.dry_run)
    except TimeoutError as exc:
        print(f"rotate-discovery: {exc}", file=sys.stderr)
        return 2

    if not moved:
        print("rotate-discovery: nothing old enough to rotate")
        return 0
    verb = "would move" if args.dry_run else "rotated"
    for month, lines in sorted(moved.items()):
        print(f"rotate-discovery: {verb} {len(lines)} verdict(s) "
              f"to discovery/assessed-{month}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
