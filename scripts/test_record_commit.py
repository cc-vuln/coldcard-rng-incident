#!/usr/bin/env python3
"""Tests for the deterministic pipeline committer, on throwaway git repos.

Every test builds a temp repository shaped like this one — same allowlist
paths, same guard-run layout under .work/agent-guard/ — and exercises the
commit-decision functions directly: what stages, what the message says, what
the lint refuses, and which guard runs block. Nothing here runs `just`, an
audit, or touches the real repository; the preconditions that shell out are
covered by construction (their checks are these functions) rather than by
execution.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import record_commit as rc  # noqa: E402


SOURCES = '''\
[meta]
generated = "by hand"

[[source]]
id = "stackernews-example-thread"
title = "An example thread"
url = "https://stacker.news/items/111111"
tier = 2
'''

REVIEWS = '''\
# Human review of detected text differences.

[[revision]]
source = "stackernews-example-thread"
timestamp = "20260801T000000Z"
status = "source-content"
summary = "An existing classification."
'''


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


class RepoFixture(unittest.TestCase):
    """A temp git repository on main with the layout the allowlist names."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "symbolic-ref", "HEAD", "refs/heads/main")
        git(self.root, "config", "user.name", "Test Operator")
        git(self.root, "config", "user.email", "operator@example.invalid")
        self.write("sources.toml", SOURCES)
        self.write("revision-reviews.toml", REVIEWS)
        self.write("archive/snapshots/stackernews-example-thread/"
                   "20260801T000000Z.txt", "the first capture\n")
        self.write("archive/index.jsonl", '{"event":"capture"}\n')
        self.write("site/src/pages/index.astro", "<h1>record</h1>\n")
        self.write("docs/README.md", "# docs\n")
        self.write("scripts/capture.py", "# capture\n")
        self.write("justfile", "default:\n    @just --list\n")
        self.write("AGENTS.md", "# agents\n")
        self.write("BACKLOG.md", "# backlog\n")
        self.write("CHANGELOG.md", "# changelog\n")
        self.write("DISCOVERY.md", "# discovery\n\n## Pending\n")
        self.write(".gitignore", ".work/\n.env\n")
        self.commit("record: the baseline")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def append(self, rel: str, text: str) -> None:
        with (self.root / rel).open("a") as handle:
            handle.write(text)

    def commit(self, message: str) -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", message, "--quiet")

    def head_epoch(self) -> int:
        return int(git(self.root, "log", "-1", "--format=%ct"))

    def guard_run(self, name: str, passed: bool, mtime: int | None = None) -> Path:
        run = self.root / ".work" / "agent-guard" / name
        run.mkdir(parents=True)
        (run / "manifest.json").write_text("{}")
        if passed:
            (run / "approved-captures.txt").write_text("")
        if mtime is not None:
            os.utime(run, (mtime, mtime))
        return run


class TestBranchAndGuardRuns(RepoFixture):

    def test_current_branch(self) -> None:
        self.assertEqual(rc.current_branch(self.root), "main")
        git(self.root, "checkout", "-b", "wip", "--quiet")
        self.assertEqual(rc.current_branch(self.root), "wip")

    def test_passed_run_does_not_block(self) -> None:
        self.guard_run("20990101T000000Z-1", passed=True)
        self.assertEqual(rc.unresolved_guard_runs(self.root), [])

    def test_unpassed_run_since_last_commit_blocks(self) -> None:
        # agent_guard.py writes approved-captures.txt only on a pass, so a
        # run directory without one — rejected, in flight, or dead mid-run —
        # must stop the committer: its edits are evidence, not churn.
        self.guard_run("20990101T000000Z-1", passed=False)
        self.assertEqual(rc.unresolved_guard_runs(self.root),
                         ["20990101T000000Z-1"])

    def test_unpassed_run_before_last_commit_is_ignored(self) -> None:
        # A run that was already sitting in the tree when the last commit was
        # made has been seen; blocking on it forever would stop the line.
        old = self.head_epoch() - 3600
        self.guard_run("20200101T000000Z-1", passed=False, mtime=old)
        self.assertEqual(rc.unresolved_guard_runs(self.root), [])

    def test_unparseable_run_name_falls_back_to_mtime(self) -> None:
        old = self.head_epoch() - 3600
        self.guard_run("stray-directory", passed=False, mtime=old)
        self.assertEqual(rc.unresolved_guard_runs(self.root), [])
        self.guard_run("fresh-stray", passed=False)  # mtime is now
        self.assertEqual(rc.unresolved_guard_runs(self.root), ["fresh-stray"])


class TestStaging(RepoFixture):

    def test_allowlist_stages_and_secrets_do_not(self) -> None:
        self.append("revision-reviews.toml", "\n[[revision]]\nsource = \"x\"\n")
        self.write("registry/sources/example.toml", "[[source]]\nid = \"example\"\n")
        self.write("site/src/data/x-thread-media.json", "{}\n")
        self.write(".env", "NOSTR_SECRET_KEY=nsec1notreal\n")
        self.write(".work/scratch.txt", "operator scratch\n")
        staged = {path: status for status, path in rc.stage(self.root)}
        self.assertIn("revision-reviews.toml", staged)
        self.assertIn("registry/sources/example.toml", staged)
        self.assertIn("site/src/data/x-thread-media.json", staged)
        self.assertNotIn(".env", staged)
        self.assertTrue(all(not p.startswith(".work/") for p in staged))

    def test_deletions_inside_the_allowlist_are_staged(self) -> None:
        (self.root / "docs" / "README.md").unlink()
        staged = {path: status for status, path in rc.stage(self.root)}
        self.assertEqual(staged.get("docs/README.md"), "D")

    def test_missing_allowlist_paths_are_tolerated(self) -> None:
        # This fixture has no quarantine/ or corrections.toml; staging must
        # not fail for their absence.
        self.assertEqual(rc.stage(self.root), [])

    def test_clean_tree_stages_nothing(self) -> None:
        self.assertEqual(rc.stage(self.root), [])


class TestSummaryAndMessage(RepoFixture):

    def churn(self) -> list[tuple[str, str]]:
        # Two new snapshot captures (three files for one, one for another),
        # a social capture, a registration, a classification, a correction,
        # a rotated discovery file and a quarantine move.
        self.write("archive/snapshots/stackernews-example-thread/"
                   "20260808T000000Z.txt", "changed text\n")
        self.write("archive/snapshots/stackernews-example-thread/"
                   "20260808T000000Z.meta.json", "{}\n")
        self.write("archive/snapshots/coldcard-hack-tracker/"
                   "20260808T000000Z.txt", "tracker page\n")
        self.write("archive/x/some-post/20260808T000000Z/event.json", "{}\n")
        self.append("sources.toml", '\n[[source]]\nid = "new-thread"\n')
        self.append("revision-reviews.toml",
                    '\n[[revision]]\nsource = "new-thread"\n'
                    'timestamp = "20260808T000000Z"\n')
        self.write("corrections.toml",
                   '# corrections\n\n[[correction]]\ndate = "2026-08-08"\n')
        self.write("discovery/assessed-2026-08.md", "# assessed\n")
        self.write("quarantine/registry-2026-08.toml", "# quarantined\n")
        return rc.stage(self.root)

    def test_summarize_counts(self) -> None:
        staged = self.churn()
        summary = rc.summarize(self.root, staged)
        self.assertEqual(summary.snapshots, 2)
        self.assertEqual(summary.social_captures, 1)
        self.assertEqual(summary.registrations, 1)
        self.assertEqual(summary.classifications, 1)
        self.assertEqual(summary.corrections, 1)
        self.assertEqual(summary.rotations, 1)
        self.assertEqual(summary.quarantines, 1)

    def test_message_shape_and_prefixes(self) -> None:
        staged = self.churn()
        message = rc.build_message(rc.summarize(self.root, staged))
        subject = message.splitlines()[0]
        self.assertTrue(subject.startswith("record: "), subject)
        self.assertIn("2 new snapshot capture(s)", subject)
        self.assertIn("1 registration(s)", subject)
        self.assertIn("1 classification(s)", subject)
        self.assertIn("1 correction(s)", subject)
        self.assertEqual(rc.lint_message(self.root, message), [])

    def test_prefixes_by_change_class(self) -> None:
        self.assertEqual(rc.classify([("A", "archive/snapshots/s/t.txt")]),
                         "record")
        self.assertEqual(rc.classify([("M", "sources.toml")]), "agents")
        self.assertEqual(rc.classify([("M", "DISCOVERY.md")]), "agents")
        self.assertEqual(rc.classify([("M", "registry/manifest.json")]),
                         "agents")
        self.assertEqual(rc.classify([("M", "site/src/pages/index.astro")]),
                         "site")
        self.assertEqual(rc.classify([("M", "scripts/capture.py"),
                                      ("M", "docs/README.md")]), "automation")

    def test_structured_candidates_are_not_counted_as_legacy_rotations(self) -> None:
        self.write("discovery/candidates/reddit/abc.json", "{}\n")
        summary = rc.summarize(self.root, rc.stage(self.root))
        self.assertEqual(0, summary.rotations)


class TestMessageLint(RepoFixture):

    def test_attribution_strings_are_refused(self) -> None:
        for bad in ("Co-Authored-By: Some Tool <tool@example>",
                    "generated with a large language model"):
            problems = rc.lint_message(self.root, f"record: x\n\n{bad}\n")
            self.assertTrue(problems, bad)

    def test_operator_needles_are_refused(self) -> None:
        self.write("site/tools/private-tokens.json",
                   '[{"needle": "sekrit-operator-needle"}]')
        problems = rc.lint_message(
            self.root, "record: x\n\nmentions SEKRIT-OPERATOR-NEEDLE here\n")
        self.assertTrue(problems)
        # Case-insensitive, and a clean message in the same repo passes.
        self.assertEqual(rc.lint_message(self.root, "record: x\n\nbody\n"),
                         [])


class TestCommitFlow(RepoFixture):

    def test_build_lock_is_exclusive(self) -> None:
        path = self.root / "build.lock"
        with rc.build_lock(path):
            with self.assertRaises(rc.BuildLockBusy):
                with rc.build_lock(path):
                    self.fail("a second owner acquired the build lock")

    def test_stage_and_commit_leaves_a_clean_tree(self) -> None:
        self.write("archive/snapshots/stackernews-example-thread/"
                   "20260808T000000Z.txt", "changed\n")
        staged = rc.stage(self.root)
        self.assertTrue(staged)
        message = rc.build_message(rc.summarize(self.root, staged))
        self.assertEqual(rc.lint_message(self.root, message), [])
        rc.commit_staged(self.root, message)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")
        self.assertEqual(
            git(self.root, "log", "-1", "--format=%s"),
            "record: 1 new snapshot capture(s)")

    def test_temp_index_leaves_the_real_index_untouched(self) -> None:
        # --dry-run stages through GIT_INDEX_FILE; the operator's index must
        # look exactly as it did before.
        self.write("docs/operations.md", "# ops\n")
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index"
            rc._prepare_temp_index(self.root, rc._staging_env(index))
            staged = rc.stage(self.root, index_file=index)
            by_path = {path: status for status, path in staged}
            self.assertEqual(by_path.get("docs/operations.md"), "A")
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"),
                         "")


if __name__ == "__main__":
    unittest.main()
