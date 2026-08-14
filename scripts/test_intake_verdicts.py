#!/usr/bin/env python3
"""Focused tests for the guarded structured intake-verdict handoff."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apply_intake_verdicts as applier
import intake_verdicts
from discovery_test_fixture import install_store


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
        self.before.write_text(OLD, encoding="utf-8")
        self.after.write_text(NEW, encoding="utf-8")
        self.approval.write_text("", encoding="utf-8")
        self.store = install_store(self.root, [
            {"line": LINE_A,
             "url": "https://www.reddit.com/r/test/comments/new111/new/",
             "at": "20260813T000000Z"},
            {"line": LINE_B, "url": "https://stacker.news/items/222",
             "at": "20260813T000001Z"},
        ])
        self.write_packet([
            ("reddit:new111", "reddit:submission:new111", LINE_A, []),
            ("stackernews:222", "stackernews:item:222", LINE_B, []),
        ])

    def write_packet(self, candidates: list[tuple[str, str, str, list[str]]]) -> None:
        records = []
        for ident, key, line, exact in candidates:
            projected = self.store.load_candidate(ident)
            records.append({
                "candidate_id": ident,
                "candidate_head": (projected["head"] if projected
                                   else "a" * 64),
                "external_key": key,
                "queue_line": line,
                "registry_exact_match": {
                    "matched": bool(exact), "source_ids": exact,
                },
            })
        value = {"schema_version": 1, "lane": "community",
                 "discovery": {"mode": "structured"},
                 "candidates": records}
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
        with self.store.locked():
            return applier.apply(
                root=self.root, packet_path=self.packet,
                verdict_path=self.outbox, before_registry_path=self.before,
                after_registry_path=self.after, approval_path=self.approval,
                lock_held=True, operation_id="fixture-run")

    def event_bytes(self) -> bytes:
        return b"".join(path.read_bytes() for path in sorted(
            (self.root / "discovery/transactions").glob("*/*.json")))

    def test_applier_moves_terminal_and_leaves_retry_pending(self) -> None:
        self.write_rows([
            row("reddit:new111", "dismissed", "not incident material"),
            row("stackernews:222", "retry", "body unavailable"),
        ])
        self.assertEqual((1, 1), self.apply())
        first = self.store.load_candidate("reddit:new111")
        second = self.store.load_candidate("stackernews:222")
        self.assertEqual("assessed", first["state"])
        self.assertEqual("dismissed", first["verdict"]["kind"])
        self.assertEqual("20260813T120000Z", first["verdict"]["at"])
        self.assertEqual("pending", second["state"])
        self.assertEqual("body unavailable", second["retry"]["reason"])
        self.assertEqual("20260813T120000Z", second["retry"]["at"])

    def test_output_order_is_packet_order_not_outbox_order(self) -> None:
        self.write_rows([
            row("stackernews:222", "dismissed", "second"),
            row("reddit:new111", "dismissed", "first"),
        ])
        normalized = self.validate()
        self.assertEqual(["reddit:new111", "stackernews:222"],
                         [value["candidate_id"] for value in normalized])
        self.apply()
        self.assertEqual("assessed",
                         self.store.load_candidate("reddit:new111")["state"])
        self.assertEqual("assessed",
                         self.store.load_candidate("stackernews:222")["state"])

    def test_queue_line_is_presentation_not_apply_identity(self) -> None:
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        packet["candidates"][0]["queue_line"] = "- cosmetic packet label"
        self.packet.write_text(json.dumps(packet), encoding="utf-8")
        self.write_rows([row("reddit:new111", "dismissed"),
                         row("stackernews:222", "retry")])
        self.apply()
        self.assertEqual("assessed",
                         self.store.load_candidate("reddit:new111")["state"])

    def test_exact_guarded_operation_replay_is_idempotent(self) -> None:
        self.write_rows([row("reddit:new111", "dismissed"),
                         row("stackernews:222", "retry")])
        self.apply()
        committed = self.event_bytes()
        self.assertEqual((1, 1), self.apply())
        self.assertEqual(committed, self.event_bytes())

    def test_operation_id_reuse_with_different_content_fails_closed(self) -> None:
        self.write_rows([row("reddit:new111", "dismissed"),
                         row("stackernews:222", "retry")])
        self.apply()
        committed = self.event_bytes()
        self.write_rows([row("reddit:new111", "dismissed", "changed reason"),
                         row("stackernews:222", "retry")])
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "reused with different content"):
            self.apply()
        self.assertEqual(committed, self.event_bytes())

    def test_missing_guard_marker_writes_nothing(self) -> None:
        self.write_rows([row("reddit:new111", "dismissed"),
                         row("stackernews:222", "retry")])
        before = self.event_bytes()
        self.approval.unlink()
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "approval marker"):
            self.apply()
        self.assertEqual(before, self.event_bytes())

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
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        packet["candidates"][1]["candidate_head"] = "0" * 64
        self.packet.write_text(json.dumps(packet), encoding="utf-8")
        before = self.event_bytes()
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "head changed"):
            self.apply()
        self.assertEqual(before, self.event_bytes())

    def test_outbox_has_a_small_regular_file_boundary(self) -> None:
        self.outbox.write_text("{}\n" * 16, encoding="utf-8")
        with self.assertRaisesRegex(intake_verdicts.VerdictError, "15 lines"):
            self.validate()
        self.outbox.unlink()
        self.outbox.symlink_to(self.before)
        with self.assertRaisesRegex(intake_verdicts.VerdictError, "non-symlink"):
            self.validate()

    def test_applier_rejects_legacy_packet_without_head_binding(self) -> None:
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        packet.pop("discovery")
        for candidate in packet["candidates"]:
            candidate.pop("candidate_head")
        self.packet.write_text(json.dumps(packet), encoding="utf-8")
        self.write_rows([row("reddit:new111", "dismissed"),
                         row("stackernews:222", "retry")])
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "requires a structured"):
            self.apply()

    def test_structured_packet_requires_every_candidate_head(self) -> None:
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        packet["candidates"][0].pop("candidate_head")
        self.packet.write_text(json.dumps(packet), encoding="utf-8")
        with self.assertRaisesRegex(intake_verdicts.VerdictError,
                                    "valid candidate head"):
            intake_verdicts.load_packet(self.packet)


if __name__ == "__main__":
    unittest.main()
