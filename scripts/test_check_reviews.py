#!/usr/bin/env python3
"""Contract tests for the additive revision-classification ledger."""
from __future__ import annotations

import unittest

import check_reviews as cr


class ShapeTests(unittest.TestCase):
    def setUp(self):
        self.key = ("source-a", "20260809T120000Z")
        self.base = {
            "source": self.key[0],
            "timestamp": self.key[1],
            "status": "capture-noise",
            "summary": "Only collection chrome changed.",
        }

    def problems(self, *entries):
        return cr.shape_problems(list(entries), {self.key})

    def test_human_override_is_additive_and_valid(self):
        corrected = {
            **self.base,
            "status": "source-content",
            "summary": "The publisher changed the incident text.",
            "classifier": "human",
        }
        self.assertEqual(self.problems(self.base, corrected), [])

    def test_non_human_duplicate_is_rejected(self):
        duplicate = {**self.base, "classifier": "review-agent"}
        self.assertTrue(any(
            "classifier = \"human\"" in problem
            for problem in self.problems(self.base, duplicate)
        ))

    def test_unknown_classifier_is_rejected(self):
        bad = {**self.base, "classifier": "mystery"}
        self.assertTrue(any(
            "unknown classifier" in problem for problem in self.problems(bad)
        ))


if __name__ == "__main__":
    unittest.main()
