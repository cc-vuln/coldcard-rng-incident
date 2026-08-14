#!/usr/bin/env python3
"""Focused tests for copying an immutable deposit source tree."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_deposit  # noqa: E402


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        text=True,
    ).stdout.strip()


class CommittedTreeCopy(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Test Operator")
        git(self.root, "config", "user.email", "operator@example.invalid")
        (self.root / "plain.txt").write_text("committed\n")
        executable = self.root / "tool.sh"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        (self.root / "link").symlink_to("plain.txt")
        held = self.root / "archive/snapshots/example/20260801T000000Z.txt"
        held.parent.mkdir(parents=True)
        held.write_text("third-party capture\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "baseline")
        self.commit = git(self.root, "rev-parse", "HEAD")
        self.real_repo = make_deposit.REPO
        make_deposit.REPO = self.root
        self.addCleanup(setattr, make_deposit, "REPO", self.real_repo)

    def test_copy_uses_the_named_commit_not_dirty_worktree_bytes(self) -> None:
        (self.root / "plain.txt").write_text("dirty replacement\n")
        out = self.root / "out"
        make_deposit.copy_committed_tree(self.commit, out)
        self.assertEqual("committed\n", (out / "plain.txt").read_text())
        self.assertFalse((out / "archive/snapshots").exists())

    def test_modes_and_symlinks_are_preserved(self) -> None:
        out = self.root / "out"
        make_deposit.copy_committed_tree(self.commit, out)
        self.assertEqual(0o755, (out / "tool.sh").stat().st_mode & 0o777)
        self.assertTrue((out / "link").is_symlink())
        self.assertEqual("plain.txt", os.readlink(out / "link"))


if __name__ == "__main__":
    unittest.main()
