#!/usr/bin/env python3
"""Focused regression tests for capture scheduling and normalisation."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

import urllib.error
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from response_headers import (
    GEO_REDACTED,
    disallowed,
    geo_in_body,
    safe_headers,
    scrub_geo,
)

import capture  # noqa: E402


class RegistryTests(unittest.TestCase):
    def source(self, **overrides) -> dict:
        source = {
            "id": "a",
            "url": "https://example.test",
            "kind": "vendor-docs",
            "tier": 1,
        }
        source.update(overrides)
        return source

    def test_duplicate_ids_across_sections_are_rejected(self) -> None:
        cfg = {
            "source": [self.source(id="same")],
            "x_post": [{"id": "same", "url": "https://x.com/a/status/1"}],
        }
        with self.assertRaisesRegex(capture.SourceConfigError, "duplicate source id"):
            capture.validate_sources(cfg)

    def post(self, **overrides) -> dict:
        post = {
            "id": "p",
            "url": "https://x.com/someone/status/2083247006139503065",
            "author": "someone",
        }
        post.update(overrides)
        return post

    def test_thread_post_needs_a_tier_so_its_cadence_is_stated(self) -> None:
        cfg = {"source": [], "x_post": [self.post(thread=True)]}
        with self.assertRaisesRegex(capture.SourceConfigError, "requires a tier"):
            capture.validate_sources(cfg)

    def test_tier_without_thread_is_rejected(self) -> None:
        # A registered post that is not polled has no cadence to state, so a
        # tier on it means someone expected polling that will not happen.
        cfg = {"source": [], "x_post": [self.post(tier=3)]}
        with self.assertRaisesRegex(capture.SourceConfigError, "only to a polled"):
            capture.validate_sources(cfg)

    def test_thread_must_be_a_boolean(self) -> None:
        cfg = {"source": [], "x_post": [self.post(thread="yes", tier=3)]}
        with self.assertRaisesRegex(capture.SourceConfigError, "true or false"):
            capture.validate_sources(cfg)

    def test_thread_capture_needs_an_x_status_url(self) -> None:
        cfg = {"source": [],
               "x_post": [self.post(thread=True, tier=3,
                                    url="https://example.test/a")]}
        with self.assertRaisesRegex(capture.SourceConfigError, "X status"):
            capture.validate_sources(cfg)

    def test_thread_capture_needs_an_author(self) -> None:
        # The author is what separates the self-thread from the replies.
        post = self.post(thread=True, tier=3)
        del post["author"]
        with self.assertRaisesRegex(capture.SourceConfigError, "needs author"):
            capture.validate_sources({"source": [], "x_post": [post]})

    def test_a_plain_registered_post_still_validates(self) -> None:
        capture.validate_sources({"source": [], "x_post": [self.post()]})

    def test_pollable_sources_includes_thread_posts_only(self) -> None:
        cfg = {
            "source": [self.source(id="web")],
            "x_post": [self.post(id="plain"),
                       self.post(id="convo", thread=True, tier=3,
                                 url="https://x.com/n/status/9", author="n")],
        }
        got = {s["id"]: s for s in capture.pollable_sources(cfg)}
        self.assertEqual(sorted(got), ["convo", "web"])
        self.assertEqual(got["convo"]["capture"], "x-thread")
        self.assertEqual(got["convo"]["kind"], "social-thread")
        self.assertEqual(got["convo"]["tier"], 3)
        self.assertEqual(got["convo"]["x_author"], "n")

    def test_pollable_sources_carries_the_withhold_flag(self) -> None:
        # A withheld conversation must stay withheld once it becomes a source.
        cfg = {"source": [], "x_post": [
            self.post(id="c", thread=True, tier=3, withhold_text=True)]}
        self.assertIs(capture.pollable_sources(cfg)[0]["withhold_text"], True)

    def test_gone_requires_a_timestamp_and_an_observation(self) -> None:
        """Retiring a source is a claim about the world, so it must be checkable."""
        with self.assertRaisesRegex(capture.SourceConfigError, "gone_since"):
            capture.validate_sources({"source": [self.source(gone=True)]})
        with self.assertRaisesRegex(capture.SourceConfigError, "gone_note"):
            capture.validate_sources({"source": [
                self.source(gone=True, gone_since="20260803T021200Z")
            ]})

    def test_gone_since_must_look_like_a_capture_timestamp(self) -> None:
        with self.assertRaisesRegex(capture.SourceConfigError, "gone_since"):
            capture.validate_sources({"source": [
                self.source(gone=True, gone_since="3 Aug 2026", gone_note="404")
            ]})

    def test_gone_fields_without_the_flag_are_rejected(self) -> None:
        """Otherwise a typo in the flag silently leaves the source being polled."""
        with self.assertRaisesRegex(capture.SourceConfigError, "require gone = true"):
            capture.validate_sources({"source": [
                self.source(gone_since="20260803T021200Z", gone_note="404")
            ]})

    def test_a_well_formed_gone_source_validates(self) -> None:
        capture.validate_sources({"source": [self.source(
            gone=True,
            gone_since="20260803T021200Z",
            gone_status="404",
            gone_note="origin returned 404 to three user agents",
        )]})

    def test_unknown_normalizer_is_rejected(self) -> None:
        cfg = {
            "source": [self.source(normalizers=["not-a-rule"])]
        }
        with self.assertRaisesRegex(capture.SourceConfigError, "invalid normalizers"):
            capture.validate_sources(cfg)

    def test_tier_is_required_and_bounded(self) -> None:
        for tier in (None, True, 0, 4, "1"):
            source = self.source(tier=tier)
            with self.subTest(tier=tier), self.assertRaisesRegex(
                capture.SourceConfigError, "tier must be an integer from 1 to 3"
            ):
                capture.validate_sources({"source": [source]})

    def test_watch_state_is_bounded(self) -> None:
        capture.validate_sources({"source": [self.source(watch="frozen")]})
        for watch in ("cooling", "weekly", "", True):
            with self.subTest(watch=watch), self.assertRaisesRegex(
                capture.SourceConfigError, "watch must be 'active' or 'frozen'"
            ):
                capture.validate_sources({"source": [self.source(watch=watch)]})

    def test_watch_until_is_bounded_and_exclusive_with_frozen(self) -> None:
        capture.validate_sources({"source": [self.source(
            watch_until="20260812T000000Z"
        )]})
        with self.assertRaisesRegex(capture.SourceConfigError, "watch_until"):
            capture.validate_sources({"source": [self.source(
                watch_until="12 August"
            )]})
        with self.assertRaisesRegex(capture.SourceConfigError, "choose watch"):
            capture.validate_sources({"source": [self.source(
                watch="frozen", watch_until="20260812T000000Z"
            )]})

    def test_community_default_window_depends_on_tier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(id="thread", kind="community-discussion")
            snapshots = root / "thread"
            snapshots.mkdir()
            (snapshots / "20260801T120000Z.txt").write_text("first")
            self.assertFalse(capture.watch_window_elapsed(
                source, "20260808T115959Z", root
            ))
            self.assertTrue(capture.watch_window_elapsed(
                source, "20260808T120000Z", root
            ))
            self.assertFalse(capture.watch_window_elapsed(
                {**source, "watch": "active"}, "20260820T120000Z", root
            ))
            tier3 = {**source, "tier": 3}
            self.assertFalse(capture.watch_window_elapsed(
                tier3, "20260804T115959Z", root
            ))
            self.assertTrue(capture.watch_window_elapsed(
                tier3, "20260804T120000Z", root
            ))

    def test_kind_is_required_and_non_empty(self) -> None:
        for kind in (None, "", "   ", 1):
            source = self.source(kind=kind)
            with self.subTest(kind=kind), self.assertRaisesRegex(
                capture.SourceConfigError, "kind must be a non-empty string"
            ):
                capture.validate_sources({"source": [source]})

    def test_required_text_must_be_a_list_of_non_empty_strings(self) -> None:
        for required_text in ("marker", [""], [1], None):
            source = self.source(required_text=required_text)
            with self.subTest(required_text=required_text), self.assertRaisesRegex(
                capture.SourceConfigError, "required_text must contain"
            ):
                capture.validate_sources({"source": [source]})

    def test_fetch_url_and_json_extractors_are_validated(self) -> None:
        invalid = (
            self.source(fetch_url=""),
            self.source(capture="browser", fetch_url="https://api.example.test"),
            self.source(fetch_post=""),
            self.source(fetch_post=42),
            self.source(capture="browser", fetch_post='{"query": "{ x }"}'),
            self.source(json_html_field=""),
            self.source(json_pretty="yes"),
            self.source(json_html_field="content", json_pretty=True),
            self.source(json_text_fields=["title"]),
            self.source(json_html_field="content", json_text_fields=[""]),
            self.source(capture="browser", json_html_field="content"),
            self.source(capture="browser", json_pretty=True),
        )
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(
                capture.SourceConfigError
            ):
                capture.validate_sources({"source": [source]})

    def test_a_well_formed_post_source_validates(self) -> None:
        capture.validate_sources({"source": [self.source(
            fetch_url="https://api.example.test/graphql",
            fetch_post='{"query": "{ item(id: 1) { title } }"}',
            json_pretty=True,
        )]})

    def test_json_html_field_extracts_readable_source_text(self) -> None:
        body = json.dumps({
            "title": "Consumer Act",
            "comments": {"note": "<p>Not yet in force</p>"},
            "content": "<article><h1>Act</h1><p>Section 7</p></article>",
        }).encode()
        text = capture.extract_source_text(
            body,
            "https://api.example.test",
            self.source(
                json_html_field="content",
                json_text_fields=["title", "comments.note"],
            ),
        )
        self.assertEqual(text, "Consumer Act\nNot yet in force\nAct\nSection 7")

    def test_json_pretty_produces_stable_key_order(self) -> None:
        before = b'{"z": 1, "a": {"b": 2}}'
        after = b'{"a": {"b": 2}, "z": 1}'
        source = self.source(json_pretty=True)
        self.assertEqual(
            capture.extract_source_text(before, source["url"], source),
            capture.extract_source_text(after, source["url"], source),
        )


class NormalizerTests(unittest.TestCase):
    def canonical(self, text: str, *rules: str) -> str:
        return capture.canonical_text(
            text, {"id": "test", "normalizers": list(rules)}
        )

    def test_relative_time_variants(self) -> None:
        before = "Updated just now\n15 hours ago4 minutes read"
        after = "Updated 1 minute ago\n16 hours ago4 minutes read"
        self.assertEqual(
            self.canonical(before, "relative-time"),
            self.canonical(after, "relative-time"),
        )

    def test_fiat_changes_do_not_hide_btc_changes(self) -> None:
        a = "1,082.57 BTC\n$68,205,137\n$63,003/BTC"
        b = "1,082.57 BTC\n$71,027,168\n$62,941/BTC"
        c = "1,081.57 BTC\n$71,027,168\n$62,941/BTC"
        self.assertEqual(self.canonical(a, "fiat-values"),
                         self.canonical(b, "fiat-values"))
        self.assertNotEqual(self.canonical(a, "fiat-values"),
                            self.canonical(c, "fiat-values"))

    def test_github_repository_counters(self) -> None:
        a = "Fork\n10\nStar\n6\nPull requests\n5\ncomment text"
        b = "Fork\n11\nStar\n7\nPull requests\n6\ncomment text"
        self.assertEqual(self.canonical(a, "github-repo-counters"),
                         self.canonical(b, "github-repo-counters"))

    def test_github_reactions_do_not_hide_comment_edits(self) -> None:
        a = "\n".join([
            "comment text",
            "👍",
            "1",
            "alice reacted with thumbs up emoji",
            "All reactions",
            "👍",
            "1 reaction",
            "next event",
        ])
        b = a.replace("\n1\nalice reacted", "\n2\nalice and bob reacted").replace(
            "\n1 reaction\n", "\n2 reactions\n"
        )
        edited = b.replace("comment text", "edited comment text")
        self.assertEqual(
            self.canonical(a, "github-reactions"),
            self.canonical(b, "github-reactions"),
        )
        self.assertNotEqual(
            self.canonical(a, "github-reactions"),
            self.canonical(edited, "github-reactions"),
        )

    def test_tftc_related_cards_are_outside_comparison(self) -> None:
        a = "article\nKeep reading\nold recommendation"
        b = "article\nKeep reading\nnew recommendation"
        self.assertEqual(self.canonical(a, "tftc-related"),
                         self.canonical(b, "tftc-related"))

    def test_theblock_tickers(self) -> None:
        a = "Live\nBTCUSD$62,930.000.06%\narticle"
        b = "Live\nBTCUSD$62,999.750.17%\narticle"
        self.assertEqual(self.canonical(a, "theblock-tickers"),
                         self.canonical(b, "theblock-tickers"))

    def test_theblock_ticker_shell_normalises_unavailable_state(self) -> None:
        live = "\n".join([
            "NEW",
            "Live",
            "BTCUSD$62,930.000.06%",
            "ETHUSD$1,864.840.21%",
            "Latest Crypto News",
            "article text",
        ])
        unavailable = "\n".join([
            "NEW",
            "No ticker data available",
            "Latest Crypto News",
            "article text",
        ])
        edited = unavailable.replace("article text", "edited article text")
        rules = ("theblock-tickers", "theblock-ticker-shell")
        self.assertEqual(self.canonical(live, *rules),
                         self.canonical(unavailable, *rules))
        self.assertNotEqual(self.canonical(live, *rules),
                            self.canonical(edited, *rules))

    def test_rolling_last_update_does_not_hide_page_edits(self) -> None:
        before = "content\nLast update:\nJuly 31, 2026\nBack to top"
        after = "content\nLast update:\nAugust 1, 2026\nBack to top"
        edited = after.replace("content", "edited content")
        self.assertEqual(
            self.canonical(before, "rolling-last-update"),
            self.canonical(after, "rolling-last-update"),
        )
        self.assertNotEqual(
            self.canonical(before, "rolling-last-update"),
            self.canonical(edited, "rolling-last-update"),
        )

    def test_cktripwire_live_age_does_not_hide_sweep_events(self) -> None:
        before = "HP-7B35\nLIVE\nlive 1h14m\nHP-3146\nSWEPT ->\nswept in 1h18m"
        after = before.replace("live 1h14m", "live 1d2h30m")
        edited = after.replace("swept in 1h18m", "swept in 1h19m")
        self.assertEqual(
            self.canonical(before, "cktripwire-live-state"),
            self.canonical(after, "cktripwire-live-state"),
        )
        self.assertNotEqual(
            self.canonical(before, "cktripwire-live-state"),
            self.canonical(edited, "cktripwire-live-state"),
        )

    def test_reddit_more_stub_counts_are_live_engagement(self) -> None:
        before = "more-stub: parent t1_abc count 19\ncomment body"
        after = "more-stub: parent t1_abc count 20\ncomment body"
        edited = after.replace("comment body", "edited comment body")
        self.assertEqual(
            self.canonical(before, "reddit-more-stub-counts"),
            self.canonical(after, "reddit-more-stub-counts"),
        )
        self.assertNotEqual(
            self.canonical(before, "reddit-more-stub-counts"),
            self.canonical(edited, "reddit-more-stub-counts"),
        )

    def test_slipstream_live_values_do_not_hide_wording_changes(self) -> None:
        before = (
            "Minimum submission rate\n2 sats/vByte\n"
            "Current mineable rate\n3 sats/vByte\nCurrent Block Height: 960541\n"
            "Client codes are currently required"
        )
        after = before.replace("2 sats/vByte", "4 sats/vByte").replace(
            "3 sats/vByte", "5 sats/vByte"
        ).replace("960541", "960600")
        edited = after.replace("currently required", "optional")
        self.assertEqual(
            self.canonical(before, "slipstream-live-state"),
            self.canonical(after, "slipstream-live-state"),
        )
        self.assertNotEqual(
            self.canonical(before, "slipstream-live-state"),
            self.canonical(edited, "slipstream-live-state"),
        )

    def test_coindesk_article_ignores_localised_chrome_and_news_rail(self) -> None:
        before = (
            "Search\nTech\nHeadline\nStandfirst\nBy Author\nShare\n"
            "Summary\nShow\nSummary text\nArticle text\nLatest Crypto News\n"
            "Old card\nCryptoCD20$1,700"
        )
        after = before.replace("By Author\nShare", "Par Auteur\nPartager").replace(
            "Old card\nCryptoCD20$1,700", "New card\nCryptoCD20$1,800"
        )
        edited = after.replace("Article text", "Revised article text")
        self.assertEqual(self.canonical(before, "coindesk-article"),
                         self.canonical(after, "coindesk-article"))
        self.assertNotEqual(self.canonical(before, "coindesk-article"),
                            self.canonical(edited, "coindesk-article"))

    def test_chaincatcher_article_ignores_tickers_and_related_reading(self) -> None:
        title = "Nunchuk responds to Coldcard vulnerability: platform keys will not be used directly"
        before = (
            "Home\nBTC $63,000 +1.00%\n" + title + "\n2026-08-01\n"
            "Article text\nRisk warning\nRelated tags\nNunchuk\nRelated reading\nOld"
        )
        after = before.replace("BTC $63,000 +1.00%", "BTC $64,000 +2.00%").replace(
            "Related reading\nOld", "Related reading\nNew"
        )
        edited = after.replace("Article text", "Revised article text")
        self.assertEqual(self.canonical(before, "chaincatcher-article"),
                         self.canonical(after, "chaincatcher-article"))
        self.assertNotEqual(self.canonical(before, "chaincatcher-article"),
                            self.canonical(edited, "chaincatcher-article"))

    def test_newsbtc_article_ignores_related_news_chrome(self) -> None:
        before = "Headline\nArticle body\nDisclaimer\nRelated News\nOld card\n2 hours ago"
        after = before.replace("Old card\n2 hours ago", "New card\n3 hours ago")
        edited = after.replace("Article body", "Revised article body")
        self.assertEqual(self.canonical(before, "newsbtc-article"),
                         self.canonical(after, "newsbtc-article"))
        self.assertNotEqual(self.canonical(before, "newsbtc-article"),
                            self.canonical(edited, "newsbtc-article"))


class ExitCodeTests(unittest.TestCase):
    def test_staleness_takes_precedence_over_changed(self) -> None:
        events = [{"event": "changed", "id": "a"}, {"event": "blocked", "id": "b"}]
        self.assertEqual(
            capture._capture_exit(events, {"b"}), capture.INCOMPLETE_EXIT
        )

    def test_a_failure_that_is_still_within_budget_does_not_block(self) -> None:
        """One refused request is weather. This is the whole point of the change."""
        events = [{"event": "changed", "id": "a"}, {"event": "error", "id": "b"}]
        self.assertEqual(capture._capture_exit(events, set()), 10)

    def test_changed_retains_exit_ten(self) -> None:
        self.assertEqual(capture._capture_exit([{"event": "changed"}], set()), 10)

    def test_blocked_source_makes_run_incomplete(self) -> None:
        source = {
            "id": "blocked",
            "url": "https://example.test",
            "min_chars": 100,
        }
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
            capture, "fetch", return_value=(b"short", {})
        ):
            result = capture.capture_one(source, dry=True)
        self.assertEqual(result["event"], "blocked")
        self.assertEqual(result["failure"], "challenged")
        self.assertEqual(capture._capture_exit([result], {"blocked"}),
                         capture.INCOMPLETE_EXIT)


class FreshnessTests(unittest.TestCase):
    """The gate asks whether the record is behind, not whether a request failed."""

    SOURCE = {"id": "flaky", "url": "https://example.test", "tier": 1}

    def freshness(self, history, now, result):
        with mock.patch.object(capture, "events_by_source", return_value=history):
            return capture.freshness([self.SOURCE], [result], now)

    def test_one_failure_soon_after_a_success_is_within_budget(self) -> None:
        history = {"flaky": [{"event": "unchanged", "ts": "20260803T020000Z"}]}
        report = self.freshness(
            history, "20260803T021500Z", {"id": "flaky", "event": "error",
                                          "failure": "transient"},
        )
        self.assertEqual(len(report), 1)
        self.assertFalse(report[0]["stale"])
        self.assertEqual(report[0]["age_seconds"], 15 * 60)

    def test_sustained_failure_past_three_cycles_is_stale(self) -> None:
        # tier 1 polls every 30 minutes, so the budget is 90.
        history = {"flaky": [{"event": "unchanged", "ts": "20260803T000000Z"}]}
        report = self.freshness(
            history, "20260803T014500Z", {"id": "flaky", "event": "error",
                                          "failure": "refused"},
        )
        self.assertTrue(report[0]["stale"])

    def test_a_source_never_captured_blocks_when_it_fails(self) -> None:
        report = self.freshness(
            {}, "20260803T014500Z", {"id": "flaky", "event": "error",
                                     "failure": "transient"},
        )
        self.assertTrue(report[0]["stale"])
        self.assertIsNone(report[0]["age_seconds"])

    def test_a_source_that_captured_this_run_is_not_reported(self) -> None:
        report = self.freshness(
            {}, "20260803T014500Z", {"id": "flaky", "event": "changed"},
        )
        self.assertEqual(report, [])


class WaybackFallbackTests(unittest.TestCase):
    SOURCE = {"id": "refused", "url": "https://example.test", "tier": 1}

    def test_refusals_are_counted_only_back_to_the_last_capture(self) -> None:
        history = {"refused": [
            {"event": "error", "failure": "refused"},
            {"event": "unchanged"},
            {"event": "error", "failure": "refused"},
            {"event": "error", "failure": "refused"},
        ]}
        with mock.patch.object(capture, "events_by_source", return_value=history):
            self.assertEqual(capture.consecutive_refusals("refused"), 2)

    def test_a_single_refusal_does_not_reach_for_wayback(self) -> None:
        with mock.patch.object(capture, "consecutive_refusals", return_value=0):
            self.assertIsNone(
                capture._try_wayback(self.SOURCE, {}, "refused", True)
            )

    def test_a_transient_failure_never_reaches_for_wayback(self) -> None:
        with mock.patch.object(capture, "consecutive_refusals", return_value=99):
            self.assertIsNone(
                capture._try_wayback(self.SOURCE, {}, "transient", True)
            )

    def test_sustained_refusal_replays_the_newest_wayback_capture(self) -> None:
        fake = SimpleNamespace(
            newest_snapshot=lambda url: ("20260802235959", b"<p>replayed</p>"),
            wb_ts_to_ours=lambda ts: "20260802T235959Z",
        )
        result = {}
        with mock.patch.object(capture, "consecutive_refusals", return_value=5), \
                mock.patch.dict(sys.modules, {"wayback": fake}), \
                contextlib.redirect_stdout(io.StringIO()):
            recovered = capture._try_wayback(self.SOURCE, result, "refused", True)
        self.assertIsNotNone(recovered)
        self.assertIn("replayed", recovered[2])
        self.assertEqual(result["provenance"], "wayback")
        self.assertEqual(result["wayback_timestamp"], "20260802T235959Z")
        self.assertTrue(result["origin_refused"])


class CaptureSelectionTests(unittest.TestCase):
    SOURCES = [
        {"id": "urgent", "url": "https://example.test/1", "kind": "vendor-advisory", "tier": 1},
        {"id": "chain", "url": "https://example.test/2", "kind": "chain-monitor", "tier": 1},
        {"id": "repo", "url": "https://example.test/3", "kind": "repo-pr", "tier": 2},
        {"id": "report", "url": "https://example.test/4", "kind": "reporting", "tier": 3},
        {"id": "frozen", "url": "https://example.test/5", "kind": "reporting", "tier": 3,
         "watch": "frozen"},
        {"id": "elapsed", "url": "https://example.test/6", "kind": "community-discussion",
         "tier": 3, "watch_until": "20200101T000000Z"},
    ]

    def run_selection(self, **overrides):
        args = SimpleNamespace(
            id=None,
            tier=None,
            kind=None,
            exclude_kind=None,
            dry_run=True,
            result_file=None,
        )
        for name, value in overrides.items():
            setattr(args, name, value)
        seen = []

        def same(source, dry=False):
            seen.append(source["id"])
            # "unchanged" is the event capture_one actually emits; "same" is
            # only the word it prints. The distinction matters now that
            # freshness judges a run by which sources succeeded.
            return {"id": source["id"], "event": "unchanged"}

        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                mock.patch.object(capture, "load_sources", return_value={"source": self.SOURCES}), \
                mock.patch.object(capture, "capture_one", side_effect=same), \
                mock.patch.object(capture.time, "sleep"):
            code = capture.cmd_capture(args)
        return code, seen, output.getvalue()

    def test_tier_selection(self) -> None:
        code, seen, _ = self.run_selection(tier=2)
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["repo"])

    def test_kind_selection(self) -> None:
        code, seen, _ = self.run_selection(kind="chain-monitor")
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["chain"])

    def test_excluded_kind_selection(self) -> None:
        code, seen, _ = self.run_selection(tier=1, exclude_kind="chain-monitor")
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["urgent"])

    def test_broad_selection_skips_frozen_sources(self) -> None:
        code, seen, output = self.run_selection(tier=3)
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["report"])
        self.assertIn("1 frozen source(s)", output)

    def test_explicit_id_can_recheck_a_frozen_source(self) -> None:
        code, seen, _ = self.run_selection(id="frozen")
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["frozen"])

    def test_broad_selection_skips_elapsed_watch_window(self) -> None:
        code, seen, output = self.run_selection(tier=3)
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["report"])
        self.assertIn("1 source watch window(s) elapsed", output)

    def test_explicit_id_can_recheck_an_elapsed_source(self) -> None:
        code, seen, _ = self.run_selection(id="elapsed")
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["elapsed"])

    def test_empty_selection_is_a_configuration_error(self) -> None:
        code, seen, output = self.run_selection(tier=9)
        self.assertEqual(code, 2)
        self.assertEqual(seen, [])
        self.assertIn("matched no sources", output)


class FlattenRedditThreadTests(unittest.TestCase):
    LISTING = json.dumps([
        {"kind": "Listing", "data": {"children": [
            {"kind": "t3", "data": {
                "id": "abc123", "author": "poster",
                "created_utc": 1754000000.0, "title": "A title",
                "selftext": "post body", "score": 482}}]}},
        {"kind": "Listing", "data": {"children": [
            {"kind": "t1", "data": {
                "id": "z9", "parent_id": "t3_abc123", "author": "late",
                "created_utc": 1754000900.0, "edited": False,
                "body": "second by id", "score": 10, "replies": ""}},
            {"kind": "t1", "data": {
                "id": "a1", "parent_id": "t3_abc123", "author": "early",
                "created_utc": 1754000100.0, "edited": 1754000500.0,
                "body": "first by id", "score": 999,
                "replies": {"kind": "Listing", "data": {"children": [
                    {"kind": "t1", "data": {
                        "id": "a1b", "parent_id": "t1_a1",
                        "author": "[deleted]",
                        "created_utc": 1754000200.0, "edited": False,
                        "body": "[deleted]", "score": 1, "replies": ""}}]}}}},
            {"kind": "more", "data": {
                "parent_id": "t3_abc123", "count": 12,
                "children": ["x", "y"]}}]}}])

    def test_flatten_orders_by_id_and_drops_counters(self) -> None:
        text = capture.flatten_reddit_thread(self.LISTING)
        self.assertEqual(
            text,
            "post: abc123\n"
            "author: poster\n"
            "created_utc: 1754000000\n"
            "title: A title\n"
            "body:\n"
            "post body\n"
            "\n"
            "comment: a1\n"
            "parent: t3_abc123\n"
            "author: early\n"
            "created_utc: 1754000100\n"
            "edited: 1754000500\n"
            "body:\n"
            "first by id\n"
            "\n"
            "comment: a1b\n"
            "parent: t1_a1\n"
            "author: [deleted]\n"
            "created_utc: 1754000200\n"
            "edited: false\n"
            "body:\n"
            "[deleted]\n"
            "\n"
            "comment: z9\n"
            "parent: t3_abc123\n"
            "author: late\n"
            "created_utc: 1754000900\n"
            "edited: false\n"
            "body:\n"
            "second by id\n"
            "\n"
            "more-stub: parent t3_abc123 count 12\n",
        )


class RedditJsonBindingTests(unittest.TestCase):
    def test_only_json_safe_reddit_normalizer_is_bound_for_json_captures(self) -> None:
        names = capture.source_normalizers(
            {"id": "reddit-ai-discovery-thread", "capture": "reddit-json"})
        self.assertEqual(
            [n for n in names if n.startswith("reddit-")],
            ["reddit-more-stub-counts"],
        )

    def test_reddit_normalizers_bound_for_browser_captures(self) -> None:
        names = capture.source_normalizers(
            {"id": "reddit-ai-discovery-thread", "capture": "browser"})
        self.assertIn("reddit-chrome", names)


class BrowserReadinessTests(unittest.TestCase):
    def test_browser_capture_waits_for_required_rendered_text(self) -> None:
        rendered = iter(("Loading holdings", "Loaded holdings\nMovement feed"))
        actions = []

        def command(action, args=None, timeout=60):
            actions.append(action)
            if action == "evaluate":
                if "location.hostname" in (args or {}).get("code", ""):
                    return {"value": "example.test"}
                return {"value": next(rendered)}
            return {}

        with mock.patch.object(capture, "wb_available", return_value=True), \
                mock.patch.object(capture, "wb_cmd", side_effect=command), \
                mock.patch.object(capture.time, "sleep") as sleep, \
                mock.patch.object(capture.time, "monotonic", side_effect=(0, 1)):
            text, pdf_bytes, info = capture.fetch_browser(
                "https://example.test",
                scroll=False,
                required_text=("Loaded holdings", "Movement feed"),
            )

        self.assertEqual(text, "Loaded holdings\nMovement feed")
        self.assertEqual(pdf_bytes, 0)
        self.assertEqual(actions.count("evaluate"), 3)
        self.assertEqual(actions[-1], "close_tab")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 2])

    def test_a_tab_showing_another_page_is_not_captured(self) -> None:
        """A crashed target leaves the daemon reading whatever tab is current:
        on 4 Aug 2026 the MARA portal was filed under a stacker.news source."""

        def command(action, args=None, timeout=60):
            if action == "evaluate":
                if "location.hostname" in (args or {}).get("code", ""):
                    return {"value": "slipstream.mara.com"}
                return {"value": "MARA portal text"}
            return {}

        with mock.patch.object(capture, "wb_available", return_value=True), \
                mock.patch.object(capture, "wb_cmd", side_effect=command), \
                mock.patch.object(capture.time, "sleep"):
            with self.assertRaises(capture.BrowserUnavailable):
                capture.fetch_browser(
                    "https://stacker.news/items/1538447", scroll=False
                )

    def test_a_transient_fetch_error_is_retried_before_being_recorded(self) -> None:
        source = {"id": "error", "url": "https://example.test"}
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(capture.time, "sleep") as sleep, \
                mock.patch.object(
                    capture, "fetch", side_effect=OSError("offline")
                ) as fetch:
            result = capture.capture_one(source, dry=True)
        self.assertEqual(result["event"], "error")
        self.assertEqual(result["failure"], "transient")
        self.assertEqual(fetch.call_count, capture.FETCH_ATTEMPTS)
        self.assertEqual(sleep.call_count, capture.FETCH_ATTEMPTS - 1)
        self.assertEqual(result["attempts"], capture.FETCH_ATTEMPTS)

    def test_a_transient_error_that_clears_on_retry_is_a_normal_capture(self) -> None:
        source = {"id": "flaky", "url": "https://example.test"}
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(capture.time, "sleep"), \
                mock.patch.object(
                    capture, "fetch",
                    side_effect=[OSError("reset"), (b"<p>hello</p>", {})],
                ):
            result = capture.capture_one(source, dry=True)
        self.assertEqual(result["event"], "first")
        self.assertNotIn("failure", result)

    def test_a_refusal_is_not_retried(self) -> None:
        """403 is a decision by the origin. Retrying it is pointless and rude."""
        source = {"id": "refused", "url": "https://example.test"}
        refusal = urllib.error.HTTPError("https://example.test", 403, "no", None, None)
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(capture, "consecutive_refusals", return_value=0), \
                mock.patch.object(capture.time, "sleep") as sleep, \
                mock.patch.object(capture, "fetch", side_effect=refusal) as fetch:
            result = capture.capture_one(source, dry=True)
        self.assertEqual(result["failure"], "refused")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(sleep.call_count, 0)

    def test_a_missing_page_is_recorded_as_absent(self) -> None:
        source = {"id": "absent", "url": "https://example.test"}
        missing = urllib.error.HTTPError("https://example.test", 404, "gone", None, None)
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(capture, "fetch", side_effect=missing) as fetch:
            result = capture.capture_one(source, dry=True)
        self.assertEqual(result["failure"], "absent")
        self.assertEqual(fetch.call_count, 1)

    def test_missing_required_text_blocks_capture(self) -> None:
        source = {
            "id": "incomplete-render",
            "url": "https://example.test",
            "required_text": ["Loaded holdings"],
        }
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
            capture, "fetch", return_value=(b"Loading holdings", {})
        ):
            result = capture.capture_one(source, dry=True)
        self.assertEqual(result["event"], "blocked")
        self.assertEqual(result["missing_required_text"], ["Loaded holdings"])
        self.assertEqual(capture._capture_exit([result], {"incomplete-render"}),
                         capture.INCOMPLETE_EXIT)


class DryRunDiffTests(unittest.TestCase):
    def test_changed_source_reports_diff_counts_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshots = Path(tmp) / "snapshots"
            diffs = Path(tmp) / "diffs"
            index = Path(tmp) / "index.jsonl"
            held = snapshots / "example"
            held.mkdir(parents=True)
            (held / "20260801T000000Z.txt").write_text(
                "same\nold line\n", encoding="utf-8"
            )
            source = {"id": "example", "url": "https://example.test"}
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(
                capture, "SNAPSHOTS", snapshots
            ), mock.patch.object(
                capture, "DIFFS", diffs
            ), mock.patch.object(
                capture, "INDEX", index
            ), mock.patch.object(
                capture, "fetch", return_value=(b"same\nnew line\n", {})
            ):
                result = capture.capture_one(source, dry=True)

            self.assertFalse(diffs.exists())
            self.assertFalse(index.exists())

        self.assertEqual(result["event"], "changed")
        self.assertEqual(result["diff_added"], 1)
        self.assertEqual(result["diff_removed"], 1)
        self.assertEqual(result["prev_ts"], "20260801T000000Z")
        self.assertIn("+1 -1", output.getvalue())

    def test_fetch_url_keeps_the_public_source_identity(self) -> None:
        source = {
            "id": "official-act",
            "url": "https://example.test/act",
            "fetch_url": "https://example.test/api/act",
        }
        body = b"official text"
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
            capture, "fetch", return_value=(body, {"_status": "200"})
        ) as fetch:
            result = capture.capture_one(source, dry=True)

        fetch.assert_called_once_with(source["fetch_url"], None)
        self.assertEqual(result["url"], source["url"])
        self.assertEqual(result["fetch_url"], source["fetch_url"])

    def test_fetch_post_reaches_the_fetch_call(self) -> None:
        source = {
            "id": "graphql-thread",
            "url": "https://example.test/items/1",
            "fetch_url": "https://example.test/api/graphql",
            "fetch_post": '{"query": "{ item(id: 1) { title } }"}',
        }
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
            capture, "fetch", return_value=(b'{"data":{}}', {"_status": "200"})
        ) as fetch:
            result = capture.capture_one(source, dry=True)

        fetch.assert_called_once_with(source["fetch_url"], source["fetch_post"])
        self.assertEqual(result["url"], source["url"])

    def test_fetch_with_a_body_sends_a_json_post(self) -> None:
        held = {}

        class Resp:
            status = 200
            headers: dict = {}

            def read(self) -> bytes:
                return b'{"data":{}}'

            def __enter__(self):
                return self

            def __exit__(self, *args) -> bool:
                return False

        def fake_urlopen(req, timeout=None):
            held["req"] = req
            return Resp()

        with mock.patch.object(capture.urllib.request, "urlopen", fake_urlopen):
            capture.fetch("https://api.example.test/graphql", '{"query":"{ x }"}')

        req = held["req"]
        self.assertEqual(req.data, b'{"query":"{ x }"}')
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_fetch_without_a_body_stays_a_get(self) -> None:
        held = {}

        class Resp:
            status = 200
            headers: dict = {}

            def read(self) -> bytes:
                return b"body"

            def __enter__(self):
                return self

            def __exit__(self, *args) -> bool:
                return False

        def fake_urlopen(req, timeout=None):
            held["req"] = req
            return Resp()

        with mock.patch.object(
            capture.urllib.request, "urlopen", fake_urlopen,
        ):
            capture.fetch("https://example.test/page")

        self.assertIsNone(held["req"].data)
        self.assertEqual(held["req"].get_method(), "GET")


class CaptureXTests(unittest.TestCase):
    def run_with_fake(self, body: str) -> tuple[subprocess.CompletedProcess, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fake = root / "gallery-dl"
        fake.write_text("#!/bin/bash\n" + textwrap.dedent(body))
        fake.chmod(0o755)
        out = root / "out"
        env = {
            **os.environ,
            "GALLERY_DL": str(fake),
            "CAPTURE_X_OUT": str(out),
            "COLDCARD_ARCHIVE_LOCK_PATH": str(root / "archive.lock"),
        }
        proc = subprocess.run(
            ["/bin/bash", "scripts/capture-x.sh",
             "https://x.com/example/status/123456789"],
            cwd=capture.ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        return proc, out

    def test_zero_result_is_failure(self) -> None:
        proc, _ = self.run_with_fake("""
            echo "No results"
            exit 0
        """)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("produced no result", proc.stderr)

    def test_matching_post_artifact_is_accepted(self) -> None:
        proc, out = self.run_with_fake("""
            destination=""
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "--dest" ]]; then destination="$2"; shift 2; continue; fi
              shift
            done
            mkdir -p "$destination/twitter/example"
            : > "$destination/twitter/example/123456789_1.png"
        """)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # Media attached to a post is stored as attachment-N, never as the
        # post itself: the site's publication rules rely on that name.
        captures = list((out / "adhoc").glob("*/attachment-1.png"))
        self.assertTrue(captures, f"no attachment written under {out / 'adhoc'}")
        self.assertFalse(
            list((out / "adhoc").glob("*/post.png")),
            "gallery-dl media must never be written as post.png",
        )

    def test_text_only_info_sidecar_is_accepted(self) -> None:
        proc, out = self.run_with_fake("""
            destination=""
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "--dest" ]]; then destination="$2"; shift 2; continue; fi
              shift
            done
            mkdir -p "$destination/twitter/example"
            printf '{"tweet_id":"123456789"}\n' > "$destination/twitter/example/info.json"
        """)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # A text-only post yields only the tool's sidecar, kept as meta.json.
        self.assertTrue(
            list((out / "adhoc").glob("*/meta.json")),
            f"no sidecar written under {out / 'adhoc'}",
        )



class ResponseHeaderTests(unittest.TestCase):
    """The archive stores what the origin served, not how it reached us.

    CDN routing headers name the edge that answered, and an edge is a place.
    Publishing them would disclose roughly where the collector sits, which is
    a fact about the operator rather than about the record.
    """

    def test_routing_and_session_headers_are_dropped(self):
        kept = safe_headers({
            "content-type": "text/html",
            "etag": '"abc"',
            "x-served-by": "cache-per-ypph1920025-PER",
            "cf-ray": "9a1b2c3d4e5f6789-PER",
            "x-github-edge-region": "australiaeast",
            "x-vercel-id": "sin1::abcde-1785000000000-0a1b2c3d4e5f",
            "x-amz-cf-pop": "SIN52-P1",
            "x-timer": "S1785563979.991692,VS0,VE258",
            "via": "1.1 varnish",
            "set-cookie": "__cf_bm=abc; path=/",
        })
        self.assertEqual(kept, {"content-type": "text/html", "etag": '"abc"'})

    def test_evidence_headers_survive(self):
        headers = {
            "_status": "200",
            "content-type": "text/html; charset=utf-8",
            "content-length": "4096",
            "date": "Sat, 01 Aug 2026 05:59:39 GMT",
            "last-modified": "Fri, 31 Jul 2026 22:00:00 GMT",
            "etag": '"deadbeef"',
            "server": "nginx",
        }
        self.assertEqual(safe_headers(headers), headers)

    def test_unknown_header_is_refused_not_ignored(self):
        # The point of an allowlist: a header from a CDN nobody has heard of
        # is dropped by default rather than published because no rule named it.
        self.assertEqual(
            disallowed({"x-brand-new-edge-pop": "PER", "date": "..."}),
            ["x-brand-new-edge-pop"],
        )

    def test_header_case_is_not_a_bypass(self):
        self.assertEqual(safe_headers({"X-Served-By": "cache-per-ypph-PER"}),
                         {})
        self.assertEqual(list(safe_headers({"Content-Type": "text/css"})),
                         ["Content-Type"])

    def test_geo_echo_in_body_is_detected(self):
        body = ('<a href="/x?country=SG&city=Singapore&region=sin1'
                '&oficialCountryName=Republic+of+Singapore&viewport=desktop">')
        self.assertTrue(geo_in_body(body))

    def test_ordinary_mention_of_a_place_is_not_a_leak(self):
        # A source writing about Singapore is captured content, not a leak.
        # This is the false positive that would make the gate unusable.
        body = ("<p>Poolin Technology Pte. Ltd., a Singapore-based company, "
                "presented at TOKEN2049 in London and Singapore.</p>")
        self.assertEqual(geo_in_body(body), [])

    def test_scrub_blanks_every_pair_in_a_percent_encoded_chain(self):
        # The CoinDesk signup link: one long percent-encoded query. Only the
        # first pair sits at a word boundary; every later key follows "%26",
        # whose trailing digit must not read as part of the key.
        body = ("country%3DSG%26city%3DSingapore%26countryRegion%3D"
                "%26region%3Dsin1%26subregion%3DSouth-Eastern%2BAsia"
                "%26oficialCountryName%3DRepublic%2Bof%2BSingapore"
                "%26currencyCode%3DSGD%26currencyName%3DSingapore%2Bdollar"
                "%26viewport%3Ddesktop")
        out, n = scrub_geo(body)
        self.assertEqual(n, 7)  # countryRegion is empty, viewport is not a geo key
        self.assertNotIn("Singapore", out)
        self.assertEqual(geo_in_body(out), [])

    def test_scrub_blanks_unicode_escaped_chains(self):
        # The same echo JSON-escaped inside a script payload, spaces carried
        # as "+" or "%20".
        for gap in ("+", "%20"):
            body = ("country=SG\\u0026city=Singapore\\u0026"
                    "countryRegion=\\u0026region=sin1\\u0026"
                    "subregion=South-Eastern" + gap + "Asia\\u0026"
                    "oficialCountryName=Republic" + gap + "of" + gap +
                    "Singapore\\u0026currencyName=Singapore" + gap +
                    "dollar\\u0026viewport=desktop")
            out, n = scrub_geo(body)
            self.assertEqual(n, 6, body)
            self.assertNotIn("Singapore", out)
            self.assertEqual(geo_in_body(out), [])

    def test_scrub_json_string_values_keep_their_spaces_together(self):
        # Inside an escaped JSON blob the value runs to the closing quote and
        # may contain plain spaces; truncating at the first space would leave
        # "of Singapore" behind in the page.
        body = ('\\"country\\":\\"SG\\",\\"city\\":'
                '\\"Singapore\\",\\"subregion\\":\\"South-Eastern '
                'Asia\\",\\"oficialCountryName\\":\\"Republic of '
                'Singapore\\"')
        out, n = scrub_geo(body)
        self.assertEqual(n, 4)
        self.assertNotIn("Singapore", out)
        self.assertNotIn("South-Eastern", out)
        self.assertEqual(geo_in_body(out), [])

    def test_scrub_leaves_prose_placeholders_and_lookalikes(self):
        prose = ("<p>Poolin Technology Pte. Ltd., a Singapore-based "
                 "company.</p>")
        self.assertEqual(scrub_geo(prose), (prose, 0))
        template = "?country={{country}}&city=$city"
        self.assertEqual(scrub_geo(template), (template, 0))
        empties = "countryRegion=&countryRegion%3D%26"
        self.assertEqual(scrub_geo(empties), (empties, 0))
        lookalikes = '<span style="opacity:.5">addressCountry</span>'
        self.assertEqual(scrub_geo(lookalikes), (lookalikes, 0))

    def test_scrubbed_body_passes_the_gate(self):
        body = ('<a href="/x?country=SG&city=Singapore&region=sin1'
                '&oficialCountryName=Republic+of+Singapore&viewport=desktop">')
        out, _ = scrub_geo(body)
        self.assertIn(GEO_REDACTED, out)
        self.assertEqual(geo_in_body(out), [])


class FailureDiagnosisTests(unittest.TestCase):
    """`failure` answers whether to retry. `diagnosis` answers what to fix."""

    @staticmethod
    def _http(code, body=b"", headers=None):
        return urllib.error.HTTPError(
            "https://example.test", code, "no", headers,
            io.BytesIO(body) if body else None,
        )

    def test_an_interstitial_is_told_from_a_flat_refusal(self):
        """Both are 403 and both are `refused`; only one is about our address."""
        challenge = self._http(403, b"<title>Just a moment...</title>")
        self.assertEqual(capture.diagnose_failure(challenge),
                         ("origin-challenge", 403))
        paywall = self._http(403, b"<h1>Subscribers only</h1>")
        self.assertEqual(capture.diagnose_failure(paywall),
                         ("origin-refused", 403))

    def test_a_challenge_header_counts_without_a_body(self):
        marked = self._http(403, headers={"cf-mitigated": "challenge"})
        self.assertEqual(capture.diagnose_failure(marked),
                         ("origin-challenge", 403))

    def test_statuses_that_mean_different_work(self):
        self.assertEqual(capture.diagnose_failure(self._http(404)),
                         ("origin-absent", 404))
        self.assertEqual(capture.diagnose_failure(self._http(429)),
                         ("origin-rate-limit", 429))
        self.assertEqual(capture.diagnose_failure(self._http(503)),
                         ("origin-server-error", 503))

    def test_an_unresolvable_name_is_not_weather(self):
        """coldcard-watch was filtered by the resolver for 52 polls, all
        recorded as `transient`, which reads as a site having a bad day."""
        dns = urllib.error.URLError(
            OSError(-2, "Name or service not known"))
        self.assertEqual(capture.diagnose_failure(dns), ("dns-unresolved", None))
        self.assertEqual(capture.classify_failure(dns), "transient")

    def test_connection_failures_keep_their_shape(self):
        self.assertEqual(capture.diagnose_failure(TimeoutError("timed out")),
                         ("connect-timeout", None))
        self.assertEqual(
            capture.diagnose_failure(ConnectionResetError("reset by peer")),
            ("connect-reset", None))

    def test_a_short_healthy_thread_is_not_a_challenge(self):
        """Four Reddit threads answered correctly for 39 polls each and were
        recorded as `challenged` because a registration default was too high."""
        thread = "post: 1vemsyq\nauthor: H8ckt1v1st\nbody: truly sad to see"
        self.assertEqual(
            capture.diagnose_content(thread, "content-below-floor"),
            "content-below-floor")

    def test_a_short_body_that_is_a_challenge_still_says_so(self):
        self.assertEqual(
            capture.diagnose_content(
                "www.theblock.co\nPerforming security verification\n",
                "content-below-floor"),
            "origin-challenge")

    def test_a_stale_marker_is_told_from_a_blocked_render(self):
        page = "Receipts\nScreenshots of what the Coldcard side published"
        self.assertEqual(
            capture.diagnose_content(page, "content-marker-missing"),
            "content-marker-missing")

    def test_browser_faults_are_separated(self):
        self.assertEqual(
            capture.diagnose_browser(
                "tab shows www.reddit.com, not slipstream.mara.com: target crashed?"),
            "browser-tab-lost")
        self.assertEqual(
            capture.diagnose_browser("webbridge daemon or browser not reachable"),
            "browser-unavailable")

    def test_capture_records_the_diagnosis_and_status(self):
        source = {"id": "challenged", "url": "https://example.test"}
        challenge = self._http(403, b"Attention Required! | Cloudflare")
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(capture, "consecutive_refusals", return_value=0), \
                mock.patch.object(capture, "fetch", side_effect=challenge):
            result = capture.capture_one(source, dry=True)
        self.assertEqual(result["failure"], "refused")
        self.assertEqual(result["diagnosis"], "origin-challenge")
        self.assertEqual(result["http_status"], 403)


class FailingSourceTests(unittest.TestCase):
    """A streak is the signal. One failure is weather."""

    def test_streak_counts_back_to_the_last_good_poll(self):
        events = [
            {"id": "a", "ts": "20260801T000000Z", "event": "first"},
            {"id": "a", "ts": "20260802T000000Z", "event": "unchanged"},
            {"id": "a", "ts": "20260803T000000Z", "event": "error",
             "failure": "refused", "diagnosis": "origin-challenge",
             "http_status": 403},
            {"id": "a", "ts": "20260804T000000Z", "event": "error",
             "failure": "refused", "diagnosis": "origin-challenge",
             "http_status": 403},
        ]
        rows = capture.failing_sources(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["streak"], 2)
        self.assertEqual(rows[0]["failing_since"], "20260803T000000Z")
        self.assertEqual(rows[0]["last_good"], "20260802T000000Z")
        self.assertEqual(rows[0]["diagnosis"], "origin-challenge")

    def test_a_source_that_recovered_is_not_listed(self):
        events = [
            {"id": "b", "ts": "20260801T000000Z", "event": "error",
             "failure": "transient"},
            {"id": "b", "ts": "20260802T000000Z", "event": "changed"},
        ]
        self.assertEqual(capture.failing_sources(events), [])

    def test_events_from_before_diagnosis_say_so(self):
        """Most of the record predates the field. Guessing a cause for it
        would be worse than admitting the record does not carry one."""
        events = [{"id": "c", "ts": "20260801T000000Z", "event": "blocked",
                   "failure": "challenged"}]
        self.assertEqual(capture.failing_sources(events)[0]["diagnosis"],
                         "unrecorded")

    def test_worst_streak_comes_first(self):
        events = [
            {"id": "short", "ts": "20260804T000000Z", "event": "error"},
            {"id": "long", "ts": "20260801T000000Z", "event": "error"},
            {"id": "long", "ts": "20260802T000000Z", "event": "error"},
            {"id": "long", "ts": "20260803T000000Z", "event": "error"},
        ]
        self.assertEqual([r["id"] for r in capture.failing_sources(events)],
                         ["long", "short"])


if __name__ == "__main__":
    unittest.main()
