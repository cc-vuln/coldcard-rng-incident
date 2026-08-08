from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import auto_classify_noise as acn

REPO = Path(__file__).resolve().parent.parent


def record(sign: str, status: str, role: str = "reply",
           created: str = "2026-08-06T12:00:00Z",
           body: list[str] | None = None) -> list[str]:
    lines = [
        f"{sign}post: {status}",
        f"{sign}role: {role}",
        f"{sign}author: somehandle",
        f"{sign}name: Some Name",
        f"{sign}created: {created}",
        f"{sign}media: 0",
        f"{sign}body:",
    ]
    lines += [f"{sign}{text}" for text in (body if body is not None else ["body text"])]
    lines.append(sign)
    return lines


class ThreadRecordTests(unittest.TestCase):
    def test_parses_records_including_multi_paragraph_bodies(self) -> None:
        lines = (record("", "111", body=["one", "", "two"])
                 + record("", "222", role="self-thread", body=[]))
        records = acn.thread_records(lines)
        self.assertEqual([r["status"] for r in records], ["111", "222"])
        self.assertEqual(records[1]["role"], "self-thread")

    def test_rejects_a_partial_record(self) -> None:
        # A mid-record edit: body lines without the post header above them.
        self.assertIsNone(acn.thread_records(["body:", "edited text", ""]))

    def test_rejects_a_gap_line(self) -> None:
        self.assertIsNone(
            acn.thread_records(["gap: reply cap reached; X ranking governs "
                                "which replies loaded"]))


class XThreadStructuralTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.snapshots = root / "snapshots"
        self.diffs = root / "diffs" / "thread-src"
        self.diffs.mkdir(parents=True)
        self.sources = {"thread-src": {"id": "thread-src",
                                       "capture": "x-thread"}}

    def decide(self, payload: list[str], capped: bool | None = True,
               timestamp: str = "20260806T120000Z",
               previous: str = "20260806T100000Z"):
        path = self.diffs / f"{timestamp}.diff"
        path.write_text("\n".join(
            [f"--- thread-src@{previous}", f"+++ thread-src@{timestamp}",
             "@@ -1,1 +1,1 @@"] + payload) + "\n")
        if capped is not None:
            target = self.snapshots / "thread-src"
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{timestamp}.json").write_text(json.dumps(
                {"depth": {"capped": capped, "replies_observed": 120}}))
        return acn.xthread_structural(path, self.sources,
                                      snapshot_root=self.snapshots)

    def test_declared_cap_reply_churn_is_noise(self) -> None:
        payload = (record("-", "111") + record("-", "222")
                   + record("+", "333"))
        status, summary = self.decide(payload)  # type: ignore[misc]
        self.assertEqual(status, "capture-noise")
        self.assertIn("cap", summary)

    def test_removals_without_a_declared_cap_abstain(self) -> None:
        # The hard constraint: a reply absent for any reason other than
        # declared cap/selection churn is never noise here. This is the
        # 6 Aug 2026 under-collection shape, and it must stay with the agent.
        self.assertIsNone(self.decide(record("-", "111"), capped=False))

    def test_missing_depth_record_abstains(self) -> None:
        self.assertIsNone(self.decide(record("-", "111"), capped=None))

    def test_non_reply_removal_abstains_even_when_capped(self) -> None:
        payload = record("-", "111", role="self-thread")
        self.assertIsNone(self.decide(payload, capped=True))

    def test_additions_posted_after_previous_capture_are_content(self) -> None:
        payload = record("+", "111", created="2026-08-06T11:00:00Z")
        status, _summary = self.decide(payload, capped=False)  # type: ignore[misc]
        self.assertEqual(status, "source-content")

    def test_additions_predating_previous_capture_abstain(self) -> None:
        # An old post entering the capture is ranking recovery or an earlier
        # scroll's miss; telling those apart is the review agent's judgement.
        payload = record("+", "111", created="2026-08-06T09:00:00Z")
        self.assertIsNone(self.decide(payload, capped=False))

    def test_mixed_addition_dates_abstain(self) -> None:
        payload = (record("+", "111", created="2026-08-06T11:00:00Z")
                   + record("+", "222", created="2026-08-06T09:00:00Z"))
        self.assertIsNone(self.decide(payload, capped=False))

    def test_non_thread_source_abstains(self) -> None:
        self.sources["thread-src"]["capture"] = "reddit-json"
        self.assertIsNone(self.decide(record("-", "111")))


class KnownCaseTests(unittest.TestCase):
    """The lane is trusted only where it reproduces a classified real diff.

    clay-attribution 20260806T031334Z is the capped ranked-sample pair the
    pilot measured (docs/design/x-thread-capture.md section 6);
    20260806T031938Z is the under-collected capture the lane must refuse;
    afilini-2085269060028170742 20260806T164808Z is four genuinely new
    replies. The archive is append-only, so these cannot drift.
    """

    CASES = [
        ("clay-attribution", "20260806T031334Z", "capture-noise"),
        ("clay-attribution", "20260806T031938Z", None),
        ("afilini-2085269060028170742", "20260806T164808Z", "source-content"),
    ]

    def test_known_diffs(self) -> None:
        import capture
        registry = capture.load_sources()
        sources = {s["id"]: s for s in capture.pollable_sources(registry)}
        for source_id, timestamp, expected in self.CASES:
            path = REPO / "archive/diffs" / source_id / f"{timestamp}.diff"
            if not path.exists():
                self.skipTest(f"archive capture not held: {path}")
            with self.subTest(diff=f"{source_id}@{timestamp}"):
                decision = acn.xthread_structural(path, sources)
                got = decision[0] if decision else None
                self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
