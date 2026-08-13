#!/usr/bin/env python3
"""Describe every held capture without reproducing any of it.

The archived snapshots and social captures are other people's copyrighted work.
This project holds them and quotes them; it does not redistribute complete
copies in a deposit designed not to be retractable. Excluding them from a
deposit, though, would leave that deposit silently describing a corpus it does
not contain, which is worse than not depositing at all.

This is the answer: one row per held capture, carrying what the capture *is*
rather than what it said. A reader can see exactly what exists, cite an
individual capture, verify a copy obtained from the repository or from the
project directly, and know precisely what a deposit is missing.

Fields are allowlisted rather than copied out of the sidecar, on the same
reasoning as scripts/response_headers.py: a sidecar accumulates collection
detail over time, and a manifest that forwards whatever it finds will publish
the next field somebody adds.

Output is JSONL, one object per line, sorted by (kind, id, captured_at), so two
runs over the same archive produce byte-identical files and a diff of two
manifests is readable.

    python3 scripts/build_manifest.py > manifest.jsonl
    python3 scripts/build_manifest.py --out manifest.jsonl --summary
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

import registry_store

REPO = Path(__file__).resolve().parent.parent
ARCHIVE = REPO / "archive"
SNAPSHOTS = ARCHIVE / "snapshots"
X_CAPTURES = ARCHIVE / "x"

TS = re.compile(r"^\d{8}T\d{6}Z$")

# What a manifest row may carry from a snapshot sidecar. Everything here
# describes the capture as an object: when it was taken, what it was taken
# from, how big it is and what its bytes hash to. Nothing here is the
# document's content, and nothing describes the machine that took it.
SIDECAR_FIELDS = {
    "bytes": "bytes",
    "event": "event",
    "url": "url",
    "provenance": "provenance",
    "status": "http_status",
    "content_type": "content_type",
    "raw_sha256": "raw_sha256",
    "text_sha256": "text_sha256",
    "capture": "capture_method",
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_registry() -> dict[str, Any]:
    """Read the layout-independent registry with the stdlib-only loader."""
    return registry_store.load(REPO)


def snapshot_rows(registry: dict[str, Any]) -> Iterator[dict[str, Any]]:
    # Two registry sections write into archive/snapshots/. A [[source]] does
    # obviously; an [[x_post]] with thread = true does too, because the thread
    # extractor produces canonical text rather than a screenshot. Looking in
    # only the first section leaves a captured thread described with a null
    # title and no author, which is exactly the understatement this manifest
    # exists to prevent.
    sources = {s["id"]: s for s in registry.get("source", [])}
    for post in registry.get("x_post", []):
        sources.setdefault(post["id"], post)
    if not SNAPSHOTS.is_dir():
        return
    for source_dir in sorted(SNAPSHOTS.iterdir()):
        if not source_dir.is_dir():
            continue
        source = sources.get(source_dir.name, {})
        for meta_path in sorted(source_dir.glob("*.meta.json")):
            ts = meta_path.name[: -len(".meta.json")]
            if not TS.match(ts):
                continue
            try:
                sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"manifest: unreadable sidecar {meta_path}: {exc}",
                      file=sys.stderr)
                continue

            row: dict[str, Any] = {
                "kind": "snapshot",
                "id": source_dir.name,
                "captured_at": ts,
                "title": source.get("title"),
                "organisation": source.get("org"),
                # An x_post carries author/posted/tag where a source carries
                # kind/published, so fall through rather than reporting null.
                "author": source.get("author"),
                "source_kind": source.get("kind") or source.get("tag"),
                "published": source.get("published") or source.get("posted"),
            }
            for field, name in SIDECAR_FIELDS.items():
                if field in sidecar:
                    row[name] = sidecar[field]
            row.setdefault("url", source.get("url"))
            row.setdefault("provenance", "cc-vuln.org")

            # What is held on disk, as distinct from what the sidecar recorded
            # at capture time. The two disagreeing is exactly the mutation the
            # archive audit exists to catch, so both belong in the manifest.
            files = {}
            for suffix in (".html", ".txt", ".json"):
                held = source_dir / f"{ts}{suffix}"
                if held.exists():
                    files[held.name] = held.stat().st_size
            row["held_files"] = files

            diff = ARCHIVE / "diffs" / source_dir.name / f"{ts}.diff"
            row["diff"] = f"archive/diffs/{source_dir.name}/{ts}.diff" if diff.exists() else None
            row["withheld_from_deposit"] = True
            yield row


def x_rows(registry: dict[str, Any]) -> Iterator[dict[str, Any]]:
    posts = {p["id"]: p for p in registry.get("x_post", [])}
    if not X_CAPTURES.is_dir():
        return
    for post_dir in sorted(X_CAPTURES.iterdir()):
        if not post_dir.is_dir():
            continue
        post = posts.get(post_dir.name, {})
        for capture_dir in sorted(post_dir.iterdir()):
            if not capture_dir.is_dir():
                continue
            # "undated" sorts after digits, so a capture directory that is not
            # a timestamp is rejected by name rather than by comparison, the
            # same check the media staging tool makes. Rejected for display is
            # not the same as absent, though: a manifest that quietly dropped
            # these would describe a corpus smaller than the one that exists,
            # which is the one thing it must not do. They are described with a
            # null capture time and the reason stated.
            dated = bool(TS.match(capture_dir.name))
            artefacts = {}
            for artefact in sorted(capture_dir.iterdir()):
                if artefact.is_file():
                    artefacts[artefact.name] = {
                        "bytes": artefact.stat().st_size,
                        "sha256": sha256_of(artefact),
                    }
            row = {
                "kind": "social-capture",
                "id": post_dir.name,
                "captured_at": capture_dir.name if dated else None,
                "title": post.get("title"),
                "author": post.get("author"),
                "organisation": post.get("org"),
                "url": post.get("url"),
                "posted": post.get("posted"),
                "provenance": "cc-vuln.org",
                "held_files": artefacts,
                "withheld_from_deposit": True,
            }
            if not dated:
                row["directory"] = capture_dir.name
                row["note"] = (
                    "capture directory is not a timestamp, so the capture time "
                    "is not established and the artefacts are not published"
                )
            yield row


# Trees under archive/ this manifest describes with a purpose-built reader.
# Everything else is described generically rather than ignored. A legacy
# archive/reddit/ tree sat unnoticed by every tool here until a deposit run
# swept it up, and a manifest whose coverage depends on somebody remembering to
# add a reader is a manifest that quietly understates the corpus. That tree has
# since been retired; the next one has not been written yet.
KNOWN_TREES = {"snapshots", "x", "diffs", "runs"}

# Trees whose captures the generic reader describes well enough that a
# purpose-built one would add nothing: one directory per capture, timestamped,
# with the artefacts inside. archive/nostr/ is the first.


def other_rows() -> Iterator[dict[str, Any]]:
    if not ARCHIVE.is_dir():
        return
    for tree in sorted(ARCHIVE.iterdir()):
        if not tree.is_dir() or tree.name in KNOWN_TREES:
            continue
        for directory in sorted(p for p in tree.rglob("*") if p.is_dir()):
            files = sorted(p for p in directory.iterdir() if p.is_file())
            if not files:
                continue
            dated = bool(TS.match(directory.name))
            relative = directory.relative_to(ARCHIVE).as_posix()
            row = {
                "kind": f"{tree.name}-capture",
                "id": relative,
                "captured_at": directory.name if dated else None,
                "provenance": "cc-vuln.org",
                "held_files": {
                    f.name: {"bytes": f.stat().st_size, "sha256": sha256_of(f)}
                    for f in files
                },
                "withheld_from_deposit": True,
            }
            if not dated:
                row["directory"] = directory.name
                row["note"] = (
                    "capture directory is not a timestamp, so the capture time "
                    "is not established and the artefacts are not published"
                )
            yield row


def build(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    registry = load_registry() if registry is None else registry
    rows = list(snapshot_rows(registry)) + list(x_rows(registry)) + list(other_rows())
    # An undated capture sorts first within its id rather than by string, since
    # "undated" would otherwise sort after every digit and land at the end.
    rows.sort(key=lambda r: (r["kind"], r["id"], r["captured_at"] or ""))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    parser.add_argument("--summary", action="store_true",
                        help="print counts to stderr when finished")
    args = parser.parse_args()

    rows = build()
    body = "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)

    if args.summary:
        snapshots = sum(1 for r in rows if r["kind"] == "snapshot")
        social = sum(1 for r in rows if r["kind"] == "social-capture")
        other = len(rows) - snapshots - social
        sources = len({r["id"] for r in rows if r["kind"] == "snapshot"})
        inherited = sum(1 for r in rows if r.get("provenance") == "wayback")
        undated = sum(1 for r in rows if r["captured_at"] is None)
        print(
            f"manifest: {len(rows)} capture(s) described "
            f"({snapshots} snapshot(s) across {sources} source(s), "
            f"{social} social capture(s), {other} in other capture tree(s), "
            f"{inherited} inherited from wayback, "
            f"{undated} without an established capture time). "
            "No captured content is included.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
