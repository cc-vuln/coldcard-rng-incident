from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from render_review_packets import changed_lines


class ReviewPacketTests(unittest.TestCase):
    def test_keeps_only_changed_lines_and_drops_file_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "change.diff"
            path.write_text(
                "--- source@old\n+++ source@new\n@@ -1 +1 @@\n"
                "-old claim\n+new claim\n unchanged context\n"
            )
            self.assertEqual(
                changed_lines(path, 10), (["-old claim", "+new claim"], False)
            )

    def test_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "change.diff"
            path.write_text("+one\n+two\n+three\n")
            self.assertEqual(changed_lines(path, 2), (["+one", "+two"], True))


if __name__ == "__main__":
    unittest.main()
