#!/usr/bin/env python3
"""Refuse to publish while any detected difference is unreviewed.

Every diff under archive/diffs must carry a classification in
revision-reviews.toml before the site can be built or deployed. The public
changes page renders a placeholder ("This detected difference has not yet
been reviewed for capture noise.") for anything unlisted; that placeholder is
an internal state, not something a reader should ever see on the live site.

The review agent appends those classifications unattended, so the file is
checked rather than trusted. Before the unreviewed gate runs, every
[[revision]] entry is validated for shape: a status from the vocabulary in
scripts/agent-review-prompt.md, a timestamp that names an existing diff file
for that source, a non-empty summary, and a controlled classifier when named.
A later entry may override one diff only when it carries
``classifier = "human"``; the latest entry wins. A malformed entry is worse than a missing one: it classifies
nothing the site can find, or says something nobody checked.

Run by `just audit`, so every build and deploy recipe inherits the gate.
Exits non-zero and names the offenders. The remedy is to run the review
agent (or classify by hand), never to delete the diff: the archive is
append-only.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The status vocabulary the review prompt (scripts/agent-review-prompt.md)
# defines, plus the "unreviewed" sentinel this gate already treats as "not
# classified": an entry carrying it is well-formed but settles nothing.
STATUSES = {"source-content", "capture-noise", "capture-correction",
            "unreviewed"}
CLASSIFIERS = {
    "review-agent", "reddit-structure", "x-thread-structure",
    "canonical-equivalence", "human",
}

TIMESTAMP = re.compile(r"^\d{8}T\d{6}Z$")


def shape_problems(revisions: list[dict], diffs: set[tuple[str, str]]) -> list[str]:
    """Each entry must be a well-formed classification of a real diff."""
    problems = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(revisions, start=1):
        label = (f"entry {index} ({entry.get('source', '?')} "
                 f"{entry.get('timestamp', '?')})")
        source, timestamp = entry.get("source"), entry.get("timestamp")
        if not isinstance(source, str) or not source.strip():
            problems.append(f"{label}: source is missing or empty")
            continue
        if not isinstance(timestamp, str) or not TIMESTAMP.match(timestamp):
            problems.append(
                f"{label}: timestamp is missing or not YYYYMMDDTHHMMSSZ")
            continue
        status = entry.get("status")
        if status not in STATUSES:
            problems.append(f"{label}: unknown status {status!r}; the "
                            f"vocabulary is {', '.join(sorted(STATUSES))}")
        summary = entry.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append(f"{label}: summary is empty; it is the one "
                            f"sentence a reader gets about what changed")
        if (source, timestamp) not in diffs:
            problems.append(
                f"{label}: names no diff at "
                f"archive/diffs/{source}/{timestamp}.diff; a classification "
                f"of nothing classifies nothing")
        classifier = entry.get("classifier")
        if classifier is not None and classifier not in CLASSIFIERS:
            problems.append(
                f"{label}: unknown classifier {classifier!r}; the vocabulary "
                f"is {', '.join(sorted(CLASSIFIERS))}"
            )
        key = (source, timestamp)
        if key in seen and classifier != "human":
            problems.append(
                f"{label}: a later classification of the same diff must "
                "carry classifier = \"human\""
            )
        seen.add(key)
    return problems


def unreviewed(root: Path) -> tuple[list[tuple[str, str]], int]:
    """Unclassified (source, timestamp) pairs, sorted, and the count on file.

    The rule the publish gate enforces below, factored out so `just status`
    (scripts/report_status.py) reports the same count the gate would refuse
    on, rather than a second reading of it that can drift.
    """
    reviews = tomllib.loads((root / "revision-reviews.toml").read_text())
    latest = {
        (r["source"], r["timestamp"]): r.get("status")
        for r in reviews.get("revision", [])
    }
    classified = {key for key, status in latest.items()
                  if status != "unreviewed"}
    diffs = {
        (p.parent.name, p.stem)
        for p in (root / "archive" / "diffs").glob("*/*.diff")
    }
    return sorted(diffs - classified), len(classified)


def main() -> int:
    reviews = tomllib.loads((ROOT / "revision-reviews.toml").read_text())
    revisions = reviews.get("revision", [])
    diffs = {
        (p.parent.name, p.stem)
        for p in (ROOT / "archive" / "diffs").glob("*/*.diff")
    }

    problems = shape_problems(revisions, diffs)
    if problems:
        print(f"review check failed: {len(problems)} malformed [[revision]] "
              f"entr(ies) in revision-reviews.toml:", file=sys.stderr)
        for problem in problems[:15]:
            print(f"  - {problem}", file=sys.stderr)
        if len(problems) > 15:
            print(f"  ... and {len(problems) - 15} more", file=sys.stderr)
        return 1

    outstanding, classified = unreviewed(ROOT)

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
          f"({classified} revisions on file)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
