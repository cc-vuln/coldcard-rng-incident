#!/usr/bin/env python3
"""Tests for corroborating a DNS failure streak before recording gone.

This tool edits the registry that decides what the 30-minute poll fetches,
and it exists because the project once called a live source gone on the
strength of its own resolver alone. Three properties are tested here:

- the decision is conservative: any public resolver answering means
  reachable-elsewhere, and confirmed-gone needs the local lookup to fail
  AND every public resolver to agree the name is absent
- the registry edit is surgery: the gone_* fields land in the named block,
  every neighbouring block stays byte-identical, and the result still
  parses and passes the gone_* field rules capture.py enforces
- candidate selection excludes what diagnose already settled (gone) and
  what a person froze, and honours the streak threshold

No test touches the network: resolver answers are stubbed dicts in the
shape doh_query returns.
"""

import tempfile
import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import corroborate_gone as cg  # noqa: E402

NOW = datetime(2026, 8, 8, 7, 0, 0, tzinfo=timezone.utc)

LOCAL_FAIL = {"ok": False, "addresses": [], "error": "[Errno -3] nope"}
LOCAL_OK = {"ok": True, "addresses": ["203.0.113.7"], "error": None}


def resolver(name, a=None, ns=None):
    """A stubbed doh_query result. Each of a/ns is a query dict, or None to
    leave that query out."""
    queries = {}
    if a is not None:
        queries["A"] = a
    if ns is not None:
        queries["NS"] = ns
    return {"server": name, "queries": queries}


NXDOMAIN = {"status": 3, "answers": []}
NODATA = {"status": 0, "answers": []}
ANSWERS = {"status": 0, "answers": ["203.0.113.7"]}
QUERY_ERR = {"error": "TimeoutError: timed out"}


class VerdictTests(unittest.TestCase):
    def test_answer_on_either_query_means_the_name_exists(self):
        r = resolver("x", a=ANSWERS, ns=NODATA)
        self.assertEqual(cg.verdict(r), "answers")
        r = resolver("x", a=NODATA, ns=ANSWERS)
        self.assertEqual(cg.verdict(r), "answers")

    def test_nxdomain_and_nodata_are_absent(self):
        self.assertEqual(cg.verdict(resolver("x", a=NXDOMAIN, ns=NXDOMAIN)),
                         "absent")
        self.assertEqual(cg.verdict(resolver("x", a=NODATA, ns=NODATA)),
                         "absent")

    def test_an_errored_query_with_no_answer_cannot_be_counted(self):
        r = resolver("x", a=QUERY_ERR, ns=NXDOMAIN)
        self.assertEqual(cg.verdict(r), "error")
        r = resolver("x", a=QUERY_ERR, ns=QUERY_ERR)
        self.assertEqual(cg.verdict(r), "error")

    def test_an_error_does_not_hide_an_answer(self):
        r = resolver("x", a=ANSWERS, ns=QUERY_ERR)
        self.assertEqual(cg.verdict(r), "answers")

    def test_no_queries_at_all_is_an_error(self):
        self.assertEqual(cg.verdict(resolver("x")), "error")


class ClassifyTests(unittest.TestCase):
    def test_confirmed_gone_needs_local_failure_and_full_agreement(self):
        resolvers = [resolver("g", a=NXDOMAIN, ns=NXDOMAIN),
                     resolver("c", a=NODATA, ns=NXDOMAIN)]
        self.assertEqual(cg.classify(LOCAL_FAIL, resolvers),
                         "confirmed-gone")

    def test_any_public_answer_means_reachable_elsewhere(self):
        resolvers = [resolver("g", a=NXDOMAIN, ns=NXDOMAIN),
                     resolver("c", a=ANSWERS, ns=NODATA)]
        self.assertEqual(cg.classify(LOCAL_FAIL, resolvers),
                         "reachable-elsewhere")

    def test_agreement_but_local_success_is_inconclusive(self):
        # The host resolves fine now, so the streak is stale or the
        # resolvers are being lied to; either way nobody sets gone on this.
        resolvers = [resolver("g", a=NXDOMAIN, ns=NXDOMAIN),
                     resolver("c", a=NXDOMAIN, ns=NXDOMAIN)]
        self.assertEqual(cg.classify(LOCAL_OK, resolvers), "inconclusive")

    def test_a_doh_error_anywhere_is_inconclusive(self):
        resolvers = [resolver("g", a=NXDOMAIN, ns=NXDOMAIN),
                     resolver("c", a=QUERY_ERR, ns=QUERY_ERR)]
        self.assertEqual(cg.classify(LOCAL_FAIL, resolvers), "inconclusive")

    def test_both_doh_down_is_inconclusive(self):
        resolvers = [resolver("g", a=QUERY_ERR, ns=QUERY_ERR),
                     resolver("c", a=QUERY_ERR, ns=QUERY_ERR)]
        self.assertEqual(cg.classify(LOCAL_FAIL, resolvers), "inconclusive")


FIXTURE = '''\
# comment before the first block

[[source]]
id = "alpha-live"
title = "A live source"
url = "https://alpha.example/post"
org = "alpha"
kind = "article"
tier = 2
note = "stays put"

[[source]]
id = "beta-dns"
title = "The failing one"
url = "https://beta.example/article"
org = "beta"
kind = "article"
tier = 1

[[source]]
id = "gamma-live"
title = "Another live source"
url = "https://gamma.example/page"
org = "gamma"
kind = "article"
tier = 3
'''


class ApplyGoneTests(unittest.TestCase):
    def setUp(self):
        self.resolvers = [resolver("dns.google", a=NXDOMAIN, ns=NXDOMAIN),
                          resolver("cloudflare-dns.com", a=NXDOMAIN,
                                   ns=NXDOMAIN)]
        self.row = {"id": "beta-dns", "streak": 7,
                    "failing_since": "20260807T080816Z",
                    "last_good": "20260807T003116Z"}
        self.note = cg.gone_note(self.row, "beta.example", LOCAL_FAIL,
                                 self.resolvers, NOW)
        self.edited = cg.apply_gone(FIXTURE, "beta-dns", "20260808T070000Z",
                                    "NXDOMAIN", self.note)

    def test_the_edit_lands_in_the_named_block(self):
        data = tomllib.loads(self.edited)
        blocks = {e["id"]: e for e in data["source"]}
        self.assertTrue(blocks["beta-dns"]["gone"])
        self.assertEqual(blocks["beta-dns"]["gone_since"], "20260808T070000Z")
        self.assertEqual(blocks["beta-dns"]["gone_status"], "NXDOMAIN")
        self.assertIn("dns.google", blocks["beta-dns"]["gone_note"])
        self.assertNotIn("gone", blocks["alpha-live"])
        self.assertNotIn("gone", blocks["gamma-live"])

    def test_neighbours_are_byte_identical(self):
        for block in FIXTURE.split("[[source]]"):
            if 'id = "beta-dns"' not in block:
                self.assertIn(block, self.edited)

    def test_gone_note_carries_the_transcript(self):
        data = tomllib.loads(self.edited)
        note = data["source"][1]["gone_note"]
        for needle in ("dns-unresolved x7 since 20260807T080816Z",
                       "last good 20260807T003116Z",
                       "getaddrinfo: failed",
                       "dns.google: A NXDOMAIN, no answer records; "
                       "NS NXDOMAIN, no answer records",
                       "cloudflare-dns.com",
                       "corroborated at 2026-08-08T07:00:00Z"):
            self.assertIn(needle, note)

    def test_the_fields_satisfy_capture_py_validation(self):
        # The same rules scripts/capture.py checks at load, kept local so
        # the test does not depend on capture.py internals.
        import re
        data = tomllib.loads(self.edited)
        e = data["source"][1]
        self.assertIs(e["gone"], True)
        self.assertTrue(re.fullmatch(r"\d{8}T\d{6}Z", e["gone_since"]))
        self.assertIsInstance(e["gone_status"], str)
        self.assertIsInstance(e["gone_note"], str)
        self.assertTrue(e["gone_note"].strip())

    def test_a_second_edit_does_not_disturb_the_first(self):
        other = cg.apply_gone(self.edited, "gamma-live", "20260808T070100Z",
                              "no-data", "second transcript")
        data = tomllib.loads(other)
        blocks = {e["id"]: e for e in data["source"]}
        self.assertTrue(blocks["beta-dns"]["gone"])
        self.assertTrue(blocks["gamma-live"]["gone"])
        self.assertNotIn("gone", blocks["alpha-live"])

    def test_refuses_an_unknown_or_gone_block(self):
        with self.assertRaises(ValueError):
            cg.apply_gone(FIXTURE, "no-such-id", "20260808T070000Z",
                          "NXDOMAIN", "note")
        with self.assertRaises(ValueError):
            cg.apply_gone(self.edited, "beta-dns", "20260808T070100Z",
                          "NXDOMAIN", "note")


class CandidateTests(unittest.TestCase):
    REGISTRY = {
        "a-failing": {"id": "a-failing", "url": "https://a.example/"},
        "b-gone": {"id": "b-gone", "url": "https://b.example/",
                   "gone": True},
        "c-frozen": {"id": "c-frozen", "url": "https://c.example/",
                     "watch": "frozen"},
        "d-short": {"id": "d-short", "url": "https://d.example/"},
        "e-http": {"id": "e-http", "url": "https://e.example/"},
    }

    def row(self, ident, diagnosis="dns-unresolved", streak=5):
        return {"id": ident, "diagnosis": diagnosis, "streak": streak}

    def test_selection(self):
        rows = [self.row("a-failing"), self.row("b-gone"),
                self.row("c-frozen"), self.row("d-short", streak=2),
                self.row("e-http", diagnosis="origin-absent"),
                self.row("not-registered")]
        picked = cg.candidates(rows, self.REGISTRY, min_streak=4)
        self.assertEqual([r["id"] for r in picked], ["a-failing"])

    def test_threshold_is_inclusive_and_configurable(self):
        rows = [self.row("a-failing", streak=4),
                self.row("d-short", streak=3)]
        picked = cg.candidates(rows, self.REGISTRY, min_streak=4)
        self.assertEqual([r["id"] for r in picked], ["a-failing"])
        picked = cg.candidates(rows, self.REGISTRY, min_streak=2)
        self.assertEqual([r["id"] for r in picked], ["a-failing", "d-short"])


class GoneStatusTests(unittest.TestCase):
    def test_nxdomain_only_when_every_resolver_says_it(self):
        both = [resolver("g", a=NXDOMAIN), resolver("c", a=NXDOMAIN)]
        self.assertEqual(cg.gone_status(both), "NXDOMAIN")
        mixed = [resolver("g", a=NXDOMAIN), resolver("c", a=NODATA)]
        self.assertEqual(cg.gone_status(mixed), "no-data")


class DryRunTests(unittest.TestCase):
    """The whole pass against a stubbed diagnose: a failing source that
    resolves publicly is reported reachable-elsewhere and the registry file
    is untouched."""

    def test_dry_run_changes_nothing(self):
        import unittest.mock as mock
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "sources.toml"
            registry.write_text(FIXTURE, encoding="utf-8")
            before = registry.read_bytes()

            rows = [{"id": "beta-dns", "diagnosis": "dns-unresolved",
                     "streak": 6, "failing_since": "20260807T080816Z",
                     "last_good": "20260807T003116Z"}]
            public = [resolver("dns.google", a=ANSWERS, ns=NODATA),
                      resolver("cloudflare-dns.com", a=NXDOMAIN,
                               ns=NXDOMAIN)]
            with mock.patch.object(cg, "run_diagnose", return_value=rows), \
                    mock.patch.object(cg, "local_resolve",
                                      return_value=LOCAL_FAIL), \
                    mock.patch.object(cg, "doh_query", side_effect=public), \
                    mock.patch.object(cg, "emit_alert") as alert, \
                    mock.patch("sys.argv", ["corroborate_gone.py",
                                            "--registry", str(registry)]):
                self.assertEqual(cg.main(), 0)

            self.assertEqual(registry.read_bytes(), before)
            alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
