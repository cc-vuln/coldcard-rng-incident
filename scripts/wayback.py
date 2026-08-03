#!/usr/bin/env python3
"""Recover pre-capture history for tracked sources from the Wayback Machine.

Our own capture only starts when we start. Anything a page said before that is
gone unless somebody else archived it. For this incident that matters a great
deal: the first Coinkite advisory said Mk4, Q and Mk5 were "not affected based
on our early analysis", and no capture of ours predates the revision that
removed it.

Snapshots recovered here are stored alongside our own with
`"provenance": "wayback"` in the meta, so a reader can always tell which
captures we took and which we inherited.

    wayback.py list ID [--from YYYYMMDD] [--to YYYYMMDD]
    wayback.py backfill ID [--from ...] [--to ...] [--limit N]
    wayback.py backfill-all [--from ...] [--to ...]
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import (  # noqa: E402  (shared helpers, deliberate)
    ROOT, SNAPSHOTS, DIFFS, extract_text, sha256, append_event, snap_dir,
    load_sources,
)
from archive_lock import (  # noqa: E402
    LOCK_BUSY_EXIT,
    ArchiveLockBusy,
    archive_lock,
)

CDX = "http://web.archive.org/cdx/search/cdx"
WB = "http://web.archive.org/web/{ts}id_/{url}"
UA = (
    "coldcard-rng-incident/1.0 "
    "(+https://github.com/cc-vuln/coldcard-rng-incident; historical preservation)"
)


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        elif data[:2] == b"\x1f\x8b":
            # The id_ endpoint replays the original bytes, gzip header and all,
            # without always setting Content-Encoding.
            data = gzip.decompress(data)
    return data


def sources() -> dict:
    cfg = load_sources()
    return {s["id"]: s for s in cfg.get("source", [])}


def cdx_query(url: str, frm: str | None, to: str | None) -> list[dict]:
    q = f"{CDX}?url={urllib.parse.quote(url, safe='')}&output=json&fl=timestamp,statuscode,digest,length&collapse=digest"
    if frm:
        q += f"&from={frm}"
    if to:
        q += f"&to={to}"
    try:
        rows = json.loads(_get(q, timeout=45).decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"  cdx query failed: {e}", file=sys.stderr)
        return []
    if len(rows) < 2:
        return []
    hdr = rows[0]
    return [dict(zip(hdr, r)) for r in rows[1:]]


def wb_ts_to_ours(ts14: str) -> str:
    """20260731015633 -> 20260731T015633Z"""
    return f"{ts14[:8]}T{ts14[8:14]}Z"


def newest_snapshot(url: str) -> tuple[str, bytes] | None:
    """The most recent successful Wayback capture of `url`, or None.

    Used as a fallback when the origin refuses this collector repeatedly. The
    returned bytes are the replayed original, so the archive can extract text
    from them exactly as it would from a direct fetch; the caller is responsible
    for recording `provenance: wayback` so a reader can always tell the
    difference.
    """

    rows = [r for r in cdx_query(url, None, None) if r.get("statuscode") == "200"]
    if not rows:
        return None
    newest = max(rows, key=lambda r: r["timestamp"])
    try:
        body = _get(WB.format(ts=newest["timestamp"], url=url))
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if not body:
        return None
    return newest["timestamp"], body


def cmd_list(args) -> int:
    src = sources().get(args.id)
    if not src:
        print(f"unknown source {args.id!r}", file=sys.stderr)
        return 2
    rows = cdx_query(src["url"], args.frm, args.to)
    if not rows:
        print("no Wayback snapshots in range")
        return 0
    print(f"{len(rows)} distinct Wayback snapshot(s) for {args.id}")
    for r in rows:
        print(f"  {wb_ts_to_ours(r['timestamp'])}  http {r['statuscode']:<4} "
              f"{r['length']:>8} bytes  digest {r['digest'][:12]}")
    return 0


def backfill_one(sid: str, src: dict, frm: str | None, to: str | None,
                 limit: int | None) -> int:
    rows = [r for r in cdx_query(src["url"], frm, to) if r.get("statuscode") == "200"]
    if not rows:
        print(f"  {sid}: no usable Wayback snapshots")
        return 0
    if limit:
        rows = rows[:limit]
    d = snap_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for r in rows:
        ts = wb_ts_to_ours(r["timestamp"])
        if (d / f"{ts}.txt").exists():
            print(f"  {sid} {ts}: already held")
            continue
        try:
            body = _get(WB.format(ts=r["timestamp"], url=src["url"]))
        except Exception as e:
            print(f"  {sid} {ts}: fetch failed ({str(e)[:60]})")
            continue
        text = extract_text(body, src["url"])
        if len(text) < 200:
            print(f"  {sid} {ts}: extracted only {len(text)} chars, skipping")
            continue
        (d / f"{ts}.txt").write_text(text, encoding="utf-8")
        (d / f"{ts}.html").write_bytes(body)
        (d / f"{ts}.meta.json").write_text(json.dumps({
            "ts": ts, "id": sid, "url": src["url"], "event": "first",
            "text_sha256": sha256(text.encode()), "raw_sha256": sha256(body),
            "bytes": len(body), "provenance": "wayback",
            "wayback_timestamp": r["timestamp"],
            "wayback_url": WB.format(ts=r["timestamp"], url=src["url"]),
            "wayback_digest": r["digest"],
            "note": "recovered from the Internet Archive, not captured by this project",
        }, indent=2, sort_keys=True), encoding="utf-8")
        append_event({"ts": ts, "id": sid, "url": src["url"], "event": "first",
                      "text_sha256": sha256(text.encode()), "bytes": len(body),
                      "provenance": "wayback"})
        print(f"  {sid} {ts}: recovered {len(text)} chars")
        n += 1
        time.sleep(1.5)
    return n


def cmd_backfill(args) -> int:
    srcs = sources()
    src = srcs.get(args.id)
    if not src:
        print(f"unknown source {args.id!r}", file=sys.stderr)
        return 2
    n = backfill_one(args.id, src, args.frm, args.to, args.limit)
    print(f"\nrecovered {n} snapshot(s). Run `just rebuild-diffs` to diff them in order.")
    return 0


def cmd_backfill_all(args) -> int:
    total = 0
    for sid, src in sources().items():
        print(f"{sid} ...")
        total += backfill_one(sid, src, args.frm, args.to, args.limit)
        time.sleep(1.0)
    print(f"\nrecovered {total} snapshot(s) across all sources.")
    return 0


def cmd_rebuild_diffs(args) -> int:
    """Regenerate diffs so recovered snapshots slot into the right order."""
    import difflib
    total = 0
    for sid in sources():
        snaps = sorted(snap_dir(sid).glob("*.txt")) if snap_dir(sid).is_dir() else []
        for prev, cur in zip(snaps, snaps[1:]):
            out = DIFFS / sid / f"{cur.stem}.diff"
            if out.exists():
                continue
            a = prev.read_text(encoding="utf-8").splitlines()
            b = cur.read_text(encoding="utf-8").splitlines()
            diff = list(difflib.unified_diff(
                a, b, fromfile=f"{sid}@{prev.stem}", tofile=f"{sid}@{cur.stem}",
                lineterm="", n=3))
            if not diff:
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(diff) + "\n", encoding="utf-8")
            added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            print(f"  {sid}: {prev.stem} -> {cur.stem}  +{added} -{removed}")
            total += 1
    print(f"\nbuilt {total} diff(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--from", dest="frm", help="YYYYMMDD")
        p.add_argument("--to", dest="to", help="YYYYMMDD")

    l = sub.add_parser("list"); l.add_argument("id"); common(l); l.set_defaults(fn=cmd_list)
    b = sub.add_parser("backfill"); b.add_argument("id"); common(b)
    b.add_argument("--limit", type=int); b.set_defaults(fn=cmd_backfill)
    ba = sub.add_parser("backfill-all"); common(ba)
    ba.add_argument("--limit", type=int); ba.set_defaults(fn=cmd_backfill_all)
    rd = sub.add_parser("rebuild-diffs"); rd.set_defaults(fn=cmd_rebuild_diffs)

    args = ap.parse_args()
    if args.cmd == "list":
        return args.fn(args)
    try:
        with archive_lock(f"wayback.py {args.cmd}"):
            return args.fn(args)
    except ArchiveLockBusy as exc:
        print(f"archive writer lock busy: {exc}", file=sys.stderr)
        return LOCK_BUSY_EXIT


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (used by cdx_query)
    sys.exit(main())
