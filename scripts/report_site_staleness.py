#!/usr/bin/env python3
"""Inventory what the site may have fallen behind on.

A deterministic packet for the page-sync agent and for a human: four lists,
each derived by grep-style scans and registry reads, none of them a judgement.
The script routes; it never decides.

Sections:

1. Unreferenced registered sources. Every id in sources.toml's four tables
   (`source`, `x_post`, `nostr_post`, `x_watch`) against every file under
   site/src/pages/. A reference is "strong" when the file links the source's
   record page (`/record/sources/<id>/`), "weak" when the bare id string
   appears without a link. `x_watch` entries have no id, so their handle is
   the match string; a weak handle mention is a substring hit and may be a
   display name rather than a citation, which is for the reader to judge.

   Exclusions, stated so they can be argued with:
   - `gone = true`: the origin stopped serving; the held capture is the
     record. A page that never linked it has fallen behind on nothing.
   - `watch = "frozen"`: deliberately not polled, kept as historical
     material; freshness does not apply.
   - `withhold_text = true`: the project itself holds the text back, so a
     page that does not cite it is following policy, not lagging.
   Excluded ids are counted in the section header, not listed.

2. Editorial-attention routing. Every `[[revision]]` with
   `status = "source-content"` newer than the previous packet's generated-at,
   with the pages that reference that source. "The source moved; these pages
   cite it." Which page, if any, needs an edit is downstream work.

3. Dated assertions aging. Lines under site/src/pages/ carrying both a
   current-state phrase and a date, older than --days. The phrase set is the
   claim-sweep's (`as of`, `as at`, `remains`, `still`, `checked on`,
   `unchanged`); that prompt's three-way date classification (capture date,
   pinned-commit check date, current-state assertion) is editorial work this
   script deliberately does not do. Everything matching is routed.

4. Tracker states. The same signal site/tools/check-trackers.mjs checks:
   `data-tracker` / `data-tracker-state` attributes in the built funds page.
   Reading the build output rather than re-implementing lib/trackers.ts's
   readers in Python keeps one definition of "lagging" and "pinned". The
   build's own mtime is reported, because a stale dist is itself a finding.

Missing inputs degrade to empty sections with a note; the exit status stays
0. Only a usage error (argparse) exits non-zero.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REGISTRY_TABLES = ("source", "x_post", "nostr_post", "x_watch")

# The claim-sweep's current-state phrase set (scripts/claim-sweep-prompt.md).
PHRASE_RE = re.compile(
    r"\b(?:as of|as at|remains?|still|checked on|unchanged)\b", re.IGNORECASE
)
# Dates as the pages write them: "1 August 2026", "1 Aug 2026", "2026-08-01".
DATE_RE = re.compile(
    r"\b(\d{1,2})\s+([A-Z][a-z]{2,8})\s+(20\d\d)\b"
    r"|\b(20\d\d)-(\d{2})-(\d{2})\b"
)
MONTHS = {
    name: i + 1
    for i, name in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}

TRACKER_ATTR_RE = re.compile(
    r'data-tracker="([^"]+)"\s+data-tracker-state="([^"]+)"'
)


def parse_ts(ts: str) -> dt.datetime | None:
    """20260731T073023Z or any ISO prefix of it, as UTC."""
    try:
        return dt.datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        pass
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_line_date(line: str) -> dt.date | None:
    """First recognisable date on a line, in either house format."""
    m = DATE_RE.search(line)
    if not m:
        return None
    try:
        if m.group(4):  # ISO
            return dt.date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        month = MONTHS.get(m.group(2)[:3])
        if not month:
            return None
        return dt.date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def page_files(pages_root: Path) -> dict[str, str]:
    """Relative path -> text, for every readable file under the pages root."""
    out: dict[str, str] = {}
    if not pages_root.is_dir():
        return out
    for path in sorted(pages_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            out[str(path.relative_to(pages_root))] = path.read_text(
                encoding="utf-8"
            )
        except (UnicodeDecodeError, OSError):
            continue  # a binary or unreadable file cites nothing
    return out


def load_registry(root: Path) -> list[dict]:
    """Every registered item across the four tables, with its table name."""
    path = root / "sources.toml"
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    items = []
    for table in REGISTRY_TABLES:
        for entry in data.get(table, []):
            items.append({
                "table": table,
                # x_watch is keyed by handle; the other tables by id.
                "id": entry.get("id") or entry.get("handle") or "",
                "group": entry.get("kind") or entry.get("tag") or "untagged",
                "gone": bool(entry.get("gone")),
                "frozen": entry.get("watch") == "frozen",
                "withheld": bool(entry.get("withhold_text")),
            })
    return [i for i in items if i["id"]]


def reference_map(items: list[dict], pages: dict[str, str]) -> dict[str, dict]:
    """Per id: files linking its record page, and files merely naming it."""
    refs: dict[str, dict] = {}
    for item in items:
        sid = item["id"]
        strong, weak = [], []
        link = f"/record/sources/{sid}/"
        for rel, text in pages.items():
            if link in text:
                strong.append(rel)
            elif sid in text:
                weak.append(rel)
        refs[sid] = {"strong": strong, "weak": weak}
    return refs


def unreferenced(items: list[dict], refs: dict[str, dict]) -> dict:
    """Section 1: registered ids no page links, grouped by table/group."""
    excluded = {"gone": 0, "frozen": 0, "withheld": 0}
    groups: dict[str, list[dict]] = {}
    for item in items:
        if item["gone"]:
            excluded["gone"] += 1
            continue
        if item["frozen"]:
            excluded["frozen"] += 1
            continue
        if item["withheld"]:
            excluded["withheld"] += 1
            continue
        r = refs.get(item["id"], {"strong": [], "weak": []})
        if r["strong"]:
            continue
        key = f"{item['table']}/{item['group']}"
        groups.setdefault(key, []).append({
            "id": item["id"],
            "strength": "weak" if r["weak"] else "none",
            "files": r["weak"],
        })
    for entries in groups.values():
        entries.sort(key=lambda e: (e["strength"] != "none", e["id"]))
    return {"groups": dict(sorted(groups.items())), "excluded": excluded}


def revision_routing(
    root: Path, since: dt.datetime, refs: dict[str, dict]
) -> list[dict]:
    """Section 2: source-content revisions newer than `since`, with pages."""
    path = root / "revision-reviews.toml"
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = []
    for rev in data.get("revision", []):
        if rev.get("status") != "source-content":
            continue
        ts = parse_ts(str(rev.get("timestamp", "")))
        if ts is None or ts <= since:
            continue
        sid = str(rev.get("source", ""))
        r = refs.get(sid, {"strong": [], "weak": []})
        out.append({
            "source": sid,
            "timestamp": str(rev.get("timestamp", "")),
            "summary": " ".join(str(rev.get("summary", "")).split()),
            "pages": sorted(set(r["strong"]) | set(r["weak"])),
        })
    out.sort(key=lambda e: e["timestamp"], reverse=True)
    return out


def dated_assertions(
    pages: dict[str, str], days: int, today: dt.date
) -> list[dict]:
    """Section 3: phrase-plus-date lines whose date is older than `days`."""
    out = []
    for rel, text in pages.items():
        for lineno, line in enumerate(text.splitlines(), 1):
            if not PHRASE_RE.search(line):
                continue
            date = parse_line_date(line)
            if date is None:
                continue
            age = (today - date).days
            if age <= days:
                continue
            snippet = " ".join(line.split())
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            out.append({
                "file": rel,
                "line": lineno,
                "date": date.isoformat(),
                "age_days": age,
                "text": snippet,
            })
    out.sort(key=lambda e: (-e["age_days"], e["file"], e["line"]))
    return out


def tracker_states(root: Path) -> dict:
    """Section 4: data-tracker states from the built funds page.

    The build output is the same input check-trackers.mjs gates on, so this
    report and the publish gate can never disagree about which tracker is
    lagging or pinned. A missing or unbuilt page degrades to an empty list.
    """
    page = root / "site" / "dist" / "record" / "funds" / "index.html"
    if not page.is_file():
        return {"built": None, "readings": [], "note": "funds page not built"}
    html = page.read_text(encoding="utf-8", errors="replace")
    readings = [
        {"id": m.group(1), "state": m.group(2)}
        for m in TRACKER_ATTR_RE.finditer(html)
    ]
    built = dt.datetime.fromtimestamp(
        page.stat().st_mtime, tz=dt.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"built": built, "readings": readings, "note": ""}


def read_marker(path: Path, now: dt.datetime) -> dt.datetime:
    """Previous generated-at; seven days back when there is no marker."""
    fallback = now - dt.timedelta(days=7)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = parse_ts(str(data.get("generated_at", "")))
        if ts is not None:
            return ts
    except (OSError, json.JSONDecodeError):
        pass
    return fallback


def write_marker(path: Path, now: dt.datetime) -> None:
    """Atomic: a torn write must never look like a newer packet ran."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}, indent=2
    ) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def iso(now: dt.datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_packet(
    root: Path,
    pages_root: Path,
    days: int,
    now: dt.datetime,
    marker_since: dt.datetime,
) -> dict:
    pages = page_files(pages_root)
    items = load_registry(root)
    refs = reference_map(items, pages)
    unref = unreferenced(items, refs)
    revisions = revision_routing(root, marker_since, refs)
    assertions = dated_assertions(pages, days, now.date())
    trackers = tracker_states(root)
    degraded = [t for t in trackers["readings"] if t["state"] != "current"]
    return {
        "generated_at": iso(now),
        "inputs": {
            "registry": str(root / "sources.toml"),
            "revisions": str(root / "revision-reviews.toml"),
            "pages": str(pages_root),
            "funds_build": str(
                root / "site" / "dist" / "record" / "funds" / "index.html"
            ),
        },
        "days": days,
        "marker_since": iso(marker_since),
        "unreferenced": unref,
        "revision_routing": revisions,
        "dated_assertions": assertions,
        "trackers": {**trackers, "degraded": degraded},
    }


def render_markdown(packet: dict) -> str:
    lines = []
    add = lines.append
    add("# Site staleness packet")
    add("")
    add(f"- generated-at: {packet['generated_at']}")
    add(f"- registry: {packet['inputs']['registry']}")
    add(f"- revisions: {packet['inputs']['revisions']}")
    add(f"- pages: {packet['inputs']['pages']}")
    add(f"- funds-build: {packet['inputs']['funds_build']}")
    add(f"- dated-assertion threshold: {packet['days']} days")
    add(f"- revision marker: newer than {packet['marker_since']}")
    add("")

    add("## 1. Unreferenced registered sources")
    unref = packet["unreferenced"]
    ex = unref["excluded"]
    add(f"<!-- excluded: gone={ex['gone']} frozen={ex['frozen']} "
        f"withheld={ex['withheld']} (see script docstring) -->")
    if not unref["groups"]:
        add("(none)")
    for group, entries in unref["groups"].items():
        add(f"### {group} ({len(entries)})")
        for e in entries:
            if e["strength"] == "weak":
                add(f"- {e['id']}: weak mention only, no record link: "
                    f"{', '.join(e['files'])}")
            else:
                add(f"- {e['id']}: no reference under pages root")
    add("")

    add("## 2. Editorial-attention routing (source-content revisions "
        f"newer than {packet['marker_since']})")
    if not packet["revision_routing"]:
        add("(none)")
    for r in packet["revision_routing"]:
        pages = ", ".join(r["pages"]) if r["pages"] else "no page references"
        add(f"- {r['timestamp']} {r['source']}: pages: {pages} | {r['summary']}")
    add("")

    add(f"## 3. Dated assertions older than {packet['days']} days")
    if not packet["dated_assertions"]:
        add("(none)")
    for a in packet["dated_assertions"]:
        add(f"- {a['file']}:{a['line']} ({a['date']}, {a['age_days']}d): "
            f"{a['text']}")
    add("")

    add("## 4. Tracker states")
    tr = packet["trackers"]
    if tr["note"]:
        add(f"({tr['note']}; section empty)")
    else:
        add(f"<!-- funds build: {tr['built']} -->")
        if not tr["degraded"]:
            add(f"(none degraded; {len(tr['readings'])} readings current)")
        for t in tr["degraded"]:
            add(f"- {t['id']}: {t['state']}")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="repository root (default: script's parent)")
    parser.add_argument("--pages", type=Path, default=None,
                        help="pages root (default: <root>/site/src/pages)")
    parser.add_argument("--days", type=int, default=21,
                        help="dated-assertion age threshold (default: 21)")
    parser.add_argument("--json", action="store_true",
                        help="also write .work/site-staleness.json")
    parser.add_argument("--out", type=Path, default=None,
                        help="packet path (default: <root>/.work/site-staleness.md)")
    parser.add_argument("--marker", type=Path, default=None,
                        help="marker path (default: <root>/.work/site-staleness-marker.json)")
    args = parser.parse_args()

    root = args.root.resolve()
    pages_root = (args.pages or root / "site" / "src" / "pages").resolve()
    out_path = args.out or root / ".work" / "site-staleness.md"
    marker_path = args.marker or root / ".work" / "site-staleness-marker.json"

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    since = read_marker(marker_path, now)

    packet = build_packet(root, pages_root, args.days, now, since)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(packet), encoding="utf-8")
    if args.json:
        json_path = out_path.with_suffix(".json")
        json_path.write_text(
            json.dumps(packet, indent=2) + "\n", encoding="utf-8"
        )
    write_marker(marker_path, now)

    counts = (
        sum(len(v) for v in packet["unreferenced"]["groups"].values()),
        len(packet["revision_routing"]),
        len(packet["dated_assertions"]),
        len(packet["trackers"]["degraded"]),
    )
    print(f"wrote {out_path}")
    print(f"unreferenced={counts[0]} revision-routing={counts[1]} "
          f"dated-assertions={counts[2]} degraded-trackers={counts[3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
