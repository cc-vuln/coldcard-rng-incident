import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_x_availability.py")
sys.path.insert(0, str(SCRIPT.parent))

import check_x_availability as cxa  # noqa: E402

FIXED_NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
STAMP = "20260808T120000Z"


def probe(**overrides):
    base = {
        "url": "https://x.com/Author/status/2084000000000001234",
        "loginForm": False,
        "wallText": False,
        "arkose": False,
        "challengeText": False,
        "rateText": False,
        "suspended": False,
        "protected": False,
        "restricted": False,
        "deletedNotice": False,
        "errorDetail": False,
        "target": False,
        "articles": 0,
        "chrome": False,
    }
    base.update(overrides)
    return base


class ClassificationTests(unittest.TestCase):
    def test_target_article_is_ok(self):
        self.assertEqual(
            cxa.classify_availability(probe(target=True, articles=1,
                                            chrome=True)),
            "ok",
        )

    def test_deleted_notice(self):
        # X's own "this page doesn't exist" error cell on the permalink.
        self.assertEqual(
            cxa.classify_availability(probe(deletedNotice=True,
                                            errorDetail=True, chrome=True)),
            "deleted",
        )

    def test_deleted_notice_without_error_cell(self):
        # "This Post was deleted by the Post author" renders inside context.
        self.assertEqual(
            cxa.classify_availability(probe(deletedNotice=True, chrome=True)),
            "deleted",
        )

    def test_suspended_account(self):
        self.assertEqual(
            cxa.classify_availability(probe(suspended=True, chrome=True)),
            "suspended",
        )

    def test_suspended_beats_deleted_text(self):
        # A suspended account's permalink serves generic absence text too;
        # the account state is the truer reading and must win.
        self.assertEqual(
            cxa.classify_availability(probe(suspended=True, deletedNotice=True,
                                            chrome=True)),
            "suspended",
        )

    def test_protected_account(self):
        self.assertEqual(
            cxa.classify_availability(probe(protected=True, chrome=True)),
            "protected",
        )

    def test_restricted_post(self):
        self.assertEqual(
            cxa.classify_availability(probe(restricted=True, chrome=True)),
            "restricted",
        )

    def test_error_cell_without_known_notice_is_unavailable_other(self):
        self.assertEqual(
            cxa.classify_availability(probe(errorDetail=True, chrome=True)),
            "unavailable-other",
        )

    def test_rendered_page_without_the_post_is_unavailable_other(self):
        # The page rendered fine, the post is not on it, and no notice X
        # prints was matched: recorded, never called a deletion outright.
        self.assertEqual(
            cxa.classify_availability(probe(articles=3, chrome=True)),
            "unavailable-other",
        )

    def test_chrome_only_is_unavailable_other(self):
        self.assertEqual(
            cxa.classify_availability(probe(chrome=True)),
            "unavailable-other",
        )

    def test_empty_page_fails_closed_as_challenge(self):
        # Same posture as x_browser.classify_session: an unrecognized empty
        # read is a session symptom, never an absence.
        self.assertEqual(cxa.classify_availability(probe()), "challenge")

    def test_login_wall_masks_everything(self):
        self.assertEqual(
            cxa.classify_availability(probe(loginForm=True, target=True,
                                            suspended=True, rateText=True)),
            "login-wall",
        )

    def test_login_redirect_is_a_login_wall(self):
        self.assertEqual(
            cxa.classify_availability(probe(url="https://x.com/i/flow/login")),
            "login-wall",
        )

    def test_challenge_masks_post_classes(self):
        self.assertEqual(
            cxa.classify_availability(probe(arkose=True, deletedNotice=True)),
            "challenge",
        )

    def test_challenge_masks_rate_limit(self):
        self.assertEqual(
            cxa.classify_availability(probe(challengeText=True, rateText=True)),
            "challenge",
        )

    def test_rate_limit_masks_post_classes(self):
        self.assertEqual(
            cxa.classify_availability(probe(rateText=True, target=True,
                                            articles=1)),
            "rate-limit",
        )

    def test_session_classes_beat_account_classes(self):
        for session_signal in ("wallText", "arkose", "rateText"):
            outcome = cxa.classify_availability(
                probe(**{session_signal: True}, suspended=True, chrome=True))
            self.assertIn(outcome, cxa.SESSION_CLASSES)


class AdvanceTests(unittest.TestCase):
    def test_ok_resets_the_streak(self):
        prior = {"last_check": "20260807T120000Z", "last_outcome": "deleted",
                 "streak": 1}
        entry = cxa.advance_post(prior, "ok", STAMP)
        self.assertEqual(entry["streak"], 0)
        self.assertEqual(entry["last_outcome"], "ok")
        self.assertEqual(entry["last_check"], STAMP)

    def test_first_absence_starts_the_streak(self):
        entry = cxa.advance_post({}, "deleted", STAMP)
        self.assertEqual(entry["streak"], 1)

    def test_consecutive_absences_increment(self):
        prior = {"last_outcome": "deleted", "streak": 1}
        entry = cxa.advance_post(prior, "deleted", STAMP)
        self.assertEqual(entry["streak"], 2)

    def test_different_absence_classes_share_the_streak(self):
        # deleted then unavailable-other is still one withdrawal unfolding.
        prior = {"last_outcome": "deleted", "streak": 1}
        entry = cxa.advance_post(prior, "unavailable-other", STAMP)
        self.assertEqual(entry["streak"], 2)

    def test_account_state_breaks_the_streak(self):
        # A protected account is not a withdrawal; it neither increments
        # nor carries the absence streak.
        prior = {"last_outcome": "deleted", "streak": 1}
        entry = cxa.advance_post(prior, "protected", STAMP)
        self.assertEqual(entry["streak"], 0)
        followup = cxa.advance_post(entry, "deleted", STAMP)
        self.assertEqual(followup["streak"], 1)

    def test_malformed_prior_streak_is_not_fatal(self):
        prior = {"last_outcome": "deleted", "streak": "many"}
        entry = cxa.advance_post(prior, "deleted", STAMP)
        self.assertEqual(entry["streak"], 1)


class AlertDecisionTests(unittest.TestCase):
    def test_first_absence_alerts_info(self):
        entry = cxa.advance_post({}, "deleted", STAMP)
        severity, reason = cxa.alert_decision({}, entry)
        self.assertEqual(severity, "info")
        self.assertIn("single absence", reason)

    def test_second_consecutive_absence_escalates(self):
        prior = {"last_outcome": "deleted", "streak": 1}
        entry = cxa.advance_post(prior, "deleted", STAMP)
        severity, reason = cxa.alert_decision(prior, entry)
        self.assertEqual(severity, "warning")
        self.assertIn("twice", reason)
        self.assertIn("likely withdrawn", reason)

    def test_ok_never_alerts(self):
        entry = cxa.advance_post({}, "ok", STAMP)
        self.assertIsNone(cxa.alert_decision({}, entry))

    def test_account_state_transition_alerts_info_once(self):
        entry = cxa.advance_post({}, "suspended", STAMP)
        severity, reason = cxa.alert_decision({}, entry)
        self.assertEqual(severity, "info")
        self.assertIn("suspended", reason)

    def test_unchanged_account_state_does_not_realert(self):
        prior = {"last_outcome": "suspended", "streak": 0}
        entry = cxa.advance_post(prior, "suspended", STAMP)
        self.assertIsNone(cxa.alert_decision(prior, entry))

    def test_account_state_never_escalates_to_warning(self):
        prior = {"last_outcome": "protected", "streak": 0}
        entry = cxa.advance_post(prior, "protected", STAMP)
        decision = cxa.alert_decision(prior, entry)
        self.assertTrue(decision is None or decision[0] == "info")


class SelectDueTests(unittest.TestCase):
    posts = [
        {"id": "a", "handle": "h", "status": "1", "url": "u"},
        {"id": "b", "handle": "h", "status": "2", "url": "u"},
        {"id": "c", "handle": "h", "status": "3", "url": "u"},
    ]

    def state(self, **entries):
        return {"version": 1, "posts": entries}

    def test_never_checked_posts_are_due_first(self):
        state = self.state(
            b={"last_check": "20260807T120000Z", "last_outcome": "ok",
               "streak": 0},
        )
        due = cxa.select_due(self.posts, state, FIXED_NOW, 24, 25)
        self.assertEqual([post["id"] for post in due], ["a", "c", "b"])

    def test_recently_checked_posts_are_not_due(self):
        state = self.state(
            a={"last_check": "20260808T060000Z", "last_outcome": "ok",
               "streak": 0},
        )
        due = cxa.select_due(self.posts, state, FIXED_NOW, 24, 25)
        self.assertEqual([post["id"] for post in due], ["b", "c"])

    def test_least_recently_checked_order(self):
        state = self.state(
            a={"last_check": "20260807T120000Z", "last_outcome": "ok",
               "streak": 0},
            b={"last_check": "20260806T120000Z", "last_outcome": "ok",
               "streak": 0},
            c={"last_check": "20260807T000000Z", "last_outcome": "ok",
               "streak": 0},
        )
        due = cxa.select_due(self.posts, state, FIXED_NOW, 24, 2)
        self.assertEqual([post["id"] for post in due], ["b", "c"])

    def test_limit_bounds_the_run(self):
        due = cxa.select_due(self.posts, self.state(), FIXED_NOW, 24, 1)
        self.assertEqual(len(due), 1)

    def test_nothing_due_inside_the_cadence(self):
        stamp = (FIXED_NOW - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ")
        state = self.state(
            **{post["id"]: {"last_check": stamp, "last_outcome": "ok",
                            "streak": 0}
               for post in self.posts}
        )
        self.assertEqual(cxa.select_due(self.posts, state, FIXED_NOW, 24, 25),
                         [])


if __name__ == "__main__":
    unittest.main()
