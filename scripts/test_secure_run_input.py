#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import secure_run_input


class SecureInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.destination = self.root / "guard" / "input"

    def test_regular_bounded_input_is_snapshotted_exclusively(self) -> None:
        self.source.write_bytes(b"verdict\n")
        self.assertEqual(8, secure_run_input.snapshot(
            self.source, self.destination))
        self.assertEqual(b"verdict\n", self.destination.read_bytes())
        self.assertEqual(0o600, self.destination.stat().st_mode & 0o777)
        self.assertFalse(self.source.exists())
        with self.assertRaises(secure_run_input.InputError):
            secure_run_input.snapshot(self.destination, self.destination)

    def test_symlink_is_rejected_and_destination_is_absent(self) -> None:
        target = self.root / "target"
        target.write_text("secret")
        self.source.symlink_to(target)
        with self.assertRaises(secure_run_input.InputError):
            secure_run_input.snapshot(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_oversize_file_is_rejected_and_destination_is_absent(self) -> None:
        self.source.write_bytes(b"x" * 65)
        with self.assertRaisesRegex(secure_run_input.InputError, "exceeds"):
            secure_run_input.snapshot(self.source, self.destination,
                                      max_bytes=64)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
