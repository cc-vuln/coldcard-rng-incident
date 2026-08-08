import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def clean_env() -> dict:
    """The test environment, minus anything that could turn hydration live.

    `just test` dotenv-loads the real .env, and the hydration fallback for X
    is the deprecated API lane: an inherited X_API_BEARER_TOKEN would make a
    candidate read a network call. Pinning it empty keeps the fallback a
    fast, local credential error.
    """
    env = dict(os.environ)
    env["X_API_BEARER_TOKEN"] = ""
    return env


class LaneRoutingTests(unittest.TestCase):
    def make_checkout(self, raw: str) -> Path:
        """A checkout complete enough for the drivers' containment to run.

        The drivers no longer just render a prompt and shell out. They record
        the tree, drop privilege, and check what came back, so the fixture
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
            # No agent account in a test tree, so the privilege drop is
            # deliberately opted out of rather than silently skipped.
            "AGENT_SANDBOX=off\n",
            encoding="utf-8",
        )
        (root / ".gitignore").write_text(".work/\n.env\n", encoding="utf-8")
        # The drivers build a coverage index from the registry before they
        # will start an agent, and refuse to run without one.
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

    def run_driver(self, root: Path, script: str,
                   *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["/bin/bash", str(root / "scripts" / script), *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            env=clean_env(),
        )

    def run_intake(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return self.run_driver(root, "agent-discovery-intake.sh", *args)

    def run_x_intake(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return self.run_driver(root, "agent-x-intake.sh", *args)

    def test_community_driver_excludes_x_candidates(self):
        """X candidates are the X lane's, whatever else is pending."""
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_intake(self.make_checkout(raw))

        self.assertEqual(result.returncode, 0)
        self.assertIn("they are the X lane's", result.stdout)

    def test_include_x_flag_is_retired(self):
        """The admission flag went with the read-only triage prompt."""
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_intake(self.make_checkout(raw), "--include-x")

        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown argument", result.stderr)

    def test_x_driver_assesses_x_candidates(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            result = self.run_x_intake(root)
            prompt = (
                root / ".work/agent-x-intake/prompt-rendered.md"
            ).read_text(encoding="utf-8")

        # /bin/false stands in for the agent binary, so the run itself fails;
        # what matters is that the driver got as far as invoking it with the
        # registering prompt.
        self.assertEqual(result.returncode, 1)
        self.assertIn("assessing 1 of 1", result.stdout)
        self.assertIn("https://x.com/researcher/status/123", prompt)
        self.assertIn("[[x_post]]", prompt)
        self.assertIn("Do not run `just ingest-x`", prompt)

    def test_x_driver_is_x_only_despite_community_backlog(self):
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
            result = self.run_x_intake(root, "--max", "1")
            prompt = (
                root / ".work/agent-x-intake/prompt-rendered.md"
            ).read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 1)
        self.assertIn("https://x.com/researcher/status/123", prompt)
        self.assertNotIn("https://stacker.news/items/999", prompt)

    def test_x_driver_with_no_x_candidates_does_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            (root / "DISCOVERY.md").write_text(
                "# Discovery intake\n\n## Pending\n\n"
                "- 2026-08-05 [community](https://stacker.news/items/999) "
                "by author, 1 comments (Stacker News)\n\n## Assessed\n",
                encoding="utf-8",
            )
            result = self.run_x_intake(root)

        self.assertEqual(result.returncode, 0)
        self.assertIn("no pending X candidates", result.stdout)

    def test_x_driver_without_a_coverage_index_does_not_start(self):
        """An agent that cannot see what is covered registers duplicates of it.

        Duplicates in the registry cost far more to undo than a skipped tick,
        so the driver refuses rather than running blind.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            (root / "sources.toml").unlink()
            result = self.run_x_intake(root)

        self.assertEqual(result.returncode, 1)
        self.assertIn("could not build the coverage index", result.stderr)
        self.assertFalse(
            (Path(raw) / ".work/agent-x-intake/prompt-rendered.md").exists())

    def test_x_driver_builds_the_coverage_index_before_the_agent(self):
        """Built as the operator account, from the registry, before the drop.

        The X template consumes it like the community one does; this checks
        the driver's half, which is that the file exists and describes the
        registry.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            self.run_x_intake(root)
            coverage = (
                root / ".work/agent-x-intake/coverage.md"
            ).read_text(encoding="utf-8")

        self.assertIn("reddit-example", coverage)
        self.assertIn("r/Bitcoin: an already registered theme", coverage)

    def test_x_driver_without_an_agent_binary_waits(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            # Explicitly empty, not omitted: `just test` dotenv-loads the real
            # .env, which may set REVIEW_AGENT_BIN, and the driver would
            # otherwise inherit it past this fixture.
            (root / ".env").write_text("REVIEW_AGENT_BIN=\n", encoding="utf-8")
            result = self.run_x_intake(root)

        self.assertEqual(result.returncode, 0)
        self.assertIn("REVIEW_AGENT_BIN is unset", result.stdout)


if __name__ == "__main__":
    unittest.main()
