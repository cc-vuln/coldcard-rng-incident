#!/usr/bin/env python3
"""Focused tests for the structured-discovery operator status."""
from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import report_status  # noqa: E402


class FakeStore:
    def __init__(self, _root: Path):
        pass

    @contextmanager
    def locked(self):
        yield

    def list_candidates(self, *, lock_held: bool = False) -> list[dict]:
        if not lock_held:
            raise AssertionError("status must reuse its held discovery lock")
        return [
            {"state": "pending", "platform": "reddit"},
            {"state": "pending", "platform": "x"},
            {"state": "deferred", "platform": "reddit"},
            {"state": "human-review", "platform": "x"},
            {"state": "assessed", "platform": "stackernews"},
        ]


class DiscoveryStatus(unittest.TestCase):
    def module(self, validator) -> types.ModuleType:
        module = types.ModuleType("discovery_store")
        module.DiscoveryStore = FakeStore
        module.validate_store = validator
        return module

    def test_valid_store_reports_actionable_counts(self) -> None:
        calls = []

        def validate(root: Path, *, lock_held: bool = False) -> None:
            calls.append((root, lock_held))

        out = StringIO()
        with patch.dict(sys.modules, {"discovery_store": self.module(validate)}), \
                redirect_stdout(out):
            self.assertTrue(report_status.discovery_queue())
        self.assertEqual([(report_status.ROOT, True)], calls)
        text = out.getvalue()
        self.assertIn("2 (1 community, 1 X)", text)
        self.assertIn("human review: 1", text)
        self.assertIn("canonical chain", text)

    def test_invalid_store_is_visible_and_fails(self) -> None:
        def validate(_root: Path, *, lock_held: bool = False) -> None:
            raise ValueError("transaction chain broke")

        out = StringIO()
        with patch.dict(sys.modules, {"discovery_store": self.module(validate)}), \
                redirect_stdout(out):
            self.assertFalse(report_status.discovery_queue())
        self.assertIn("INVALID: transaction chain broke", out.getvalue())


if __name__ == "__main__":
    unittest.main()
