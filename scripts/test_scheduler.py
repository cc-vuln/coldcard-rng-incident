#!/usr/bin/env python3
"""Deterministic offline tests for the due-state capture scheduler."""

from __future__ import annotations

import fcntl
import json
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scheduled_runner as scheduler  # noqa: E402


UTC = timezone.utc


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeCaptureRunner:
    def __init__(
        self,
        clock: FakeClock,
        exits: dict[str, int] | None = None,
        changed: dict[str, list[str]] | None = None,
        duration_seconds: int = 5,
        omit_results: set[str] | None = None,
        record_run_exit: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.clock = clock
        self.exits = exits or {}
        self.changed = changed or {}
        self.duration_seconds = duration_seconds
        self.omit_results = omit_results or set()
        self.record_run_exit = record_run_exit
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict]] = []
        self.record_run_calls: list[tuple[list[str], dict]] = []

    @staticmethod
    def _job_name(command: list[str]) -> str:
        if "--kind" in command:
            return "chain-monitor"
        return f"tier{command[command.index('--tier') + 1]}"

    def __call__(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        if "record-run" in command:
            self.record_run_calls.append((command, kwargs))
            self.clock.advance(self.duration_seconds)
            return subprocess.CompletedProcess(
                command, self.record_run_exit, self.stdout, self.stderr
            )
        self.calls.append((command, kwargs))
        job = self._job_name(command)
        return_code = self.exits.get(job, 0)
        changed_ids = self.changed.get(job, [])
        result_path = Path(command[command.index("--result-file") + 1])
        self.clock.advance(self.duration_seconds)
        result_path.unlink(missing_ok=True)
        if job not in self.omit_results:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            events = [
                {"id": source_id, "event": "changed"}
                for source_id in changed_ids
            ]
            if return_code == scheduler.INCOMPLETE_EXIT:
                events.append(
                    {"id": f"{job}-blocked", "event": "blocked", "chars": 0}
                )
            payload = {
                "schema": 1,
                "command": "capture",
                "exit_code": return_code,
                "events": events,
            }
            result_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(
            command, return_code, self.stdout, self.stderr
        )


def valid_state_record(
    *,
    success: str | None,
    attempt: str | None = None,
    next_due: str | None = None,
    exit_code: int | None = 0,
    failures: int = 0,
) -> dict:
    return {
        "last_attempt_at": attempt,
        "last_success_at": success,
        "next_due_at": next_due,
        "last_exit_code": exit_code,
        "consecutive_failures": failures,
    }


class SchedulerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state_path = self.root / "last-run.json"
        self.lock_path = self.root / "scheduler.lock"
        self.tick_path = self.root / "tick.json"
        self.start = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)

    def run_tick(
        self,
        runner: FakeCaptureRunner,
        *,
        jobs: tuple[scheduler.Job, ...] = scheduler.DEFAULT_JOBS,
        notifier=None,
    ) -> tuple[int, dict]:
        return scheduler.run_tick(
            state_path=self.state_path,
            lock_path=self.lock_path,
            tick_result_path=self.tick_path,
            jobs=jobs,
            python_executable=Path("/test/venv/python"),
            capture_script=Path("/repo/scripts/capture.py"),
            clock=runner.clock,
            process_runner=runner,
            notifier=notifier,
        )

    def run_default_tick(
        self,
        runner: FakeCaptureRunner,
        *,
        jobs: tuple[scheduler.Job, ...] = scheduler.DEFAULT_JOBS,
        notifier=None,
    ) -> tuple[int, dict]:
        return scheduler.run_tick(
            state_path=self.state_path,
            lock_path=self.lock_path,
            jobs=jobs,
            python_executable=Path("/test/venv/python"),
            capture_script=Path("/repo/scripts/capture.py"),
            clock=runner.clock,
            process_runner=runner,
            notifier=notifier,
        )


class DefaultJobTests(SchedulerTestCase):
    def test_default_jobs_have_the_approved_cadences_and_selectors(self) -> None:
        self.assertEqual(
            [
                (job.name, job.interval_seconds, job.selector_args)
                for job in scheduler.DEFAULT_JOBS
            ],
            [
                (
                    "tier1",
                    1800,
                    ("--tier", "1", "--exclude-kind", "chain-monitor"),
                ),
                ("chain-monitor", 1800, ("--kind", "chain-monitor")),
                (
                    "tier2",
                    21600,
                    ("--tier", "2", "--exclude-kind", "chain-monitor"),
                ),
                (
                    "tier3",
                    21600,
                    ("--tier", "3", "--exclude-kind", "chain-monitor"),
                ),
            ],
        )

    def test_missing_state_runs_every_job_once_as_argv(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)

        exit_code, result = self.run_tick(runner)

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["counts"]["jobs_due"], 4)
        self.assertEqual(len(runner.calls), 4)
        for command, kwargs in runner.calls:
            self.assertIsInstance(command, list)
            self.assertEqual(command[:3], [
                "/test/venv/python", "/repo/scripts/capture.py", "capture"
            ])
            self.assertIs(kwargs["shell"], False)
            self.assertIs(kwargs["check"], False)
            self.assertIn("--result-file", command)
        self.assertIn("--kind", runner.calls[1][0])
        self.assertEqual(runner.calls[0][0].count("--exclude-kind"), 1)
        self.assertEqual(runner.calls[2][0].count("--exclude-kind"), 1)
        self.assertEqual(runner.calls[3][0].count("--exclude-kind"), 1)

    def test_every_registered_web_source_has_exactly_one_default_owner(self) -> None:
        registry = tomllib.loads(
            (scheduler.ROOT / "sources.toml").read_text(encoding="utf-8")
        )

        def matches(job: scheduler.Job, source: dict) -> bool:
            arguments = iter(job.selector_args)
            for flag, expected in zip(arguments, arguments):
                if flag == "--tier" and source.get("tier") != int(expected):
                    return False
                if flag == "--kind" and source.get("kind") != expected:
                    return False
                if flag == "--exclude-kind" and source.get("kind") == expected:
                    return False
            return True

        ownership = {
            source["id"]: [
                job.name for job in scheduler.DEFAULT_JOBS if matches(job, source)
            ]
            for source in registry["source"]
            if source.get("watch", "active") == "active"
        }
        violations = {
            source_id: owners
            for source_id, owners in ownership.items()
            if len(owners) != 1
        }
        self.assertEqual(violations, {})

    def test_overdue_jobs_catch_up_once_not_once_per_missed_interval(self) -> None:
        old = "20260729T000000Z"
        state = {
            "schema": 1,
            "updated_at": old,
            "jobs": {
                job.name: valid_state_record(
                    success=old,
                    attempt=old,
                    next_due=old,
                )
                for job in scheduler.DEFAULT_JOBS
            },
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)

        exit_code, _ = self.run_tick(runner)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(runner.calls), 4)


class StateTransitionTests(SchedulerTestCase):
    JOB = (scheduler.Job("tier1", 1800, ("--tier", "1")),)

    def test_success_uses_completion_time_for_attempt_success_and_next_due(self) -> None:
        for child_exit in (0, 10):
            with self.subTest(child_exit=child_exit):
                self.state_path.unlink(missing_ok=True)
                clock = FakeClock(self.start)
                changed = {"tier1": ["vendor-advisory"]} if child_exit == 10 else {}
                runner = FakeCaptureRunner(
                    clock,
                    exits={"tier1": child_exit},
                    changed=changed,
                    duration_seconds=125,
                )

                exit_code, _ = self.run_tick(runner, jobs=self.JOB)
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                record = state["jobs"]["tier1"]

                self.assertEqual(exit_code, child_exit)
                self.assertEqual(record["last_attempt_at"], "20260801T000205Z")
                self.assertEqual(record["last_success_at"], "20260801T000205Z")
                self.assertEqual(record["next_due_at"], "20260801T003205Z")
                self.assertEqual(record["last_exit_code"], child_exit)
                self.assertEqual(record["consecutive_failures"], 0)
                self.assertEqual(
                    len(runner.record_run_calls), 1 if child_exit == 10 else 0
                )

    def test_non_due_job_is_not_run_and_clean_tick_is_silent(self) -> None:
        success = "20260801T000000Z"
        state = {
            "schema": 1,
            "updated_at": success,
            "jobs": {
                "tier1": valid_state_record(
                    success=success,
                    attempt=success,
                    next_due="20260801T003000Z",
                )
            },
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        clock = FakeClock(self.start + timedelta(minutes=10))
        runner = FakeCaptureRunner(clock)
        notifications: list[tuple[str, str]] = []

        exit_code, result = self.run_tick(
            runner,
            jobs=self.JOB,
            notifier=lambda title, message: notifications.append((title, message)),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(notifications, [])
        self.assertEqual(result["notification"], {"attempted": False, "sent": False})

    def test_failure_codes_do_not_advance_last_success(self) -> None:
        success = "20260731T000000Z"
        for child_exit in (20, 21, 2):
            with self.subTest(child_exit=child_exit):
                state = {
                    "schema": 1,
                    "updated_at": success,
                    "jobs": {
                        "tier1": valid_state_record(
                            success=success,
                            attempt=success,
                            next_due="20260731T003000Z",
                            failures=2,
                        )
                    },
                }
                self.state_path.write_text(json.dumps(state), encoding="utf-8")
                clock = FakeClock(self.start)
                runner = FakeCaptureRunner(
                    clock,
                    exits={"tier1": child_exit},
                    omit_results={"tier1"} if child_exit == 21 else set(),
                )

                exit_code, _ = self.run_tick(runner, jobs=self.JOB)
                record = json.loads(
                    self.state_path.read_text(encoding="utf-8")
                )["jobs"]["tier1"]

                self.assertEqual(exit_code, child_exit)
                self.assertEqual(record["last_success_at"], success)
                self.assertEqual(record["last_attempt_at"], "20260801T000005Z")
                expected_due = (
                    "20260801T003005Z" if child_exit == 20
                    else "20260731T003000Z"
                )
                self.assertEqual(record["next_due_at"], expected_due)
                self.assertEqual(record["last_exit_code"], child_exit)
                self.assertEqual(record["consecutive_failures"], 3)

    def test_completed_incomplete_poll_waits_until_the_next_cadence(self) -> None:
        clock = FakeClock(self.start)
        first = FakeCaptureRunner(clock, exits={"tier1": 20})
        first_exit, _ = self.run_tick(first, jobs=self.JOB)

        second = FakeCaptureRunner(clock)
        second_exit, _ = self.run_tick(second, jobs=self.JOB)

        self.assertEqual(first_exit, 20)
        self.assertEqual(second_exit, 0)
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 0)

    def test_lock_contention_remains_due_on_the_next_tick(self) -> None:
        clock = FakeClock(self.start)
        first = FakeCaptureRunner(
            clock, exits={"tier1": 21}, omit_results={"tier1"}
        )
        first_exit, _ = self.run_tick(first, jobs=self.JOB)

        second = FakeCaptureRunner(clock)
        second_exit, _ = self.run_tick(second, jobs=self.JOB)

        self.assertEqual(first_exit, 21)
        self.assertEqual(second_exit, 0)
        self.assertEqual(len(second.calls), 1)


class StateValidationTests(SchedulerTestCase):
    JOB = (scheduler.Job("tier1", 1800, ("--tier", "1")),)

    def test_corrupt_or_unknown_state_is_a_visible_config_failure(self) -> None:
        cases = (
            "{broken",
            json.dumps({"schema": 999, "updated_at": None, "jobs": {}}),
            json.dumps({
                "schema": 1,
                "updated_at": None,
                "jobs": {"tier1": {"last_success_at": "not-a-time"}},
            }),
        )
        for state_text in cases:
            with self.subTest(state_text=state_text):
                self.state_path.write_text(state_text, encoding="utf-8")
                original = self.state_path.read_bytes()
                self.tick_path.unlink(missing_ok=True)
                clock = FakeClock(self.start)
                runner = FakeCaptureRunner(clock)
                notifications: list[tuple[str, str]] = []

                exit_code, result = self.run_tick(
                    runner,
                    jobs=self.JOB,
                    notifier=lambda title, message: notifications.append(
                        (title, message)
                    ),
                )

                self.assertEqual(exit_code, 2)
                self.assertEqual(result["outcome"], "config-error")
                self.assertIn("error", result)
                self.assertEqual(runner.calls, [])
                self.assertEqual(self.state_path.read_bytes(), original)
                self.assertTrue(self.tick_path.exists())
                self.assertEqual(len(notifications), 1)

    def test_legacy_iso_timestamps_are_accepted_and_normalized(self) -> None:
        legacy = "2026-07-31T23:00:00Z"
        state = {
            "schema": 1,
            "updated_at": legacy,
            "jobs": {
                "tier1": valid_state_record(
                    success=legacy,
                    attempt=legacy,
                    next_due="2026-07-31T23:30:00Z",
                )
            },
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)

        exit_code, _ = self.run_tick(runner, jobs=self.JOB)
        normalized = json.loads(self.state_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(normalized["updated_at"], "20260801T000005Z")
        self.assertEqual(
            normalized["jobs"]["tier1"]["last_success_at"],
            "20260801T000005Z",
        )

    def test_healthy_child_without_result_is_config_failure_and_not_advanced(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock, omit_results={"tier1"})

        exit_code, result = self.run_tick(runner, jobs=self.JOB)
        record = json.loads(self.state_path.read_text(encoding="utf-8"))["jobs"][
            "tier1"
        ]

        self.assertEqual(exit_code, 2)
        self.assertIn("did not write", result["jobs"][0]["error"])
        self.assertIsNone(record["last_success_at"])
        self.assertEqual(record["last_exit_code"], 2)


class LockAndResultTests(SchedulerTestCase):
    JOB = (scheduler.Job("tier1", 1800, ("--tier", "1")),)

    def test_scheduler_lock_is_nonblocking_and_does_not_create_state(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            clock = FakeClock(self.start)
            runner = FakeCaptureRunner(clock)

            exit_code, result = self.run_tick(runner, jobs=self.JOB)

            fcntl.flock(held.fileno(), fcntl.LOCK_UN)

        self.assertEqual(exit_code, 21)
        self.assertEqual(result["outcome"], "lock-busy")
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.state_path.exists())
        self.assertTrue(self.tick_path.exists())

    def test_multiple_changes_and_a_failure_make_one_aggregate_notification(self) -> None:
        jobs = (
            scheduler.Job("tier1", 1800, ("--tier", "1")),
            scheduler.Job("tier2", 21600, ("--tier", "2")),
        )
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(
            clock,
            exits={"tier1": 10, "tier2": 20},
            changed={"tier1": ["a", "b"]},
        )
        notifications: list[tuple[str, str]] = []

        exit_code, result = self.run_tick(
            runner,
            jobs=jobs,
            notifier=lambda title, message: notifications.append((title, message)),
        )

        self.assertEqual(exit_code, 20)
        self.assertEqual(result["counts"]["changed_sources"], 2)
        self.assertEqual(result["counts"]["jobs_failed"], 1)
        self.assertEqual(
            [event["id"] for event in result["events"] if event["event"] == "changed"],
            ["a", "b"],
        )
        self.assertEqual(len(notifications), 1)
        self.assertIn("a, b", notifications[0][1])
        self.assertIn("tier2", notifications[0][1])
        on_disk = json.loads(self.tick_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["outcome"], "incomplete")
        self.assertEqual(len(on_disk["jobs"]), 2)
        self.assertEqual(on_disk["tick_result_file"], str(self.tick_path))
        self.assertEqual(len(runner.record_run_calls), 1)

    def test_record_run_failure_stays_pending_and_makes_tick_incomplete(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(
            clock,
            exits={"tier1": 10},
            changed={"tier1": ["changed-source"]},
            record_run_exit=1,
        )
        notifications: list[tuple[str, str]] = []

        exit_code, result = self.run_tick(
            runner,
            jobs=self.JOB,
            notifier=lambda title, message: notifications.append((title, message)),
        )

        self.assertEqual(exit_code, 20)
        self.assertEqual(len(runner.record_run_calls), 1)
        command, kwargs = runner.record_run_calls[0]
        self.assertEqual(command, [
            "/test/venv/python",
            "/repo/scripts/capture.py",
            "record-run",
            str(self.tick_path),
        ])
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(result["record_run"]["outcome"], "incomplete")
        self.assertEqual(result["record_run"]["exit_code"], 1)
        self.assertEqual(result["outcome"], "incomplete")
        self.assertFalse(result["finalized"])
        self.assertTrue(result["pending_record_run"])
        self.assertEqual(result["tick_result_file"], str(self.tick_path))
        self.assertEqual(notifications, [])

    def test_record_run_config_failure_sets_exit_two(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(
            clock,
            exits={"tier1": 10},
            changed={"tier1": ["changed-source"]},
            record_run_exit=2,
        )

        exit_code, result = self.run_tick(runner, jobs=self.JOB)

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["record_run"]["outcome"], "config-error")
        self.assertEqual(result["outcome"], "config-error")
        self.assertTrue(result["pending_record_run"])

    def test_record_run_failure_is_retried_from_pending_outbox(self) -> None:
        clock = FakeClock(self.start)
        first = FakeCaptureRunner(
            clock,
            exits={"tier1": 10},
            changed={"tier1": ["changed-source"]},
            record_run_exit=1,
        )
        delivered: list[tuple[str, str]] = []

        first_exit, first_result = self.run_default_tick(
            first,
            jobs=self.JOB,
            notifier=lambda title, message: delivered.append((title, message)),
        )
        pending = list((self.root / "pending").glob("*.json"))

        self.assertEqual(first_exit, 20)
        self.assertFalse(first_result["finalized"])
        self.assertTrue(first_result["pending_record_run"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(first_result["tick_result_file"], str(pending[0]))
        self.assertEqual(delivered, [])

        second = FakeCaptureRunner(clock)
        second_exit, second_result = self.run_default_tick(
            second,
            jobs=self.JOB,
            notifier=lambda title, message: delivered.append((title, message)),
        )

        retained = self.root / "ticks" / pending[0].name
        retained_payload = json.loads(retained.read_text(encoding="utf-8"))
        self.assertEqual(second_exit, 0)
        self.assertEqual(second.calls, [])
        self.assertEqual(len(second.record_run_calls), 1)
        self.assertEqual(second_result["recovery"]["attempted"], 1)
        self.assertEqual(second_result["recovery"]["finalized"], 1)
        self.assertEqual(second_result["recovery"]["healthy"], 1)
        self.assertEqual(len(delivered), 1)
        self.assertIn("changed-source", delivered[0][1])
        self.assertEqual(retained_payload["tick_result_file"], str(retained))
        self.assertEqual(retained_payload["record_run"]["outcome"], "recorded")
        self.assertNotIn("pending_record_run", retained_payload)
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])

    def test_notification_failure_is_visible_without_rolling_back_capture(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(
            clock,
            exits={"tier1": 10},
            changed={"tier1": ["changed-source"]},
        )

        def unavailable_notifier(_title: str, _message: str) -> None:
            raise OSError("notifications unavailable")

        exit_code, result = self.run_tick(
            runner,
            jobs=self.JOB,
            notifier=unavailable_notifier,
        )
        state = json.loads(self.state_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 20)
        self.assertEqual(result["outcome"], "incomplete")
        self.assertTrue(result["notification"]["attempted"])
        self.assertFalse(result["notification"]["sent"])
        self.assertIn("notifications unavailable", result["error"])
        self.assertFalse(result["finalized"])
        self.assertTrue(result["pending_notification"])
        self.assertEqual(state["jobs"]["tier1"]["last_exit_code"], 10)
        self.assertEqual(state["jobs"]["tier1"]["last_success_at"], "20260801T000005Z")

    def test_failed_notification_is_retried_from_pending_outbox(self) -> None:
        clock = FakeClock(self.start)
        first = FakeCaptureRunner(
            clock,
            exits={"tier1": 10},
            changed={"tier1": ["changed-source"]},
        )

        def unavailable_notifier(_title: str, _message: str) -> None:
            raise OSError("notifications unavailable")

        first_exit, first_result = self.run_default_tick(
            first,
            jobs=self.JOB,
            notifier=unavailable_notifier,
        )
        pending = list((self.root / "pending").glob("*.json"))

        delivered: list[tuple[str, str]] = []
        second = FakeCaptureRunner(clock)
        second_exit, second_result = self.run_default_tick(
            second,
            jobs=self.JOB,
            notifier=lambda title, message: delivered.append((title, message)),
        )

        self.assertEqual(first_exit, 20)
        self.assertFalse(first_result["finalized"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(first_result["tick_result_file"], str(pending[0]))
        self.assertEqual(second_exit, 0)
        self.assertEqual(second_result["recovery"]["attempted"], 1)
        self.assertEqual(second_result["recovery"]["finalized"], 1)
        self.assertEqual(second_result["recovery"]["healthy"], 1)
        self.assertEqual(len(delivered), 1)
        self.assertIn("changed-source", delivered[0][1])
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])

    def test_repeated_notification_outage_does_not_multiply_outboxes(self) -> None:
        clock = FakeClock(self.start)
        first = FakeCaptureRunner(
            clock,
            exits={"tier1": 10},
            changed={"tier1": ["changed-source"]},
        )

        def unavailable_notifier(_title: str, _message: str) -> None:
            raise OSError("notifications unavailable")

        first_exit, _ = self.run_default_tick(
            first,
            jobs=self.JOB,
            notifier=unavailable_notifier,
        )
        self.assertEqual(first_exit, 20)

        for _ in range(3):
            retry = FakeCaptureRunner(clock)
            retry_exit, retry_result = self.run_default_tick(
                retry,
                jobs=self.JOB,
                notifier=unavailable_notifier,
            )
            self.assertEqual(retry_exit, 20)
            self.assertEqual(
                retry_result["notification_suppressed"],
                "recovered tick retains notification responsibility",
            )
            self.assertEqual(
                len(list((self.root / "pending").glob("*.json"))),
                1,
            )

        delivered: list[tuple[str, str]] = []
        restored = FakeCaptureRunner(clock)
        restored_exit, _ = self.run_default_tick(
            restored,
            jobs=self.JOB,
            notifier=lambda title, message: delivered.append((title, message)),
        )

        self.assertEqual(restored_exit, 0)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])

    def test_clean_due_tick_writes_one_result_without_notifying(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)
        notifications: list[tuple[str, str]] = []

        exit_code, result = self.run_tick(
            runner,
            jobs=self.JOB,
            notifier=lambda title, message: notifications.append((title, message)),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["outcome"], "clean")
        self.assertEqual(notifications, [])
        self.assertEqual(
            list(self.tick_path.parent.glob(self.tick_path.name)), [self.tick_path]
        )

    def test_default_tick_moves_from_pending_to_retained_history(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)

        exit_code, result = self.run_default_tick(runner, jobs=self.JOB)

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["finalized"])
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])
        retained = list((self.root / "ticks").glob("*.json"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(result["tick_result_file"], str(retained[0]))

    def test_recovery_does_not_scan_finalized_history(self) -> None:
        history = self.root / "ticks"
        history.mkdir()
        (history / "old.json").write_text("{not-json", encoding="utf-8")
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)

        exit_code, result = self.run_tick(runner, jobs=self.JOB)

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["recovery"]["attempted"], 0)

    def test_scheduler_state_artifacts_are_owner_only(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)

        exit_code, result = self.run_tick(runner, jobs=self.JOB)

        self.assertEqual(exit_code, 0)
        paths = [self.state_path, self.lock_path, self.tick_path]
        paths.extend(Path(job["result_file"]) for job in result["jobs"] if job["due"])
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_existing_custom_state_parent_permissions_are_not_changed(self) -> None:
        shared_parent = self.root / "shared-parent"
        shared_parent.mkdir(mode=0o755)
        shared_parent.chmod(0o755)
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)

        exit_code, _ = scheduler.run_tick(
            state_path=shared_parent / "last-run.json",
            lock_path=shared_parent / "scheduler.lock",
            tick_result_path=shared_parent / "tick.json",
            jobs=self.JOB,
            python_executable=Path("/test/venv/python"),
            capture_script=Path("/repo/scripts/capture.py"),
            clock=clock,
            process_runner=runner,
            notifier=None,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stat.S_IMODE(shared_parent.stat().st_mode), 0o755)
        self.assertEqual(
            stat.S_IMODE((shared_parent / "runs").stat().st_mode),
            0o700,
        )

    def test_unexpected_child_output_is_retained_only_on_failure(self) -> None:
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(
            clock,
            exits={"tier1": 20},
            stdout="capture diagnostic",
            stderr="network failure",
        )

        exit_code, result = self.run_tick(runner, jobs=self.JOB)

        self.assertEqual(exit_code, 20)
        self.assertEqual(
            result["jobs"][0]["diagnostics"],
            {"stdout": "capture diagnostic", "stderr": "network failure"},
        )

    def test_interrupted_tick_is_finalized_before_due_state_advances(self) -> None:
        previous_path = self.root / "pending" / "20260731T235900Z-p1.json"
        final_path = self.root / "ticks" / previous_path.name
        previous = scheduler._base_tick(self.start - timedelta(minutes=1), self.state_path)
        previous["counts"]["jobs_due"] = 1
        previous["counts"]["jobs_succeeded"] = 1
        previous["counts"]["changed_sources"] = 1
        previous["changed_ids"] = ["vendor-advisory"]
        previous["events"] = [{"id": "vendor-advisory", "event": "changed"}]
        previous["jobs"] = [
            {
                "name": "tier1",
                "due": True,
                "exit_code": 10,
            }
        ]
        scheduler._write_json_atomic(previous_path, previous)
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)
        notifications: list[tuple[str, str]] = []

        exit_code, result = self.run_tick(
            runner,
            jobs=self.JOB,
            notifier=lambda title, message: notifications.append((title, message)),
        )

        recovered = json.loads(final_path.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 0)
        self.assertTrue(recovered["finalized"])
        self.assertEqual(recovered["record_run"]["outcome"], "recorded")
        self.assertEqual(result["recovery"]["attempted"], 1)
        self.assertEqual(result["recovery"]["finalized"], 1)
        self.assertEqual(result["recovery"]["healthy"], 1)
        self.assertEqual(len(runner.record_run_calls), 1)
        self.assertEqual(len(notifications), 1)

    def test_prerequisite_write_failure_leaves_outbox_recoverable(self) -> None:
        clock = FakeClock(self.start)
        first = FakeCaptureRunner(
            clock,
            exits={"tier1": 10},
            changed={"tier1": ["changed-source"]},
        )
        original_write = scheduler._write_json_atomic
        pending_writes = 0

        def fail_second_pending_write(path: Path, payload: dict) -> None:
            nonlocal pending_writes
            if path.parent.name == "pending":
                pending_writes += 1
                if pending_writes == 2:
                    raise OSError("transient finalizer write failure")
            original_write(path, payload)

        with mock.patch.object(
            scheduler,
            "_write_json_atomic",
            side_effect=fail_second_pending_write,
        ):
            first_exit, first_result = self.run_default_tick(
                first,
                jobs=self.JOB,
            )

        self.assertEqual(first_exit, 2)
        self.assertFalse(first_result["finalized"])
        self.assertEqual(len(first.record_run_calls), 0)
        self.assertEqual(len(list((self.root / "pending").glob("*.json"))), 1)

        notifications: list[tuple[str, str]] = []
        second = FakeCaptureRunner(clock)
        second_exit, second_result = self.run_default_tick(
            second,
            jobs=self.JOB,
            notifier=lambda title, message: notifications.append((title, message)),
        )

        self.assertEqual(second_exit, 0)
        self.assertEqual(second_result["recovery"]["attempted"], 1)
        self.assertEqual(second_result["recovery"]["finalized"], 1)
        self.assertEqual(len(second.record_run_calls), 1)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(list((self.root / "pending").glob("*.json")), [])

    def test_incomplete_recovery_counts_as_finalized_but_not_healthy(self) -> None:
        previous_path = self.root / "pending" / "20260731T235900Z-p1.json"
        previous = scheduler._base_tick(
            self.start - timedelta(minutes=1), self.state_path
        )
        previous["counts"]["jobs_due"] = 1
        previous["counts"]["jobs_failed"] = 1
        previous["jobs"] = [
            {"name": "tier1", "due": True, "exit_code": 20}
        ]
        scheduler._write_json_atomic(previous_path, previous)
        clock = FakeClock(self.start)
        runner = FakeCaptureRunner(clock)

        exit_code, result = self.run_tick(runner, jobs=self.JOB)

        self.assertEqual(exit_code, 20)
        self.assertEqual(result["recovery"]["attempted"], 1)
        self.assertEqual(result["recovery"]["finalized"], 1)
        self.assertEqual(result["recovery"]["healthy"], 0)


class ResolveLocalNotifierTest(unittest.TestCase):
    """`--notify local` must mean "if this host has a channel", not "osascript".

    The capture host is a headless Linux VM. Returning a notifier that cannot
    work marks every tick incomplete, which is what the scheduler's exit code is
    supposed to signal to `capture-gate` and `publish`.
    """

    def test_macos_uses_osascript_when_present(self) -> None:
        with mock.patch.object(scheduler.sys, "platform", "darwin"), \
             mock.patch.object(scheduler.Path, "exists", return_value=True):
            self.assertIs(scheduler.resolve_local_notifier(), scheduler.local_notification)

    def test_macos_without_osascript_has_no_channel(self) -> None:
        with mock.patch.object(scheduler.sys, "platform", "darwin"), \
             mock.patch.object(scheduler.Path, "exists", return_value=False):
            self.assertIsNone(scheduler.resolve_local_notifier())

    def test_headless_linux_has_no_channel(self) -> None:
        with mock.patch.object(scheduler.sys, "platform", "linux"), \
             mock.patch.object(scheduler.shutil, "which", return_value="/usr/bin/notify-send"), \
             mock.patch.dict(scheduler.os.environ, {}, clear=True):
            self.assertIsNone(scheduler.resolve_local_notifier())

    def test_linux_with_a_session_bus_uses_notify_send(self) -> None:
        with mock.patch.object(scheduler.sys, "platform", "linux"), \
             mock.patch.object(scheduler.shutil, "which", return_value="/usr/bin/notify-send"), \
             mock.patch.dict(scheduler.os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/bus"}, clear=True):
            self.assertIs(scheduler.resolve_local_notifier(), scheduler.linux_notification)

    def test_linux_without_notify_send_has_no_channel(self) -> None:
        with mock.patch.object(scheduler.sys, "platform", "linux"), \
             mock.patch.object(scheduler.shutil, "which", return_value=None), \
             mock.patch.dict(scheduler.os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/bus"}, clear=True):
            self.assertIsNone(scheduler.resolve_local_notifier())


if __name__ == "__main__":
    unittest.main()
