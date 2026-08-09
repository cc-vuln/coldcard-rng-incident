#!/usr/bin/env python3
"""Tests for vetting intake host proposals (scripts/vet_host.py).

This tool edits the two files that decide what the poll may fetch and what
the agent proxy may reach, so the properties tested here are the ones a bad
admission would break:

- the verdict is conservative: disagreement or error anywhere in the DNS
  evidence is inconclusive rather than a rejection or an admission, and a
  redirect off the proposed host's own domain is a rejection for a human
- robots.txt is honoured in both directions: a Disallow covering the
  candidate's path rejects, and a missing robots.txt allows by convention
- the TOML edit is surgery: the new host lands in alphabetical order,
  comments stay attached to the entries they describe, and removing the
  inserted line reproduces the original file byte-for-byte
- a rejection is not retried inside the 30-day window

No test touches the network: resolver answers are stubbed dicts in the
shapes local_resolve and doh_query return, and HTTP is a stubbed fetcher.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import vet_host as vh  # noqa: E402

NOW = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)

LOCAL_OK = {"ok": True, "addresses": ["93.184.216.34"], "error": None}
LOCAL_FAIL = {"ok": False, "addresses": [], "error": "[Errno -3] nope"}


def resolver(name, a=None, ns=None):
    """A stubbed doh_query result. Each of a/ns is a query dict, or None to
    leave that query out."""
    queries = {}
    if a is not None:
        queries["A"] = a
    if ns is not None:
        queries["NS"] = ns
    return {"server": name, "queries": queries}


ANSWERS = {"status": 0, "answers": ["93.184.216.34"]}
NXDOMAIN = {"status": 3, "answers": []}
QUERY_ERR = {"error": "TimeoutError: timed out"}

DOH_ANSWERS = lambda h, s: resolver(s["name"], a=ANSWERS, ns=ANSWERS)  # noqa: E731
DOH_ABSENT = lambda h, s: resolver(s["name"], a=NXDOMAIN, ns=NXDOMAIN)  # noqa: E731


def stub_fetch(routes):
    """A fetcher over {url: response-dict or exception}. An unrouted URL is
    a failure the test did not plan, so it raises."""
    def fetcher(url, timeout):
        route = routes[url]
        if isinstance(route, Exception):
            raise route
        return route
    return fetcher


def ok(url_root, robots_status=404, robots_body=b""):
    """The standard happy-path routes for one host: root 200, robots as
    given."""
    return {
        f"https://{url_root}/": {"status": 200, "location": None, "body": b""},
        f"https://{url_root}/robots.txt": {
            "status": robots_status, "location": None, "body": robots_body},
    }


class ParseProposalTests(unittest.TestCase):
    def test_four_tab_fields(self):
        text = ("https://example.com/thread\texample.com\t"
                "incident write-up\t20260808T080000Z\n")
        proposals, problems = vh.parse_proposals(text)
        self.assertFalse(problems)
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual(p["candidate"], "https://example.com/thread")
        self.assertEqual(p["host"], "example.com")
        self.assertEqual(p["reason"], "incident write-up")
        self.assertEqual(p["stamp"], "20260808T080000Z")

    def test_host_is_lowercased(self):
        proposals, _ = vh.parse_proposals("x\tExample.COM\twhy\tstamp\n")
        self.assertEqual(proposals[0]["host"], "example.com")

    def test_comments_and_blanks_are_skipped(self):
        text = "# a note\n\nx\texample.com\twhy\tstamp\n"
        proposals, problems = vh.parse_proposals(text)
        self.assertEqual(len(proposals), 1)
        self.assertFalse(problems)

    def test_malformed_lines_are_reported_not_fatal(self):
        text = "not a proposal\nx\texample.com\twhy\tstamp\n"
        proposals, problems = vh.parse_proposals(text)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(problems), 1)
        self.assertIn("line 1", problems[0])


class NameRejectionTests(unittest.TestCase):
    def test_ip_literals(self):
        self.assertIn("IP literal", vh.name_rejection("203.0.113.7"))
        self.assertIn("IP literal", vh.name_rejection("2001:db8::1"))

    def test_local_and_private_names(self):
        self.assertIn("localhost", vh.name_rejection("localhost"))
        self.assertIsNotNone(vh.name_rejection("printer.local"))
        self.assertIsNotNone(vh.name_rejection("wiki.internal"))
        self.assertIsNotNone(vh.name_rejection("nas.lan"))

    def test_single_label(self):
        self.assertIn("single-label", vh.name_rejection("intranet"))

    def test_shorteners(self):
        self.assertIn("shortener", vh.name_rejection("bit.ly"))
        self.assertIn("shortener", vh.name_rejection("t.co"))

    def test_not_a_hostname(self):
        self.assertIsNotNone(vh.name_rejection("exa_mple.com"))
        self.assertIsNotNone(vh.name_rejection("-example.com"))

    def test_public_hosts_pass(self):
        self.assertIsNone(vh.name_rejection("example.com"))
        self.assertIsNone(vh.name_rejection("blog.deep.sub-example.co.uk"))


class VetTests(unittest.TestCase):
    def vet(self, host, routes, resolve=LOCAL_OK, doh=DOH_ANSWERS,
            candidate=None):
        return vh.vet(host, candidate_url=candidate,
                      resolve=lambda h: resolve, doh=doh,
                      fetcher=stub_fetch(routes))

    def test_admit(self):
        result = self.vet("example.com", ok("example.com"))
        self.assertEqual(result["outcome"], "admit")
        self.assertEqual(result["host"], "example.com")
        self.assertIsNone(result["reason"])
        self.assertTrue(any("robots" in c for c in result["checks"]))

    def test_dns_absent_everywhere_is_a_rejection(self):
        result = self.vet("gone.example", ok("gone.example"),
                          resolve=LOCAL_FAIL, doh=DOH_ABSENT)
        self.assertEqual(result["outcome"], "reject")
        self.assertIn("does not resolve", result["reason"])

    def test_dns_disagreement_is_inconclusive_not_a_verdict(self):
        # This host's resolver fails while the public ones answer: nothing
        # is decided, a later run retries.
        result = self.vet("weather.example", ok("weather.example"),
                          resolve=LOCAL_FAIL, doh=DOH_ANSWERS)
        self.assertEqual(result["outcome"], "inconclusive")

    def test_a_doh_error_anywhere_is_inconclusive(self):
        doh = lambda h, s: resolver(s["name"], a=QUERY_ERR, ns=QUERY_ERR)  # noqa: E731
        result = self.vet("example.com", ok("example.com"), doh=doh)
        self.assertEqual(result["outcome"], "inconclusive")

    def test_private_local_answer_is_rejected_before_http(self):
        private = {"ok": True, "addresses": ["127.0.0.1"], "error": None}
        result = self.vet("example.com", {}, resolve=private)
        self.assertEqual(result["outcome"], "reject")
        self.assertIn("non-global", result["reason"])

    def test_private_public_resolver_answer_is_rejected(self):
        private_answer = {"status": 0, "answers": ["169.254.169.254"]}
        private_doh = lambda h, s: resolver(  # noqa: E731
            s["name"], a=private_answer, ns=ANSWERS
        )
        result = self.vet("example.com", {}, doh=private_doh)
        self.assertEqual(result["outcome"], "reject")
        self.assertIn("169.254.169.254", result["reason"])

    def test_redirect_to_a_different_domain_is_rejected(self):
        routes = ok("example.com")
        routes["https://example.com/"] = {
            "status": 301, "location": "https://elsewhere.example/", "body": b""}
        result = self.vet("example.com", routes)
        self.assertEqual(result["outcome"], "reject")
        self.assertEqual(result["reason"], "redirect to elsewhere.example")

    def test_redirect_to_a_registered_or_candidate_domain_is_named(self):
        routes = ok("old.example")
        routes["https://old.example/"] = {
            "status": 302, "location": "https://x.com/someone", "body": b""}
        result = self.vet("old.example", routes)
        self.assertEqual(result["outcome"], "reject")
        self.assertEqual(result["reason"], "redirect to x.com")

    def test_www_redirect_normalises_the_host(self):
        routes = {
            "https://example.com/": {
                "status": 301, "location": "https://www.example.com/",
                "body": b""},
            "https://www.example.com/robots.txt": {
                "status": 404, "location": None, "body": b""},
        }
        result = self.vet("example.com", routes)
        self.assertEqual(result["outcome"], "admit")
        self.assertEqual(result["host"], "www.example.com")

    def test_a_path_redirect_on_the_same_host_is_fine(self):
        routes = ok("example.com")
        routes["https://example.com/"] = {
            "status": 302, "location": "/en/", "body": b""}
        result = self.vet("example.com", routes)
        self.assertEqual(result["outcome"], "admit")
        self.assertEqual(result["host"], "example.com")

    def test_a_lookalike_domain_is_not_a_variant(self):
        routes = ok("example.com")
        routes["https://example.com/"] = {
            "status": 301, "location": "https://example.com.evil.example/",
            "body": b""}
        result = self.vet("example.com", routes)
        self.assertEqual(result["outcome"], "reject")

    def test_root_failure_is_inconclusive(self):
        routes = {"https://example.com/": OSError("connection refused"),
                  "https://example.com/robots.txt": {
                      "status": 404, "location": None, "body": b""}}
        result = self.vet("example.com", routes)
        self.assertEqual(result["outcome"], "inconclusive")

    def test_robots_disallowing_the_candidate_path_rejects(self):
        routes = ok("example.com", 200,
                    b"User-agent: *\nDisallow: /post\n")
        result = self.vet("example.com", routes,
                          candidate="https://example.com/post/123")
        self.assertEqual(result["outcome"], "reject")
        self.assertIn("robots.txt", result["reason"])

    def test_robots_allowing_other_paths_admits(self):
        routes = ok("example.com", 200,
                    b"User-agent: *\nDisallow: /admin\n")
        result = self.vet("example.com", routes,
                          candidate="https://example.com/post/123")
        self.assertEqual(result["outcome"], "admit")

    def test_robots_403_is_disallow_all_by_convention(self):
        routes = ok("example.com", 403)
        result = self.vet("example.com", routes)
        self.assertEqual(result["outcome"], "reject")
        self.assertIn("403", result["reason"])

    def test_robots_5xx_is_inconclusive(self):
        routes = ok("example.com", 503)
        result = self.vet("example.com", routes)
        self.assertEqual(result["outcome"], "inconclusive")

    def test_a_bad_name_never_touches_the_network(self):
        def boom(*a, **k):
            raise AssertionError("network was touched")
        result = vh.vet("bit.ly", resolve=boom, doh=boom, fetcher=boom)
        self.assertEqual(result["outcome"], "reject")


FIXTURE = '''\
# Hosts the source registry is allowed to name.

[hosts]

# Community platforms.
community = [
  "bitcointalk.org",
  "stacker.news",
]

# Other vendors, wallet projects and their responses.
industry = [
  "bitkey.world",
  # Added by hand for a reason that must stay attached to this entry.
  "opensats.org",
  "trezor.io",
]
'''

COMMENT = "# Admitted by scripts/vet_host.py after vetting."


class InsertHostTests(unittest.TestCase):
    def test_alphabetical_insertion(self):
        edited = vh.insert_host(FIXTURE, "industry", "karma-x.io", COMMENT)
        import tomllib
        self.assertEqual(
            tomllib.loads(edited)["hosts"]["industry"],
            ["bitkey.world", "karma-x.io", "opensats.org", "trezor.io"])

    def test_neighbours_are_byte_identical(self):
        edited = vh.insert_host(FIXTURE, "industry", "karma-x.io", COMMENT)
        self.assertEqual(
            edited.replace('  "karma-x.io",\n', "", 1), FIXTURE)

    def test_insert_before_a_commented_entry_keeps_its_comment(self):
        edited = vh.insert_host(FIXTURE, "industry", "karma-x.io", COMMENT)
        lines = edited.splitlines()
        i = lines.index('  "karma-x.io",')
        # The new entry lands above the comment, so the comment still
        # describes opensats.org, not karma-x.io.
        self.assertTrue(lines[i + 1].lstrip().startswith("#"))
        self.assertEqual(lines[i + 2], '  "opensats.org",')

    def test_insert_after_the_last_entry(self):
        edited = vh.insert_host(FIXTURE, "industry", "zzz.example", COMMENT)
        self.assertEqual(
            edited.replace('  "zzz.example",\n', "", 1), FIXTURE)
        import tomllib
        self.assertEqual(
            tomllib.loads(edited)["hosts"]["industry"][-1], "zzz.example")

    def test_a_missing_group_is_appended(self):
        edited = vh.insert_host(FIXTURE, "admitted", "newsite.example",
                                COMMENT)
        import tomllib
        self.assertEqual(
            tomllib.loads(edited)["hosts"]["admitted"], ["newsite.example"])
        self.assertTrue(edited.startswith(FIXTURE))
        self.assertIn("admitted = [", edited)

    def test_an_existing_host_is_refused(self):
        with self.assertRaises(ValueError):
            vh.insert_host(FIXTURE, "industry", "trezor.io", COMMENT)

    def test_a_duplicate_in_another_group_is_refused(self):
        with self.assertRaises(ValueError):
            vh.insert_host(FIXTURE, "industry", "stacker.news", COMMENT)

    def test_the_real_files_parse_after_a_synthetic_insert(self):
        for name in ("registry_hosts.toml", "agent_egress_hosts.toml"):
            text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            edited = vh.insert_host(text, "admitted",
                                    "vet-host-test.invalid", COMMENT)
            import tomllib
            self.assertIn("vet-host-test.invalid",
                          tomllib.loads(edited)["hosts"]["admitted"])


class StateTests(unittest.TestCase):
    def test_roundtrip_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host-vetting.json"
            state = {"version": 1, "hosts": {"example.com": {
                "status": "admitted", "when": "20260808T090000Z"}}}
            vh.save_state(path, state)
            self.assertEqual(vh.load_state(path), state)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_corrupt_state_refuses_to_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "host-vetting.json"
            path.write_text("{ not json", encoding="utf-8")
            self.assertIsNone(vh.load_state(path))

    def test_recent_rejection_is_not_retried(self):
        state = {"hosts": {"bad.example": {
            "status": "rejected", "reason": "redirect to x.com",
            "when": f"{NOW - timedelta(days=3):%Y%m%dT%H%M%SZ}"}}}
        self.assertEqual(vh.rejected_recently(state, "bad.example", NOW),
                         "redirect to x.com")

    def test_an_old_rejection_is_retried(self):
        state = {"hosts": {"bad.example": {
            "status": "rejected", "reason": "redirect to x.com",
            "when": f"{NOW - timedelta(days=31):%Y%m%dT%H%M%SZ}"}}}
        self.assertIsNone(vh.rejected_recently(state, "bad.example", NOW))

    def test_admissions_and_unknowns_are_not_rejections(self):
        state = {"hosts": {"good.example": {
            "status": "admitted", "when": "20260808T090000Z"}}}
        self.assertIsNone(vh.rejected_recently(state, "good.example", NOW))
        self.assertIsNone(vh.rejected_recently(state, "other.example", NOW))


if __name__ == "__main__":
    unittest.main()
