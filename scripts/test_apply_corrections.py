#!/usr/bin/env python3
"""Tests for the deterministic corrections applier.

apply_corrections.py is the only writer in the corrections pipeline: the
agent proposes, this validates and applies, and the corrections convention
("fixed on the page AND appended here; both, or neither counts") makes a
wrong apply worse than no automation at all. Tested here, against temporary
fixture trees with a real GNU patch:

- the happy path: page patched, block appended as a pure append, proposal
  moved to applied/
- `said` that is not a verbatim substring of the page is rejected
- a route that resolves to no file under site/src/pages/ is rejected
- the corrections.toml edit is always a pure append (prefix check)
- a diff that only applies with fuzz or an offset is rejected
- advice-only proposals are never applied and never moved
- all-or-nothing: a proposal whose diff fails leaves the page and
  corrections.toml byte-identical, and lands in rejected/ with a reason
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import apply_corrections as ac  # noqa: E402

PAGE_REL = "site/src/pages/record/funds.astro"
PAGE_TEXT = """---
title: Funds
---
<Layout>
  <p>The tracker carries the wave at 443.34 BTC across 703 addresses.</p>
  <p>Neither Galaxy nor the tracker itemises a reconciliation.</p>
  <p>An unrelated closing paragraph.</p>
</Layout>
"""

CORRECTIONS_HEAD = """# Corrections to what this project published.

[[correction]]
date = "2026-08-06"
pages = ["/cite/"]
kind = "correction"
summary = "An earlier entry."
said = "Captures are append-only."
says = "Snapshots are append-only from 6 August 2026."
why = "Found while auditing."
"""

SAID = "Neither Galaxy nor the tracker itemises a reconciliation."

TOML_BLOCK = """[[correction]]
date = "2026-08-08"
pages = ["/record/funds/"]
kind = "correction"
summary = "The tracker had itemised a reconciliation in a held capture."
said = \"\"\"
Neither Galaxy nor the tracker itemises a reconciliation.
\"\"\"
says = \"\"\"
The tracker states its own derivation.
\"\"\"
why = "Found rechecking the funds page against held captures."
"""

GOOD_DIFF = """--- a/site/src/pages/record/funds.astro
+++ b/site/src/pages/record/funds.astro
@@ -4,5 +4,5 @@
 <Layout>
   <p>The tracker carries the wave at 443.34 BTC across 703 addresses.</p>
-  <p>Neither Galaxy nor the tracker itemises a reconciliation.</p>
+  <p>The tracker states its own derivation.</p>
   <p>An unrelated closing paragraph.</p>
 </Layout>
"""


def proposal_text(toml_block=TOML_BLOCK, diff=GOOD_DIFF, status="proposal",
                  said_says_note="A rationale paragraph."):
    return f"""# Correction proposal: the tracker itemised a reconciliation

- status: {status}
- page: /record/funds/
- drafted: 20260808T120000Z

{said_says_note}

## toml
```toml
{toml_block}
```

## diff
```diff
{diff}
```
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        if shutil.which("patch") is None:
            self.skipTest("GNU patch is not on PATH")
        self.tmp = tempfile.TemporaryDirectory(prefix="applycorr-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Applied proposals emit a correction-applied alert; keep the real
        # alerts.jsonl out of the tests (the override alert.py defines for
        # exactly this).
        self._alert_dir = os.environ.get("CC_ALERT_STATE_DIR")
        os.environ["CC_ALERT_STATE_DIR"] = str(self.root / "alerts")
        self.addCleanup(self._restore_alert_dir)
        page = self.root / PAGE_REL
        page.parent.mkdir(parents=True)
        page.write_text(PAGE_TEXT)
        (self.root / "corrections.toml").write_text(CORRECTIONS_HEAD)
        self.queue = self.root / ".work" / "correction-proposals"
        self.queue.mkdir(parents=True)

    def _restore_alert_dir(self):
        if self._alert_dir is None:
            os.environ.pop("CC_ALERT_STATE_DIR", None)
        else:
            os.environ["CC_ALERT_STATE_DIR"] = self._alert_dir

    def write(self, text, name="20260808T120000Z-record-funds.md"):
        path = self.queue / name
        path.write_text(text)
        return path

    def page_text(self):
        return (self.root / PAGE_REL).read_text()

    def corrections_text(self):
        return (self.root / "corrections.toml").read_text()


class ParseTests(Fixture):
    def test_parse_happy(self):
        path = self.write(proposal_text())
        proposal = ac.parse_proposal(path)
        self.assertFalse(proposal.advice_only)
        self.assertEqual(proposal.entry["kind"], "correction")
        self.assertEqual(proposal.entry["pages"], ["/record/funds/"])
        self.assertEqual(proposal.diff_targets, [PAGE_REL])
        self.assertIn("[[correction]]", proposal.toml_text)

    def test_parse_advice_only(self):
        path = self.write(proposal_text(status="advice-only"))
        self.assertTrue(ac.parse_proposal(path).advice_only)

    def test_parse_missing_fences(self):
        path = self.write("# proposal\n\n- status: proposal\n\nno fences\n")
        with self.assertRaises(ac.ProposalError):
            ac.parse_proposal(path)


class ValidateTests(Fixture):
    def proposal(self, text=None):
        return ac.parse_proposal(self.write(text or proposal_text()))

    def test_happy_path_validates(self):
        self.assertEqual(ac.validate(self.root, self.proposal()), [])

    def test_said_not_verbatim_rejected(self):
        block = TOML_BLOCK.replace(
            "Neither Galaxy nor the tracker itemises a reconciliation.",
            "Neither side itemised a reconciliation between the figures.")
        problems = ac.validate(self.root, self.proposal(proposal_text(
            toml_block=block)))
        self.assertTrue(any("verbatim" in p for p in problems), problems)

    def test_missing_route_rejected(self):
        block = TOML_BLOCK.replace('pages = ["/record/funds/"]',
                                   'pages = ["/record/funds/", "/no/such/"]')
        problems = ac.validate(self.root, self.proposal(proposal_text(
            toml_block=block)))
        self.assertTrue(any("no file under site/src/pages/" in p
                            for p in problems), problems)

    def test_bad_kind_rejected(self):
        block = TOML_BLOCK.replace('kind = "correction"',
                                   'kind = "typo-fix"')
        problems = ac.validate(self.root, self.proposal(proposal_text(
            toml_block=block)))
        self.assertTrue(any("kind must be one of" in p for p in problems),
                        problems)

    def test_says_required_unless_withdrawal(self):
        block = TOML_BLOCK.replace(
            'says = """\nThe tracker states its own derivation.\n"""\n', "")
        problems = ac.validate(self.root, self.proposal(proposal_text(
            toml_block=block)))
        self.assertTrue(any("says is required" in p for p in problems),
                        problems)

    def test_fuzzy_diff_rejected(self):
        # The hunk context no longer matches: patch would need fuzz.
        fuzzed = GOOD_DIFF.replace("An unrelated closing paragraph.",
                                   "A rewritten closing paragraph.")
        problems = ac.validate(self.root, self.proposal(proposal_text(
            diff=fuzzed)))
        self.assertTrue(any("patch rejects" in p for p in problems),
                        problems)

    def test_offset_diff_rejected(self):
        # Correct context but a hunk header pointing five lines too far
        # down: patch would succeed with an offset, which a correction
        # must not do.
        drifted = GOOD_DIFF.replace("@@ -4,5 +4,5 @@", "@@ -9,5 +9,5 @@")
        problems = ac.validate(self.root, self.proposal(proposal_text(
            diff=drifted)))
        self.assertTrue(any("drift" in p for p in problems), problems)

    def test_diff_outside_pages_rejected(self):
        stray = GOOD_DIFF.replace(PAGE_REL, "corrections.toml")
        problems = ac.validate(self.root, self.proposal(proposal_text(
            diff=stray)))
        self.assertTrue(any("not under site/src/pages/" in p
                            for p in problems), problems)

    def test_duplicate_correction_rejected(self):
        # The same `said` already logged: a rerun must not apply twice.
        already = CORRECTIONS_HEAD + '\nsaid = """\n' + SAID + '\n"""\n'
        (self.root / "corrections.toml").write_text(already)
        problems = ac.validate(self.root, self.proposal())
        self.assertTrue(any("already in corrections.toml" in p
                            for p in problems), problems)

    def test_append_only_ok(self):
        self.assertTrue(ac.append_only_ok("abc", "abc\ndef"))
        self.assertFalse(ac.append_only_ok("abc", "abX\ndef"))
        self.assertFalse(ac.append_only_ok("abc", "ab"))

    def test_build_append_is_a_pure_append(self):
        after = ac.build_corrections_append(CORRECTIONS_HEAD, TOML_BLOCK)
        self.assertTrue(after.startswith(CORRECTIONS_HEAD))
        import tomllib
        rows = tomllib.loads(after)["correction"]
        self.assertEqual(len(rows), 2)


class ApplyTests(Fixture):
    def test_happy_path_apply(self):
        path = self.write(proposal_text())
        proposal = ac.parse_proposal(path)
        self.assertEqual(ac.validate(self.root, proposal), [])
        ok, summary = ac.apply_proposal(self.root, proposal)
        self.assertTrue(ok, summary)
        # The page carries the fix where the claim was.
        self.assertIn("The tracker states its own derivation.",
                      self.page_text())
        self.assertNotIn(SAID, self.page_text())
        # And the log indexes it, as a pure append that still parses.
        after = self.corrections_text()
        self.assertTrue(after.startswith(CORRECTIONS_HEAD))
        import tomllib
        self.assertEqual(len(tomllib.loads(after)["correction"]), 2)
        self.assertIn("correction on /record/funds/", summary)

    def test_all_or_nothing_on_bad_diff(self):
        # A valid TOML half and a diff that fails: nothing is applied,
        # byte-identical, and the proposal lands in rejected/ with a reason.
        fuzzed = GOOD_DIFF.replace("An unrelated closing paragraph.",
                                   "A rewritten closing paragraph.")
        path = self.write(proposal_text(diff=fuzzed))
        before_page, before_log = self.page_text(), self.corrections_text()
        rc = ac.main(["--yes", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.page_text(), before_page)
        self.assertEqual(self.corrections_text(), before_log)
        rejected = self.queue / "rejected" / path.name
        self.assertTrue(rejected.is_file())
        self.assertIn("## rejected", rejected.read_text())
        self.assertFalse(path.exists())
        self.assertFalse((self.queue / "applied").exists())

    def test_advice_only_never_applied(self):
        path = self.write(proposal_text(status="advice-only"))
        before_page, before_log = self.page_text(), self.corrections_text()
        rc = ac.main(["--yes", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.page_text(), before_page)
        self.assertEqual(self.corrections_text(), before_log)
        self.assertTrue(path.exists())  # left in place for a human

    def test_main_dry_run_writes_nothing(self):
        path = self.write(proposal_text())
        before_page, before_log = self.page_text(), self.corrections_text()
        rc = ac.main(["--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.page_text(), before_page)
        self.assertEqual(self.corrections_text(), before_log)
        self.assertTrue(path.exists())

    def test_main_yes_applies_and_moves(self):
        path = self.write(proposal_text())
        rc = ac.main(["--yes", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertTrue((self.queue / "applied" / path.name).is_file())
        self.assertIn("The tracker states its own derivation.",
                      self.page_text())

    def test_main_empty_queue(self):
        for child in self.queue.glob("*.md"):
            child.unlink()
        self.assertEqual(ac.main(["--yes", "--root", str(self.root)]), 0)


if __name__ == "__main__":
    unittest.main()
