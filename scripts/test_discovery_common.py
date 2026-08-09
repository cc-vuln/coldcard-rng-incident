"""Tests for the discovery plumbing every lane shares.

This code used to live in discover_stackernews.py and was reachable only
through a lane's main(), so it had no direct tests. It is now imported by
five lanes, which means a regression here is a regression in all of them at
once, and DISCOVERY.md is the one tracked file these scripts write.

What is worth pinning down, in order of how much damage getting it wrong
does:

- Assessed entries survive verbatim. They are the intake agent's record of a
  decision, and a lane that quietly dropped them would erase the reasoning
  behind every dismissal
- A candidate already sitting in Assessed is not re-queued. Without this a
  dismissed thread reappears on every run forever
- A thread that has since been registered is pruned from Pending
- persist_run advances the checkpoint and appends the raw log, and --no-state
  does neither
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discovery_common as dc  # noqa: E402


EXISTING = """# Discovery intake

Header prose the lanes must not disturb.

## Pending

- 2026-08-01 [now registered](https://stacker.news/items/111) by alice, 3 comments (~bitcoin)
- 2026-08-02 [still pending](https://stacker.news/items/222) by bob, 7 comments (~bitcoin)

## Assessed

- 2026-07-30 [dismissed thread](https://stacker.news/items/333) dismissed, off topic
"""


def community(item_id: str, title: str = "Coldcard RNG") -> dict:
    return {
        "id": item_id,
        "url": f"https://stacker.news/items/{item_id}",
        "sub": "bitcoin",
        "label": "~bitcoin",
        "title": title,
        "author": "carol",
        "createdAt": "2026-08-03T10:00:00Z",
        "ncomments": 5,
        "foundAt": "20260803T100000Z",
        "matched": True,
    }


class IntakeFixture:
    """A sandboxed DISCOVERY.md. Mixed into a TestCase rather than subclassed
    from one, so the deferral suite does not re-run the queue suite."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / ".work").mkdir()
        # Point the module's file constants at the sandbox. These are module
        # globals by design: the lanes all write one shared queue.
        self._saved = (dc.ROOT, dc.WORK, dc.INTAKE, dc.INTAKE_LOCK)
        dc.ROOT = root
        dc.WORK = root / ".work"
        dc.INTAKE = root / "DISCOVERY.md"
        dc.INTAKE_LOCK = root / ".work" / "agent-discovery-intake" / "intake.lock"
        dc.INTAKE.write_text(EXISTING, encoding="utf-8")
        self.root = root

    def tearDown(self):
        dc.ROOT, dc.WORK, dc.INTAKE, dc.INTAKE_LOCK = self._saved
        self.tmp.cleanup()

    def _section(self, heading: str) -> list[str]:
        text = dc.INTAKE.read_text(encoding="utf-8")
        return dc.section(dc.split_sections(text), heading)

    def pending(self) -> list[str]:
        return self._section(dc.PENDING_H)

    def assessed(self) -> list[str]:
        return self._section(dc.ASSESSED_H)

    def deferred(self) -> list[str]:
        return self._section(dc.DEFERRED_H)


class IntakeQueueTests(IntakeFixture, unittest.TestCase):
    def test_assessed_entries_survive_verbatim(self):
        dc.update_intake([community("444")], set())
        self.assertEqual(
            self.assessed(),
            ["- 2026-07-30 [dismissed thread](https://stacker.news/items/333) "
             "dismissed, off topic"],
        )

    def test_assessed_candidate_is_not_requeued(self):
        # The lane rediscovers a thread a human already dismissed.
        again = community("333", title="dismissed thread")
        dc.update_intake([again], set())
        self.assertNotIn(
            "https://stacker.news/items/333",
            "\n".join(self.pending()),
        )

    def test_registered_thread_is_pruned_from_pending(self):
        dc.update_intake([], {"https://stacker.news/items/111"})
        joined = "\n".join(self.pending())
        self.assertNotIn("https://stacker.news/items/111", joined)
        self.assertIn("https://stacker.news/items/222", joined)

    def test_new_candidate_is_appended_once(self):
        dc.update_intake([community("444")], set())
        dc.update_intake([community("444")], set())
        joined = "\n".join(self.pending())
        self.assertEqual(joined.count("https://stacker.news/items/444"), 1)

    def test_header_prose_is_preserved(self):
        dc.update_intake([community("444")], set())
        self.assertIn("Header prose the lanes must not disturb.",
                      dc.INTAKE.read_text(encoding="utf-8"))

    def test_rewrite_preserves_the_file_mode(self):
        # The intake agent reads and writes DISCOVERY.md through the group.
        # A rewrite that resets the mode to mkstemp's 0600 locks it out.
        import stat as stat_mod
        dc.INTAKE.chmod(0o660)
        dc.update_intake([community("444")], set())
        mode = stat_mod.S_IMODE(dc.INTAKE.stat().st_mode)
        self.assertEqual(mode, 0o660)

    def test_x_and_nostr_candidates_get_their_own_line_shapes(self):
        x = {"platform": "x", "url": "https://x.com/r/status/9",
             "label": "X @r", "title": "x post",
             "createdAt": "2026-08-03T11:00:00Z"}
        nostr = {"platform": "nostr", "url": "https://njump.me/note1abc",
                 "label": "nostr", "title": "nostr note", "author": "npub1xy",
                 "relayCount": 1, "createdAt": "2026-08-03T12:00:00Z"}
        # Neither carries ncomments; the community line shape would raise.
        self.assertEqual(dc.intake_line(x),
                         "- 2026-08-03 [x post](https://x.com/r/status/9) (X @r)")
        self.assertEqual(
            dc.intake_line(nostr),
            "- 2026-08-03 [nostr note](https://njump.me/note1abc) by npub1xy "
            "(1 known relay) (nostr)")

    def test_relay_count_is_pluralised(self):
        nostr = {"platform": "nostr", "url": "https://njump.me/note1abc",
                 "label": "nostr", "title": "n", "author": "npub1xy",
                 "relayCount": 3, "createdAt": "2026-08-03T12:00:00Z"}
        self.assertIn("(3 known relays)", dc.intake_line(nostr))


class TierTests(unittest.TestCase):
    """The two-tier vocabulary, which decides only where a candidate waits."""

    def test_tier_one_vocabulary_in_the_title(self):
        self.assertEqual(dc.match_tier("Coldcard MK4 dice question"), "strong")
        self.assertEqual(dc.match_tier("Is the RNG fixed yet?"), "strong")

    def test_tier_two_alone_is_topical(self):
        self.assertEqual(dc.match_tier("BIP39 or SLIP39 and why?"), None)
        self.assertEqual(dc.match_tier("Entropy and seed phrases"), "topical")

    def test_a_body_only_match_is_named_as_one(self):
        self.assertEqual(
            dc.match_tier("Cold storage options", "I moved off my coldcard"),
            "body")
        self.assertIsNone(dc.match_tier("Cold storage options", "no match here"))

    def test_deferral_needs_both_weak_signals(self):
        quiet_topical = community("1", title="Entropy questions")
        quiet_topical.update(ncomments=1, tier="topical")
        self.assertTrue(dc.should_defer(quiet_topical))
        # Either signal on its own is not enough.
        busy = dict(quiet_topical, ncomments=40)
        self.assertFalse(dc.should_defer(busy))
        named = dict(quiet_topical, tier="strong")
        self.assertFalse(dc.should_defer(named))

    def test_a_candidate_with_no_comment_count_is_never_deferred(self):
        # X and nostr have nothing comparable to read, so the rule abstains
        # rather than guessing.
        self.assertFalse(dc.should_defer(
            {"platform": "nostr", "tier": "topical", "ncomments": None}))


class DeferralQueueTests(IntakeFixture, unittest.TestCase):
    """Deferral is a waiting room, not a verdict: nothing leaves the record."""

    def quiet(self, item_id: str, ncomments: int = 1) -> dict:
        c = community(item_id, title="Entropy and seed phrases")
        c.update(ncomments=ncomments, tier="topical")
        return c

    def test_a_quiet_topical_candidate_waits_in_deferred(self):
        dc.update_intake([self.quiet("444")], set())
        self.assertEqual(len(self.deferred()), 1)
        self.assertNotIn("items/444", "\n".join(self.pending()))
        self.assertIn("[topical]", self.deferred()[0])

    def test_a_named_candidate_goes_straight_to_pending(self):
        c = community("555", title="Coldcard entropy bug")
        c.update(ncomments=0, tier="strong")
        dc.update_intake([c], set())
        self.assertIn("items/555", "\n".join(self.pending()))
        self.assertEqual(self.deferred(), [])

    def test_a_deferred_thread_promotes_itself_once_it_grows(self):
        dc.update_intake([self.quiet("444", ncomments=1)], set())
        self.assertEqual(len(self.deferred()), 1)
        # The lane re-reports it on a later run with a current count.
        grown = self.quiet("444", ncomments=30)
        dc.update_intake([grown], set())
        self.assertEqual(self.deferred(), [])
        self.assertIn("items/444", "\n".join(self.pending()))
        self.assertIn("30 comments", "\n".join(self.pending()))

    def test_re_reporting_refreshes_the_count_without_duplicating(self):
        dc.update_intake([self.quiet("444", ncomments=1)], set())
        dc.update_intake([self.quiet("444", ncomments=2)], set())
        self.assertEqual(len(self.deferred()), 1)
        self.assertIn("2 comments", self.deferred()[0])

    def test_a_registered_thread_is_pruned_from_deferred(self):
        dc.update_intake([self.quiet("444")], set())
        dc.update_intake([], {"https://stacker.news/items/444"})
        self.assertEqual(self.deferred(), [])

    def test_an_assessed_candidate_is_never_deferred(self):
        # A dismissed thread that a lane re-reports must not reappear in any
        # queue: that is the regression the Assessed check exists for.
        already = self.quiet("333")
        already["url"] = "https://stacker.news/items/333"
        dc.update_intake([already], set())
        self.assertEqual(self.deferred(), [])
        self.assertEqual(len(self.assessed()), 1)

    def test_deferred_urls_reports_what_the_lanes_must_re_report(self):
        dc.update_intake([self.quiet("444")], set())
        self.assertEqual(dc.deferred_urls(),
                         {"https://stacker.news/items/444"})

    def test_the_explanatory_note_is_written_once(self):
        dc.update_intake([self.quiet("444")], set())
        dc.update_intake([self.quiet("555")], set())
        text = dc.INTAKE.read_text(encoding="utf-8")
        self.assertEqual(text.count("Queued, but held back from the agent"), 1)

    def test_the_human_link_review_section_survives(self):
        dc.INTAKE.write_text(
            EXISTING + "\n## Link review, held for a human decision\n\n"
            "- 2026-08-05 [a link](https://x.com/a/1) (link review)\n",
            encoding="utf-8")
        dc.update_intake([self.quiet("444")], set())
        text = dc.INTAKE.read_text(encoding="utf-8")
        self.assertIn("## Link review, held for a human decision", text)
        self.assertIn("[a link](https://x.com/a/1)", text)
        # And a line already sitting there is not re-queued as a candidate.
        self.assertNotIn("x.com/a/1", "\n".join(self.pending()))


class PersistRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / ".work").mkdir()
        self._saved = (dc.ROOT, dc.WORK, dc.INTAKE, dc.INTAKE_LOCK)
        dc.ROOT = root
        dc.WORK = root / ".work"
        dc.INTAKE = root / "DISCOVERY.md"
        dc.INTAKE_LOCK = root / ".work" / "agent-discovery-intake" / "intake.lock"
        dc.INTAKE.write_text(EXISTING, encoding="utf-8")
        self.state_path = root / ".work" / "state.json"
        self.candidates_path = root / ".work" / "candidates.jsonl"

    def tearDown(self):
        dc.ROOT, dc.WORK, dc.INTAKE, dc.INTAKE_LOCK = self._saved
        self.tmp.cleanup()

    def run_persist(self, save: bool):
        state = {"seen": ["1"]}
        dc.persist_run(state=state, seen={"1", "444"},
                       candidates=[community("444")], known=set(),
                       state_path=self.state_path,
                       candidates_path=self.candidates_path, save=save)
        return state

    def test_saving_advances_checkpoint_and_logs(self):
        self.run_persist(save=True)
        self.assertEqual(
            json.loads(self.state_path.read_text())["seen"], ["1", "444"])
        logged = [json.loads(l) for l in
                  self.candidates_path.read_text().splitlines()]
        self.assertEqual([c["id"] for c in logged], ["444"])
        self.assertIn("items/444", dc.INTAKE.read_text(encoding="utf-8"))

    def test_no_state_writes_nothing(self):
        self.run_persist(save=False)
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.candidates_path.exists())
        self.assertEqual(dc.INTAKE.read_text(encoding="utf-8"), EXISTING)

    def test_queue_failure_does_not_advance_checkpoint_or_log(self):
        with mock.patch.object(
            dc, "update_intake", side_effect=TimeoutError("busy")
        ):
            with self.assertRaisesRegex(TimeoutError, "busy"):
                self.run_persist(save=True)
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.candidates_path.exists())

    def test_seen_set_is_trimmed_to_the_cap(self):
        state = {"seen": []}
        seen = {f"{n:07d}" for n in range(dc.SEEN_KEEP + 50)}
        dc.persist_run(state=state, seen=seen, candidates=[], known=set(),
                       state_path=self.state_path,
                       candidates_path=self.candidates_path, save=True)
        kept = json.loads(self.state_path.read_text())["seen"]
        self.assertEqual(len(kept), dc.SEEN_KEEP)
        # The newest ids are the ones kept, which is what stops a busy lane
        # from re-queueing what it just saw.
        self.assertEqual(kept[-1], f"{dc.SEEN_KEEP + 49:07d}")


class RegisteredUrlsTests(unittest.TestCase):
    def test_reads_the_named_table_and_skips_other_lanes(self):
        with tempfile.TemporaryDirectory() as raw:
            sources = Path(raw) / "sources.toml"
            sources.write_text(
                '[[source]]\nid = "a"\nurl = "https://stacker.news/items/1"\n\n'
                '[[source]]\nid = "b"\nurl = "https://www.reddit.com/r/x/comments/z/t/"\n\n'
                '[[nostr_post]]\nid = "c"\nurl = "https://njump.me/note1abc"\n',
                encoding="utf-8")
            saved = dc.SOURCES
            dc.SOURCES = sources
            try:
                sn = dc.registered_urls(
                    lambda u: u if "stacker.news" in u else None)
                notes = dc.registered_urls(lambda u: u, table="nostr_post")
            finally:
                dc.SOURCES = saved
        self.assertEqual(sn, {"https://stacker.news/items/1"})
        self.assertEqual(notes, {"https://njump.me/note1abc"})

    def test_entries_without_a_usable_url_are_skipped(self):
        with tempfile.TemporaryDirectory() as raw:
            sources = Path(raw) / "sources.toml"
            sources.write_text(
                '[[source]]\nid = "a"\nurl = ""\n\n'
                '[[source]]\nid = "b"\n\n'
                '[[source]]\nid = "c"\nurl = "https://stacker.news/items/9"\n',
                encoding="utf-8")
            saved = dc.SOURCES
            dc.SOURCES = sources
            try:
                found = dc.registered_urls(lambda u: u)
            finally:
                dc.SOURCES = saved
        self.assertEqual(found, {"https://stacker.news/items/9"})


if __name__ == "__main__":
    unittest.main()
