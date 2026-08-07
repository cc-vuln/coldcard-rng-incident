"""Tests for DISCOVERY.md verdict rotation.

The queue file is shared with five lanes and two agent prompts, so the
properties worth pinning down are the ones a regression would silently
break:

- A rotated line is byte-identical to the line that sat in the queue, and
  a kept line — pending or assessed — is not touched
- A verdict without a stamp never rotates: there is no assessment date to
  file it under
- A corrected verdict (two stamps) is filed by its last stamp
- Month files are append-only and dedupe, so a restore-and-rerun cannot
  duplicate a line
- A run with nothing old enough writes nothing at all
"""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discovery_common as dc  # noqa: E402
import rotate_discovery as rd  # noqa: E402

QUEUE = """# Discovery intake

Header prose the rotation must not disturb.

## Pending

- 2026-08-06 [pending thread](https://stacker.news/items/111) by alice, 3 comments (~bitcoin)

## Assessed

- 2026-08-05 [fresh verdict](https://stacker.news/items/222) by bob, 7 comments (~bitcoin) -> dismissed: repetitive (20260806T080000Z)

- 2026-07-02 [old verdict](https://stacker.news/items/333) by carol, 1 comments (~bitcoin) -> registered as stackernews-old (20260702T090000Z)
- 2026-07-03 [corrected verdict](https://stacker.news/items/444) by dan, 2 comments (~bitcoin) -> dismissed: first pass (20260703T090000Z); corrected on re-check (20260801T090000Z)
- 2026-06-01 [hand dismissed](https://stacker.news/items/555) by eve, 0 comments (~bitcoin) -> dismissed: no stamp here
"""


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / ".work").mkdir()
        self._saved = (dc.ROOT, dc.WORK, dc.INTAKE, dc.INTAKE_LOCK)
        dc.ROOT = root
        dc.WORK = root / ".work"
        dc.INTAKE = root / "DISCOVERY.md"
        dc.INTAKE_LOCK = root / ".work" / "agent-discovery-intake" / "intake.lock"
        dc.INTAKE.write_text(QUEUE, encoding="utf-8")
        self.root = root

    def tearDown(self):
        dc.ROOT, dc.WORK, dc.INTAKE, dc.INTAKE_LOCK = self._saved
        self.tmp.cleanup()

    def queue_text(self) -> str:
        return dc.INTAKE.read_text(encoding="utf-8")

    def test_old_verdict_moves_to_its_month_file_verbatim(self):
        moved = rd.rotate(date(2026, 8, 7), keep_days=31)
        self.assertEqual(sorted(moved), ["2026-07"])
        line = moved["2026-07"][0]
        self.assertIn("https://stacker.news/items/333", line)
        month_file = (self.root / "discovery" / "assessed-2026-07.md")
        self.assertIn(line, month_file.read_text(encoding="utf-8").splitlines())
        self.assertNotIn("items/333", self.queue_text())

    def test_recent_pending_and_unstamped_lines_stay(self):
        rd.rotate(date(2026, 8, 7), keep_days=31)
        text = self.queue_text()
        self.assertIn("items/111", text)  # pending is never rotation's business
        self.assertIn("items/222", text)  # fresh verdict
        self.assertIn("items/555", text)  # no stamp: no date to file under

    def test_corrected_verdict_is_filed_by_its_last_stamp(self):
        # The first stamp (July) is old enough to rotate; the correction
        # (1 August) is not, so the line stays.
        moved = rd.rotate(date(2026, 8, 7), keep_days=31)
        self.assertNotIn("items/444", "\n".join(moved.get("2026-07", [])))
        self.assertIn("items/444", self.queue_text())

    def test_header_and_pending_survive_verbatim(self):
        rd.rotate(date(2026, 8, 7), keep_days=31)
        head = self.queue_text().split("## Assessed", 1)[0]
        self.assertEqual(head, QUEUE.split("## Assessed", 1)[0])

    def test_month_file_dedupes_a_second_rotation(self):
        rd.rotate(date(2026, 8, 7), keep_days=31)
        # The line comes back, as it would after a restore from backup.
        with dc.INTAKE.open("a", encoding="utf-8") as fh:
            fh.write("- 2026-07-02 [old verdict](https://stacker.news/items/333) "
                     "by carol, 1 comments (~bitcoin) -> registered as "
                     "stackernews-old (20260702T090000Z)\n")
        rd.rotate(date(2026, 8, 7), keep_days=31)
        body = (self.root / "discovery" / "assessed-2026-07.md").read_text()
        self.assertEqual(body.count("items/333"), 1)

    def test_nothing_old_enough_writes_nothing(self):
        before = self.queue_text()
        moved = rd.rotate(date(2026, 7, 5), keep_days=31)
        self.assertEqual(moved, {})
        self.assertEqual(self.queue_text(), before)
        self.assertFalse((self.root / "discovery").exists())

    def test_dry_run_reports_without_writing(self):
        before = self.queue_text()
        moved = rd.rotate(date(2026, 8, 7), keep_days=31, dry_run=True)
        self.assertEqual(sorted(moved), ["2026-07"])
        self.assertEqual(self.queue_text(), before)
        self.assertFalse((self.root / "discovery").exists())

    def test_missing_assessed_section_fails_visibly(self):
        dc.INTAKE.write_text("# Discovery intake\n\n## Pending\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            rd.rotate(date(2026, 8, 7), keep_days=31)

    def test_later_sections_are_not_verdicts(self):
        # "Link review, held for a human decision" follows Assessed in the
        # live queue. Its lines are not verdicts: even a stamped one must
        # survive, byte for byte.
        third = ("## Link review, held for a human decision\n\n"
                 "- [a link](https://example.com/a) held for review "
                 "(20260101T000000Z)\n")
        dc.INTAKE.write_text(QUEUE.rstrip("\n") + "\n\n" + third, encoding="utf-8")
        rd.rotate(date(2026, 8, 7), keep_days=31)
        text = self.queue_text()
        self.assertIn(third.rstrip("\n"), text)
        self.assertNotIn("items/333", text)
        self.assertFalse((self.root / "discovery" / "assessed-2026-01.md").exists())


if __name__ == "__main__":
    unittest.main()
