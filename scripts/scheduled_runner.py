#!/usr/bin/env python3
"""Run due capture groups from one recurring timer tick.

The scheduler deliberately owns only polling cadence. ``capture.py`` remains
the authority for source selection, archive writes and capture exit semantics.
State and scheduler results live outside the repository by default.

Exit codes mirror the capture contract where practical:

* 0: every due job completed without a source change
* 10: every due job was healthy and at least one source changed
* 20: at least one due job was incomplete or failed unexpectedly
* 21: the scheduler or archive writer lock was busy
* 2: scheduler state, configuration or a child result was invalid
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Sequence


SCHEMA_VERSION = 1
HEALTHY_EXIT_CODES = frozenset({0, 10})
INCOMPLETE_EXIT = 20
LOCK_BUSY_EXIT = 21
CONFIG_EXIT = 2
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
MAX_DIAGNOSTIC_CHARS = 4000

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_FILE = (
    Path.home() / ".local" / "state" / "coldcard-archive" / "last-run.json"
)
DEFAULT_CAPTURE_SCRIPT = ROOT / "scripts" / "capture.py"


@dataclass(frozen=True)
class Job:
    """One independently scheduled capture selection."""

    name: str
    interval_seconds: int
    selector_args: tuple[str, ...]


DEFAULT_JOBS: tuple[Job, ...] = (
    Job(
        "tier1",
        30 * 60,
        ("--tier", "1", "--exclude-kind", "chain-monitor"),
    ),
    Job("chain-monitor", 30 * 60, ("--kind", "chain-monitor")),
    Job(
        "tier2",
        6 * 60 * 60,
        ("--tier", "2", "--exclude-kind", "chain-monitor"),
    ),
    Job(
        "tier3",
        6 * 60 * 60,
        ("--tier", "3", "--exclude-kind", "chain-monitor"),
    ),
)

Clock = Callable[[], datetime]
ProcessRunner = Callable[..., subprocess.CompletedProcess]
Notifier = Callable[[str, str], object]


class SchedulerConfigError(RuntimeError):
    """Raised when scheduler inputs or retained state are invalid."""


class SchedulerLockBusy(RuntimeError):
    """Raised when another scheduler tick still owns the scheduler lock."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise SchedulerConfigError("clock returned a naive datetime")
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _as_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _parse_time(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SchedulerConfigError(f"{field} must be a UTC timestamp ending in Z")
    for timestamp_format in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, timestamp_format).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    raise SchedulerConfigError(
        f"{field} must use YYYYMMDDTHHMMSSZ"
    )


def _compact_time(value: datetime) -> str:
    return _format_time(value)


def _ensure_private_directory(path: Path) -> None:
    """Create a scheduler-owned directory without changing an existing parent."""

    path = Path(path)
    if path.exists():
        if not path.is_dir():
            raise NotADirectoryError(path)
        return

    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    if not cursor.is_dir():
        raise NotADirectoryError(cursor)

    for directory in reversed(missing):
        try:
            directory.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            if not directory.is_dir():
                raise NotADirectoryError(directory)
        else:
            directory.chmod(PRIVATE_DIRECTORY_MODE)


def _ensure_private_file(path: Path) -> None:
    """Restrict an existing scheduler state file to its owner."""

    if path.exists():
        path.chmod(PRIVATE_FILE_MODE)


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Replace a JSON file atomically and durably within its directory."""

    _ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            PRIVATE_FILE_MODE,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(PRIVATE_FILE_MODE)
        temporary.replace(path)
        path.chmod(PRIVATE_FILE_MODE)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _move_tick_artifact(source: Path, destination: Path) -> None:
    """Move a finalized outbox into retained history on the same filesystem."""

    if source == destination:
        return
    _ensure_private_directory(destination.parent)
    source.replace(destination)
    destination.chmod(PRIVATE_FILE_MODE)
    for directory in (source.parent, destination.parent):
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


@contextlib.contextmanager
def scheduler_lock(path: Path) -> Iterator[None]:
    """Acquire a scheduler-only advisory lock without waiting."""

    _ensure_private_directory(path.parent)
    handle = path.open("a+", encoding="utf-8")
    os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        owner = handle.read().strip() or "owner details unavailable"
        handle.close()
        raise SchedulerLockBusy(owner) from exc

    try:
        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "pid": os.getpid(),
                "label": "scheduled-runner",
                "acquired_at": _format_time(utc_now()),
            },
            handle,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _empty_job_state() -> dict:
    return {
        "last_attempt_at": None,
        "last_success_at": None,
        "next_due_at": None,
        "last_exit_code": None,
        "consecutive_failures": 0,
    }


def _validate_job_state(name: str, value: object) -> dict:
    if not isinstance(value, dict):
        raise SchedulerConfigError(f"state for job {name!r} must be an object")

    required = set(_empty_job_state())
    missing = sorted(required.difference(value))
    if missing:
        raise SchedulerConfigError(
            f"state for job {name!r} is missing {', '.join(missing)}"
        )

    normalized = {field: value[field] for field in required}
    for field in ("last_attempt_at", "last_success_at", "next_due_at"):
        parsed = _parse_time(value[field], f"jobs.{name}.{field}")
        normalized[field] = _format_time(parsed) if parsed is not None else None
    exit_code = value["last_exit_code"]
    if exit_code is not None and type(exit_code) is not int:
        raise SchedulerConfigError(
            f"jobs.{name}.last_exit_code must be an integer or null"
        )
    failures = value["consecutive_failures"]
    if type(failures) is not int or failures < 0:
        raise SchedulerConfigError(
            f"jobs.{name}.consecutive_failures must be a non-negative integer"
        )
    return normalized


def load_state(path: Path) -> dict:
    """Load and validate scheduler state, or return new state when absent."""

    if not path.exists():
        return {"schema": SCHEMA_VERSION, "updated_at": None, "jobs": {}}
    _ensure_private_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchedulerConfigError(f"cannot read scheduler state {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchedulerConfigError("scheduler state must be a JSON object")
    if payload.get("schema") != SCHEMA_VERSION:
        raise SchedulerConfigError(
            f"unsupported scheduler state schema {payload.get('schema')!r}"
        )
    if "updated_at" not in payload:
        raise SchedulerConfigError("scheduler state is missing updated_at")
    updated_at = _parse_time(payload["updated_at"], "updated_at")
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        raise SchedulerConfigError("scheduler state jobs must be an object")
    return {
        "schema": SCHEMA_VERSION,
        "updated_at": _format_time(updated_at) if updated_at is not None else None,
        "jobs": {
            name: _validate_job_state(name, value)
            for name, value in jobs.items()
        },
    }


def _validate_jobs(jobs: Sequence[Job]) -> tuple[Job, ...]:
    if not jobs:
        raise SchedulerConfigError("at least one scheduled job is required")
    names: set[str] = set()
    validated: list[Job] = []
    for job in jobs:
        allowed_name_characters = "abcdefghijklmnopqrstuvwxyz0123456789-"
        if not job.name or any(
            char not in allowed_name_characters for char in job.name
        ):
            raise SchedulerConfigError(
                f"invalid job name {job.name!r}; use lowercase letters, digits and hyphens"
            )
        if job.name in names:
            raise SchedulerConfigError(f"duplicate scheduled job {job.name!r}")
        if type(job.interval_seconds) is not int or job.interval_seconds <= 0:
            raise SchedulerConfigError(
                f"job {job.name!r} interval must be a positive integer"
            )
        if not job.selector_args or not all(
            isinstance(arg, str) and arg for arg in job.selector_args
        ):
            raise SchedulerConfigError(
                f"job {job.name!r} must have non-empty selector arguments"
            )
        names.add(job.name)
        validated.append(job)
    return tuple(validated)


def _next_due(record: dict, job: Job) -> datetime | None:
    last_success = _parse_time(
        record["last_success_at"], f"jobs.{job.name}.last_success_at"
    )
    if last_success is None:
        return None
    return last_success + timedelta(seconds=job.interval_seconds)


def _job_is_due(record: dict, job: Job, evaluated_at: datetime) -> bool:
    next_due = _next_due(record, job)
    return next_due is None or _as_utc(evaluated_at) >= next_due


def _read_child_result(path: Path, return_code: int) -> tuple[dict | None, str | None]:
    if not path.exists():
        if return_code in HEALTHY_EXIT_CODES:
            return None, "healthy capture did not write its structured result"
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read capture result: {exc}"
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        return None, "capture result has an unsupported schema"
    if payload.get("exit_code") != return_code:
        return None, "capture result exit code does not match the child process"
    events = payload.get("events")
    if not isinstance(events, list) or not all(isinstance(event, dict) for event in events):
        return None, "capture result events must be a list of objects"
    changed = [
        event for event in events if event.get("event") in ("changed", "first")
    ]
    if return_code == 10 and not changed:
        return None, "capture exited 10 without a changed or first event"
    if return_code == 0 and changed:
        return None, "capture exited 0 while reporting a changed or first event"
    return payload, None


def _build_capture_command(
    job: Job,
    result_path: Path,
    *,
    python_executable: Path,
    capture_script: Path,
) -> list[str]:
    return [
        str(python_executable),
        str(capture_script),
        "capture",
        *job.selector_args,
        "--result-file",
        str(result_path),
    ]


def _changed_ids(payload: dict | None) -> list[str]:
    if not payload:
        return []
    return [
        str(event.get("id", "unknown"))
        for event in payload.get("events", [])
        if event.get("event") in ("changed", "first")
    ]


def _failure_events(payload: dict | None) -> int:
    if not payload:
        return 0
    return sum(
        event.get("event")
        in ("error", "blocked", "skipped", "config-error", "launcher-error")
        for event in payload.get("events", [])
    )


def _bounded_diagnostic(value: object) -> str | None:
    """Return the tail of unexpected child output for private diagnostics."""

    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) <= MAX_DIAGNOSTIC_CHARS:
        return cleaned
    return f"[truncated to final {MAX_DIAGNOSTIC_CHARS} characters]\n{cleaned[-MAX_DIAGNOSTIC_CHARS:]}"


def _tick_exit(job_results: Sequence[dict]) -> int:
    codes = [result["exit_code"] for result in job_results if result["due"]]
    if any(code == CONFIG_EXIT for code in codes):
        return CONFIG_EXIT
    if any(code not in HEALTHY_EXIT_CODES | {LOCK_BUSY_EXIT} for code in codes):
        return INCOMPLETE_EXIT
    if any(code == LOCK_BUSY_EXIT for code in codes):
        return LOCK_BUSY_EXIT
    if any(code == 10 for code in codes):
        return 10
    return 0


def _notification_text(result: dict) -> tuple[str, str] | None:
    if result.get("notification_suppressed"):
        return None
    changed = result["counts"]["changed_sources"]
    failed_jobs = [
        job["name"]
        for job in result["jobs"]
        if job["due"] and job["exit_code"] not in HEALTHY_EXIT_CODES
    ]
    top_level_failure = result.get("exit_code") in (
        CONFIG_EXIT,
        INCOMPLETE_EXIT,
        LOCK_BUSY_EXIT,
    )
    if not changed and not failed_jobs and not top_level_failure:
        return None
    if failed_jobs or top_level_failure:
        title = "COLDCARD archive: scheduled capture incomplete"
    else:
        title = f"COLDCARD archive: {changed} source(s) changed"
    parts = [f"{changed} source(s) changed"]
    changed_ids = result["changed_ids"]
    if changed_ids:
        parts.append(", ".join(changed_ids))
    if failed_jobs:
        parts.append(f"failed jobs: {', '.join(failed_jobs)}")
    elif top_level_failure:
        parts.append(result.get("error", f"runner exited {result['exit_code']}"))
    record_run = result["record_run"]
    if (
        failed_jobs
        and record_run["attempted"]
        and record_run["exit_code"] != 0
    ):
        parts.append("change-log update failed")
    return title, "; ".join(parts)


def linux_notification(title: str, message: str) -> None:
    """Deliver one freedesktop notification without invoking a shell."""

    completed = subprocess.run(
        ["notify-send", "--", title, message],
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"notify-send exited {completed.returncode}"
        raise OSError(detail)


def resolve_local_notifier() -> Notifier | None:
    """The desktop notifier for this host, or None where there is no channel.

    `--notify local` used to mean osascript unconditionally. After the capture
    host moved from a Mac to a headless Linux VM that call could never succeed,
    and because an undelivered notification marks a tick incomplete, every run
    reported a failed job. That made the exit code `capture-gate` and `publish`
    read meaningless, which is the part that mattered.

    Returning None means "no channel is configured on this host", which the tick
    already treats as nothing to deliver. A channel that exists and then fails
    still raises, and is still reported.
    """

    if sys.platform == "darwin":
        return local_notification if Path("/usr/bin/osascript").exists() else None
    if sys.platform.startswith("linux"):
        # notify-send needs a session bus to deliver to. Under systemd on a
        # headless box there is none, and it would fail on every tick.
        if shutil.which("notify-send") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return linux_notification
    return None


def local_notification(title: str, message: str) -> None:
    """Deliver one macOS notification without invoking a shell."""

    apple_script = """on run argv
  display notification (item 2 of argv) with title (item 1 of argv)
end run
"""
    completed = subprocess.run(
        ["/usr/bin/osascript", "-", title, message],
        input=apple_script,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"osascript exited {completed.returncode}"
        raise OSError(detail)


def _notify_once(result: dict, notifier: Notifier | None) -> None:
    result["notification"] = {"attempted": False, "sent": False}
    content = _notification_text(result)
    if content is None or notifier is None:
        return
    title, message = content
    result["notification"] = {
        "attempted": True,
        "sent": False,
        "title": title,
        "message": message,
    }
    try:
        notifier(title, message)
    except Exception as exc:  # notification failure must not discard capture state
        result["notification"]["error"] = f"{type(exc).__name__}: {exc}"
    else:
        result["notification"]["sent"] = True


def _base_tick(started_at: datetime, state_path: Path) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "command": "scheduled-runner",
        "finalized": False,
        "started_at": _format_time(started_at),
        "finished_at": None,
        "state_file": str(state_path),
        "outcome": None,
        "exit_code": None,
        "counts": {
            "jobs_due": 0,
            "jobs_succeeded": 0,
            "jobs_failed": 0,
            "changed_sources": 0,
            "failure_events": 0,
        },
        "changed_ids": [],
        # These are the original capture events. The finalizer passes the
        # aggregate tick result to ``capture.py record-run`` once, rather than
        # recording each due job separately.
        "events": [],
        "jobs": [],
        "record_run": {
            "attempted": False,
            "exit_code": None,
            "outcome": "not-needed",
        },
        "notification": {"attempted": False, "sent": False},
        "recovery": {
            "attempted": 0,
            "finalized": 0,
            "healthy": 0,
            "ticks": [],
        },
    }


def _finish_tick(result: dict, exit_code: int, finished_at: datetime) -> None:
    result["finished_at"] = _format_time(finished_at)
    result["exit_code"] = exit_code
    if exit_code == CONFIG_EXIT:
        result["outcome"] = "config-error"
    elif exit_code == LOCK_BUSY_EXIT:
        result["outcome"] = "lock-busy"
    elif exit_code == INCOMPLETE_EXIT:
        result["outcome"] = "incomplete"
    elif exit_code == 10:
        result["outcome"] = "changed"
    else:
        result["outcome"] = "clean"


def _default_tick_result_path(state_path: Path, started_at: datetime) -> Path:
    name = f"{_compact_time(started_at)}-p{os.getpid()}.json"
    return state_path.parent / "pending" / name


def _default_final_tick_result_path(
    state_path: Path, pending_path: Path
) -> Path:
    return state_path.parent / "ticks" / pending_path.name


def _record_changes(
    result: dict,
    tick_result_path: Path,
    *,
    python_executable: Path,
    capture_script: Path,
    process_runner: ProcessRunner,
    clock: Clock,
) -> int:
    """Append the aggregate tick changes to CHANGES.md once."""

    command = [
        str(python_executable),
        str(capture_script),
        "record-run",
        str(tick_result_path),
    ]
    record_result = {
        "attempted": True,
        "started_at": _format_time(_as_utc(clock())),
        "finished_at": None,
        "command": command,
        "exit_code": None,
        "outcome": None,
    }
    try:
        completed = process_runner(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            shell=False,
        )
        return_code = int(completed.returncode)
    except OSError as exc:
        return_code = CONFIG_EXIT
        record_result["error"] = f"{type(exc).__name__}: {exc}"
    record_result["finished_at"] = _format_time(_as_utc(clock()))
    record_result["exit_code"] = return_code
    if return_code == 0:
        record_result["outcome"] = "recorded"
    elif return_code == CONFIG_EXIT:
        record_result["outcome"] = "config-error"
    else:
        record_result["outcome"] = "incomplete"
    if return_code != 0 and "completed" in locals():
        diagnostics = {
            key: value
            for key, value in (
                ("stdout", _bounded_diagnostic(completed.stdout)),
                ("stderr", _bounded_diagnostic(completed.stderr)),
            )
            if value is not None
        }
        if diagnostics:
            record_result["diagnostics"] = diagnostics
    result["record_run"] = record_result
    return return_code


def _finalize_tick_artifact(
    result: dict,
    tick_result_path: Path,
    *,
    final_result_path: Path,
    python_executable: Path,
    capture_script: Path,
    process_runner: ProcessRunner,
    clock: Clock,
    notifier: Notifier | None,
) -> None:
    """Record aggregate changes, notify once and retain one tick artifact."""

    result["tick_result_file"] = str(tick_result_path)
    result["finalized"] = False
    try:
        # record-run consumes this aggregate artifact. The same path is
        # atomically replaced below with final record and notification status.
        _write_json_atomic(tick_result_path, result)
    except OSError as exc:
        result["result_write_error"] = f"{type(exc).__name__}: {exc}"
        _finish_tick(result, CONFIG_EXIT, _as_utc(clock()))
        # A provisional outbox may already be present from the per-job loop.
        # Never replace it with a terminal marker when the prerequisite write
        # failed, because recovery still needs to record and notify its events.
        return

    should_record = (
        result["counts"]["changed_sources"] > 0
        and not result["record_run"]["attempted"]
    )
    if should_record:
        pre_record_exit = int(result["exit_code"])
        record_exit = _record_changes(
            result,
            tick_result_path,
            python_executable=python_executable,
            capture_script=capture_script,
            process_runner=process_runner,
            clock=clock,
        )
        _finish_tick(result, int(result["exit_code"]), _as_utc(clock()))
        if record_exit != 0:
            if record_exit == CONFIG_EXIT:
                combined_exit = CONFIG_EXIT
            elif result["exit_code"] == CONFIG_EXIT:
                combined_exit = CONFIG_EXIT
            else:
                combined_exit = INCOMPLETE_EXIT
            result["error"] = (
                "change-log update failed with exit "
                f"{record_exit}"
            )
            result["pre_record_exit_code"] = pre_record_exit
            result["pending_record_run"] = True
            _finish_tick(result, combined_exit, _as_utc(clock()))
            try:
                _write_json_atomic(tick_result_path, result)
            except OSError as exc:
                result["result_write_error"] = f"{type(exc).__name__}: {exc}"
                _finish_tick(result, CONFIG_EXIT, _as_utc(clock()))
            return

    pre_notification_exit = int(result["exit_code"])
    _notify_once(result, notifier)
    notification = result["notification"]
    if notification["attempted"] and not notification["sent"]:
        detail = notification.get("error", "delivery status was not confirmed")
        result["pre_notification_exit_code"] = pre_notification_exit
        result["pending_notification"] = True
        result["notification_error"] = detail
        result["error"] = f"notification delivery failed: {detail}"
        if result["exit_code"] in HEALTHY_EXIT_CODES:
            _finish_tick(result, INCOMPLETE_EXIT, _as_utc(clock()))
        try:
            _write_json_atomic(tick_result_path, result)
        except OSError as exc:
            result["result_write_error"] = f"{type(exc).__name__}: {exc}"
            _finish_tick(result, CONFIG_EXIT, _as_utc(clock()))
        return

    result.pop("pre_notification_exit_code", None)
    result.pop("pending_notification", None)
    result.pop("notification_error", None)
    result["finalized"] = True
    result["tick_result_file"] = str(final_result_path)
    try:
        _write_json_atomic(tick_result_path, result)
    except OSError as exc:
        result["finalized"] = False
        result["tick_result_file"] = str(tick_result_path)
        result["result_write_error"] = f"{type(exc).__name__}: {exc}"
        _finish_tick(result, CONFIG_EXIT, _as_utc(clock()))
        with contextlib.suppress(OSError):
            _write_json_atomic(tick_result_path, result)
        return

    try:
        _move_tick_artifact(tick_result_path, final_result_path)
    except OSError as exc:
        # The outbox is already terminal. Recovery can relocate it without
        # repeating change recording or notification delivery.
        result["tick_result_file"] = str(tick_result_path)
        result["artifact_move_error"] = f"{type(exc).__name__}: {exc}"
        _finish_tick(result, CONFIG_EXIT, _as_utc(clock()))
        with contextlib.suppress(OSError):
            _write_json_atomic(tick_result_path, result)


def _recover_pending_ticks(
    state_path: Path,
    current_tick_path: Path,
    *,
    python_executable: Path,
    capture_script: Path,
    process_runner: ProcessRunner,
    clock: Clock,
    notifier: Notifier | None,
) -> list[dict]:
    """Finalize durable tick outboxes left by an interrupted scheduler."""

    pending_directory = state_path.parent / "pending"
    if not pending_directory.exists():
        return []
    recovered: list[dict] = []
    for candidate in sorted(pending_directory.glob("*.json")):
        if candidate == current_tick_path:
            continue
        final_result_path = _default_final_tick_result_path(
            state_path, candidate
        )
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchedulerConfigError(
                f"cannot read pending tick {candidate}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            continue
        if payload.get("command") != "scheduled-runner":
            continue

        if payload.get("finalized") is True:
            payload["tick_result_file"] = str(final_result_path)
            _write_json_atomic(candidate, payload)
            _move_tick_artifact(candidate, final_result_path)
            recovered.append(
                {
                    "file": str(final_result_path),
                    "outcome": payload.get("outcome"),
                    "exit_code": payload.get("exit_code"),
                    "finalized": True,
                }
            )
            continue

        if payload.get("finalized") is not False:
            continue

        payload["tick_result_file"] = str(candidate)
        if payload.pop("pending_record_run", False):
            original_exit = payload.pop(
                "pre_record_exit_code",
                _tick_exit(payload.get("jobs", [])),
            )
            if str(payload.get("error", "")).startswith(
                "change-log update failed with exit "
            ):
                payload.pop("error", None)
            payload.pop("result_write_error", None)
            payload["record_run"] = {
                "attempted": False,
                "exit_code": None,
                "outcome": "not-needed",
            }
            _finish_tick(payload, int(original_exit), _as_utc(clock()))

        if payload.pop("pending_notification", False):
            original_exit = payload.pop(
                "pre_notification_exit_code", payload.get("exit_code", 0)
            )
            payload.pop("notification_error", None)
            if str(payload.get("error", "")).startswith(
                "notification delivery failed:"
            ):
                payload.pop("error", None)
            payload["notification"] = {"attempted": False, "sent": False}
            _finish_tick(payload, int(original_exit), _as_utc(clock()))

        if payload.get("exit_code") is None:
            _finish_tick(
                payload,
                _tick_exit(payload.get("jobs", [])),
                _as_utc(clock()),
            )
        _finalize_tick_artifact(
            payload,
            candidate,
            final_result_path=final_result_path,
            python_executable=python_executable,
            capture_script=capture_script,
            process_runner=process_runner,
            clock=clock,
            notifier=notifier,
        )
        recovered.append(
            {
                "file": str(
                    final_result_path
                    if payload.get("finalized") is True
                    else candidate
                ),
                "outcome": payload.get("outcome"),
                "exit_code": payload.get("exit_code"),
                "finalized": payload.get("finalized") is True,
            }
        )
    return recovered


def run_tick(
    *,
    state_path: Path = DEFAULT_STATE_FILE,
    lock_path: Path | None = None,
    tick_result_path: Path | None = None,
    jobs: Sequence[Job] = DEFAULT_JOBS,
    python_executable: Path = Path(sys.executable),
    capture_script: Path = DEFAULT_CAPTURE_SCRIPT,
    clock: Clock = utc_now,
    process_runner: ProcessRunner = subprocess.run,
    notifier: Notifier | None = None,
) -> tuple[int, dict]:
    """Evaluate and run one scheduler tick.

    The injected clock, process runner and notifier keep tests offline and make
    completion-time scheduling observable without sleeping.
    """

    state_path = Path(state_path).expanduser()
    lock_path = (
        Path(lock_path).expanduser()
        if lock_path is not None
        else state_path.parent / "scheduled-runner.lock"
    )
    started_at = _as_utc(clock())
    explicit_tick_result = tick_result_path is not None
    tick_result_path = (
        Path(tick_result_path).expanduser()
        if explicit_tick_result
        else _default_tick_result_path(state_path, started_at)
    )
    final_result_path = (
        tick_result_path
        if explicit_tick_result
        else _default_final_tick_result_path(state_path, tick_result_path)
    )
    result = _base_tick(started_at, state_path)
    result["tick_result_file"] = str(tick_result_path)
    finalization_attempted = False

    try:
        validated_jobs = _validate_jobs(jobs)
        with scheduler_lock(lock_path):
            recovered = _recover_pending_ticks(
                state_path,
                tick_result_path,
                python_executable=Path(python_executable),
                capture_script=Path(capture_script),
                process_runner=process_runner,
                clock=clock,
                notifier=notifier,
            )
            result["recovery"] = {
                "attempted": len(recovered),
                "finalized": sum(
                    item["finalized"] for item in recovered
                ),
                "healthy": sum(
                    item["finalized"]
                    and item["exit_code"] in HEALTHY_EXIT_CODES
                    for item in recovered
                ),
                "ticks": recovered,
            }
            state = load_state(state_path)
            for job in validated_jobs:
                record = state["jobs"].setdefault(job.name, _empty_job_state())
                evaluated_at = _as_utc(clock())
                if not _job_is_due(record, job, evaluated_at):
                    result["jobs"].append(
                        {
                            "name": job.name,
                            "cadence_seconds": job.interval_seconds,
                            "due": False,
                            "next_due_at": _format_time(_next_due(record, job)),
                            "exit_code": None,
                        }
                    )
                    continue

                result["counts"]["jobs_due"] += 1
                job_started_at = _as_utc(clock())
                child_result_path = (
                    state_path.parent
                    / "runs"
                    / (
                        f"{_compact_time(started_at)}-p{os.getpid()}-"
                        f"{job.name}.json"
                    )
                )
                _ensure_private_directory(child_result_path.parent)
                command = _build_capture_command(
                    job,
                    child_result_path,
                    python_executable=Path(python_executable),
                    capture_script=Path(capture_script),
                )
                launch_error: str | None = None
                completed: subprocess.CompletedProcess | None = None
                try:
                    completed = process_runner(
                        command,
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                        shell=False,
                    )
                    child_exit = int(completed.returncode)
                except OSError as exc:
                    child_exit = CONFIG_EXIT
                    launch_error = f"{type(exc).__name__}: {exc}"
                completed_at = _as_utc(clock())

                child_payload, result_error = _read_child_result(
                    child_result_path, child_exit
                )
                effective_exit = CONFIG_EXIT if result_error else child_exit
                if launch_error:
                    result_error = launch_error

                record["last_attempt_at"] = _format_time(completed_at)
                record["last_exit_code"] = effective_exit
                if effective_exit in HEALTHY_EXIT_CODES:
                    record["last_success_at"] = _format_time(completed_at)
                    record["next_due_at"] = _format_time(
                        completed_at + timedelta(seconds=job.interval_seconds)
                    )
                    record["consecutive_failures"] = 0
                    result["counts"]["jobs_succeeded"] += 1
                else:
                    next_due = _next_due(record, job)
                    record["next_due_at"] = _format_time(
                        next_due if next_due is not None else evaluated_at
                    )
                    record["consecutive_failures"] += 1
                    result["counts"]["jobs_failed"] += 1

                changed_ids = _changed_ids(child_payload)
                if child_payload:
                    result["events"].extend(child_payload["events"])
                result["changed_ids"].extend(changed_ids)
                result["counts"]["changed_sources"] += len(changed_ids)
                result["counts"]["failure_events"] += _failure_events(child_payload)
                job_result = {
                    "name": job.name,
                    "cadence_seconds": job.interval_seconds,
                    "due": True,
                    "started_at": _format_time(job_started_at),
                    "finished_at": _format_time(completed_at),
                    "command": command,
                    "result_file": str(child_result_path),
                    "exit_code": effective_exit,
                    "capture_exit_code": child_exit,
                    "changed_ids": changed_ids,
                    "failure_events": _failure_events(child_payload),
                    "next_due_at": record["next_due_at"],
                }
                if result_error:
                    job_result["error"] = result_error
                if completed is not None and (
                    effective_exit not in HEALTHY_EXIT_CODES or result_error
                ):
                    diagnostics = {
                        key: value
                        for key, value in (
                            ("stdout", _bounded_diagnostic(completed.stdout)),
                            ("stderr", _bounded_diagnostic(completed.stderr)),
                        )
                        if value is not None
                    }
                    if diagnostics:
                        job_result["diagnostics"] = diagnostics
                result["jobs"].append(job_result)

                # Persist the aggregate event outbox before advancing due-state.
                # If the process is interrupted in between, the next tick can
                # still record and notify the captured change.
                _write_json_atomic(tick_result_path, result)
                _ensure_private_file(child_result_path)
                state["updated_at"] = _format_time(completed_at)
                _write_json_atomic(state_path, state)

            exit_code = _tick_exit(result["jobs"])
            recovered_codes = [item["exit_code"] for item in recovered]
            current_tick_needs_notification = (
                result["counts"]["changed_sources"] > 0
                or any(
                    job["due"]
                    and job["exit_code"] not in HEALTHY_EXIT_CODES
                    for job in result["jobs"]
                )
            )
            if any(code == CONFIG_EXIT for code in recovered_codes):
                exit_code = CONFIG_EXIT
                result["error"] = "a recovered scheduler tick had a configuration failure"
            elif any(code not in HEALTHY_EXIT_CODES for code in recovered_codes):
                exit_code = INCOMPLETE_EXIT
                result["error"] = "a recovered scheduler tick was incomplete"
            if (
                recovered_codes
                and any(code not in HEALTHY_EXIT_CODES for code in recovered_codes)
                and not current_tick_needs_notification
            ):
                result["notification_suppressed"] = (
                    "recovered tick retains notification responsibility"
                )
            finished_at = _as_utc(clock())
            _finish_tick(result, exit_code, finished_at)
            _finalize_tick_artifact(
                result,
                tick_result_path,
                final_result_path=final_result_path,
                python_executable=Path(python_executable),
                capture_script=Path(capture_script),
                process_runner=process_runner,
                clock=clock,
                notifier=notifier,
            )
            finalization_attempted = True
    except SchedulerLockBusy as exc:
        result["error"] = f"scheduler lock busy: {exc}"
        _finish_tick(result, LOCK_BUSY_EXIT, _as_utc(clock()))
    except SchedulerConfigError as exc:
        result["error"] = str(exc)
        _finish_tick(result, CONFIG_EXIT, _as_utc(clock()))
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        _finish_tick(result, CONFIG_EXIT, _as_utc(clock()))

    if not finalization_attempted:
        _finalize_tick_artifact(
            result,
            tick_result_path,
            final_result_path=final_result_path,
            python_executable=Path(python_executable),
            capture_script=Path(capture_script),
            process_runner=process_runner,
            clock=clock,
            notifier=notifier,
        )
    return int(result["exit_code"]), result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"scheduler state path (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        help="scheduler lock path (default: beside the state file)",
    )
    parser.add_argument(
        "--tick-result",
        type=Path,
        help="explicit structured tick path (default: pending, then retained under ticks/)",
    )
    parser.add_argument(
        "--notify",
        choices=("local", "none"),
        default="local",
        help="notification delivery for changes and failures",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python executable used for capture.py",
    )
    parser.add_argument(
        "--capture-script",
        type=Path,
        default=DEFAULT_CAPTURE_SCRIPT,
        help="capture.py path",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    notifier = resolve_local_notifier() if args.notify == "local" else None
    exit_code, result = run_tick(
        state_path=args.state_file,
        lock_path=args.lock_file,
        tick_result_path=args.tick_result,
        python_executable=args.python,
        capture_script=args.capture_script,
        notifier=notifier,
    )
    print(
        f"scheduled capture {result['outcome']}: "
        f"{result['counts']['jobs_due']} due, "
        f"{result['counts']['changed_sources']} changed, "
        f"{result['counts']['jobs_failed']} failed"
    )
    print(f"tick result: {result['tick_result_file']}")
    if result.get("error"):
        print(result["error"], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
