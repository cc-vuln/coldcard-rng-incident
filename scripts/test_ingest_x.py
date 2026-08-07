#!/usr/bin/env python3
"""Offline regression tests for ingest-x.py.

Pure parts only: URL parsing, registry lookup, the --thread/--tier contract
and the registry block this script appends. No browser, no network, no writes
outside a temporary directory.

The block test round-trips through capture.validate_sources rather than
asserting on the text, because what matters about a written block is that the
capture the timer runs next will accept it. A thread block missing its tier
parses as TOML perfectly well and is still unpollable.

Run with: PYTHONPATH=scripts .venv/bin/python -m unittest scripts/test_ingest_x.py
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import capture  # noqa: E402


def load_ingest_x():
    """Import the hyphenated script under a legal module name."""
    spec = importlib.util.spec_from_file_location(
        "ingest_x", SCRIPTS / "ingest-x.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ingest = load_ingest_x()

URL = "https://x.com/clay_garrett/status/2083247006139503065"
STATUS = "2083247006139503065"


class ParseTweet(unittest.TestCase):
    def test_handle_and_status(self):
        self.assertEqual(ingest.parse_tweet(URL), ("clay_garrett", STATUS))

    def test_rejects_a_profile_url(self):
        with self.assertRaises(SystemExit):
            ingest.parse_tweet("https://x.com/clay_garrett")


class RegistryLookup(unittest.TestCase):
    """The registry is read to decide where a capture goes, so a registry
    that will not parse must not stop one being taken."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sources.toml"
        self._real = ingest.SOURCES
        ingest.SOURCES = self.path
        self.addCleanup(setattr, ingest, "SOURCES", self._real)

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def test_finds_the_block_by_status_id(self):
        self.write(f'[[x_post]]\nid = "clay-attribution"\nurl = "{URL}"\n'
                   'author = "clay_garrett"\nthread = true\ntier = 3\n')
        post = ingest.registered_post(STATUS)
        self.assertEqual(post["id"], "clay-attribution")
        self.assertEqual(ingest.registered_id(STATUS), "clay-attribution")
        self.assertTrue(ingest.thread_enabled("clay-attribution"))

    def test_unregistered_status(self):
        self.write('[[x_post]]\nid = "other"\n'
                   'url = "https://x.com/a/status/9"\nauthor = "a"\n')
        self.assertIsNone(ingest.registered_post(STATUS))
        self.assertIsNone(ingest.registered_id(STATUS))

    def test_thread_enabled_is_false_for_a_plain_post(self):
        self.write(f'[[x_post]]\nid = "plain"\nurl = "{URL}"\n'
                   'author = "clay_garrett"\n')
        self.assertFalse(ingest.thread_enabled("plain"))

    def test_thread_enabled_does_not_match_a_web_source_id(self):
        # A [[source]] sharing the slug must not make a conversation look
        # pollable: the thread capture polls by id, and the wrong id would
        # file this post's capture under someone else's source.
        self.write('[[source]]\nid = "clay-attribution"\n'
                   'url = "https://example.org/"\n')
        self.assertFalse(ingest.thread_enabled("clay-attribution"))

    def test_a_shorter_id_does_not_match_a_longer_one(self):
        # The lookup used to be `tweet_id in url`. X ids vary in length, so a
        # substring test resolves this post to somebody else's entry and files
        # the capture under the wrong id.
        self.write('[[x_post]]\nid = "other"\n'
                   f'url = "https://x.com/a/status/{STATUS}"\nauthor = "a"\n')
        self.assertIsNone(ingest.registered_post(STATUS[:8]))
        self.assertIsNone(ingest.registered_post(STATUS[3:]))
        self.assertEqual(ingest.registered_post(STATUS)["id"], "other")

    def test_a_non_x_url_names_no_status(self):
        self.assertIsNone(ingest.status_in("https://example.org/status/123"))
        self.assertIsNone(ingest.status_in(""))

    def test_unparseable_registry_is_survivable(self):
        self.write("[[x_post]]\nid = not-a-toml-value\n")
        self.assertIsNone(ingest.registered_post(STATUS))
        self.assertFalse(ingest.thread_enabled("clay-attribution"))

    def test_missing_registry_is_survivable(self):
        self.assertIsNone(ingest.registered_post(STATUS))


class ResolveTier(unittest.TestCase):
    """--thread states a cadence, or it does not run."""

    def test_no_thread_no_tier(self):
        self.assertIsNone(ingest.resolve_tier(False, None, None, False))

    def test_tier_without_thread_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            ingest.resolve_tier(False, 3, None, False)
        self.assertIn("--tier applies only to a polled thread", str(ctx.exception))

    def test_new_post_needs_a_tier(self):
        with self.assertRaises(ValueError) as ctx:
            ingest.resolve_tier(True, None, None, False)
        self.assertIn("--tier", str(ctx.exception))

    def test_new_post_with_a_tier(self):
        self.assertEqual(ingest.resolve_tier(True, 3, None, False), 3)

    def test_tier_must_be_a_known_lane(self):
        with self.assertRaises(ValueError):
            ingest.resolve_tier(True, 4, None, False)

    def test_no_register_has_nothing_to_poll(self):
        with self.assertRaises(ValueError) as ctx:
            ingest.resolve_tier(True, 3, None, True)
        self.assertIn("--no-register", str(ctx.exception))

    def test_registered_thread_supplies_its_own_tier(self):
        existing = {"id": "clay-attribution", "thread": True, "tier": 3}
        self.assertEqual(ingest.resolve_tier(True, None, existing, False), 3)
        self.assertEqual(ingest.resolve_tier(True, 3, existing, False), 3)

    def test_registered_thread_may_be_captured_with_no_register(self):
        existing = {"id": "clay-attribution", "thread": True, "tier": 3}
        self.assertEqual(ingest.resolve_tier(True, None, existing, True), 3)

    def test_tier_disagreeing_with_the_registry_is_refused(self):
        existing = {"id": "clay-attribution", "thread": True, "tier": 3}
        with self.assertRaises(ValueError) as ctx:
            ingest.resolve_tier(True, 1, existing, False)
        self.assertIn("tier 3", str(ctx.exception))

    def test_enabling_a_thread_on_a_registered_post_stays_a_human_edit(self):
        # Appending a second block for the same post would give one
        # conversation two registry entries and two source pages.
        existing = {"id": "clay-attribution"}
        with self.assertRaises(ValueError) as ctx:
            ingest.resolve_tier(True, 3, existing, False)
        self.assertIn("thread = true", str(ctx.exception))


class RegisterBlock(unittest.TestCase):
    """What is appended must be what capture.py will poll."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sources.toml"
        self.path.write_text("", encoding="utf-8")
        self._real = ingest.SOURCES
        ingest.SOURCES = self.path
        self.addCleanup(setattr, ingest, "SOURCES", self._real)

    def written(self) -> dict:
        return tomllib.loads(self.path.read_text(encoding="utf-8"))

    def test_plain_post_declares_no_thread(self):
        ingest.register("clay-attribution", URL, "clay_garrett",
                        "2026-07-31T17:42:45Z", None, "why this matters")
        cfg = self.written()
        post = cfg["x_post"][0]
        self.assertNotIn("thread", post)
        self.assertNotIn("tier", post)
        capture.validate_sources(cfg)

    def test_thread_post_declares_thread_and_tier(self):
        ingest.register("clay-attribution", URL, "clay_garrett",
                        "2026-07-31T17:42:45Z", "attribution",
                        "why this matters", thread=True, tier=3)
        cfg = self.written()
        post = cfg["x_post"][0]
        self.assertIs(post["thread"], True)
        self.assertEqual(post["tier"], 3)
        self.assertEqual(post["author"], "clay_garrett")
        capture.validate_sources(cfg)

    def test_the_written_block_polls_as_a_thread_source(self):
        ingest.register("clay-attribution", URL, "clay_garrett",
                        "2026-07-31T17:42:45Z", None, "why this matters",
                        thread=True, tier=3)
        cfg = self.written()
        sources = capture.pollable_sources(cfg)
        self.assertEqual([s["id"] for s in sources], ["clay-attribution"])
        source = sources[0]
        self.assertEqual(source["capture"], "x-thread")
        self.assertEqual(source["kind"], "social-thread")
        self.assertEqual(source["x_author"], "clay_garrett")
        self.assertEqual(source["tier"], 3)


class CaptureThreadGuard(unittest.TestCase):
    """The thread capture polls by id, so it refuses an id it cannot verify
    resolves to this post's conversation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sources.toml"
        self._real = ingest.SOURCES
        ingest.SOURCES = self.path
        self.addCleanup(setattr, ingest, "SOURCES", self._real)
        self.ran: list[list[str]] = []

    def test_refuses_without_spawning_a_capture(self):
        self.path.write_text(f'[[x_post]]\nid = "plain"\nurl = "{URL}"\n'
                             'author = "clay_garrett"\n', encoding="utf-8")
        real_run = ingest.subprocess.run
        ingest.subprocess.run = lambda *a, **k: self.fail("spawned a capture")
        self.addCleanup(setattr, ingest.subprocess, "run", real_run)
        self.assertEqual(ingest.capture_thread_now("plain"), 2)

    def register_thread(self) -> None:
        self.path.write_text(f'[[x_post]]\nid = "convo"\nurl = "{URL}"\n'
                             'author = "clay_garrett"\nthread = true\n'
                             'tier = 3\n', encoding="utf-8")

    def stub_run(self, code: int) -> None:
        class Result:
            returncode = code

        def fake_run(cmd, **kwargs):
            self.ran.append(cmd)
            return Result()

        real_run = ingest.subprocess.run
        ingest.subprocess.run = fake_run
        self.addCleanup(setattr, ingest.subprocess, "run", real_run)

    def test_polls_the_registered_thread_by_id_and_kind(self):
        self.register_thread()
        self.stub_run(0)
        self.assertEqual(ingest.capture_thread_now("convo"), 0)
        self.assertEqual(len(self.ran), 1)
        cmd = self.ran[0]
        self.assertIn("capture", cmd)
        self.assertEqual(cmd[cmd.index("--id") + 1], "convo")
        self.assertEqual(cmd[cmd.index("--kind") + 1], "social-thread")
        self.assertTrue(cmd[1].endswith("capture.py"))

    def test_a_change_is_the_asked_for_outcome_not_a_failure(self):
        # capture.py exits 10 on a healthy run that found a change, and a
        # first capture of a conversation is a change by definition.
        self.register_thread()
        self.stub_run(10)
        self.assertEqual(ingest.capture_thread_now("convo"), 0)

    def test_an_incomplete_poll_still_fails(self):
        self.register_thread()
        self.stub_run(20)
        self.assertEqual(ingest.capture_thread_now("convo"), 20)

    def test_a_busy_writer_lock_still_fails(self):
        self.register_thread()
        self.stub_run(21)
        self.assertEqual(ingest.capture_thread_now("convo"), 21)


if __name__ == "__main__":
    unittest.main()
