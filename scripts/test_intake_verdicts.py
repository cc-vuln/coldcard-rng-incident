#!/usr/bin/env python3
"""Focused tests for the guarded legacy intake-verdict handoff."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apply_intake_verdicts as applier
import intake_verdicts


OLD = """\
[meta]
incident = "fixture"

[[source]]
id = "reddit-old"
title = "Old"
url = "https://www.reddit.com/r/test/comments/old111/old/"
org = "reddit"
"""

NEW = OLD + """\

[[source]]
id = "reddit-new"
title = "New"
url = "https://www.reddit.com/r/test/comments/new111/new/"
org = "reddit"
"""

LINE_A = "- 2026-08-13 [A](https://www.reddit.com/r/test/comments/new111/new/) by a, 3 comments (r/test)"
LINE_B = "- 2026-08-13 [B](https://stacker.news/items/222) by b, 3 comments (Stacker News)"


def row(candidate: str, action: str, reason: str = "fixture reason",
        source_id: str | None = None, at: str = "20260813T120000Z") -> dict:
    value = {"schema_version": 1, "candidate_id": candidate,
             "action": action, "reason": reason, "at": at}
    if source_id is not None:
        value["source_id"] = source_id
    return value


class VerdictCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        (self.run / "before").mkdir(parents=True)
        self.before = self.run / "before/sources.toml"
        self.after = self.root / "sources.toml"
        self.packet = self.run / "intake-packet.json"
        self.outbox = self.run / "intake-verdicts.jsonl"
        self.approval = self.run / "approved-captures.txt"
        self.intake = self.root / "DISCOVERY.md"
        self.before.write_text(OLD, encoding="utf-8")
        self.after.write_text(NEW, encoding="utf-8")
        self.approval.write_text("", encoding="utf-8")
        self.intake.write_text(
            "# Discovery\n\n## Pending\n\n" + LINE_A + "\n" + LINE_B +
            "\n\n## Assessed\n\n- an old verdict\n\n"
            "## Link review, held for a human decision\n\n- held line\n",
            encoding="utf-8")
        self.write_packet([
            ("reddit:new111", "reddit:submission:new111", LINE_A, []),
            ("stackernews:222", "stackernews:item:222", LINE_B, []),
        ])

    def write_packet(self, candidates: list[tuple[str, str, str, list[str]]]) -> None:
        value = {"schema_version": 1, "lane": "community", "candidates": [
            {"candidate_id": ident, "external_key": key, "queue_line": line,
             "registry_exact_match": {"matched": bool(exact),
                                      "source_ids": exact}}
            for ident, key, line, exact in candidates
        ]}
        self.packet.write_text(json.dumps(value), encoding="utf-8")

    def write_rows(self, rows: list[dict]) -> None:
        self.outbox.write_text(
            "".join(json.dumps(value) + "\n" for value in rows),
            encoding="utf-8")

    def validate(self) -> list[dict]:
        return intake_verdicts.validate_paths(
            packet_path=self.packet, outbox_path=self.outbox,
            before_registry_path=self.before,
            after_registry_path=self.after)

    def apply(self) -> tuple[int, int]:
        return applier.apply(
            intake_path=self.intake, packet_path=self.packet,
            verdict_path=self.outbox, before_registry_path=self.before,
            after_registry_path=self.after, approval_path=self.approval)

    def test_applier_moves_terminal_and_leaves_retry_pending(self) -> None:
        self.write_rows([
            row("reddit:new111", "dismissed", "not incident material"),
            row("stackernews:222", "retry", "body unavailable"),
        ])
        self.assertEqual((1, 1), self.apply())
        text = self.intake.read_text(encoding="utf-8")
        pending = text.split("## Pending", 1)[1].split("## Assessed", 1)[0]
        assessed = text.split("## Assessed", 1)[1].split("## Link review", 1)[0]
        self.assertNotIn(LINE_A, pending)
        self.assertIn(LINE_B, pending)
        self.assertIn(LINE_A + " -> dismissed: not incident material ", assessed)
        self.assertIn("- held line", text)

    def test_output_order_is_packet_order_not_outbox_order(self) -> None:
        self.write_rows([
            row("stackernews:222", "dismissed", "second"),
            row("reddit:new111", "dismissed", "first"),
        ])
        normalized = self.validate()
        self.assertEqual(["reddit:new111", "stackernews:222"],
                         [value["candidate_id"] for value in normalized])
        self.apply()
        assessed = self.intake.read_text().split("## Assessed", 1)[1]
        self.assertLess(assessed.index(LINE_A), assessed.index(LINE_B))

    def test_missing_guard_marker_writes_nothing(self) -> None:
        self.write_rows([row("reddit:new111", "dismissed"),
                         row("stackernews:222", "retry")])
        before = self.intake.read_bytes()
        self.approval.unlink()
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "approval marker"):
            self.apply()
        self.assertEqual(before, self.intake.read_bytes())

    def test_registered_must_name_same_native_object(self) -> None:
        wrong = NEW.replace("comments/new111/new", "comments/other999/other")
        self.after.write_text(wrong, encoding="utf-8")
        self.write_rows([row("reddit:new111", "registered", source_id="reddit-new"),
                         row("stackernews:222", "retry")])
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "different native object"):
            self.validate()

    def test_one_new_source_cannot_settle_two_packet_candidates(self) -> None:
        self.write_packet([
            ("reddit:new111", "reddit:submission:new111", LINE_A, []),
            ("reddit:new111-alias", "reddit:submission:new111", LINE_B, []),
        ])
        self.write_rows([
            row("reddit:new111", "registered", source_id="reddit-new"),
            row("reddit:new111-alias", "registered", source_id="reddit-new"),
        ])
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "more than one verdict"):
            self.validate()

    def test_exact_match_cannot_be_registered_again(self) -> None:
        self.write_packet([
            ("reddit:old111", "reddit:submission:old111", LINE_A,
             ["reddit-old"]),
        ])
        self.write_rows([row("reddit:old111", "registered",
                             source_id="reddit-new")])
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "already has an exact"):
            self.validate()

    def test_already_registered_requires_packet_exact_match(self) -> None:
        self.write_rows([row("reddit:new111", "already-registered",
                             source_id="reddit-old"),
                         row("stackernews:222", "retry")])
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "not an exact packet match"):
            self.validate()

    def test_impossible_time_and_em_dash_are_rejected(self) -> None:
        for bad, pattern in [
            (row("reddit:new111", "retry", at="20260230T120000Z"),
             "impossible UTC"),
            (row("reddit:new111", "retry", reason="wait — body missing"),
             "em-dash"),
        ]:
            with self.subTest(pattern=pattern):
                self.write_rows([bad, row("stackernews:222", "retry")])
                with self.assertRaisesRegex(intake_verdicts.VerdictError, pattern):
                    self.validate()

    def test_validation_failure_is_all_or_nothing(self) -> None:
        self.write_rows([row("reddit:new111", "dismissed"),
                         row("stackernews:222", "retry")])
        self.intake.write_text(
            self.intake.read_text().replace(LINE_B + "\n", ""),
            encoding="utf-8")
        before = self.intake.read_bytes()
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "appears 0 times"):
            self.apply()
        self.assertEqual(before, self.intake.read_bytes())

    def test_outbox_has_a_small_regular_file_boundary(self) -> None:
        self.outbox.write_text("{}\n" * 16, encoding="utf-8")
        with self.assertRaisesRegex(intake_verdicts.VerdictError, "15 lines"):
            self.validate()
        self.outbox.unlink()
        self.outbox.symlink_to(self.before)
        with self.assertRaisesRegex(intake_verdicts.VerdictError, "non-symlink"):
            self.validate()

    def test_applier_import_has_no_discovery_store_dependency(self) -> None:
        scripts = Path(__file__).resolve().parent
        code = """
import builtins, sys
real = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'discovery_store':
        raise ImportError('structured store deliberately absent')
    return real(name, *args, **kwargs)
builtins.__import__ = blocked
sys.path.insert(0, sys.argv[1])
import apply_intake_verdicts
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(scripts)], text=True,
            capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
