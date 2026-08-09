#!/usr/bin/env python3
"""Focused tests for Wayback replay handling."""

from __future__ import annotations

import unittest

import wayback


class WaybackPrivacyTests(unittest.TestCase):
    def test_replayed_body_uses_the_live_capture_geo_scrubber(self) -> None:
        body = (
            b'<a href="/signup?country%3DCA%26city%3DToronto">join</a>'
            b'<p>The publisher discusses Canada and Toronto.</p>'
        )
        scrubbed, count = wayback.scrub_replay_geo(body)
        text = scrubbed.decode()
        self.assertEqual(count, 2)
        self.assertNotIn("country%3DCA", text)
        self.assertNotIn("city%3DToronto", text)
        self.assertIn("discusses Canada and Toronto", text)


if __name__ == "__main__":
    unittest.main()
