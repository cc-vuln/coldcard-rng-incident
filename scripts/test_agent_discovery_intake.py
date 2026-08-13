import os
import json
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
            "AGENT_SANDBOX=off\n"
            # Fixture rejections are not operator signal: keep the driver's
            # guard-rejection alerts out of the real alert stream.
            "AGENT_ALERTS=off\n",
            encoding="utf-8",
        )
        (root / ".gitignore").write_text(
            ".work/\n.env\n__pycache__/\n", encoding="utf-8")
        # The drivers build an intake packet from the registry before they
        # will start an agent, and refuse to run without one.
        (root / "sources.toml").write_text(
            '[meta]\nincident = "fixture"\n\n'
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

    def stub_hydration(self, root: Path) -> None:
        """Replace network hydration with deterministic failed-body evidence."""
        (root / "scripts/hydrate_candidates.py").write_text(
            """#!/usr/bin/env python3
import sys

lines = [line.rstrip("\\n") for line in sys.stdin if line.strip()]
for number, line in enumerate(lines, 1):
    print(f"### Candidate {number}")
    print(f"Queue line: {line}")
    print("Platform: fixture (id fixture)")
    print("Body: fetch failed (fixture fetch failed)")
    print("Leave this candidate Pending and report the failure.\\n")
""", encoding="utf-8")

    def stub_verdict_agent(self, root: Path, candidate_id: str) -> None:
        agent = root / "verdict-agent.sh"
        agent.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' '" + json.dumps({
                "schema_version": 1, "candidate_id": candidate_id,
                "action": "dismissed", "reason": "fixture dismissal",
                "at": "20260813T120000Z",
            }, separators=(",", ":")) +
            "' > .work/intake-verdicts.jsonl\n",
            encoding="utf-8")
        agent.chmod(0o755)
        (root / ".env").write_text(
            f"REVIEW_AGENT_BIN={agent}\nAGENT_SANDBOX=off\nAGENT_ALERTS=off\n",
            encoding="utf-8")

    def test_community_driver_excludes_x_candidates(self):
        """X candidates are the X lane's, whatever else is pending."""
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_intake(self.make_checkout(raw))

        self.assertEqual(result.returncode, 0)
        self.assertIn("they are the X lane's", result.stdout)

    def test_community_driver_renders_one_scoped_packet(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            self.stub_hydration(root)
            candidate = (
                "- 2026-08-05 [community](https://stacker.news/items/999) "
                "by author, 1 comments (Stacker News)"
            )
            (root / "DISCOVERY.md").write_text(
                "# Discovery intake\n\n## Pending\n\n" + candidate +
                "\n\n## Assessed\n", encoding="utf-8")
            result = self.run_intake(root)
            packet = json.loads((
                root / ".work/agent-discovery-intake/intake-packet.json"
            ).read_text(encoding="utf-8"))
            prompt = (
                root / ".work/agent-discovery-intake/prompt-rendered.md"
            ).read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(packet["lane"], "community")
        self.assertEqual(packet["candidates"][0]["external_key"],
                         "stackernews:item:999")
        self.assertEqual(prompt.count(candidate), 1)
        self.assertNotIn("{INTAKE_PACKET}", prompt)

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

    def test_x_driver_without_a_registry_does_not_start(self):
        """An agent that cannot see what is covered registers duplicates of it.

        Duplicates in the registry cost far more to undo than a skipped tick,
        so the driver refuses rather than running blind.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            (root / "sources.toml").unlink()
            result = self.run_x_intake(root)

        self.assertEqual(result.returncode, 1)
        self.assertIn("could not build a bounded intake packet", result.stderr)
        self.assertFalse(
            (Path(raw) / ".work/agent-x-intake/prompt-rendered.md").exists())

    def test_x_driver_builds_one_bounded_packet_before_the_agent(self):
        """Built as the operator account and retained with the guard record.

        Zero-history rows are counted rather than copied into every prompt,
        and the candidate queue line occurs only once in the rendered prompt.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            self.run_x_intake(root)
            packet = json.loads((
                root / ".work/agent-x-intake/intake-packet.json"
            ).read_text(encoding="utf-8"))
            prompt = (
                root / ".work/agent-x-intake/prompt-rendered.md"
            ).read_text(encoding="utf-8")
            retained = list((root / ".work/agent-guard").glob(
                "*/intake-packet.json"))

        self.assertEqual(packet["coverage"]["total_registry_entries"], 1)
        self.assertEqual(packet["coverage"]["included_nonzero_entries"], 0)
        self.assertEqual(packet["coverage"]["omitted_zero_entries"], 1)
        self.assertEqual(packet["candidates"][0]["external_key"],
                         "x:status:123")
        self.assertEqual(prompt.count(
            "- 2026-08-05 [candidate](https://x.com/researcher/status/123) "
            "(X @researcher)"), 1)
        self.assertEqual(len(retained), 1)

    def test_drivers_refresh_registry_only_after_guard_acceptance(self):
        """The compatibility file remains the guarded agent write surface."""
        for name in ("agent-discovery-intake.sh", "agent-x-intake.sh"):
            script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            finish = script.index('agent_finish "$ROLE"')
            accepted = script.index("if [[ $grc -ne 0 ]]", finish)
            refresh = script.index("migrate_registry.py --refresh", accepted)
            provider_failure = script.index("if [[ $rc -ne 0 ]]", refresh)
            apply = script.index("apply_intake_verdicts.py", provider_failure)
            captures = script.index("agent_run_captures", apply)
            self.assertLess(finish, accepted)
            self.assertLess(accepted, refresh)
            self.assertLess(refresh, provider_failure)
            self.assertLess(provider_failure, apply)
            self.assertLess(apply, captures)

    def test_community_driver_applies_guarded_outbox(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            self.stub_hydration(root)
            self.stub_verdict_agent(root, "stackernews:999")
            candidate = (
                "- 2026-08-05 [community](https://stacker.news/items/999) "
                "by author, 1 comments (Stacker News)"
            )
            (root / "DISCOVERY.md").write_text(
                "# Discovery intake\n\n## Pending\n\n" + candidate +
                "\n\n## Assessed\n", encoding="utf-8")
            result = self.run_intake(root)
            text = (root / "DISCOVERY.md").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(candidate + " -> dismissed: fixture dismissal ", text)
        self.assertNotIn(candidate, text.split("## Pending", 1)[1]
                         .split("## Assessed", 1)[0])

    def test_x_driver_applies_guarded_outbox(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            self.stub_hydration(root)
            self.stub_verdict_agent(root, "x:123")
            result = self.run_x_intake(root)
            text = (root / "DISCOVERY.md").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("(X @researcher) -> dismissed: fixture dismissal ", text)

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
