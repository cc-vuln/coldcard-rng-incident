#!/usr/bin/env python3
"""Classify unreviewed diffs proven mechanical by current capture rules.

Three deterministic lanes: text equal under the tested canonical comparison
rules, tier-3 Reddit structure churn, and X-thread selection churn. A lane
classifies only the case it can prove from the diff and the capture's own
records; anything short of proof stays for the review agent.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import capture
from list_unreviewed_diffs import unreviewed

ROOT = Path(__file__).resolve().parent.parent
HEADER = re.compile(r"^--- ([^@]+)@(\d{8}T\d{6}Z)$")
POST_RE = re.compile(r"post: (\d+)")
ROLE_RE = re.compile(r"role: (ancestor|focal|self-thread|reply)")
CREATED_RE = re.compile(r"created: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")


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


def thread_records(lines: list[str]) -> list[dict] | None:
    """Whole post records from one side of an x-thread diff, or None.

    A record is the fixed block flatten_thread writes: post, role, author,
    name, created, media, body:, then body text up to the next record. A diff
    side that is anything else (a gap line, a mid-record edit, a partial
    record) is not mechanically classifiable, so the lane abstains.
    """
    records: list[dict] = []
    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue
        post = POST_RE.fullmatch(lines[i])
        if post is None or i + 6 >= len(lines):
            return None
        fields = lines[i + 1 : i + 7]
        role = ROLE_RE.fullmatch(fields[0])
        created = CREATED_RE.fullmatch(fields[3])
        if (role is None or created is None
                or not fields[1].startswith("author: ")
                or not fields[2].startswith("name: ")
                or not re.fullmatch(r"media: \d+", fields[4])
                or fields[5] != "body:"):
            return None
        j = i + 7
        while j < len(lines) and not POST_RE.fullmatch(lines[j]):
            j += 1
        records.append({"status": post.group(1), "role": role.group(1),
                        "created": created.group(1)})
        i = j
    return records


def thread_depth_capped(snapshot_root: Path, source_id: str,
                        timestamp: str) -> bool | None:
    """Whether the capture's own depth record declares a binding reply cap."""
    try:
        record = json.loads(
            (snapshot_root / source_id / f"{timestamp}.json").read_text(
                encoding="utf-8"))
    except (OSError, ValueError):
        return None
    depth = record.get("depth")
    if not isinstance(depth, dict):
        return None
    capped = depth.get("capped")
    return capped if isinstance(capped, bool) else None


def xthread_structural(path: Path, sources: dict[str, dict],
                       snapshot_root: Path | None = None,
                       ) -> tuple[str, str] | None:
    """(status, summary) for mechanically classifiable thread churn, or None.

    Two cases only. Selection churn: every removal is a reply record and the
    capture's own depth record declares the reply cap was reached, so the
    capture is a ranked sample and which replies it holds is X's choice, not
    the conversation's. Growth: additions only, and every record was first
    posted after the previous capture ran, so it cannot be a reply an earlier
    scroll missed. Anything else stays for the review agent: absence is not
    deletion (docs/design/x-thread-capture.md section 6), and a reply missing
    for any reason short of a declared cap is never noise here. A visible gap
    in collection is better than an invisible fabrication of deletion.
    """
    source = sources.get(path.parent.name)
    if source is None or source.get("capture") != "x-thread":
        return None
    snapshot_root = snapshot_root or (ROOT / "archive/snapshots")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    match = HEADER.fullmatch(lines[0])
    if not match or match.group(1) != path.parent.name:
        return None
    previous_ts = match.group(2)
    added = thread_records([line[1:] for line in lines
                            if line.startswith("+")
                            and not line.startswith("+++")])
    removed = thread_records([line[1:] for line in lines
                              if line.startswith("-")
                              and not line.startswith("---")])
    if added is None or removed is None:
        return None
    if removed:
        if any(record["role"] != "reply" for record in removed):
            return None
        if thread_depth_capped(snapshot_root, path.parent.name,
                               path.stem) is not True:
            return None
        return (
            "capture-noise",
            f"X served a different ranked subset of replies while this "
            f"capture's own depth record declares the reply cap was reached: "
            f"{len(removed)} reply record(s) left the capture and "
            f"{len(added)} entered, every removal a reply whose text is "
            f"preserved in this diff. A capped capture is a ranked sample, "
            f"so which replies it holds is selection churn, not deletion.",
        )
    if not added:
        return None
    previous_iso = (f"{previous_ts[0:4]}-{previous_ts[4:6]}-"
                    f"{previous_ts[6:8]}T{previous_ts[9:11]}:"
                    f"{previous_ts[11:13]}:{previous_ts[13:15]}Z")
    if any(record["created"] <= previous_iso for record in added):
        return None
    roles = sorted({record["role"] for record in added})
    return (
        "source-content",
        f"{len(added)} new post record(s) ({', '.join(roles)}) entered the "
        f"capture, each first posted after the previous capture ran; no "
        f"record left the capture.",
    )



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    registry = capture.load_sources()
    sources = {source["id"]: source
               for source in capture.pollable_sources(registry)}
    review_file = ROOT / "revision-reviews.toml"
    proven = []
    structural = []
    thread = []
    for path in unreviewed(ROOT / "archive/diffs", review_file):
        equivalent, normalizers = canonical_equivalent(path, sources)
        if equivalent:
            proven.append((path, normalizers))
            continue
        source = sources.get(path.parent.name, {})
        if source.get("capture") == "reddit-json" and source.get("tier") == 3:
            structural.append((path, reddit_structural_summary(path)))
            continue
        decision = xthread_structural(path, sources)
        if decision is not None:
            thread.append((path, *decision))

    for path, normalizers in proven:
        print(f"{path.parent.name} {path.stem}: {', '.join(normalizers)}")
    for path, _summary in structural:
        print(f"{path.parent.name} {path.stem}: tier3 reddit structure")
    for path, status, _summary in thread:
        print(f"{path.parent.name} {path.stem}: x-thread {status}")
    if args.apply and (proven or structural or thread):
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
            for path, status, summary in thread:
                escaped = summary.replace("\\", "\\\\").replace('"', '\\"')
                handle.write(
                    "\n[[revision]]\n"
                    f'source = "{path.parent.name}"\n'
                    f'timestamp = "{path.stem}"\n'
                    f'status = "{status}"\n'
                    f'summary = "{escaped}"\n'
                    'classifier = "x-thread-structure"\n'
                )
        print(f"classified {len(proven)} canonical-equivalent, "
              f"{len(structural)} Tier 3 Reddit and "
              f"{len(thread)} X-thread difference(s)")
    elif not args.apply:
        print(f"{len(proven)} canonical-equivalent, {len(structural)} "
              f"Tier 3 Reddit and {len(thread)} X-thread difference(s), "
              "dry run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
