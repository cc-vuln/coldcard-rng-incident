"""First-capture dispatch in agent_run_captures.

The driver's last step turns guard-approved capture requests into captures.
A [[nostr_post]] id is the case that broke: capture.py's pollable_sources()
excludes the table, so `just capture-one <nostr-id>` can only exit 2, and a
nostr registration's first capture would fail at the one step with no poll
to retry it. The driver must route those ids to `just ingest-nostr` with the
note ref read out of the registry block instead.

These tests source agent-run-common.sh in bash with a stub `just` on PATH
that logs its arguments, so nothing is fetched, captured or polled.
"""

import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def first_nostr_id() -> tuple[str, str]:
    """One real (id, note-ref) pair from the live registry."""
    with open(ROOT / "sources.toml", "rb") as fh:
        data = tomllib.load(fh)
    block = data["nostr_post"][0]
    return block["id"], block["url"].rstrip("/").rsplit("/", 1)[-1]


class CaptureDispatchTests(unittest.TestCase):
    def run_driver(self, ids: list[str]) -> str:
        """Run agent_run_captures over the ids; return the stub's call log."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "just.log"
            stub = tmp_path / "just"
            stub.write_text(
                f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n',
                encoding="utf-8",
            )
            stub.chmod(0o755)
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            (run_dir / "approved-captures.txt").write_text(
                "\n".join(ids) + "\n", encoding="utf-8"
            )
            script = (
                f'source "{ROOT}/scripts/agent-run-common.sh"\n'
                f'AGENT_RUN_DIR="{run_dir}"\n'
                f'AGENT_RUN_ID="test-run"\n'
                "agent_run_captures\n"
            )
            env = dict(os.environ, PATH=f"{tmp}:{os.environ.get('PATH', '')}")
            proc = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return log.read_text(encoding="utf-8") if log.exists() else ""

    def test_nostr_id_is_ingested_not_polled(self) -> None:
        nostr_id, note_ref = first_nostr_id()
        calls = self.run_driver([nostr_id])
        self.assertIn(f"ingest-nostr {note_ref}", calls)
        self.assertNotIn("capture-one", calls)

    def test_pollable_id_still_goes_to_capture_one(self) -> None:
        with open(ROOT / "sources.toml", "rb") as fh:
            data = tomllib.load(fh)
        source_id = data["source"][0]["id"]
        calls = self.run_driver([source_id])
        self.assertIn(f"capture-one {source_id}", calls)
        self.assertNotIn("ingest-nostr", calls)

    def test_mixed_approval_list_dispatches_each_kind(self) -> None:
        nostr_id, note_ref = first_nostr_id()
        with open(ROOT / "sources.toml", "rb") as fh:
            data = tomllib.load(fh)
        source_id = data["source"][0]["id"]
        calls = self.run_driver([nostr_id, source_id])
        self.assertIn(f"ingest-nostr {note_ref}", calls)
        self.assertIn(f"capture-one {source_id}", calls)


if __name__ == "__main__":
    unittest.main()
