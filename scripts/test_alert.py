"""Tests for the alert writer.

The properties worth pinning down are the ones a regression would silently
break:

- The same key is never appended twice inside the window, and is appended
  again once the window has passed
- Streak thresholds fire per diagnosis family (content-* at 2,
  dns-unresolved at 4, origin-* at 6) against stubbed diagnose JSON, with
  no repo state involved
- The idempotency check reads the tail from EOF: a large file's early lines
  are never touched, and a key sitting just inside the window is still seen
- Tests only ever write into a temp dir via CC_ALERT_STATE_DIR; the real
  alerts.jsonl is never touched
"""

import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import alert  # noqa: E402

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)


def stub_row(sid, diagnosis, streak):
    return {"id": sid, "diagnosis": diagnosis, "streak": streak,
            "failing_since": "20260808T000000Z", "detail": ""}


class StreakThresholdTests(unittest.TestCase):
    def test_content_fires_at_two(self):
        alerts = alert.streak_alerts(
            [stub_row("a", "content-below-floor", 2)], NOW)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "failure-streak")
        self.assertEqual(alerts[0]["severity"], "warning")
        self.assertIn("a", alerts[0]["key"])

    def test_content_below_two_is_quiet(self):
        self.assertEqual(alert.streak_alerts(
            [stub_row("a", "content-marker-missing", 1)], NOW), [])

    def test_dns_fires_at_four(self):
        self.assertEqual(alert.streak_alerts(
            [stub_row("a", "dns-unresolved", 3)], NOW), [])
        self.assertEqual(len(alert.streak_alerts(
            [stub_row("a", "dns-unresolved", 4)], NOW)), 1)

    def test_origin_fires_at_six(self):
        self.assertEqual(alert.streak_alerts(
            [stub_row("a", "origin-challenge", 5)], NOW), [])
        self.assertEqual(len(alert.streak_alerts(
            [stub_row("a", "origin-challenge", 6)], NOW)), 1)

    def test_other_diagnoses_never_fire(self):
        for diagnosis in ("connect-timeout", "browser-unavailable",
                          "unrecorded", "unknown"):
            self.assertEqual(alert.streak_alerts(
                [stub_row("a", diagnosis, 99)], NOW), [], diagnosis)


class AlertTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("CC_ALERT_STATE_DIR")
        os.environ["CC_ALERT_STATE_DIR"] = self.tmp.name
        self.path = Path(self.tmp.name) / alert.ALERTS_NAME

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CC_ALERT_STATE_DIR", None)
        else:
            os.environ["CC_ALERT_STATE_DIR"] = self._saved
        self.tmp.cleanup()

    def read_alerts(self):
        if not self.path.exists():
            return []
        return [json.loads(l) for l in
                self.path.read_text(encoding="utf-8").splitlines() if l.strip()]


class IdempotencyTests(AlertTestBase):
    def test_same_key_suppressed_within_window(self):
        self.assertTrue(alert.emit("capture-failure", "warning", "k1",
                                   "first", now=NOW))
        self.assertFalse(alert.emit("capture-failure", "warning", "k1",
                                    "second", now=NOW + timedelta(hours=1)))
        alerts = self.read_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["summary"], "first")

    def test_same_key_appends_after_window(self):
        self.assertTrue(alert.emit("capture-failure", "warning", "k1",
                                   "first", now=NOW))
        later = NOW + timedelta(hours=25)
        self.assertTrue(alert.emit("capture-failure", "warning", "k1",
                                   "second", now=later))
        self.assertEqual(len(self.read_alerts()), 2)

    def test_distinct_keys_do_not_suppress_each_other(self):
        self.assertTrue(alert.emit("capture-failure", "warning", "k1",
                                   "one", now=NOW))
        self.assertTrue(alert.emit("capture-failure", "warning", "k2",
                                   "two", now=NOW))
        self.assertEqual(len(self.read_alerts()), 2)

    def test_shorter_window_expires_sooner(self):
        self.assertTrue(alert.emit("unit-failure", "warning", "k1", "one",
                                   window_hours=1, now=NOW))
        self.assertTrue(alert.emit("unit-failure", "warning", "k1", "two",
                                   window_hours=1,
                                   now=NOW + timedelta(hours=2)))
        self.assertEqual(len(self.read_alerts()), 2)

    def test_record_shape(self):
        alert.emit("guard-rejection", "urgent", "k1", "summary",
                   detail="evidence", now=NOW)
        (record,) = self.read_alerts()
        self.assertEqual(record["ts"], "20260808T120000Z")
        self.assertEqual(record["key"], "k1")
        self.assertEqual(record["severity"], "urgent")
        self.assertEqual(record["kind"], "guard-rejection")
        self.assertEqual(record["summary"], "summary")
        self.assertEqual(record["detail"], "evidence")

    def test_detail_omitted_when_absent(self):
        alert.emit("host-admission", "info", "k1", "summary", now=NOW)
        (record,) = self.read_alerts()
        self.assertNotIn("detail", record)


class TailReadTests(AlertTestBase):
    def test_key_found_far_back_in_a_large_file(self):
        # A file of several MB must still surface a key sitting at its
        # start, because the scan keeps walking back until it crosses the
        # window edge.
        now = NOW
        with open(self.path, "w", encoding="utf-8") as fh:
            first = {"ts": "20260808T000000Z", "key": "old-key",
                     "severity": "info", "kind": "host-admission",
                     "summary": "early"}
            fh.write(json.dumps(first) + "\n")
            filler = {"ts": "20260808T060000Z", "key": "filler",
                      "severity": "info", "kind": "capture-failure",
                      "summary": "x" * 200}
            for _ in range(20000):
                fh.write(json.dumps(filler) + "\n")
        self.assertGreater(self.path.stat().st_size, 4 * 1024 * 1024)
        self.assertFalse(alert.emit("host-admission", "info", "old-key",
                                    "duplicate", now=now))
        # And a new key appends at the end, leaving every prior line intact.
        self.assertTrue(alert.emit("host-admission", "info", "new-key",
                                   "fresh", now=now))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 20002)
        self.assertEqual(json.loads(lines[-1])["key"], "new-key")

    def test_scan_stops_at_the_window_edge(self):
        # An alert older than the window must not suppress: the scan stops
        # at it, so its key is treated as free again.
        old = {"ts": "20260807T000000Z", "key": "k1", "severity": "info",
               "kind": "capture-failure", "summary": "old"}
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(old) + "\n")
        self.assertTrue(alert.emit("capture-failure", "info", "k1",
                                   "fresh", now=NOW))
        self.assertEqual(len(self.read_alerts()), 2)

    def test_corrupt_lines_are_skipped(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write('{"ts": "20260808T110000Z", "key": "k1", '
                     '"severity": "info", "kind": "capture-failure", '
                     '"summary": "ok"}\n')
            fh.write("{truncated\n")
        self.assertFalse(alert.emit("capture-failure", "info", "k1",
                                    "dup", now=NOW))

    def test_empty_and_missing_files(self):
        self.assertEqual(alert._recent_keys(io.BytesIO(b""), 0, NOW), set())
        self.assertTrue(alert.emit("capture-failure", "info", "k1",
                                   "first ever", now=NOW))


if __name__ == "__main__":
    unittest.main()
