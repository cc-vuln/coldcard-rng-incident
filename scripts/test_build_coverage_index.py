"""Tests for the intake coverage index.

The index answers one question for the intake agent: is this candidate's theme
already in the record? Two things about it are worth pinning down, because
both have already been got wrong once:

- an `absorbed` count is only ever as good as the id it was read from. The
  first version of the referent pattern demanded three hyphen-separated
  segments to keep prose out of the counts, which silently excluded
  `optech-416` and every `<author>-<status-id>` social post. The pattern is
  loose now and the registry does the filtering, so the test that matters is
  that hyphenated prose still scores nothing
- every registered table is indexed. A candidate can duplicate an X post or a
  chain monitor's page, not only another thread, and the assessed corpus holds
  exactly those verdicts
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_coverage_index as bci  # noqa: E402


SOURCES = """\
[[source]]
id = "reddit-dice-seed-generation"
title = "r/Bitcoin: generating seeds with physical dice"
url = "https://www.reddit.com/r/Bitcoin/comments/aaa/x/"
org = "reddit"

[[source]]
id = "optech-416"
title = "Bitcoin Optech Newsletter 416"
url = "https://bitcoinops.org/en/newsletters/416/"
org = "Bitcoin Optech"

[[source]]
id = "cktripwire-honeypot-monitor"
title = "CKTRIPWIRE honeypot monitor"
url = "https://example.invalid/ck"
org = "CKTRIPWIRE"

[[x_post]]
id = "fatmanterra-2084791442212339942"
title = "On the vendor's response"
url = "https://x.com/fatmanterra/status/2084791442212339942"
org = "@fatmanterra"

[[nostr_post]]
id = "nostr-victim-letter"
title = "A victim's letter"
url = "https://njump.me/note1abc"
org = "nostr"
"""

INTAKE = """\
# Discovery intake

## Pending

## Assessed

- 2026-08-05 [A](https://e.invalid/1) by a, 3 comments (r/Bitcoin) -> dismissed: \
repetitive dice-seed workflow discussion already represented by \
reddit-dice-seed-generation (20260805T000000Z)
- 2026-08-05 [B](https://e.invalid/2) by b, 3 comments (r/Bitcoin) -> dismissed: \
duplicate of reddit-dice-seed-generation and optech-416 (20260805T000000Z)
- 2026-08-05 [C](https://e.invalid/3) by c, 3 comments (r/Bitcoin) -> dismissed: \
relay of already registered material, duplicate of \
fatmanterra-2084791442212339942 (20260805T000000Z)
- 2026-08-05 [D](https://e.invalid/4) by d, 3 comments (r/Bitcoin) -> dismissed: \
repetitive self-custody and confidence-loss sentiment (20260805T000000Z)
- 2026-08-05 [E](https://e.invalid/5) by e, 3 comments (r/Bitcoin) -> \
registered as reddit-dice-seed-generation (20260805T000000Z)
"""


class CoverageIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "discovery").mkdir()
        (root / "sources.toml").write_text(SOURCES, encoding="utf-8")
        (root / "DISCOVERY.md").write_text(INTAKE, encoding="utf-8")
        self._saved = (bci.ROOT, bci.SOURCES, bci.INTAKE, bci.ROTATED)
        bci.ROOT = root
        bci.SOURCES = root / "sources.toml"
        bci.INTAKE = root / "DISCOVERY.md"
        bci.ROTATED = root / "discovery"
        self.root = root

    def tearDown(self):
        bci.ROOT, bci.SOURCES, bci.INTAKE, bci.ROTATED = self._saved
        self.tmp.cleanup()

    def counts(self):
        rows = bci.entries()
        return bci.absorbed_counts(bci.assessed_lines(),
                                   {r["id"] for r in rows})

    def test_every_registered_table_is_indexed(self):
        ids = {r["id"] for r in bci.entries()}
        self.assertIn("reddit-dice-seed-generation", ids)   # source
        self.assertIn("fatmanterra-2084791442212339942", ids)  # x_post
        self.assertIn("nostr-victim-letter", ids)           # nostr_post

    def test_absorbed_counts_come_from_dismissals(self):
        self.assertEqual(self.counts()["reddit-dice-seed-generation"], 2)

    def test_a_second_referent_on_one_line_is_counted(self):
        self.assertEqual(self.counts()["optech-416"], 1)

    def test_a_two_segment_social_id_is_counted(self):
        # The regression: `<author>-<status-id>` has one hyphen, and the
        # earlier pattern required two.
        self.assertEqual(self.counts()["fatmanterra-2084791442212339942"], 1)

    def test_hyphenated_prose_scores_nothing(self):
        counts = self.counts()
        for prose in ("self-custody", "confidence-loss", "dice-seed"):
            self.assertNotIn(prose, counts)

    def test_a_registration_is_not_an_absorbed_candidate(self):
        # Line E registers that id; only dismissals count as absorbed.
        self.assertEqual(self.counts()["reddit-dice-seed-generation"], 2)

    def test_rotated_verdicts_are_read_too(self):
        (self.root / "discovery" / "assessed-2026-07.md").write_text(
            "# Assessed\n\n## Assessed\n\n"
            "- 2026-07-30 [F](https://e.invalid/6) by f, 3 comments "
            "(r/Bitcoin) -> dismissed: already represented by optech-416 "
            "(20260730T000000Z)\n", encoding="utf-8")
        self.assertEqual(self.counts()["optech-416"], 2)

    def test_the_index_is_sorted_most_absorbed_first(self):
        text = bci.render(bci.entries(), self.counts(), 100)
        after = text.split("## Community threads", 1)[1].splitlines()
        first = next(l for l in after[1:] if l.strip())
        self.assertTrue(first.startswith("reddit-dice-seed-generation"), first)

    def test_an_entry_with_no_absorbed_candidate_says_nothing(self):
        text = bci.render(bci.entries(), self.counts(), 100)
        line = [l for l in text.splitlines()
                if l.startswith("cktripwire-honeypot-monitor")][0]
        self.assertNotIn("absorbed", line)

    def test_long_titles_are_truncated(self):
        rows = bci.entries()
        text = bci.render(rows, self.counts(), 20)
        for line in text.splitlines():
            if line.startswith("optech-416"):
                self.assertIn("…", line)
                break
        else:
            self.fail("optech-416 not in the index")

    def test_blocks_separate_community_from_the_rest(self):
        text = bci.render(bci.entries(), self.counts(), 100)
        self.assertIn("## Community threads (1)", text)
        self.assertIn("## Other registered sources (2)", text)
        self.assertIn("## Registered social posts (2)", text)

    def test_intake_templates_consume_one_scoped_packet(self):
        # The packet replaces three overlapping prompt payloads. Keeping the
        # assertion here makes an accidental return to the full index visible.
        for name in ("agent-discovery-intake-prompt.md",
                     "agent-x-intake-prompt.md"):
            template = (ROOT / "scripts" / name).read_text()
            self.assertEqual(template.count("{INTAKE_PACKET}"), 1)
            self.assertNotIn("{COVERAGE}", template)
            self.assertNotIn("{CANDIDATES}", template)
            self.assertNotIn("{HYDRATED}", template)

    def test_the_index_carries_no_instructions(self):
        # It is fenced as untrusted and the framing lives in the template.
        # Anything imperative here would be prose the agent could act on.
        text = bci.render(bci.entries(), self.counts(), 100)
        body = [l for l in text.splitlines()
                if l and not l.startswith("## ")]
        for line in body:
            self.assertRegex(line, r"^[a-z0-9][a-z0-9-]*  \[")


if __name__ == "__main__":
    unittest.main()
