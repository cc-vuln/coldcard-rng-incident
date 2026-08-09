#!/usr/bin/env python3
"""Refuse a deploy whose build is not exactly its current clean commit.

The public citation contract names the commit in ``/version.json``. A warning
after upload is too late: capture or commit activity during a build can change
HEAD or tracked files after Astro read them. This gate runs after the indexable
build and before deployment, and requires all three views to agree:

* the built file says ``matches_commit: true``;
* its stamped commit is the current HEAD; and
* every tracked file still matches that HEAD.

Untracked files are ignored, matching ``site/src/lib/version.ts``. Archive
writers always append the tracked ``archive/index.jsonl``, so a new capture is
still detected even while its new snapshot files are untracked.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def problems(root: Path, version_path: Path) -> list[str]:
    found: list[str] = []
    try:
        version = json.loads(version_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{version_path}: cannot read the built version ({exc})"]

    build = version.get("build", {})
    if build.get("matches_commit") is not True:
        found.append("built /version.json does not say matches_commit: true")

    head_result = git(root, "rev-parse", "HEAD")
    if head_result.returncode != 0:
        found.append("cannot resolve the current git HEAD")
    else:
        head = head_result.stdout.strip()
        if build.get("commit") != head:
            found.append(
                f"built commit {build.get('commit')!r} is not current HEAD {head!r}"
            )

    status = git(root, "status", "--porcelain=v1", "--untracked-files=no")
    if status.returncode != 0:
        found.append("cannot check whether tracked files match HEAD")
    elif status.stdout.strip():
        found.append("tracked files changed after /version.json was generated")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--version", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    version_path = args.version or root / "site" / "dist" / "version.json"
    found = problems(root, version_path)
    if found:
        print(f"version exactness check failed ({len(found)} problem(s)):")
        for problem in found:
            print(f"  - {problem}")
        return 1
    print("version exactness check ok: built commit, HEAD and tracked tree agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
