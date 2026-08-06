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


class IntakeQueueTests(unittest.TestCase):
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

    def pending(self) -> list[str]:
        text = dc.INTAKE.read_text(encoding="utf-8")
        head = text.split("## Assessed", 1)[0]
        return [l for l in head.splitlines() if l.startswith("- ")]

    def assessed(self) -> list[str]:
        text = dc.INTAKE.read_text(encoding="utf-8")
        tail = text.split("## Assessed", 1)[1]
        return [l for l in tail.splitlines() if l.startswith("- ")]

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
