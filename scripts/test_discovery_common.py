"""Focused tests for the discovery plumbing shared by every producer lane."""

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discovery_common as dc  # noqa: E402
from discovery_store import DiscoveryStore  # noqa: E402


def event_count(store: DiscoveryStore) -> int:
    return sum(len(transaction["events"])
               for transaction in store.load_transactions())


def view_text(root: Path, state: str) -> str:
    directory = root / "discovery/views" / state
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted(directory.rglob("*.md")))


def community(item_id: str, title: str = "Coldcard RNG", *,
              ncomments: int = 5, found_at: str = "20260803T100000Z") -> dict:
    return {
        "id": item_id,
        "url": f"https://stacker.news/items/{item_id}",
        "sub": "bitcoin",
        "label": "~bitcoin",
        "title": title,
        "author": "carol",
        "createdAt": "2026-08-03T10:00:00Z",
        "ncomments": ncomments,
        "foundAt": found_at,
        "matched": True,
    }


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".work").mkdir()
        self.saved = dc.ROOT, dc.WORK, dc.SOURCES
        dc.ROOT = self.root
        dc.WORK = self.root / ".work"
        dc.SOURCES = self.root / "sources.toml"
        marker = self.root / "discovery/migration-v1/manifest.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{\n  "schema": 1\n}\n', encoding="utf-8")
        self.store = DiscoveryStore(self.root)

    def tearDown(self):
        dc.ROOT, dc.WORK, dc.SOURCES = self.saved
        self.tmp.cleanup()


class RenderingTests(unittest.TestCase):
    def test_x_and_nostr_candidates_get_their_own_line_shapes(self):
        x = {"platform": "x", "url": "https://x.com/r/status/9",
             "label": "X @r", "title": "x post",
             "createdAt": "2026-08-03T11:00:00Z"}
        nostr = {"platform": "nostr", "url": "https://njump.me/note1abc",
                 "label": "nostr", "title": "nostr note", "author": "npub1xy",
                 "relayCount": 1, "createdAt": "2026-08-03T12:00:00Z"}
        self.assertEqual(
            dc.intake_line(x),
            "- 2026-08-03 [x post](https://x.com/r/status/9) (X @r)",
        )
        self.assertEqual(
            dc.intake_line(nostr),
            "- 2026-08-03 [nostr note](https://njump.me/note1abc) by npub1xy "
            "(1 known relay) (nostr)",
        )

    def test_nostr_plural_and_operator_fallback(self):
        nostr = {"platform": "nostr", "url": "https://njump.me/note1abc",
                 "label": "nostr", "title": "n", "author": "npub1xy",
                 "relayCount": 3, "createdAt": "2026-08-03T12:00:00Z"}
        self.assertIn("(3 known relays)", dc.intake_line(nostr))
        operator = dict(nostr, relayCount=None, ncomments=0)
        self.assertIn("0 comments", dc.intake_line(operator))


class TierTests(unittest.TestCase):
    def test_match_tiers(self):
        self.assertEqual(dc.match_tier("Coldcard MK4 dice question"), "strong")
        self.assertEqual(dc.match_tier("Entropy and seed phrases"), "topical")
        self.assertEqual(
            dc.match_tier("Cold storage options", "I moved off my coldcard"),
            "body",
        )
        self.assertIsNone(dc.match_tier("Cold storage options", "no match"))

    def test_deferral_needs_both_weak_signals(self):
        quiet = community("1", "Entropy questions", ncomments=1)
        quiet["tier"] = "topical"
        self.assertTrue(dc.should_defer(quiet))
        self.assertFalse(dc.should_defer(dict(quiet, ncomments=40)))
        self.assertFalse(dc.should_defer(dict(quiet, tier="strong")))
        self.assertFalse(dc.should_defer(
            {"platform": "nostr", "tier": "topical", "ncomments": None}))


class ReconciliationTests(StoreFixture):
    def quiet(self, item_id: str, ncomments: int = 1, *,
              found_at: str = "20260803T100000Z") -> dict:
        candidate = community(
            item_id, "Entropy and seed phrases", ncomments=ncomments,
            found_at=found_at,
        )
        candidate["tier"] = "topical"
        return candidate

    def test_new_observation_keeps_its_display_line_and_renders(self):
        candidate = community("444")
        candidate["snippet"] = "ignored driver-side body excerpt"
        dc.update_intake([candidate], set())
        stored = self.store.load_candidate("stackernews:444")
        observation = stored["observations"][-1]
        display = observation["display_line"]
        self.assertEqual(display, dc.intake_line(candidate))
        self.assertNotIn("snippet", observation)
        self.assertEqual(stored["state"], "pending")
        self.assertIn(
            "https://stacker.news/items/444",
            view_text(self.root, "pending"),
        )

    def test_identical_replay_is_idempotent(self):
        candidate = community("444")
        dc.update_intake([candidate], set())
        dc.update_intake([candidate], set())
        self.assertEqual(event_count(self.store), 1)
        self.assertEqual(
            len(self.store.load_candidate("stackernews:444")["observations"]),
            1,
        )

    def test_one_lane_run_is_one_transactional_observation_batch(self):
        dc.update_intake([community("444"), community("555")], set())
        transactions = self.store.load_transactions()
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["kind"], "discovery-batch")
        self.assertEqual(len(transactions[0]["events"]), 2)

    def test_assessed_candidate_gains_observation_without_reopening(self):
        dc.update_intake([community("444")], set())
        self.store.record_verdict(
            "stackernews:444", "dismissed", reason="off topic",
            at="20260803T110000Z",
        )
        again = community("444", ncomments=9, found_at="20260804T100000Z")
        dc.update_intake([again], set())
        stored = self.store.load_candidate("stackernews:444")
        self.assertEqual(stored["state"], "assessed")
        self.assertEqual(len(stored["observations"]), 2)

    def test_registered_candidate_is_settled_explicitly(self):
        url = "https://stacker.news/items/444"
        dc.update_intake([community("444")], set())
        dc.update_intake([], {url: "stackernews-known"})
        stored = self.store.load_candidate("stackernews:444")
        self.assertEqual(stored["state"], "assessed")
        self.assertEqual(stored["verdict"]["kind"], "already-registered")
        self.assertEqual(stored["verdict"]["source_id"], "stackernews-known")
        self.assertNotIn(url, view_text(self.root, "pending"))

    def test_fresh_observation_precedes_known_url_settlement(self):
        url = "https://stacker.news/items/444"
        dc.update_intake([
            community("444", ncomments=1, found_at="20260801T100000Z")
        ], set())
        with mock.patch(
                "discovery_store.stamp_now",
                return_value="20260814T100000Z"):
            dc.update_intake([
                community("444", ncomments=9,
                          found_at="20260802T100000Z")
            ], {url: "stackernews-known"})
        stored = self.store.load_candidate("stackernews:444")
        self.assertEqual(stored["state"], "assessed")
        self.assertEqual(len(stored["observations"]), 2)
        self.assertEqual(stored["event_history"][-1]["type"], "verdict")
        self.assertEqual(stored["head"], stored["verdict"]["event_id"])
        self.assertEqual(stored["last_recorded"], "20260814T100000Z")

    def test_quiet_candidate_promotes_when_discussion_grows(self):
        dc.update_intake([self.quiet("444")], set())
        self.assertEqual(
            self.store.load_candidate("stackernews:444")["state"], "deferred")
        dc.update_intake([
            self.quiet("444", 30, found_at="20260804T100000Z")
        ], set())
        stored = self.store.load_candidate("stackernews:444")
        self.assertEqual(stored["state"], "pending")
        self.assertIn("30 comments", stored["observations"][-1]["display_line"])

    def test_deferred_urls_are_read_from_candidate_projections(self):
        dc.update_intake([self.quiet("444")], set())
        dc.update_intake([community("555")], set())
        self.assertEqual(
            dc.deferred_urls(), {"https://stacker.news/items/444"})


class PersistRunTests(StoreFixture):
    def setUp(self):
        super().setUp()
        self.state_path = self.root / ".work/state.json"
        self.candidates_path = self.root / ".work/candidates.jsonl"

    def run_persist(self, save: bool):
        state = {"seen": ["1"]}
        dc.persist_run(
            state=state, seen={"1", "444"}, candidates=[community("444")],
            known=set(), state_path=self.state_path,
            candidates_path=self.candidates_path, save=save,
        )
        return state

    def test_store_then_raw_log_then_checkpoint(self):
        self.run_persist(save=True)
        self.assertIsNotNone(
            DiscoveryStore(self.root).load_candidate("stackernews:444"))
        logged = [json.loads(line) for line in
                  self.candidates_path.read_text().splitlines()]
        self.assertEqual([candidate["id"] for candidate in logged], ["444"])
        self.assertEqual(
            json.loads(self.state_path.read_text())["seen"], ["1", "444"])

    def test_no_state_writes_nothing(self):
        self.run_persist(save=False)
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.candidates_path.exists())
        self.assertFalse((self.root / "discovery/transactions").exists())
        self.assertFalse((self.root / "discovery/candidates").exists())

    def test_store_failure_does_not_spend_raw_log_or_checkpoint(self):
        with mock.patch.object(
                dc, "update_intake", side_effect=TimeoutError("busy")):
            with self.assertRaisesRegex(TimeoutError, "busy"):
                self.run_persist(save=True)
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.candidates_path.exists())

    def test_raw_log_failure_does_not_advance_checkpoint(self):
        self.candidates_path = self.root / "missing/candidates.jsonl"
        with self.assertRaises(FileNotFoundError):
            self.run_persist(save=True)
        self.assertFalse(self.state_path.exists())
        self.assertIsNotNone(
            DiscoveryStore(self.root).load_candidate("stackernews:444"))

    def test_checkpoint_loss_replays_without_duplicate_transaction(self):
        self.run_persist(save=True)
        self.state_path.unlink()
        self.run_persist(save=True)
        self.assertEqual(len(self.store.load_transactions()), 1)
        self.assertEqual(event_count(self.store), 1)
        # The ignored diagnostic log may duplicate; the durable store may not.
        self.assertEqual(len(self.candidates_path.read_text().splitlines()), 2)
        self.assertTrue(self.state_path.exists())

    def test_seen_set_is_trimmed_to_the_cap(self):
        state = {"seen": []}
        seen = {f"{number:07d}" for number in range(dc.SEEN_KEEP + 50)}
        dc.persist_run(
            state=state, seen=seen, candidates=[], known=set(),
            state_path=self.state_path, candidates_path=self.candidates_path,
            save=True,
        )
        kept = json.loads(self.state_path.read_text())["seen"]
        self.assertEqual(len(kept), dc.SEEN_KEEP)
        self.assertEqual(kept[-1], f"{dc.SEEN_KEEP + 49:07d}")

    def test_checkpoint_replacement_preserves_mode(self):
        self.state_path.write_text("old\n")
        self.state_path.chmod(0o660)
        dc.atomic_text(self.state_path, "new\n")
        self.assertEqual(stat.S_IMODE(self.state_path.stat().st_mode), 0o660)


class RegisteredUrlsTests(unittest.TestCase):
    def test_reads_named_table_and_skips_unusable_urls(self):
        with tempfile.TemporaryDirectory() as raw:
            sources = Path(raw) / "sources.toml"
            sources.write_text(
                '[[source]]\nid = "a"\nurl = "https://stacker.news/items/1"\n\n'
                '[[source]]\nid = "b"\nurl = ""\n\n'
                '[[nostr_post]]\nid = "c"\nurl = "https://njump.me/note1abc"\n',
                encoding="utf-8",
            )
            saved = dc.SOURCES
            dc.SOURCES = sources
            try:
                sn = dc.registered_urls(
                    lambda url: url if "stacker.news" in url else None)
                notes = dc.registered_urls(lambda url: url, table="nostr_post")
            finally:
                dc.SOURCES = saved
        self.assertEqual(sn, {"https://stacker.news/items/1": "a"})
        self.assertEqual(notes, {"https://njump.me/note1abc": "c"})


if __name__ == "__main__":
    unittest.main()
