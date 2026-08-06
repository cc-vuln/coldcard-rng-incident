#!/usr/bin/env python3
"""Regression tests for X conversation capture.

No network and no browser. The capture loop takes its bridge by injection, so
a fake replays recorded extraction passes; the fixtures below are shaped from
real passes harvested on 6 Aug 2026 from the clay_garrett attribution thread
and the afilini libngu thread, trimmed for readability.

The load-bearing test is `test_ranking_churn_yields_identical_text`. X serves
replies in ranked order and reorders them between loads, so if pass order
could reach the canonical text, every poll would report a change that never
happened and the change record this project exists to keep would be noise.
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import x_thread as X  # noqa: E402


FOCAL = "2083247006139503065"
AUTHOR = "clay_garrett"


def article(status, author, text="body text", *, name=None, order=0,
            created="2026-07-31T17:42:45.000Z", media=0, beyond=False):
    return {
        "order": order,
        "status": status,
        "author": author,
        "name": name or author.title(),
        "created": created,
        "media": media,
        "text": text,
        "beyondConversation": beyond,
    }


def clay_first_pass():
    """The un-scrolled head: focal, two self-thread posts, then replies."""
    return [
        article(FOCAL, "clay_garrett", "1/ During our investigation ...",
                name="Clay Garrett", order=0),
        article("2083247007808774228", "clay_garrett",
                "2/ We contacted the provider directly ...",
                name="Clay Garrett", order=1),
        article("2083247009125822647", "clay_garrett",
                "3/ We are sharing the relevant information ...",
                name="Clay Garrett", order=2),
        article("2083261381139464364", "SGBarbour", "Doing Satoshi's work.",
                order=3),
        article("2083420933243433347", "KevinKelbie",
                "Have you considered that the pattern could also be ...",
                order=4),
    ]


class NormaliseTests(unittest.TestCase):
    def test_drops_platform_recommendations(self):
        # X appends recommended posts under "Discover more". A recommendation
        # is not a reply and must never reach the canonical text.
        raw = clay_first_pass() + [
            article("9999999999999999999", "someone_else",
                    "unrelated recommended post", order=5, beyond=True),
        ]
        got = X.normalise_articles(raw)
        self.assertNotIn("9999999999999999999", [p["status"] for p in got])
        self.assertEqual(len(got), 5)

    def test_drops_articles_without_a_resolvable_status(self):
        raw = [
            article(FOCAL, "clay_garrett"),
            {"order": 1, "status": None, "author": "x", "text": "t"},
            {"order": 2, "status": "not-a-number", "author": "x", "text": "t"},
            {"order": 3, "status": "123", "author": "", "text": "t"},
        ]
        self.assertEqual([p["status"] for p in X.normalise_articles(raw)],
                         [FOCAL])

    def test_created_is_normalised_to_second_resolution(self):
        got = X.normalise_articles([article(FOCAL, "a")])[0]
        self.assertEqual(got["created"], "2026-07-31T17:42:45Z")

    def test_missing_created_survives_as_none(self):
        raw = [article(FOCAL, "a", created=None)]
        self.assertIsNone(X.normalise_articles(raw)[0]["created"])


class RoleTests(unittest.TestCase):
    def roles(self, posts, focal=FOCAL, author=AUTHOR):
        return X.assign_roles(X.normalise_articles(posts), focal, author)

    def test_focal_self_thread_and_replies(self):
        roles = self.roles(clay_first_pass())
        self.assertEqual(roles[FOCAL], "focal")
        self.assertEqual(roles["2083247007808774228"], "self-thread")
        self.assertEqual(roles["2083247009125822647"], "self-thread")
        self.assertEqual(roles["2083261381139464364"], "reply")
        self.assertEqual(roles["2083420933243433347"], "reply")

    def test_articles_above_the_focal_post_are_ancestors(self):
        posts = [article("2083000000000000001", "someone", "the parent post",
                         order=0)] + clay_first_pass()
        roles = self.roles(posts)
        self.assertEqual(roles["2083000000000000001"], "ancestor")
        self.assertEqual(roles[FOCAL], "focal")

    def test_author_replying_again_lower_down_is_a_reply(self):
        # A self-thread is contiguous. The first article by anyone else ends
        # it, so the author answering a question further down is a reply,
        # which is what it is.
        posts = clay_first_pass() + [
            article("2083500000000000000", "clay_garrett",
                    "Answering the question above", order=5),
        ]
        roles = self.roles(posts)
        self.assertEqual(roles["2083247007808774228"], "self-thread")
        self.assertEqual(roles["2083500000000000000"], "reply")

    def test_author_match_is_case_insensitive(self):
        posts = [
            article(FOCAL, "Clay_Garrett", order=0),
            article("2083247007808774228", "clay_garrett", order=1),
        ]
        roles = X.assign_roles(X.normalise_articles(posts), FOCAL,
                               "CLAY_GARRETT")
        self.assertEqual(roles["2083247007808774228"], "self-thread")

    def test_missing_focal_post_is_a_loud_failure(self):
        # Deleted, protected, or the page did not hydrate. A capture that
        # cannot find its own subject must never write a partial thread.
        with self.assertRaises(X.ThreadCaptureError):
            self.roles([article("2083261381139464364", "SGBarbour")])


class MergeTests(unittest.TestCase):
    def thread(self):
        t = X.new_thread(FOCAL, "https://x.com/clay_garrett/status/" + FOCAL,
                         AUTHOR)
        posts = X.normalise_articles(clay_first_pass())
        X.merge_posts(t, posts, X.assign_roles(posts, FOCAL, AUTHOR))
        return t

    def test_reseeing_a_post_does_not_duplicate_it(self):
        t = self.thread()
        again = X.normalise_articles(clay_first_pass())
        delta = X.merge_posts(t, again)
        self.assertEqual(delta["added"], [])
        self.assertEqual(delta["changed"], [])
        self.assertEqual(len(t["posts"]), 5)

    def test_a_later_pass_adds_replies(self):
        t = self.thread()
        delta = X.merge_posts(t, X.normalise_articles([
            article("2083600000000000000", "newcommenter", "a later reply"),
        ]))
        self.assertEqual(delta["added"], ["2083600000000000000"])
        self.assertEqual(t["roles"]["2083600000000000000"], "reply")

    def test_edited_text_is_reported_as_changed(self):
        t = self.thread()
        delta = X.merge_posts(t, X.normalise_articles([
            article("2083420933243433347", "KevinKelbie", "edited body"),
        ]))
        self.assertEqual(delta["changed"], ["2083420933243433347"])
        self.assertEqual(t["posts"]["2083420933243433347"]["text"],
                         "edited body")

    def test_a_role_change_is_recorded_not_applied_silently(self):
        # Role is stable by construction, so a change means X moved something
        # under us. It must be visible rather than rewrite the partition.
        t = self.thread()
        X.merge_posts(t, X.normalise_articles(clay_first_pass()),
                      {FOCAL: "reply"})
        self.assertEqual(t["roles"][FOCAL], "focal")
        self.assertEqual(t["role_changes"],
                         [{"status": FOCAL, "from": "focal", "to": "reply"}])


class FlattenTests(unittest.TestCase):
    def thread(self, passes=None):
        t = X.new_thread(FOCAL, "https://x.com/clay_garrett/status/" + FOCAL,
                         AUTHOR)
        first = X.normalise_articles(clay_first_pass())
        X.merge_posts(t, first, X.assign_roles(first, FOCAL, AUTHOR))
        for extra in passes or []:
            X.merge_posts(t, X.normalise_articles(extra))
        return t

    def test_partitions_by_role_then_sorts_by_status_id(self):
        text = X.flatten_thread(self.thread())
        roles = [ln.split(": ", 1)[1] for ln in text.splitlines()
                 if ln.startswith("role: ")]
        self.assertEqual(roles,
                         ["focal", "self-thread", "self-thread", "reply",
                          "reply"])
        ids = [ln.split(": ", 1)[1] for ln in text.splitlines()
               if ln.startswith("post: ")]
        self.assertEqual(ids[3:], sorted(ids[3:], key=int))

    def test_ranking_churn_yields_identical_text(self):
        # The load-bearing property. X reorders replies between loads; the
        # canonical text must not move because of it, or every poll reports a
        # change that never happened.
        late = [
            article("2083700000000000000", "aaa", "later reply one"),
            article("2083800000000000000", "bbb", "later reply two"),
        ]
        forwards = X.flatten_thread(self.thread([late]))
        backwards = X.flatten_thread(self.thread([list(reversed(late))]))
        self.assertEqual(forwards, backwards)

    def test_pass_boundaries_do_not_move_the_text(self):
        one = X.flatten_thread(self.thread([
            [article("2083700000000000000", "aaa", "x"),
             article("2083800000000000000", "bbb", "y")],
        ]))
        two = X.flatten_thread(self.thread([
            [article("2083800000000000000", "bbb", "y")],
            [article("2083700000000000000", "aaa", "x")],
        ]))
        self.assertEqual(one, two)

    def test_gaps_are_declared_at_the_end(self):
        t = self.thread()
        t["gaps"].append("reply cap reached; X ranking governs which replies "
                         "loaded")
        text = X.flatten_thread(t)
        self.assertTrue(text.rstrip().endswith(
            "gap: reply cap reached; X ranking governs which replies loaded"))

    def test_body_matching_the_block_delimiter_is_refused(self):
        # Would split into a phantom post on read-back. Refuse rather than
        # write a capture the site would parse into something else.
        t = self.thread()
        t["posts"][FOCAL]["text"] = "look at this\npost: 12345\nand this"
        with self.assertRaises(X.ThreadCaptureError):
            X.flatten_thread(t)

    def test_structured_record_carries_depth_and_roles(self):
        t = self.thread()
        rec = X.structured_record(t, {"scroll_rounds": 4, "capped": False})
        self.assertEqual(rec["thread"], FOCAL)
        self.assertEqual(rec["posts"][0]["role"], "focal")
        # Depth is what lets a reviewer tell a deleted reply from one that
        # ranking pushed below the cap. Absence is not deletion.
        self.assertEqual(rec["depth"]["scroll_rounds"], 4)


class FakeBridge:
    """Replays recorded extraction passes; records what was asked of it."""

    def __init__(self, passes, *, isolate_ok=True):
        self.passes = list(passes)
        self.isolate_ok = isolate_ok
        self.calls: list[str] = []
        self.shots: list[str] = []
        self.expands = 0

    def __call__(self, action, args=None, fatal=True):
        args = args or {}
        if action != "evaluate":
            self.calls.append(action)
            if action == "cdp":
                return {"data": base64.b64encode(b"PNG").decode()}
            return {}
        code = args.get("code", "")
        if "scrollBy" in code:
            self.calls.append("scroll")
            return {"value": "1"}
        if "beyondConversation" in code:
            self.calls.append("extract")
            # Exhausted fixtures model a settled page at the bottom, not a
            # lagging loader; without atBottom every test would stall.
            payload = (self.passes.pop(0) if self.passes
                       else {"articles": [], "controls": [],
                             "atBottom": True})
            return {"value": json.dumps(payload)}
        if "b.click()" in code:
            self.expands += 1
            self.calls.append("expand")
            return {"value": json.dumps({"clicked": 1})}
        if "cc-thread-overlay" in code and "async" in code:
            status = code.split('const status = "')[1].split('"')[0]
            self.shots.append(status)
            return {"value": json.dumps(
                {"found": self.isolate_ok, "w": 500, "h": 300})}
        return {"value": "1"}


def a_pass(articles, controls=None, at_bottom=True):
    # at_bottom defaults true: most fixtures model a conversation that has
    # finished loading. A false value models the loader lagging mid-page,
    # which must never be read as the conversation having ended.
    return {"articles": articles, "controls": controls or [],
            "atBottom": at_bottom}


class CaptureLoopTests(unittest.TestCase):
    def capture(self, passes, **kw):
        bridge = FakeBridge(passes)
        kw.setdefault("want_screenshots", False)
        thread, depth, shots = X.capture_thread(
            "https://x.com/clay_garrett/status/" + FOCAL, FOCAL, AUTHOR,
            bridge=bridge, sleep=lambda _s: None,
            hydrate_seconds=0, settle_seconds=0, loader_grace_seconds=0, **kw,
        )
        return bridge, thread, depth, shots

    def test_stops_after_the_dry_streak(self):
        passes = [a_pass(clay_first_pass())] + [a_pass([])] * 10
        _, thread, depth, _ = self.capture(passes, dry_rounds_to_stop=3)
        self.assertEqual(depth["scroll_rounds"], 3)
        self.assertEqual(depth["dry_rounds"], 3)
        self.assertEqual(len(thread["posts"]), 5)
        self.assertEqual(thread["gaps"], [])

    def test_a_converged_run_declares_no_scroll_limit_gap(self):
        # A run told to stop at a high dry value still converges; the gap line
        # must reflect the thread, not the caller's configuration.
        passes = [a_pass(clay_first_pass())] + [a_pass([])] * 30
        _, thread, _, _ = self.capture(
            passes, scroll_rounds=6, dry_rounds_to_stop=99)
        self.assertEqual(thread["gaps"], [])

    def test_scroll_limit_gap_when_still_yielding(self):
        passes = [a_pass(clay_first_pass())] + [
            a_pass([article(f"20840000000000000{n:02d}", f"u{n}", "reply")])
            for n in range(10)
        ]
        _, thread, _, _ = self.capture(passes, scroll_rounds=4)
        self.assertIn(
            "scroll limit reached before the conversation stopped growing",
            thread["gaps"])

    def test_reply_cap_is_declared(self):
        passes = [a_pass(clay_first_pass())] + [
            a_pass([article(f"20840000000000000{n:02d}", f"u{n}", "reply")])
            for n in range(10)
        ]
        _, thread, depth, _ = self.capture(passes, reply_cap=4)
        self.assertTrue(depth["capped"])
        self.assertIn(
            "reply cap reached; X ranking governs which replies loaded",
            thread["gaps"])

    def test_truncated_posts_are_expanded_before_extraction(self):
        # X serves a long post cut off mid-sentence. Capturing that and
        # presenting it as what someone said is the failure this whole
        # module exists to avoid.
        passes = [a_pass(clay_first_pass())] + [a_pass([])] * 5
        bridge, _, depth, _ = self.capture(passes)
        self.assertEqual(bridge.calls[1], "expand")
        self.assertEqual(bridge.calls[2], "extract")
        self.assertGreaterEqual(depth["posts_expanded"], 1)

    def test_a_posts_own_expander_is_not_declared_as_a_gap(self):
        # "Show more" on a post is its truncation control, already handled.
        # Only conversation-loading controls are gaps.
        passes = [a_pass(clay_first_pass(), controls=["Show more replies"])]
        passes += [a_pass([])] * 5
        _, thread, _, _ = self.capture(passes)
        self.assertEqual(thread["gaps"],
                         ["'Show more replies' control present, not expanded"])

    def test_a_quiet_round_mid_page_is_a_stall_not_the_end(self):
        # The failure this exists to prevent, measured live 6 Aug 2026: a
        # capture of a 146-reply thread stopped at 45 replies and declared
        # nothing, which reads exactly like 88 replies having been deleted.
        # Quiet rounds while the loader is behind, then the rest arrives.
        passes = [a_pass(clay_first_pass())]
        passes += [a_pass([], at_bottom=False)] * 6
        passes += [a_pass([article("2084000000000000001", "late", "late reply")])]
        passes += [a_pass([])] * 12
        _, thread, depth, _ = self.capture(passes, dry_rounds_to_stop=3)
        self.assertIn("2084000000000000001", thread["posts"])
        self.assertEqual(thread["gaps"], [])
        self.assertEqual(depth["replies_observed"], 3)

    def test_a_persistent_stall_is_declared_not_silently_accepted(self):
        passes = [a_pass(clay_first_pass())]
        passes += [a_pass([], at_bottom=False)] * 40
        _, thread, _, _ = self.capture(passes, stall_limit=5)
        self.assertIn("loading stalled before the end of the conversation",
                      thread["gaps"])

    def test_replies_arriving_during_the_loader_grace_reset_the_streak(self):
        # At the bottom, quiet is re-checked after a grace wait before it
        # counts: X appends there, so the first look is the unreliable one.
        late = article("2084000000000000009", "slow", "arrived late")
        passes = [a_pass(clay_first_pass())]
        passes += [a_pass([]), a_pass([late])]
        passes += [a_pass([])] * 12
        _, thread, _, _ = self.capture(passes, dry_rounds_to_stop=3)
        self.assertIn("2084000000000000009", thread["posts"])

    def test_no_articles_is_a_loud_failure(self):
        with self.assertRaises(X.ThreadCaptureError):
            self.capture([a_pass([])])

    def test_screenshots_taken_once_per_new_post(self):
        passes = [a_pass(clay_first_pass())] + [a_pass([])] * 5
        bridge, _, depth, shots = self.capture(passes, want_screenshots=True)
        self.assertEqual(sorted(shots), sorted(p["status"]
                                               for p in clay_first_pass()))
        self.assertEqual(len(bridge.shots), 5)
        self.assertEqual(depth["screenshots_taken"], 5)

    def test_already_held_screenshots_are_not_retaken(self):
        # Without this a tier-3 poll of a fifty-reply thread writes hundreds
        # of PNGs a day of images the archive already holds.
        held = frozenset(p["status"] for p in clay_first_pass())
        passes = [a_pass(clay_first_pass())] + [a_pass([])] * 5
        bridge, _, _, shots = self.capture(
            passes, want_screenshots=True, held_statuses=held)
        self.assertEqual(shots, {})
        self.assertEqual(bridge.shots, [])

    def test_a_held_post_is_reshot_when_its_text_changes(self):
        held = frozenset(p["status"] for p in clay_first_pass())
        edited = article("2083420933243433347", "KevinKelbie", "edited body")
        passes = [a_pass(clay_first_pass()), a_pass([edited])]
        passes += [a_pass([])] * 5
        bridge, _, _, shots = self.capture(
            passes, want_screenshots=True, held_statuses=held)
        self.assertEqual(list(shots), ["2083420933243433347"])
        self.assertEqual(bridge.shots, ["2083420933243433347"])

    def test_an_unframeable_post_is_skipped_not_approximated(self):
        # A mis-framed screenshot attributed to someone is worse than none.
        bridge = FakeBridge([a_pass(clay_first_pass())] + [a_pass([])] * 5,
                            isolate_ok=False)
        thread, depth, shots = X.capture_thread(
            "https://x.com/clay_garrett/status/" + FOCAL, FOCAL, AUTHOR,
            bridge=bridge, sleep=lambda _s: None, hydrate_seconds=0,
            settle_seconds=0, want_screenshots=True,
        )
        self.assertEqual(shots, {})
        self.assertEqual(depth["screenshots_taken"], 0)
        # The text is still a complete capture: a missing image never
        # silently reduces what the record holds.
        self.assertEqual(len(thread["posts"]), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
