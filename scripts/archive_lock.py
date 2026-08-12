#!/usr/bin/env python3
"""Serialize writers that mutate the archive or source registry.

The capture tools are intentionally callable both through ``just`` and
directly.  Locking only the scheduled wrapper therefore does not protect a
manual capture, a Wayback backfill, or an X ingest from writing at the same
time.  This module provides one process-wide advisory lock for every writer.

It is also executable, which lets shell-only writers use the same lock:

    .venv/bin/python scripts/archive_lock.py --label capture-x -- command ...

Exit 21 means another archive writer currently holds the lock.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

LOCK_BUSY_EXIT = 21
STATE = Path.home() / ".local" / "state" / "coldcard-archive"
LOCK_PATH = Path(
    os.environ.get("COLDCARD_ARCHIVE_LOCK_PATH", STATE / "archive.lock")
).expanduser()


class ArchiveLockBusy(RuntimeError):
    """Raised when another process owns the archive writer lock."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextlib.contextmanager
def archive_lock(label: str, *, shared: bool = False) -> Iterator[None]:
    """Acquire the common archive lock without waiting.

    Read-side gates such as ``audit`` use a shared lock.  Writers use the
    default exclusive lock.  A non-blocking failure is explicit rather than a
    silent timer skip, so monitoring can distinguish overlap from success.
    """

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = LOCK_PATH.open("a+", encoding="utf-8")
    operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    try:
        fcntl.flock(fh.fileno(), operation | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        fh.seek(0)
        owner = fh.read().strip() or "owner details unavailable"
        fh.close()
        raise ArchiveLockBusy(owner) from exc

    try:
        if not shared:
            fh.seek(0)
            fh.truncate()
            json.dump(
                {"pid": os.getpid(), "label": label, "acquired_at": _now_iso()},
                fh,
                sort_keys=True,
            )
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="archive-writer")
    parser.add_argument("--shared", action="store_true",
                        help="take the read-side shared lock (a build or "
                             "gate that must not be written under) instead "
                             "of the exclusive writer lock")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    try:
        with archive_lock(args.label, shared=args.shared):
            # The held-marker tells a nested writer its parent serialized
            # the archive. Only an exclusive holder may make that claim: a
            # child writing under a shared lock is exactly what the lock
            # exists to prevent.
            env = dict(os.environ)
            if not args.shared:
                env["COLDCARD_ARCHIVE_LOCK_HELD"] = "1"
            # close_fds=False matches flock(1): a lock wrapper must not
            # change which descriptors the wrapped command inherits. The
            # publish build probes the inherited build-lock fd to tell an
            # ancestor-held lock from a free one, and silently dropping it
            # deadlocks the build behind its own ancestor (12 Aug 2026).
            return subprocess.run(command, env=env, check=False,
                                  close_fds=False).returncode
    except ArchiveLockBusy as exc:
        print(f"archive writer lock busy: {exc}", file=sys.stderr)
        return LOCK_BUSY_EXIT


if __name__ == "__main__":
    sys.exit(main())
