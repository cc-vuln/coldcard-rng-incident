"""Tests for the post-cutover rotate-discovery compatibility command."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discovery_common as dc  # noqa: E402
import rotate_discovery as rd  # noqa: E402
from discovery_store import DiscoveryStore  # noqa: E402


def transaction_bytes(root: Path) -> dict[str, bytes]:
    transaction_root = root / "discovery/transactions"
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(transaction_root.rglob("*.json"))
    }


def event_count(store: DiscoveryStore) -> int:
    return sum(len(transaction["events"])
               for transaction in store.load_transactions())


class RotationCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.saved_root = dc.ROOT
        dc.ROOT = self.root
        marker = self.root / "discovery/migration-v1/manifest.json"
        marker.parent.mkdir(parents=True)
        marker.write_text('{\n  "schema": 1\n}\n', encoding="utf-8")
        self.store = DiscoveryStore(self.root)
        self.store.record_observation(
            {"url": "https://stacker.news/items/44", "title": "candidate",
             "display_line": ("- 2026-08-01 [candidate]"
                              "(https://stacker.news/items/44)")},
            event_at="20260801T000000Z",
        )
        self.store.record_verdict(
            "stackernews:44", "dismissed", reason="off topic",
            at="20260802T000000Z",
        )

    def tearDown(self):
        dc.ROOT = self.saved_root
        self.tmp.cleanup()

    def test_validates_then_renders_without_changing_history(self):
        history_before = transaction_bytes(self.root)
        with mock.patch.object(rd, "validate_migration", return_value={}) as check:
            written = rd.rotate(date(2026, 8, 14), keep_days=1)
        check.assert_called_once_with(self.root)
        self.assertIn("DISCOVERY.md", written)
        self.assertTrue((self.root / "DISCOVERY.md").exists())
        self.assertEqual(transaction_bytes(self.root), history_before)

    def test_age_options_no_longer_select_or_delete_history(self):
        with mock.patch.object(rd, "validate_migration", return_value={}):
            first = rd.rotate(date(2000, 1, 1), keep_days=0)
            second = rd.rotate(date(2099, 1, 1), keep_days=99999)
        self.assertEqual(first, second)
        self.assertEqual(self.store.count(state="assessed"), 1)
        self.assertEqual(event_count(self.store), 2)

    def test_dry_run_validates_without_rendering(self):
        with mock.patch.object(rd, "validate_migration", return_value={}) as check, \
                mock.patch.object(rd.DiscoveryStore, "render_all") as render:
            self.assertEqual(rd.rotate(dry_run=True), {})
        check.assert_called_once_with(self.root)
        render.assert_not_called()

    def test_validation_failure_prevents_render(self):
        with mock.patch.object(
                rd, "validate_migration", side_effect=ValueError("bad ledger")), \
                mock.patch.object(rd.DiscoveryStore, "render_all") as render:
            with self.assertRaisesRegex(ValueError, "bad ledger"):
                rd.rotate()
        render.assert_not_called()

    def test_main_reports_compatibility_result(self):
        output = io.StringIO()
        with mock.patch.object(sys, "argv", ["rotate_discovery.py", "--dry-run"]), \
                mock.patch.object(rd, "rotate", return_value={}), \
                redirect_stdout(output):
            self.assertEqual(rd.main(), 0)
        self.assertIn("no files written", output.getvalue())

    def test_main_reports_validation_error(self):
        error = io.StringIO()
        with mock.patch.object(sys, "argv", ["rotate_discovery.py"]), \
                mock.patch.object(rd, "rotate",
                                  side_effect=ValueError("bad migration")), \
                redirect_stderr(error):
            self.assertEqual(rd.main(), 2)
        self.assertIn("bad migration", error.getvalue())


if __name__ == "__main__":
    unittest.main()
