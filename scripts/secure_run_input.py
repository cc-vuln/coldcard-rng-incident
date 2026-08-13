#!/usr/bin/env python3
"""Snapshot one agent-written file into an operator-owned guard run safely."""
from __future__ import annotations

import argparse
import errno
import os
import stat
import sys
from pathlib import Path

MAX_BYTES = 65536


class InputError(ValueError):
    pass


def snapshot(source: Path, destination: Path, *, max_bytes: int = MAX_BYTES) -> int:
    """Copy a bounded regular file without following or racing a path swap."""
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT}:
            raise InputError(f"unsafe or missing agent input {source}") from exc
        raise InputError(f"cannot open agent input {source}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise InputError(f"agent input {source} is not a regular file")
        if info.st_size > max_bytes:
            raise InputError(f"agent input {source} exceeds {max_bytes} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise InputError(f"agent input {source} exceeds {max_bytes} bytes")
        # A background writer cannot turn a partial moving file into evidence.
        after = os.fstat(fd)
        if after.st_size != info.st_size or total != info.st_size:
            raise InputError(f"agent input {source} changed while being read")
        data = b"".join(chunks)
    finally:
        os.close(fd)

    destination.parent.mkdir(parents=True, exist_ok=True)
    out_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    try:
        out = os.open(destination, out_flags, 0o600)
    except OSError as exc:
        raise InputError(f"cannot create protected input {destination}: {exc}") from exc
    try:
        view = memoryview(data)
        while view:
            written = os.write(out, view)
            view = view[written:]
        os.fsync(out)
    except BaseException:
        try:
            destination.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(out)
    directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    try:
        source.unlink()
    except OSError:
        # The protected snapshot is already complete. Leaving an agent-owned
        # input behind is visible and agent_begin removes it before reuse.
        pass
    return len(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    args = parser.parse_args(argv)
    try:
        snapshot(args.source, args.destination, max_bytes=args.max_bytes)
    except (OSError, ValueError) as exc:
        print(f"secure-run-input: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
