import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from discovery_test_fixture import install_store  # noqa: E402

X_CANDIDATE = (
    "- 2026-08-05 [candidate](https://x.com/researcher/status/123) "
    "(X @researcher)"
)


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
    def make_checkout(self, raw: str,
                      pending: list[str] | None = None) -> Path:
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
        pending = [X_CANDIDATE] if pending is None else pending
        install_store(root, [
            {"line": line,
             "url": re.search(r"\((https?://[^)]+)\)", line).group(1),
             "at": f"20260805T{number:06d}Z"}
            for number, line in enumerate(pending)
        ])
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
import json
import sys

for line in sys.stdin:
    if not line.strip():
        continue
    candidate = json.loads(line)
    platform = candidate["candidate_id"].split(":", 1)[0]
    print(json.dumps({
        **candidate,
        "platform": platform,
        "hydration_status": "failed",
        "hydration_detail": "fixture fetch failed",
        "body": None,
    }, sort_keys=True))
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

    def stub_retry_agent(
            self, root: Path, candidate_id: str, *, exit_code: int = 0,
            concurrent_observation: dict | None = None) -> None:
        """Write a complete retry outbox, optionally advancing discovery.

        AGENT_SANDBOX=off is explicit in these throwaway checkouts, so the
        optional store write stands in for an operator-side discovery lane
        that runs while the provider process is active.
        """
        row = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "action": "retry",
            "reason": "fixture evidence unavailable",
            "at": "20260814T120000Z",
        }
        agent = root / "retry-agent.py"
        script = [
            "#!/usr/bin/env python3",
            "import sys",
            "from pathlib import Path",
            ("Path('.work/intake-verdicts.jsonl').write_text("
             f"{(json.dumps(row, separators=(',', ':')) + chr(10))!r}, "
             "encoding='utf-8')"),
        ]
        if concurrent_observation is not None:
            script.extend([
                "sys.path.insert(0, str(Path('scripts').resolve()))",
                "from discovery_store import DiscoveryStore",
                ("DiscoveryStore(Path('.')).record_observation("
                 f"{concurrent_observation!r}, "
                 "operation_id='fixture-concurrent-discovery')"),
            ])
        script.append(f"raise SystemExit({exit_code})")
        agent.write_text("\n".join(script) + "\n", encoding="utf-8")
        agent.chmod(0o755)
        (root / ".env").write_text(
            f"REVIEW_AGENT_BIN={agent}\nAGENT_SANDBOX=off\nAGENT_ALERTS=off\n",
            encoding="utf-8")

    def only_guard_run(self, root: Path) -> Path:
        runs = [path for path in (root / ".work/agent-guard").iterdir()
                if path.is_dir()]
        self.assertEqual(1, len(runs), runs)
        return runs[0]

    def test_community_driver_excludes_x_candidates(self):
        """X candidates are the X lane's, whatever else is pending."""
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_intake(self.make_checkout(raw))

        self.assertEqual(result.returncode, 0)
        self.assertIn("they are the X lane's", result.stdout)

    def test_community_driver_renders_one_scoped_packet(self):
        with tempfile.TemporaryDirectory() as raw:
            candidate = (
                "- 2026-08-05 [community](https://stacker.news/items/999) "
                "by author, 1 comments (Stacker News)"
            )
            root = self.make_checkout(raw, [candidate])
            self.stub_hydration(root)
            result = self.run_intake(root)
            packet = json.loads((
                root / ".work/agent-discovery-intake/intake-packet.json"
            ).read_text(encoding="utf-8"))
            prompt = (
                root / ".work/agent-discovery-intake/prompt-rendered.md"
            ).read_text(encoding="utf-8")

        safe_candidate = (
            "- 2026-08-05 [community](<https://stacker.news/items/999>) "
            "by author, 1 comments (Stacker News)"
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(packet["lane"], "community")
        self.assertEqual(packet["candidates"][0]["external_key"],
                         "stackernews:item:999")
        self.assertEqual(packet["candidates"][0]["queue_line"], safe_candidate)
        self.assertEqual(prompt.count(safe_candidate), 1)
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
            community = (
                "- 2026-08-05 [community](https://stacker.news/items/999) "
                "by author, 1 comments (Stacker News)"
            )
            root = self.make_checkout(raw, [community, X_CANDIDATE])
            result = self.run_x_intake(root, "--max", "1")
            prompt = (
                root / ".work/agent-x-intake/prompt-rendered.md"
            ).read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 1)
        self.assertIn("https://x.com/researcher/status/123", prompt)
        self.assertNotIn("https://stacker.news/items/999", prompt)

    def test_x_driver_with_no_x_candidates_does_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            community = (
                "- 2026-08-05 [community](https://stacker.news/items/999) "
                "by author, 1 comments (Stacker News)"
            )
            root = self.make_checkout(raw, [community])
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
            "- 2026-08-05 [candidate](<https://x.com/researcher/status/123>) "
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
            complete = script.index("agent_mark_workflow_complete", captures)
            self.assertLess(finish, accepted)
            self.assertLess(accepted, refresh)
            self.assertLess(refresh, provider_failure)
            self.assertLess(provider_failure, apply)
            self.assertLess(apply, captures)
            self.assertLess(captures, complete)

    def test_drivers_use_head_bound_snapshots_not_an_hour_long_lock(self):
        for name, lane in (("agent-discovery-intake.sh", "community"),
                           ("agent-x-intake.sh", "x")):
            script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("awk '", script)
            self.assertIn(f"--state pending --lane {lane}", script)
            self.assertIn("--format intake-json --limit", script)
            self.assertNotIn("--lock-held", script)
            self.assertNotIn("discovery.lock\"", script)
            self.assertLess(script.index("--format intake-json"),
                            script.index('agent_begin "$ROLE"'))

    def test_community_driver_applies_guarded_outbox(self):
        with tempfile.TemporaryDirectory() as raw:
            candidate = (
                "- 2026-08-05 [community](https://stacker.news/items/999) "
                "by author, 1 comments (Stacker News)"
            )
            root = self.make_checkout(raw, [candidate])
            self.stub_hydration(root)
            self.stub_verdict_agent(root, "stackernews:999")
            result = self.run_intake(root)
            projected = json.loads((
                root / "discovery/candidates/stackernews/999.json"
            ).read_text(encoding="utf-8"))
            workflow_complete = (
                self.only_guard_run(root) / "workflow-complete").is_file()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual("assessed", projected["state"])
        self.assertEqual("dismissed", projected["verdict"]["kind"])
        self.assertEqual("fixture dismissal", projected["verdict"]["reason"])
        self.assertTrue(workflow_complete)

    def test_x_driver_applies_guarded_outbox(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw)
            self.stub_hydration(root)
            self.stub_verdict_agent(root, "x:123")
            result = self.run_x_intake(root)
            projected = json.loads((
                root / "discovery/candidates/x/123.json"
            ).read_text(encoding="utf-8"))
            workflow_complete = (
                self.only_guard_run(root) / "workflow-complete").is_file()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual("assessed", projected["state"])
        self.assertEqual("dismissed", projected["verdict"]["kind"])
        self.assertTrue(workflow_complete)

    def test_provider_failure_after_guard_pass_has_no_workflow_marker(self):
        candidate = (
            "- 2026-08-05 [community](https://stacker.news/items/999) "
            "by author, 1 comments (Stacker News)"
        )
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw, [candidate])
            self.stub_hydration(root)
            self.stub_retry_agent(root, "stackernews:999", exit_code=1)
            result = self.run_intake(root)
            guard_run = self.only_guard_run(root)
            projected = json.loads((
                root / "discovery/candidates/stackernews/999.json"
            ).read_text(encoding="utf-8"))

            self.assertTrue((guard_run / "approved-captures.txt").is_file())
            self.assertFalse((guard_run / "workflow-complete").exists())

        self.assertEqual(result.returncode, 1)
        self.assertIn("agent run failed; entries stay pending", result.stderr)
        self.assertEqual("pending", projected["state"])
        self.assertNotIn("retry", projected)

    def test_selected_head_change_after_guard_has_no_workflow_marker(self):
        candidate = (
            "- 2026-08-05 [community](https://stacker.news/items/999) "
            "by author, 1 comments (Stacker News)"
        )
        changed = {
            "url": "https://stacker.news/items/999",
            "title": "concurrent selected-candidate update",
            "foundAt": "20260814T010000Z",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw, [candidate])
            self.stub_hydration(root)
            self.stub_retry_agent(
                root, "stackernews:999", concurrent_observation=changed)
            result = self.run_intake(root)
            guard_run = self.only_guard_run(root)
            projected = json.loads((
                root / "discovery/candidates/stackernews/999.json"
            ).read_text(encoding="utf-8"))

            self.assertTrue((guard_run / "approved-captures.txt").is_file())
            self.assertFalse((guard_run / "workflow-complete").exists())

        self.assertEqual(result.returncode, 1)
        self.assertIn("packet candidate head changed", result.stderr)
        self.assertEqual("pending", projected["state"])
        self.assertNotIn("retry", projected)
        self.assertEqual(
            ["observation", "observation"],
            [row["type"] for row in projected["event_history"]])

    def test_unrelated_discovery_change_survives_guard_and_apply(self):
        selected = (
            "- 2026-08-05 [selected](https://stacker.news/items/999) "
            "by author, 1 comments (Stacker News)"
        )
        neighbour = (
            "- 2026-08-05 [neighbour](https://stacker.news/items/1000) "
            "by author, 1 comments (Stacker News)"
        )
        changed = {
            "url": "https://stacker.news/items/1000",
            "title": "concurrent unrelated-candidate update",
            "foundAt": "20260814T010000Z",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = self.make_checkout(raw, [selected, neighbour])
            self.stub_hydration(root)
            self.stub_retry_agent(
                root, "stackernews:999", concurrent_observation=changed)
            result = self.run_intake(root, "--max", "1")
            guard_run = self.only_guard_run(root)
            selected_projection = json.loads((
                root / "discovery/candidates/stackernews/999.json"
            ).read_text(encoding="utf-8"))
            neighbour_projection = json.loads((
                root / "discovery/candidates/stackernews/1000.json"
            ).read_text(encoding="utf-8"))
            workflow_complete = (guard_run / "workflow-complete").is_file()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("structured discovery path(s) also changed", result.stdout)
        self.assertEqual(
            "fixture evidence unavailable",
            selected_projection["retry"]["reason"])
        self.assertEqual(2, len(neighbour_projection["observations"]))
        self.assertTrue(workflow_complete)

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
