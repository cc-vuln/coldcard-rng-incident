import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class XProbationTests(unittest.TestCase):
    def make_checkout(self, raw: str) -> Path:
        """A checkout complete enough for the driver's containment to run.

        The driver no longer just renders a prompt and shells out. It records
        the tree, drops privilege, and checks what came back, so the fixture
        needs the whole of scripts/, an interpreter, and a git repository for
        the guard to enumerate. Copying less would test a driver that does
        not exist.
        """
        root = Path(raw)
        shutil.copytree(ROOT / "scripts", root / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (root / ".venv").symlink_to(ROOT / ".venv")
        (root / ".work").mkdir()
        (root / ".env").write_text(
            "REVIEW_AGENT_BIN=/bin/false\n"
            "X_REVIEW_AGENT_BIN=/bin/false\n"
            # No agent account in a test tree, so the privilege drop is
            # deliberately opted out of rather than silently skipped.
            "AGENT_SANDBOX=off\n",
            encoding="utf-8",
        )
        (root / ".gitignore").write_text(".work/\n.env\n", encoding="utf-8")
        # The driver builds a coverage index from the registry before it will
        # start an agent, and refuses to run without one.
        (root / "sources.toml").write_text(
            '[[source]]\nid = "reddit-example"\n'
            'title = "r/Bitcoin: an already registered theme"\n'
            'url = "https://www.reddit.com/r/Bitcoin/comments/aaa/x/"\n'
            'org = "reddit"\n',
            encoding="utf-8",
        )
        (root / "DISCOVERY.md").write_text(
            "# Discovery intake\n\n## Pending\n\n"
            "- 2026-08-05 [candidate](https://x.com/researcher/status/123) "
            "(X @researcher)\n\n## Assessed\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
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

    def test_a_run_without_a_coverage_index_does_not_start(self):
        """An agent that cannot see what is covered registers duplicates of it.

        Duplicates in the registry cost far more to undo than a skipped tick,
        so the driver refuses rather than running blind.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            (root / "sources.toml").unlink()
            result = self.run_intake(root, "--include-x")

        self.assertEqual(result.returncode, 1)
        self.assertIn("could not build the coverage index", result.stderr)
        self.assertFalse(
            (Path(raw) / ".work/agent-discovery-intake/prompt-rendered.md").exists())

    def test_the_driver_builds_the_coverage_index_before_the_agent(self):
        """Built as the operator account, from the registry, before the drop.

        The community template consumes it; this checks the driver's half,
        which is that the file exists and describes the registry. Asserting on
        the rendered community prompt would mean hydrating a candidate body
        over the network, which these tests do not do.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            self.run_intake(root, "--include-x")
            coverage = (
                root / ".work/agent-discovery-intake/coverage.md"
            ).read_text(encoding="utf-8")

        self.assertIn("reddit-example", coverage)
        self.assertIn("r/Bitcoin: an already registered theme", coverage)

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
            # Explicitly empty, not omitted: `just test` dotenv-loads the real
            # .env, which may now set X_REVIEW_AGENT_BIN, and the driver would
            # otherwise inherit it past this fixture.
            (root / ".env").write_text(
                "REVIEW_AGENT_BIN=/bin/false\nX_REVIEW_AGENT_BIN=\n",
                encoding="utf-8",
            )
            result = self.run_intake(root, "--include-x")

        self.assertEqual(result.returncode, 0)
        self.assertIn("X_REVIEW_AGENT_BIN is unset", result.stdout)


if __name__ == "__main__":
    unittest.main()
