import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class XProbationTests(unittest.TestCase):
    def make_checkout(self, raw: str) -> Path:
        root = Path(raw)
        scripts = root / "scripts"
        scripts.mkdir()
        shutil.copy2(
            ROOT / "scripts/agent-discovery-intake.sh",
            scripts / "agent-discovery-intake.sh",
        )
        shutil.copy2(
            ROOT / "scripts/agent-discovery-intake-prompt.md",
            scripts / "agent-discovery-intake-prompt.md",
        )
        shutil.copy2(
            ROOT / "scripts/agent-x-discovery-triage-prompt.md",
            scripts / "agent-x-discovery-triage-prompt.md",
        )
        (root / ".env").write_text(
            "REVIEW_AGENT_BIN=/bin/false\n"
            "X_REVIEW_AGENT_BIN=/bin/false\n",
            encoding="utf-8",
        )
        (root / "DISCOVERY.md").write_text(
            "# Discovery intake\n\n## Pending\n\n"
            "- 2026-08-05 [candidate](https://x.com/researcher/status/123) "
            "(X @researcher)\n\n## Assessed\n",
            encoding="utf-8",
        )
        return root

    def run_intake(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/bash", str(root / "scripts/agent-discovery-intake.sh"), *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_scheduled_shape_excludes_x_candidates(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_intake(self.make_checkout(raw))

        self.assertEqual(result.returncode, 0)
        self.assertIn("require --include-x", result.stdout)

    def test_include_x_explicitly_makes_candidate_eligible(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_intake(self.make_checkout(raw), "--include-x")

        self.assertEqual(result.returncode, 1)
        self.assertIn("assessing 1 of 1", result.stdout)

    def test_include_x_is_x_only_despite_community_backlog(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            (root / "DISCOVERY.md").write_text(
                "# Discovery intake\n\n## Pending\n\n"
                "- 2026-08-05 [community](https://stacker.news/items/999) "
                "by author, 1 comments (Stacker News)\n"
                "- 2026-08-05 [candidate](https://x.com/researcher/status/123) "
                "(X @researcher)\n\n## Assessed\n",
                encoding="utf-8",
            )
            result = self.run_intake(
                root, "--include-x", "--max", "1"
            )
            prompt = (
                root / ".work/agent-discovery-intake/prompt-rendered.md"
            ).read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 1)
        self.assertIn("https://x.com/researcher/status/123", prompt)
        self.assertNotIn("https://stacker.news/items/999", prompt)
        self.assertIn("Do not run `ingest-x.py`", prompt)

    def test_x_intake_never_falls_back_to_general_agent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            (root / ".env").write_text(
                "REVIEW_AGENT_BIN=/bin/false\n", encoding="utf-8"
            )
            result = self.run_intake(root, "--include-x")

        self.assertEqual(result.returncode, 0)
        self.assertIn("X_REVIEW_AGENT_BIN is unset", result.stdout)


if __name__ == "__main__":
    unittest.main()
