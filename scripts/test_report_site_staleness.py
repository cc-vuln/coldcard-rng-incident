#!/usr/bin/env python3
"""Fixtures for scripts/report_site_staleness.py.

Everything runs against temp directories: no live sources.toml, no live
site/src/pages, no live dist. The packet's job is deterministic routing, so
the tests pin the scans, the exclusions, the marker arithmetic and the
degradation rules.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import report_site_staleness as rss

NOW = dt.datetime(2026, 8, 8, 10, 0, 0, tzinfo=dt.timezone.utc)

SOURCES_TOML = """\
[[source]]
id = "linked-source"
kind = "vendor-advisory"

[[source]]
id = "weak-source"
kind = "vendor-advisory"

[[source]]
id = "lost-source"
kind = "reporting"

[[source]]
id = "gone-source"
kind = "reporting"
gone = true
gone_since = "20260804T011339Z"

[[source]]
id = "frozen-source"
kind = "community-discussion"
watch = "frozen"

[[source]]
id = "withheld-source"
kind = "victim-account"
withhold_text = true

[[x_post]]
id = "linked-post"
tag = "community"

[[nostr_post]]
id = "lost-note"
tag = "community"

[[x_watch]]
handle = "watchedhandle"
"""

PAGES = {
    "record/funds.astro": '<a href="/record/sources/linked-source/">x</a>\n'
                          '<a href="/record/sources/linked-post/">y</a>\n',
    "about.astro": "The weak-source total is discussed here.\n"
                   "No first-hand account is captured as of 1 July 2026.\n"
                   "Checked on 2026-07-10 the PR remains unmerged.\n",
    "index.astro": "Recent material remains fresh as of 7 August 2026.\n"
                   "No date on this line but remains a phrase.\n"
                   "A bare date 2026-01-01 with no phrase.\n",
}

REVISIONS_TOML = """\
[[revision]]
source = "linked-source"
timestamp = "20260807T033234Z"
status = "source-content"
summary = "The advisory   rewrote its migration section."

[[revision]]
source = "lost-source"
timestamp = "20260806T000000Z"
status = "capture-noise"
summary = "Fiat conversions only."

[[revision]]
source = "gone-source"
timestamp = "20260701T000000Z"
status = "source-content"
summary = "Older than the marker."

[[revision]]
source = "never-cited"
timestamp = "20260808T000000Z"
status = "source-content"
summary = "No page cites this one."
"""


def make_tree(base: Path) -> tuple[Path, Path]:
    """A minimal repo: registry, revisions, pages, no dist build."""
    (base / "sources.toml").write_text(SOURCES_TOML)
    (base / "revision-reviews.toml").write_text(REVISIONS_TOML)
    pages = base / "site" / "src" / "pages"
    for rel, text in PAGES.items():
        path = pages / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return base, pages


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root, self.pages = make_tree(Path(self._tmp.name))

    def packet(self, days=21, since=None):
        since = since or (NOW - dt.timedelta(days=7))
        return rss.build_packet(self.root, self.pages, days, NOW, since)


class TestReferences(Fixture):
    def test_strong_and_weak(self):
        items = rss.load_registry(self.root)
        refs = rss.reference_map(items, rss.page_files(self.pages))
        self.assertEqual(refs["linked-source"]["strong"], ["record/funds.astro"])
        self.assertEqual(refs["weak-source"]["weak"], ["about.astro"])
        self.assertEqual(refs["lost-source"], {"strong": [], "weak": []})

    def test_x_watch_handle_is_the_match_string(self):
        items = rss.load_registry(self.root)
        handles = [i["id"] for i in items if i["table"] == "x_watch"]
        self.assertEqual(handles, ["watchedhandle"])

    def test_unreferenced_groups_and_exclusions(self):
        unref = self.packet()["unreferenced"]
        # gone, frozen and withheld ids are counted, never listed.
        self.assertEqual(unref["excluded"],
                         {"gone": 1, "frozen": 1, "withheld": 1})
        flat = {e["id"] for entries in unref["groups"].values() for e in entries}
        self.assertEqual(flat, {"weak-source", "lost-source",
                                "lost-note", "watchedhandle"})
        self.assertNotIn("gone-source", flat)
        self.assertNotIn("frozen-source", flat)
        self.assertNotIn("withheld-source", flat)
        by_group = {g: [e["id"] for e in entries]
                    for g, entries in unref["groups"].items()}
        self.assertEqual(by_group["source/vendor-advisory"], ["weak-source"])
        self.assertEqual(by_group["source/reporting"], ["lost-source"])
        weak = unref["groups"]["source/vendor-advisory"][0]
        self.assertEqual(weak["strength"], "weak")
        self.assertEqual(weak["files"], ["about.astro"])


class TestRevisionRouting(Fixture):
    def test_only_source_content_newer_than_marker(self):
        routed = self.packet()["revision_routing"]
        got = {r["source"] for r in routed}
        # capture-noise is not routed; the pre-marker revision is not routed.
        self.assertEqual(got, {"linked-source", "never-cited"})

    def test_pages_and_summary(self):
        routed = {r["source"]: r for r in self.packet()["revision_routing"]}
        self.assertEqual(routed["linked-source"]["pages"],
                         ["record/funds.astro"])
        self.assertEqual(routed["never-cited"]["pages"], [])
        # Multi-line TOML summaries flatten to one line.
        self.assertEqual(
            routed["linked-source"]["summary"],
            "The advisory rewrote its migration section.",
        )

    def test_marker_boundary(self):
        since = rss.parse_ts("20260807T033234Z")
        routed = self.packet(since=since)["revision_routing"]
        # Strictly newer: the boundary timestamp itself drops out.
        self.assertEqual([r["source"] for r in routed], ["never-cited"])


class TestDatedAssertions(Fixture):
    def test_aged_matches_only(self):
        rows = self.packet()["dated_assertions"]
        got = {(r["file"], r["line"]) for r in rows}
        self.assertIn(("about.astro", 2), got)   # 1 July 2026, long month
        self.assertIn(("about.astro", 3), got)   # 2026-07-10, ISO
        # 7 August 2026 is fresh; phrase-only and date-only lines never match.
        self.assertNotIn(("index.astro", 1), got)
        self.assertEqual(len(rows), 2)

    def test_age_and_date_fields(self):
        rows = {r["line"]: r for r in self.packet()["dated_assertions"]}
        self.assertEqual(rows[2]["date"], "2026-07-01")
        self.assertEqual(rows[2]["age_days"], 38)
        self.assertEqual(rows[3]["date"], "2026-07-10")

    def test_threshold_moves(self):
        rows = self.packet(days=60)["dated_assertions"]
        self.assertEqual(rows, [])


class TestTrackers(Fixture):
    def test_missing_build_degrades(self):
        trackers = self.packet()["trackers"]
        self.assertEqual(trackers["readings"], [])
        self.assertEqual(trackers["degraded"], [])
        self.assertTrue(trackers["note"])

    def test_reads_states_from_build(self):
        funds = self.root / "site" / "dist" / "record" / "funds"
        funds.mkdir(parents=True)
        (funds / "index.html").write_text(
            '<div data-tracker="alpha" data-tracker-state="current"></div>'
            '<div data-tracker="beta" data-tracker-state="lagging"></div>'
            '<div data-tracker="gamma" data-tracker-state="pinned"></div>'
        )
        trackers = self.packet()["trackers"]
        self.assertEqual(trackers["degraded"],
                         [{"id": "beta", "state": "lagging"},
                          {"id": "gamma", "state": "pinned"}])
        self.assertIsNotNone(trackers["built"])


class TestMarker(Fixture):
    def test_first_run_falls_back_seven_days(self):
        since = rss.read_marker(self.root / "nope.json", NOW)
        self.assertEqual(since, NOW - dt.timedelta(days=7))

    def test_roundtrip(self):
        path = self.root / ".work" / "marker.json"
        rss.write_marker(path, NOW)
        self.assertEqual(rss.read_marker(path, NOW), NOW)

    def test_corrupt_marker_falls_back(self):
        path = self.root / "marker.json"
        path.write_text("{ not json")
        self.assertEqual(rss.read_marker(path, NOW),
                         NOW - dt.timedelta(days=7))


class TestPacketShape(Fixture):
    def test_markdown_sections(self):
        md = rss.render_markdown(self.packet())
        for heading in ("## 1. Unreferenced registered sources",
                        "## 2. Editorial-attention routing",
                        "## 3. Dated assertions",
                        "## 4. Tracker states"):
            self.assertIn(heading, md)
        self.assertIn("generated-at: 2026-08-08T10:00:00Z", md)
        # One line per item, id first after the bullet.
        self.assertIn("- weak-source: weak mention only", md)
        self.assertIn("- 20260807T033234Z linked-source:", md)
        self.assertIn("- about.astro:2 (2026-07-01, 38d):", md)

    def test_empty_sections_render_none(self):
        empty = Path(self._tmp.name) / "empty"
        (empty / "site" / "src" / "pages").mkdir(parents=True)
        packet = rss.build_packet(
            empty, empty / "site" / "src" / "pages", 21, NOW,
            NOW - dt.timedelta(days=7),
        )
        self.assertEqual(packet["unreferenced"]["groups"], {})
        self.assertEqual(packet["revision_routing"], [])
        self.assertEqual(packet["dated_assertions"], [])
        md = rss.render_markdown(packet)
        self.assertEqual(md.count("(none)"), 3)


if __name__ == "__main__":
    unittest.main()
