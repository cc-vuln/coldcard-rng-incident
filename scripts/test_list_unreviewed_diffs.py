from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from list_unreviewed_diffs import bounded, unreviewed


class UnreviewedDiffTests(unittest.TestCase):
    def test_excludes_classified_and_orders_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            diffs = root / "diffs"
            for source, timestamp in [
                ("alpha", "20260805T020000Z"),
                ("beta", "20260805T010000Z"),
                ("alpha", "20260805T000000Z"),
            ]:
                path = diffs / source / f"{timestamp}.diff"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("diff")
            reviews = root / "reviews.toml"
            reviews.write_text(
                "[[revision]]\nsource = \"alpha\"\n"
                "timestamp = \"20260805T000000Z\"\n"
                "status = \"capture-noise\"\nsummary = \"done\"\n"
            )
            self.assertEqual(
                [(p.parent.name, p.stem) for p in unreviewed(diffs, reviews)],
                [("beta", "20260805T010000Z"),
                 ("alpha", "20260805T020000Z")],
            )

    def test_explicit_unreviewed_entry_stays_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "diffs/source/20260805T000000Z.diff"
            path.parent.mkdir(parents=True)
            path.write_text("diff")
            reviews = root / "reviews.toml"
            reviews.write_text(
                "[[revision]]\nsource = \"source\"\n"
                "timestamp = \"20260805T000000Z\"\n"
                "status = \"unreviewed\"\nsummary = \"pending\"\n"
            )
            self.assertEqual(unreviewed(root / "diffs", reviews), [path])

    def test_byte_budget_stops_before_the_next_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for number in range(3):
                path = root / f"{number}.diff"
                path.write_bytes(b"x" * 10)
                paths.append(path)
            self.assertEqual(bounded(paths, limit=3, max_bytes=19), [paths[0]])
            self.assertEqual(bounded(paths, limit=2, max_bytes=20), paths[:2])

    def test_byte_budget_allows_one_oversized_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.diff"
            path.write_bytes(b"x" * 20)
            self.assertEqual(bounded([path], limit=1, max_bytes=10), [path])


if __name__ == "__main__":
    unittest.main()
