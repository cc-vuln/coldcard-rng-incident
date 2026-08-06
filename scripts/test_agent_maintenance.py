"""The maintenance wrapper's caller guards.

Every case here must fail *before* the wrapper touches systemctl, which is
also why these tests are safe to run on the capture host: a guard that only
fired after `systemctl stop` would leave the timers down on a bad invocation,
and a test for it could not run anywhere the timers are real.

The guards exist because `just` word-splits its `*ARGS`. A quoted compound
command reaches the wrapper as separate arguments, `bash -c` then runs only
the first word, and the window closes reporting success having done nothing
(observed 6 Aug 2026, during the reddit-engagement normalizer removal).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "agent-maintenance.sh"

# Anything systemctl would print if a guard let execution through. The wrapper
# announces the pause before doing anything else, so its absence is proof the
# guard fired first.
PAUSE_NOTICE = "pausing"


class CallerGuardTests(unittest.TestCase):
    def run_wrapper(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def assert_refused(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("timers untouched", result.stderr)
        self.assertNotIn(PAUSE_NOTICE, result.stdout + result.stderr)

    def test_split_compound_command_is_refused(self) -> None:
        # Exactly what `just agent-maintenance bash -c 'a && b'` delivers.
        self.assert_refused(
            self.run_wrapper("bash", "-c", "true", "&&", "true")
        )

    def test_bare_shell_operator_argument_is_refused(self) -> None:
        self.assert_refused(self.run_wrapper("true", "&&", "true"))
        self.assert_refused(self.run_wrapper("true", "|", "cat"))
        self.assert_refused(self.run_wrapper("true", ";", "true"))

    def test_shell_dash_c_with_trailing_arguments_is_refused(self) -> None:
        self.assert_refused(self.run_wrapper("bash", "-c", "echo hi", "extra"))

    def test_missing_executable_is_refused(self) -> None:
        result = self.run_wrapper("definitely-not-a-real-command-9c09d1")
        self.assert_refused(result)
        self.assertIn("not an executable", result.stderr)

    def test_no_arguments_is_refused(self) -> None:
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(PAUSE_NOTICE, result.stdout + result.stderr)

    def test_a_single_shell_c_string_is_allowed_through_the_guards(self) -> None:
        # `bash -c 'echo hi'` as ONE argument is the legitimate shape and must
        # reach the systemctl stage rather than being refused.
        #
        # This test runs on the capture host, where stopping archive-poll.timer
        # for real would be a test with a production side effect. `sudo` is
        # shadowed on PATH by a stub that refuses, so the wrapper gets as far
        # as trying to pause the timers and no further. The pause notice is
        # printed before that call, so its presence proves the guards let the
        # command past, which is the whole assertion.
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "sudo"
            stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            stub.chmod(0o755)
            env = dict(os.environ, PATH=f"{tmp}:{os.environ.get('PATH', '')}")
            result = subprocess.run(
                [str(SCRIPT), "bash", "-c", "echo hi"],
                capture_output=True, text=True, timeout=30, env=env,
            )
        self.assertNotEqual(result.returncode, 2)
        self.assertIn(PAUSE_NOTICE, result.stdout + result.stderr)
        self.assertNotIn("timers untouched", result.stderr)


if __name__ == "__main__":
    unittest.main()
