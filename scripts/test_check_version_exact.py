#!/usr/bin/env python3
"""Tests for the pre-deploy commit exactness gate."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import check_version_exact as check


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class VersionExactnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init")
        git(self.root, "config", "user.name", "Test Operator")
        git(self.root, "config", "user.email", "operator@example.invalid")
        tracked = self.root / "tracked.txt"
        tracked.write_text("one\n", encoding="utf-8")
        git(self.root, "add", "tracked.txt")
        git(self.root, "commit", "-m", "baseline", "--quiet")
        self.version = self.root / "version.json"
        self.write_version(git(self.root, "rev-parse", "HEAD"), True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_version(self, commit: str, matches: bool) -> None:
        self.version.write_text(json.dumps({
            "build": {"commit": commit, "matches_commit": matches},
        }), encoding="utf-8")

    def test_exact_clean_commit_passes(self) -> None:
        self.assertEqual(check.problems(self.root, self.version), [])

    def test_build_marked_dirty_fails(self) -> None:
        self.write_version(git(self.root, "rev-parse", "HEAD"), False)
        self.assertIn(
            "matches_commit",
            " ".join(check.problems(self.root, self.version)),
        )

    def test_head_moved_after_build_fails(self) -> None:
        old = git(self.root, "rev-parse", "HEAD")
        (self.root / "tracked.txt").write_text("two\n", encoding="utf-8")
        git(self.root, "commit", "-am", "next", "--quiet")
        self.write_version(old, True)
        self.assertIn(
            "not current HEAD",
            " ".join(check.problems(self.root, self.version)),
        )

    def test_tracked_file_changed_after_build_fails(self) -> None:
        (self.root / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        self.assertIn(
            "tracked files changed",
            " ".join(check.problems(self.root, self.version)),
        )

    def test_untracked_file_does_not_fail(self) -> None:
        (self.root / "untracked.txt").write_text("ignored here\n", encoding="utf-8")
        self.assertEqual(check.problems(self.root, self.version), [])

    def test_missing_version_fails_closed(self) -> None:
        self.version.unlink()
        self.assertTrue(check.problems(self.root, self.version))


if __name__ == "__main__":
    unittest.main()
