"""Tests for moving a rejected registration out of the live registry.

This runs unattended, after a guard rejection, on the file that decides what
the 30-minute poll fetches. Its safety rests on two properties, and both are
tested here because neither is obvious from reading the code:

- it can only remove, never add. No path adds a host, relaxes a rule or edits
  a block that survives
- it can only remove what the run being judged added. A pre-existing id is
  never eligible, so an injected run cannot use a deliberate rule breach to
  evict a long-standing source it dislikes

The rest is file surgery on TOML that must not disturb its neighbours, which
is the other way this could do damage quietly.
"""

import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import quarantine_registry as qr  # noqa: E402


GOOD = '''\
[[source]]
id = "reddit-first"
title = "First"
url = "https://www.reddit.com/r/Bitcoin/comments/aaa/x/"
org = "reddit"
kind = "community-discussion"
tier = 1

[[source]]
id = "reddit-second"
title = "Second"
url = "https://www.reddit.com/r/Bitcoin/comments/bbb/y/"
org = "reddit"
kind = "community-discussion"
tier = 1
'''

BAD_BLOCK = '''
[[source]]
id = "elsewhere-article"
title = "An article on an unlisted host"
url = "https://elsewhere.example/blog/post"
org = "Elsewhere"
kind = "article"
tier = 3
'''


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.registry = self.root / "sources.toml"
        self.before = self.root / "before.toml"
        # No module state to patch: the tool derives the quarantine
        # directory from the registry it was pointed at, so a sandboxed
        # registry cannot write into the real tree. That is the property, and
        # running the tool as a subprocess here is what tests it.

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/quarantine_registry.py"),
             "--registry", str(self.registry), *args],
            cwd=ROOT, capture_output=True, text=True, check=False)

    # -- the two safety properties ------------------------------------------

    def test_a_pre_existing_offender_is_left_alone(self):
        """An agent must not be able to evict a source by breaking a rule.

        Audit mode, with no --before, is where this bites: the delta check
        only reports what a run changed, so a pre-existing breach is invisible
        to it and the tool has nothing to do. Audit sees the breach, and must
        still refuse to touch a block the run did not add.
        """
        self.registry.write_text(GOOD + BAD_BLOCK, encoding="utf-8")
        self.before.write_text(GOOD + BAD_BLOCK, encoding="utf-8")
        result = self.run_tool("--before", str(self.before))
        # Delta mode: not this run's doing, so not this run's problem.
        self.assertEqual(result.returncode, 0)
        self.assertIn("nothing to do", result.stdout)
        self.assertIn("elsewhere-article",
                      self.registry.read_text(encoding="utf-8"))

        # Audit mode: the breach is real, but still not eligible to move.
        audit = self.run_tool()
        self.assertEqual(audit.returncode, 1)
        self.assertIn("no --before baseline", audit.stderr)
        self.assertIn("elsewhere-article",
                      self.registry.read_text(encoding="utf-8"))

    def test_only_the_offending_block_moves(self):
        self.registry.write_text(GOOD + BAD_BLOCK, encoding="utf-8")
        self.before.write_text(GOOD, encoding="utf-8")
        result = self.run_tool("--before", str(self.before))

        self.assertEqual(result.returncode, 0, result.stderr)
        left = self.registry.read_text(encoding="utf-8")
        self.assertNotIn("elsewhere-article", left)
        # The neighbours survive byte for byte.
        self.assertEqual(left.rstrip("\n"), GOOD.rstrip("\n"))

    # -- the file surgery ---------------------------------------------------

    def test_the_block_is_preserved_verbatim(self):
        self.registry.write_text(GOOD + BAD_BLOCK, encoding="utf-8")
        self.before.write_text(GOOD, encoding="utf-8")
        self.run_tool("--before", str(self.before), "--run-id", "RUN1")

        held = next((self.root / "quarantine").glob("registry-*.toml"))
        text = held.read_text(encoding="utf-8")
        self.assertIn('id = "elsewhere-article"', text)
        self.assertIn("https://elsewhere.example/blog/post", text)
        # And it says why, and which run did it.
        self.assertIn("RUN1", text)
        self.assertIn("registry_hosts.toml", text)

    def test_the_quarantine_file_is_valid_toml(self):
        self.registry.write_text(GOOD + BAD_BLOCK, encoding="utf-8")
        self.before.write_text(GOOD, encoding="utf-8")
        self.run_tool("--before", str(self.before))
        held = next((self.root / "quarantine").glob("*.toml"))
        parsed = tomllib.loads(held.read_text(encoding="utf-8"))
        self.assertEqual(parsed["source"][0]["id"], "elsewhere-article")

    def test_the_registry_is_left_parseable_and_passing(self):
        self.registry.write_text(GOOD + BAD_BLOCK, encoding="utf-8")
        self.before.write_text(GOOD, encoding="utf-8")
        result = self.run_tool("--before", str(self.before))
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = tomllib.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for e in parsed["source"]],
                         ["reddit-first", "reddit-second"])

    def test_a_clean_registry_is_untouched(self):
        self.registry.write_text(GOOD, encoding="utf-8")
        self.before.write_text(GOOD, encoding="utf-8")
        result = self.run_tool("--before", str(self.before))
        self.assertEqual(result.returncode, 0)
        self.assertIn("nothing to do", result.stdout)
        self.assertEqual(self.registry.read_text(encoding="utf-8"), GOOD)
        self.assertFalse((self.root / "quarantine").exists())

    def test_dry_run_changes_nothing(self):
        self.registry.write_text(GOOD + BAD_BLOCK, encoding="utf-8")
        self.before.write_text(GOOD, encoding="utf-8")
        before_text = self.registry.read_text(encoding="utf-8")
        result = self.run_tool("--before", str(self.before), "--dry-run")
        self.assertEqual(result.returncode, 0)
        self.assertIn("would move", result.stdout)
        self.assertEqual(self.registry.read_text(encoding="utf-8"), before_text)
        self.assertFalse((self.root / "quarantine").exists())

    def test_repeated_quarantines_append(self):
        self.registry.write_text(GOOD + BAD_BLOCK, encoding="utf-8")
        self.before.write_text(GOOD, encoding="utf-8")
        self.run_tool("--before", str(self.before))
        second = BAD_BLOCK.replace("elsewhere-article", "elsewhere-other")
        self.registry.write_text(
            self.registry.read_text(encoding="utf-8") + second,
            encoding="utf-8")
        self.run_tool("--before", str(self.before))
        held = next((self.root / "quarantine").glob("*.toml"))
        parsed = tomllib.loads(held.read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for e in parsed["source"]],
                         ["elsewhere-article", "elsewhere-other"])

    # -- the block finder ---------------------------------------------------

    def test_extract_matches_blocks_by_position_not_by_name(self):
        # Two tables, and an id that appears as a substring of another, so a
        # text search would take the wrong block.
        text = ('[[source]]\nid = "a-thread"\nurl = "https://x.invalid/1"\n\n'
                '[[x_post]]\nid = "a-thread-relay"\n'
                'url = "https://x.invalid/2"\n')
        new_text, taken = qr.extract(text, {"a-thread"})
        self.assertEqual(set(taken), {"a-thread"})
        self.assertIn("a-thread-relay", new_text)
        self.assertNotIn('id = "a-thread"\n', new_text)


class DriverWiringTests(unittest.TestCase):
    """agent_finish quarantines by itself, which is the point of the whole thing.

    Exercised through agent-run-common.sh rather than through a driver: the
    community intake path hydrates candidate bodies over the network before it
    reaches the guard, and these tests do not touch the network.
    """

    SCRIPT = """
        set -euo pipefail
        cd "$ROOT_DIR"
        source scripts/agent-run-common.sh
        agent_load_env
        agent_begin intake
        # What an injected or careless run does: register an unlistable host.
        cat >> sources.toml <<'BLOCK'

[[source]]
id = "elsewhere-article"
title = "An article on an unlisted host"
url = "https://elsewhere.example/blog/post"
org = "Elsewhere"
kind = "article"
tier = 3
BLOCK
        rc=0
        agent_finish intake || rc=$?
        echo "GUARD_RC=$rc"
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "scripts", self.root / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / ".venv").symlink_to(ROOT / ".venv")
        (self.root / ".work").mkdir()
        (self.root / ".env").write_text("AGENT_SANDBOX=off\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".work/\n.env\n", encoding="utf-8")
        (self.root / "sources.toml").write_text(GOOD, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_agent_finish_quarantines_a_rejected_registration(self):
        done = subprocess.run(
            ["/bin/bash", "-c", self.SCRIPT],
            env={**__import__("os").environ, "ROOT_DIR": str(self.root)},
            cwd=self.root, capture_output=True, text=True)
        out = done.stdout + done.stderr

        # The run is still rejected: quarantine is cleanup, not forgiveness.
        self.assertIn("GUARD_RC=1", done.stdout, out)
        self.assertIn("REJECTED", out)

        # And the tree is left working, with no person in the loop.
        registry = (self.root / "sources.toml").read_text(encoding="utf-8")
        self.assertNotIn("elsewhere-article", registry)
        self.assertEqual(registry.rstrip("\n"), GOOD.rstrip("\n"))

        held = next((self.root / "quarantine").glob("registry-*.toml"))
        self.assertIn("elsewhere-article", held.read_text(encoding="utf-8"))

        check = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_registry.py"),
             "--registry", str(self.root / "sources.toml")],
            capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)


if __name__ == "__main__":
    unittest.main()
