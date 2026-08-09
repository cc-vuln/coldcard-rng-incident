import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).with_name("discover_x_browser.py")
sys.path.insert(0, str(SCRIPT.parent))

import discover_x  # noqa: E402
import discovery_common  # noqa: E402
import x_browser  # noqa: E402
import discover_x_browser as dxb  # noqa: E402

FIXED_NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


def fixed_clock():
    return FIXED_NOW


def raw_post(**overrides):
    base = {
        "id": "2084000000000001234",
        "href": "/Researcher/status/2084000000000001234",
        "handle": "Researcher",
        "snippet": "new coldcard rng analysis posted",
        "time": "2026-08-08T11:30:00.000Z",
        "context": "",
        "ad": False,
    }
    base.update(overrides)
    return base


class SessionHealthTests(unittest.TestCase):
    def test_healthy_signed_in_page(self):
        probe = {"url": "https://x.com/home", "articles": 17, "chrome": True}
        self.assertEqual(x_browser.classify_session(probe), "ok")

    def test_healthy_chrome_without_articles(self):
        # A slow timeline with the signed-in shell mounted is still healthy.
        probe = {"url": "https://x.com/home", "articles": 0, "chrome": True}
        self.assertEqual(x_browser.classify_session(probe), "ok")

    def test_login_form_is_a_login_wall(self):
        probe = {"url": "https://x.com/home", "loginForm": True}
        self.assertEqual(x_browser.classify_session(probe), "login-wall")

    def test_logged_out_redirect_is_a_login_wall(self):
        probe = {"url": "https://x.com/i/flow/login", "articles": 0}
        self.assertEqual(x_browser.classify_session(probe), "login-wall")

    def test_wall_text_is_a_login_wall(self):
        probe = {"url": "https://x.com/", "wallText": True, "articles": 0}
        self.assertEqual(x_browser.classify_session(probe), "login-wall")

    def test_arkose_iframe_is_a_challenge(self):
        probe = {"url": "https://x.com/home", "arkose": True, "articles": 0}
        self.assertEqual(x_browser.classify_session(probe), "challenge")

    def test_challenge_text_is_a_challenge(self):
        probe = {"url": "https://x.com/home", "challengeText": True}
        self.assertEqual(x_browser.classify_session(probe), "challenge")

    def test_rate_limit_text_is_a_rate_limit(self):
        probe = {"url": "https://x.com/home", "rateText": True, "articles": 0}
        self.assertEqual(x_browser.classify_session(probe), "rate-limit")

    def test_login_wall_masks_other_symptoms(self):
        # The class only a person can repair must never be hidden behind a
        # secondary symptom.
        probe = {"url": "https://x.com/home", "loginForm": True,
                 "rateText": True, "arkose": True}
        self.assertEqual(x_browser.classify_session(probe), "login-wall")

    def test_challenge_masks_rate_limit(self):
        probe = {"url": "https://x.com/home", "challengeText": True,
                 "rateText": True}
        self.assertEqual(x_browser.classify_session(probe), "challenge")

    def test_unrecognized_empty_page_fails_closed(self):
        # No content, no chrome, no known marker: report a challenge and stop
        # rather than scan a page that rendered nothing and call it healthy.
        probe = {"url": "https://x.com/home", "articles": 0, "chrome": False}
        self.assertEqual(x_browser.classify_session(probe), "challenge")


class ProfileClassTests(unittest.TestCase):
    def test_distinct_per_profile_outcomes(self):
        self.assertEqual(
            dxb.classify_profile({"suspended": True}), "suspended")
        self.assertEqual(
            dxb.classify_profile({"protected": True}), "protected")
        self.assertEqual(
            dxb.classify_profile({"nonexistent": True}), "unavailable")

    def test_healthy_empty_profile_is_ok(self):
        self.assertEqual(dxb.classify_profile({"articles": 0}), "ok")

    def test_session_classes_mask_profile_outcomes(self):
        probe = {"loginForm": True, "suspended": True}
        self.assertEqual(dxb.classify_profile(probe), "login-wall")
        probe = {"rateText": True, "protected": True}
        self.assertEqual(dxb.classify_profile(probe), "rate-limit")
        probe = {"arkose": True, "nonexistent": True}
        self.assertEqual(dxb.classify_profile(probe), "challenge")


class CooldownTests(unittest.TestCase):
    def test_write_and_read_active_cooldown(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cooldown.json"
            state = x_browser.write_cooldown(path, "login-wall",
                                             clock=fixed_clock)
            self.assertEqual(state["class"], "login-wall")
            active = x_browser.read_cooldown(path, clock=fixed_clock)
            self.assertEqual(active["class"], "login-wall")
            self.assertEqual(active["until"], "20260809T120000Z")

    def test_expired_cooldown_reads_as_none(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cooldown.json"
            x_browser.write_cooldown(path, "rate-limit", clock=fixed_clock)
            later = lambda: FIXED_NOW + timedelta(hours=25)  # noqa: E731
            self.assertIsNone(x_browser.read_cooldown(path, clock=later))

    def test_missing_file_reads_as_none(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cooldown.json"
            self.assertIsNone(x_browser.read_cooldown(path, clock=fixed_clock))

    def test_malformed_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cooldown.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(x_browser.XBrowserConfigError):
                x_browser.read_cooldown(path, clock=fixed_clock)
            path.write_text('{"class": "ok", "until": "20260809T120000Z"}',
                            encoding="utf-8")
            with self.assertRaises(x_browser.XBrowserConfigError):
                x_browser.read_cooldown(path, clock=fixed_clock)

    def test_clear_cooldown(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cooldown.json"
            self.assertFalse(x_browser.clear_cooldown(path))
            x_browser.write_cooldown(path, "challenge", clock=fixed_clock)
            self.assertTrue(x_browser.clear_cooldown(path))
            self.assertFalse(path.exists())

    def test_refusing_to_cool_down_on_ok(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "cooldown.json"
            with self.assertRaises(x_browser.XBrowserConfigError):
                x_browser.write_cooldown(path, "ok", clock=fixed_clock)
            self.assertFalse(path.exists())


class NormalizePostTests(unittest.TestCase):
    def test_full_extraction(self):
        post = dxb.normalize_post(raw_post())
        self.assertEqual(post["id"], "2084000000000001234")
        self.assertEqual(
            post["url"], "https://x.com/Researcher/status/2084000000000001234")
        self.assertEqual(post["author"], "Researcher")
        self.assertEqual(post["actor"], "Researcher")
        self.assertEqual(post["relation"], "post")
        self.assertEqual(post["createdAt"], "2026-08-08T11:30:00.000Z")

    def test_handle_falls_back_to_permalink_path(self):
        post = dxb.normalize_post(raw_post(handle=None))
        self.assertEqual(post["author"], "Researcher")

    def test_watched_actor_is_the_default_actor(self):
        post = dxb.normalize_post(
            raw_post(context="@Researcher reposted"),
            default_actor="nvk",
        )
        self.assertEqual(post["actor"], "nvk")
        self.assertEqual(post["author"], "Researcher")
        self.assertEqual(post["relation"], "repost")

    def test_timeline_repost_context_keeps_the_authors_relation(self):
        # On the home timeline a "reposted" context names an unknown third
        # party, not the author; "@author repost" would misattribute it.
        post = dxb.normalize_post(raw_post(context="Somebody reposted"))
        self.assertEqual(post["relation"], "post")

    def test_rejections(self):
        self.assertIsNone(dxb.normalize_post(raw_post(id="abc")))
        self.assertIsNone(dxb.normalize_post(raw_post(id="")))
        self.assertIsNone(dxb.normalize_post(raw_post(handle=None, href="")))
        self.assertIsNone(dxb.normalize_post(raw_post(ad=True)))
        self.assertIsNone(dxb.normalize_post("not a dict"))

    def test_snippet_is_collapsed_and_bounded(self):
        post = dxb.normalize_post(raw_post(snippet="  a\n\nb   c " + "x" * 500))
        self.assertEqual(post["snippet"], ("a b c " + "x" * 500)[:200])


class QueueDecisionTests(unittest.TestCase):
    def test_watched_handles_queue_unfiltered(self):
        # The watch registry means no keyword filter: the post that matters
        # is the one that never names the incident.
        post = dxb.normalize_post(raw_post(snippet="gm, coffee time"))
        self.assertEqual(dxb.queue_decision(post, {"researcher"}), "watch")

    def test_keyword_tiers_for_the_timeline(self):
        strong = dxb.normalize_post(raw_post(snippet="coldcard rng writeup"))
        self.assertEqual(dxb.queue_decision(strong, set()), "strong")
        topical = dxb.normalize_post(
            raw_post(snippet="on hardware wallet entropy generally"))
        self.assertEqual(dxb.queue_decision(topical, set()), "topical")

    def test_unmatched_timeline_post_is_skipped(self):
        post = dxb.normalize_post(raw_post(snippet="beautiful sunrise today"))
        self.assertIsNone(dxb.queue_decision(post, set()))

    def test_empty_snippet_is_skipped(self):
        post = dxb.normalize_post(raw_post(snippet=""))
        self.assertIsNone(dxb.queue_decision(post, set()))


class CandidateShapeTests(unittest.TestCase):
    def test_candidate_carries_api_lane_fields(self):
        watch = discover_x.Watch(
            handle="Researcher", why="Primary technical work", org="Lab")
        post = dxb.normalize_post(raw_post())
        candidate = dxb.candidate_for_intake(
            post, "20260808T120000Z", source="watch:Researcher", tier="watch",
            watch=watch)
        for key in ("id", "url", "platform", "actor", "org", "relation",
                    "createdAt", "watchWhy", "label", "foundAt", "title"):
            self.assertIn(key, candidate)
        self.assertEqual(candidate["platform"], "x")
        self.assertEqual(candidate["label"], "X @Researcher")
        self.assertEqual(candidate["org"], "Lab")
        # Snippet and source are the browser lane's additions.
        self.assertEqual(candidate["source"], "watch:Researcher")
        self.assertTrue(candidate["snippet"])

    def test_missing_timestamp_falls_back_to_a_date(self):
        post = dxb.normalize_post(raw_post(time=None))
        candidate = dxb.candidate_for_intake(
            post, "20260808T120000Z", source="home-timeline", tier="strong")
        self.assertEqual(candidate["createdAt"], "2026-08-08")

    def test_intake_line_matches_the_api_lane_format(self):
        watch = discover_x.Watch(handle="Researcher", why="technical work")
        post = dxb.normalize_post(raw_post())
        ours = dxb.candidate_for_intake(
            post, "20260808T120000Z", source="watch:Researcher", tier="watch",
            watch=watch)
        api_lane = discover_x.candidate_for_intake({
            "id": post["id"], "url": post["url"], "actor": post["actor"],
            "org": None, "relation": post["relation"],
            "createdAt": post["createdAt"], "replyId": None, "quoteId": None,
            "sourceTweetId": post["id"], "watchWhy": "technical work",
            "label": "X @Researcher",
        }, "20260808T120000Z")
        self.assertEqual(
            discovery_common.intake_line(ours),
            discovery_common.intake_line(api_lane),
        )
        self.assertRegex(
            discovery_common.intake_line(ours),
            r"^- 2026-08-08 \[@Researcher post "
            r"\(text available during approved intake\)\]"
            r"\(https://x\.com/Researcher/status/2084000000000001234\) "
            r"\(X @Researcher\)$",
        )

    def test_update_intake_queues_the_line_in_pending(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            saved = (discovery_common.INTAKE, discovery_common.INTAKE_LOCK)
            discovery_common.INTAKE = root / "DISCOVERY.md"
            discovery_common.INTAKE_LOCK = (
                root / ".work" / "agent-discovery-intake" / "intake.lock")
            try:
                post = dxb.normalize_post(raw_post())
                candidate = dxb.candidate_for_intake(
                    post, "20260808T120000Z", source="watch:Researcher",
                    tier="watch")
                discovery_common.update_intake([candidate], set())
                text = discovery_common.INTAKE.read_text(encoding="utf-8")
            finally:
                discovery_common.INTAKE, discovery_common.INTAKE_LOCK = saved
        pending = text.split("## Pending", 1)[1].split("## Assessed", 1)[0]
        self.assertIn(
            "https://x.com/Researcher/status/2084000000000001234", pending)


class WatchDecisionTests(unittest.TestCase):
    def setUp(self):
        self.watch = discover_x.Watch(
            handle="Researcher", why="technical work", since="2026-07-29")
        self.posts = [
            dxb.normalize_post(raw_post(id="300", time="2026-08-08T10:00:00Z")),
            dxb.normalize_post(raw_post(id="200", time="2026-08-07T10:00:00Z")),
            dxb.normalize_post(raw_post(id="100", time="2026-07-01T10:00:00Z")),
        ]

    def test_first_contact_queues_history_without_repair_flag(self):
        queueable, baseline = dxb.decide_watch(
            self.posts, {}, self.watch, set(), set(),
            queue_initial=False, overflow=False)
        self.assertTrue(baseline)
        self.assertEqual([post["id"] for post in queueable], ["300", "200"])

    def test_first_contact_reconsiders_global_seen(self):
        queueable, baseline = dxb.decide_watch(
            self.posts, {}, self.watch, {"300", "200"}, set(),
            queue_initial=False, overflow=False)
        self.assertTrue(baseline)
        self.assertEqual([post["id"] for post in queueable], ["300", "200"])

    def test_queue_initial_imports_history(self):
        queueable, baseline = dxb.decide_watch(
            self.posts, {}, self.watch, set(), set(),
            queue_initial=True, overflow=False)
        self.assertTrue(baseline)
        self.assertEqual([post["id"] for post in queueable], ["300", "200"])

    def test_queue_initial_reconsiders_ids_marked_seen_by_baseline(self):
        queueable, baseline = dxb.decide_watch(
            self.posts, {"last_success": "20260807T000000Z"}, self.watch,
            {"300", "200"}, set(), queue_initial=True, overflow=False)
        self.assertFalse(baseline)
        self.assertEqual([post["id"] for post in queueable], ["300", "200"])

    def test_queue_initial_still_skips_registered_ids(self):
        queueable, _ = dxb.decide_watch(
            self.posts, {}, self.watch, {"300", "200"}, {"300"},
            queue_initial=True, overflow=False)
        self.assertEqual([post["id"] for post in queueable], ["200"])

    def test_incremental_skips_seen_registered_and_before_since(self):
        prior = {"last_success": "20260807T000000Z", "newest_id": "200"}
        queueable, baseline = dxb.decide_watch(
            self.posts, prior, self.watch, {"300"}, {"200"},
            queue_initial=False, overflow=False)
        self.assertFalse(baseline)
        self.assertEqual(queueable, [])
        queueable, _ = dxb.decide_watch(
            self.posts, prior, self.watch, set(), set(),
            queue_initial=False, overflow=False)
        # 100 predates the watch's since; 200 and 300 are new and in range.
        self.assertEqual([post["id"] for post in queueable], ["300", "200"])

    def test_overflow_queues_nothing(self):
        prior = {"last_success": "20260807T000000Z", "newest_id": "50"}
        queueable, _ = dxb.decide_watch(
            self.posts, prior, self.watch, set(), set(),
            queue_initial=False, overflow=True)
        self.assertEqual(queueable, [])


class OverflowTests(unittest.TestCase):
    def posts(self, ids):
        return [dxb.normalize_post(raw_post(id=i)) for i in ids]

    def test_baseline_never_overflows(self):
        self.assertFalse(dxb.window_overflow(self.posts(["300"]), {},
                                             exhausted=True))

    def test_checkpoint_reached_is_no_overflow(self):
        prior = {"last_success": "x", "newest_id": "100"}
        self.assertFalse(dxb.window_overflow(
            self.posts(["300", "200", "100"]), prior, exhausted=True))

    def test_full_window_past_checkpoint_is_overflow(self):
        prior = {"last_success": "x", "newest_id": "100"}
        self.assertTrue(dxb.window_overflow(
            self.posts(["300", "200"]), prior, exhausted=True))

    def test_early_stop_is_no_overflow(self):
        # The read reached a no-growth stop, so the absent checkpoint means
        # the timeline moved on, not that posts were missed.
        prior = {"last_success": "x", "newest_id": "100"}
        self.assertFalse(dxb.window_overflow(
            self.posts(["300", "200"]), prior, exhausted=False))


class AdvanceWatchTests(unittest.TestCase):
    def setUp(self):
        self.watch = discover_x.Watch(handle="Researcher", why="work")

    def test_healthy_read_advances(self):
        posts = [dxb.normalize_post(raw_post(id="300")),
                 dxb.normalize_post(raw_post(id="200"))]
        state = dxb.advance_watch({}, self.watch, posts, "20260808T120000Z",
                                  "ok")
        self.assertEqual(state["last_success"], "20260808T120000Z")
        self.assertEqual(state["newest_id"], "300")
        self.assertEqual(state["baseline_count"], 2)

    def test_checkpoint_never_moves_backwards(self):
        prior = {"last_success": "20260807T000000Z", "newest_id": "900"}
        posts = [dxb.normalize_post(raw_post(id="300"))]
        state = dxb.advance_watch(prior, self.watch, posts, "20260808T120000Z",
                                  "ok")
        self.assertEqual(state["newest_id"], "900")

    def test_failure_keeps_checkpoint_and_records_detail(self):
        prior = {"last_success": "20260807T000000Z", "newest_id": "200"}
        state = dxb.advance_watch(prior, self.watch, [], "20260808T120000Z",
                                  "suspended", "account is suspended")
        self.assertEqual(state["newest_id"], "200")
        self.assertEqual(state["last_success"], "20260807T000000Z")
        self.assertEqual(state["status"], "suspended")
        self.assertEqual(state["detail"], "account is suspended")

    def test_window_exceeded_does_not_advance(self):
        prior = {"last_success": "20260807T000000Z", "newest_id": "200"}
        state = dxb.advance_watch(prior, self.watch, [], "20260808T120000Z",
                                  "window-exceeded", "checkpoint held")
        self.assertEqual(state["newest_id"], "200")
        self.assertNotIn("baseline_count", state)


class CoverageRecordTests(unittest.TestCase):
    def test_records_requested_boundary_observed_range_and_stop(self):
        watch = discover_x.Watch(
            handle="Researcher", why="work", since="2026-07-29"
        )
        posts = [
            dxb.normalize_post(raw_post(id="300", time="2026-08-08T10:00:00Z")),
            dxb.normalize_post(raw_post(id="200", time="2026-07-29T09:00:00Z")),
        ]
        record = dxb.coverage_record(
            watch, posts, passes=7, stop_reason="since-reached", queued=2
        )
        self.assertEqual(record["requested_since"], "2026-07-29")
        self.assertEqual(record["oldest_observed"], "2026-07-29")
        self.assertEqual(record["newest_observed"], "2026-08-08")
        self.assertEqual(record["passes"], 7)
        self.assertEqual(record["stop_reason"], "since-reached")
        self.assertEqual(record["queued"], 2)


class StateFileTests(unittest.TestCase):
    def test_missing_state_is_empty(self):
        with tempfile.TemporaryDirectory() as raw:
            state = dxb.load_state(Path(raw) / "state.json")
        self.assertEqual(state["watches"], {})
        self.assertEqual(state["seen"], [])

    def test_bad_version_and_shape_refuse_live_reads(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "state.json"
            path.write_text(json.dumps({"version": 99, "seen": [],
                                        "watches": {}}), encoding="utf-8")
            with self.assertRaisesRegex(discover_x.ConfigError, "version"):
                dxb.load_state(path)
            path.write_text(json.dumps({"version": 1, "seen": ["abc"],
                                        "watches": {}}), encoding="utf-8")
            with self.assertRaisesRegex(discover_x.ConfigError, "numeric"):
                dxb.load_state(path)


class KillSwitchTests(unittest.TestCase):
    def test_enabled_requires_exact_true(self):
        # Exact match, tighter than the API lane's truthy spellings.
        import os
        from unittest import mock
        for value, expected in (("true", True), ("True", False), ("1", False),
                                ("yes", False), ("", False)):
            with mock.patch.dict(os.environ,
                                 {"X_BROWSER_DISCOVERY_ENABLED": value}):
                got = os.environ.get("X_BROWSER_DISCOVERY_ENABLED", "") == "true"
                self.assertEqual(got, expected, value)


if __name__ == "__main__":
    unittest.main()
