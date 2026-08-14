"""Focused tests for operator-drop intake through the structured store."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import queue_candidates as qc  # noqa: E402
from discovery_store import DiscoveryStore  # noqa: E402


def event_count(store: DiscoveryStore) -> int:
    return sum(len(transaction["events"])
               for transaction in store.load_transactions())


class QueueFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".work").mkdir()
        self.saved = qc.ROOT, qc.DROP
        qc.ROOT = self.root
        qc.DROP = self.root / ".work/operator-candidates.txt"
        marker = self.root / "discovery/migration-v1/manifest.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{\n  "schema": 1\n}\n', encoding="utf-8")
        self.store = DiscoveryStore(self.root)
        self.registry = {"meta": {}, "source": [], "x_post": [],
                         "nostr_post": [], "x_watch": []}
        self.load = mock.patch.object(
            qc.registry_store, "load", side_effect=lambda _root: self.registry)
        self.load.start()

    def tearDown(self):
        self.load.stop()
        qc.ROOT, qc.DROP = self.saved
        self.tmp.cleanup()

    def test_x_drop_is_canonical_pending_and_operator_priority(self):
        queued, left = qc.queue(
            ["https://twitter.com/Alice/status/123?ref=old"], "2026-08-14")
        self.assertEqual(queued, ["https://x.com/Alice/status/123"])
        self.assertEqual(left, [])
        candidate = self.store.load_candidate("x:123")
        self.assertEqual(candidate["state"], "pending")
        self.assertEqual(candidate["priority"], "operator")
        observation = candidate["observations"][-1]
        self.assertEqual(observation["foundAt"], "2026-08-14")
        self.assertIn("https://x.com/Alice/status/123",
                      observation["display_line"])

    def test_existing_deferred_candidate_is_forced_pending(self):
        self.store.record_observation(
            {"url": "https://stacker.news/items/44", "title": "quiet"},
            state="deferred", event_at="20260813T000000Z",
        )
        queued, _ = qc.queue(
            ["https://stacker.news/items/44"], "2026-08-14")
        self.assertEqual(queued, ["https://stacker.news/items/44"])
        candidate = self.store.load_candidate("stackernews:44")
        self.assertEqual(candidate["state"], "pending")
        self.assertEqual(candidate["priority"], "operator")

    def test_existing_pending_candidate_gains_operator_priority(self):
        self.store.record_observation(
            {"url": "https://stacker.news/items/44", "title": "lane result"},
            event_at="20260813T000000Z",
        )
        queued, _ = qc.queue(
            ["https://stacker.news/items/44"], "2026-08-14")
        self.assertEqual(len(queued), 1)
        self.assertEqual(
            self.store.load_candidate("stackernews:44")["priority"], "operator")

    def test_operator_priority_places_drop_before_lane_backlog(self):
        self.store.record_observation(
            {"url": "https://stacker.news/items/11", "title": "older lane row"},
            event_at="20260801T000000Z",
        )
        qc.queue(["https://stacker.news/items/44"], "2026-08-14")
        pending = self.store.list_candidates(state="pending")
        self.assertEqual(pending[0]["identity"], "stackernews:44")

    def test_assessed_drop_does_not_reopen_verdict(self):
        self.store.record_observation(
            {"url": "https://stacker.news/items/44", "title": "old"},
            event_at="20260813T000000Z",
        )
        self.store.record_verdict(
            "stackernews:44", "dismissed", reason="off topic",
            at="20260813T010000Z",
        )
        before = event_count(self.store)
        queued, left = qc.queue(
            ["https://stacker.news/items/44"], "2026-08-14")
        self.assertEqual((queued, left), ([], []))
        self.assertEqual(event_count(self.store), before)
        self.assertEqual(
            self.store.load_candidate("stackernews:44")["state"], "assessed")

    def test_two_url_spellings_share_one_native_identity(self):
        queued, left = qc.queue([
            "https://x.com/Alice/status/123",
            "https://twitter.com/Bob/status/123",
        ], "2026-08-14")
        self.assertEqual(queued, ["https://x.com/Alice/status/123"])
        self.assertEqual(left, [])
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(event_count(self.store), 1)

    def test_registered_native_identity_is_consumed_and_settles_queue(self):
        self.store.record_observation(
            {"url": "https://twitter.com/Alice/status/123", "title": "old"},
            event_at="20260813T000000Z",
        )
        self.registry["x_post"] = [{
            "id": "registered-post",
            "url": "https://x.com/DifferentHandle/status/123",
        }]
        queued, left = qc.queue(
            ["https://twitter.com/Alice/status/123"], "2026-08-14")
        self.assertEqual((queued, left), ([], []))
        candidate = self.store.load_candidate("x:123")
        self.assertEqual(candidate["state"], "assessed")
        self.assertEqual(candidate["verdict"]["kind"], "already-registered")
        self.assertEqual(candidate["verdict"]["source_id"], "registered-post")

    def test_unrecognised_url_is_left_for_a_person(self):
        url = "https://example.com/not-an-intake-platform"
        with mock.patch.object(
                qc.DiscoveryStore, "reconcile_observations") as reconcile:
            self.assertEqual(qc.queue([url], "2026-08-14"), ([], [url]))
        reconcile.assert_not_called()
        self.assertEqual(self.store.count(), 0)

    def test_main_spends_drop_file_only_after_store_success(self):
        recognised = "https://stacker.news/items/44"
        unknown = "https://example.com/unknown"
        qc.DROP.write_text(f"# note\n{recognised}\n{unknown}\n")
        self.assertEqual(qc.main(), 0)
        text = qc.DROP.read_text()
        self.assertNotIn(recognised, text)
        self.assertIn(unknown, text)
        self.assertIsNotNone(self.store.load_candidate("stackernews:44"))

        before = qc.DROP.read_text()
        with mock.patch.object(qc, "queue", side_effect=OSError("store failed")):
            with self.assertRaisesRegex(OSError, "store failed"):
                qc.main()
        self.assertEqual(qc.DROP.read_text(), before)


if __name__ == "__main__":
    unittest.main()
