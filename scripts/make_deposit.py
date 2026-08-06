#!/usr/bin/env python3
"""Stage the archival deposit: everything this project created, nothing it did not.

This assembles a deposit tree under `.work/`, reports exactly what went in and
what was held back, and stops. **It has no upload path and never will.** Where
the deposit goes, and whether it is open or access-restricted, is an operator
decision made after looking at what this prints.

The rule is subtractive, not additive: the deposit is every git-tracked file
minus three trees. Subtractive is what makes it auditable. An allowlist silently
omits whatever nobody remembered to add, and the omission looks identical to a
deliberate exclusion; a denylist of three named trees can be checked against
`git ls-files` by anyone in one command.

What is held back, and why:

  archive/snapshots/   complete copies of other people's articles, threads and
  archive/x/           posts. This project holds and quotes them under an
                       archival rationale; it does not warrant the right to
                       redistribute them in a deposit designed to be permanent,
                       and it could not: the works have several hundred authors,
                       none of whom were asked. `archive/manifest.jsonl`
                       describes every one of them instead.
  archive/runs/        per-run operational telemetry. No research value that
                       archive/index.jsonl does not already carry, and a
                       permanent artefact is the wrong place for anything that
                       might one day record a path or a host.

Diffs are included, deliberately. They contain third-party text in their added
and removed lines, so strictly they are the same class of material as the
snapshots, and this is the one genuine judgment call here. They are
excerpt-scale, the published site already shows them under exactly that
rationale, and they are the single most valuable research artefact after the
registry, being the record of how each account changed. Excluding them would
make the deposit consistent at the cost of making it much less useful.

    python3 scripts/make_deposit.py            # stage and report
    python3 scripts/make_deposit.py --tar      # also write a .tar.gz
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / ".work" / "deposit"

# The withheld trees, each with the reason it is withheld. Prefixes, matched
# against git's forward-slash paths. The reasons are carried here rather than
# in the deposit's README so that adding a tree cannot leave the README saying
# something that stopped being true.
EXCLUDED_TREES = (
    ("archive/snapshots/",
     "complete copies of pages as served, whose copyright belongs to their "
     "authors"),
    ("archive/x/",
     "captured social posts and their media, likewise"),
    ("archive/nostr/",
     "captured notes and their replies, likewise"),
    ("archive/runs/",
     "per-run operational telemetry, carrying nothing archive/index.jsonl "
     "does not"),
)
EXCLUDED = tuple(prefix for prefix, _ in EXCLUDED_TREES)

# Everything under archive/ that is this project's own record rather than
# somebody else's words. Anything under archive/ matching neither this nor
# EXCLUDED stops the staging run.
#
# This gate exists because the subtractive rule has one failure mode, and it
# found it on the first run: a legacy archive/reddit/ tree held a captured
# account, an image and raw platform metadata, and matched no exclusion because
# nobody had thought to write one. That tree was retired on 6 Aug 2026, so the
# gate no longer guards anything that exists, which is the point. The next
# capture backend will create the next tree, and the deposit must not decide
# what to do with it by default. Classify it here, deliberately, or nothing
# stages.
ARCHIVE_INCLUDED = (
    "archive/index.jsonl",
    "archive/diffs/",
    "archive/CHANGES.md",
)

# Groups exist only to make the report legible. Every tracked path that matches
# no group still goes in, counted under "other", so the grouping can never
# silently drop a file.
GROUPS = (
    ("registry", ("sources.toml", "revision-reviews.toml", "corrections.toml")),
    ("change record", ("archive/index.jsonl", "archive/diffs/", "archive/CHANGES.md")),
    ("capture tooling", ("scripts/", "capture-browser/", "justfile")),
    ("site", ("site/",)),
    ("method and documentation", ("docs/",)),
    ("project", (
        "README.md", "AGENTS.md", "BACKLOG.md", "CHANGELOG.md",
        "CONTRIBUTING.md", "SECURITY.md", "DISCOVERY.md", "LICENSE",
        "LICENSE-CONTENT.md", "CITATION.cff", ".github/", ".gitignore",
        ".env.example",
    )),
)

# A deposit is permanent, so the cheap check for an operator detail escaping
# into one is worth running even though the repository is already public.
#
# Path tokens are matched as paths, not as substrings: this repository
# documents these very tokens (AGENTS.md describes a build failure "on /home/
# paths", check-public-output.mjs defines them), and a scanner that cannot tell
# a leak from its own specification fails on every honest run, which is the
# fastest way to teach an operator to pass --no-scan. A real leak carries a
# path segment after the prefix. Operator needles stay exact substrings,
# because those strings have no innocent form.
LEAK_PATH_TOKENS = (
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/private/tmp/[A-Za-z0-9._-]+"),
)

TEXT_SUFFIXES = {
    ".md", ".txt", ".toml", ".json", ".jsonl", ".py", ".sh", ".ts", ".mjs",
    ".js", ".astro", ".css", ".yml", ".yaml", ".cff", ".diff", ".example",
}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def tracked_files() -> list[str]:
    return [line for line in git("ls-files").splitlines() if line]


def version_label() -> tuple[str, str]:
    """(version, commit). The tag when there is one, else the short commit."""
    commit = git("rev-parse", "HEAD")
    try:
        tag = git("describe", "--tags", "--abbrev=0")
    except subprocess.CalledProcessError:
        tag = ""
    return (tag or commit[:12]), commit


def human(size: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{size:.0f}B" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def scan_for_leaks(root: Path) -> list[str]:
    needles: list[str] = []
    tokens_path = REPO / "site" / "tools" / "private-tokens.json"
    if tokens_path.exists():
        try:
            needles = [
                entry["needle"]
                for entry in json.loads(tokens_path.read_text(encoding="utf-8"))
                if entry.get("needle")
            ]
        except (OSError, ValueError, TypeError) as exc:
            return [f"could not read the operator needle list: {exc}"]

    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # The matched string is never printed: these are exactly the strings
        # that must not be published, and a report is a file too.
        if any(pattern.search(text) for pattern in LEAK_PATH_TOKENS):
            hits.append(f"{path.relative_to(root)} contains a machine path")
        elif any(needle in text for needle in needles):
            hits.append(f"{path.relative_to(root)} contains a withheld token")
    return hits


DEPOSIT_README = """# COLDCARD predictable-RNG incident archive: deposit

This is the deposit of everything the cc-vuln.org project created: the source
registry, the poll record, the classification of every detected difference, the
corrections log, the capture tooling, the site and the method documentation.

Version {version} ({commit}).
Staged {staged}.

## What is here

{groups}

`archive/manifest.jsonl` describes every capture this project holds: one row per
capture, carrying source id, capture time, original URL, provenance, byte size
and content hashes. It contains no captured content.

## What is not here, and why

{excluded}

Copyright in the captured material belongs to its several hundred authors, who
were not asked. This project holds it for archival and research purposes and
quotes it as attributed excerpts, which is a narrower act than redistributing
complete copies under a permanent identifier, and it will not warrant a right
it does not have.

Nothing is hidden by the exclusion. Every withheld capture is described in
`archive/manifest.jsonl` with the hashes needed to verify a copy obtained
elsewhere, and the complete archive is public in the project repository:

  {repository}

## Reuse

Project writing and data: CC BY 4.0. Tooling and site code: MIT. See
LICENSE and LICENSE-CONTENT.md. Attribute "cc-vuln.org". Citation guidance,
including how to cite an individual preserved source state rather than this
deposit, is at https://cc-vuln.org/cite/ and in CITATION.cff.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"staging directory (default {DEFAULT_OUT})")
    parser.add_argument("--tar", action="store_true",
                        help="also write a .tar.gz beside the staged tree")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="stage even though tracked files differ from HEAD")
    args = parser.parse_args()

    dirty = bool(git("status", "--porcelain=v1", "--untracked-files=no"))
    if dirty and not args.allow_dirty:
        print(
            "deposit: the working tree has uncommitted changes to tracked files.\n"
            "A deposit names a commit, so stage from a clean tree, or pass\n"
            "--allow-dirty to stage anyway for a dry look.",
            file=sys.stderr,
        )
        return 2

    unclassified = sorted({
        path.split("/")[1] if "/" in path[len("archive/"):] else path
        for path in tracked_files()
        if path.startswith("archive/")
        and not path.startswith(EXCLUDED)
        and not path.startswith(ARCHIVE_INCLUDED)
    })
    if unclassified:
        print(
            "deposit: unclassified path(s) under archive/. A deposit must not\n"
            "decide by default whether a capture tree is this project's record\n"
            "or somebody else's words. Add each to EXCLUDED or ARCHIVE_INCLUDED\n"
            "in scripts/make_deposit.py, then re-stage:",
            file=sys.stderr,
        )
        for name in unclassified:
            print(f"  - archive/{name}", file=sys.stderr)
        return 3

    version, commit = version_label()
    name = f"cc-vuln-coldcard-rng-incident-{version}"
    root = args.out / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    included: list[str] = []
    withheld: list[str] = []
    for path in tracked_files():
        (withheld if path.startswith(EXCLUDED) else included).append(path)

    sizes: dict[str, tuple[int, int]] = {}
    for path in included:
        source = REPO / path
        if not source.exists():           # a deleted-but-staged path
            continue
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        label = next(
            (name for name, prefixes in GROUPS if path.startswith(prefixes)),
            "other",
        )
        count, total = sizes.get(label, (0, 0))
        sizes[label] = (count + 1, total + source.stat().st_size)

    # The manifest is generated into the deposit rather than committed: it is
    # derived from the archive, and a derived file in archive/ would be the one
    # thing there that is not a capture.
    sys.path.insert(0, str(REPO / "scripts"))
    import build_manifest

    rows = build_manifest.build()
    manifest_path = root / "archive" / "manifest.jsonl"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )

    staged = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repository = "https://github.com/cc-vuln/coldcard-rng-incident"
    group_lines = "\n".join(
        f"- **{label}**: {count} file(s), {human(total)}"
        for label, (count, total) in sorted(sizes.items())
    )
    excluded_lines = "\n".join(
        f"- `{prefix}`: {reason}" for prefix, reason in EXCLUDED_TREES
    )
    (root / "DEPOSIT.md").write_text(
        DEPOSIT_README.format(
            version=version, commit=commit, staged=staged,
            groups=group_lines, excluded=excluded_lines, repository=repository,
        ),
        encoding="utf-8",
    )

    metadata = {
        "title": "COLDCARD predictable-RNG incident archive",
        "upload_type": "dataset",
        "version": version,
        "creators": [{"name": "cc-vuln.org"}],
        "license": "cc-by-4.0",
        "keywords": [
            "bitcoin", "hardware wallet", "random number generation", "entropy",
            "vulnerability disclosure", "web archiving", "primary sources",
            "COLDCARD", "Coinkite",
        ],
        "related_identifiers": [
            {"identifier": repository, "relation": "isSupplementTo",
             "scheme": "url"},
            {"identifier": "https://cc-vuln.org/", "relation": "isDocumentedBy",
             "scheme": "url"},
        ],
        "notes": (
            "Project-created material only. Captured third-party material "
            "(archive/snapshots/, archive/x/) is excluded and remains its "
            "authors' copyright; every withheld capture is described in "
            "archive/manifest.jsonl. Built from commit " + commit + "."
        ),
    }
    (root / "deposit-metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    leaks = scan_for_leaks(root)
    if leaks:
        print("deposit: staging FAILED, withheld tokens found:", file=sys.stderr)
        for hit in leaks:
            print(f"  - {hit}", file=sys.stderr)
        print("Nothing was uploaded; this tool cannot upload. Fix and re-stage.",
              file=sys.stderr)
        return 1

    checksums = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksums.append(f"{digest}  {path.relative_to(root)}")
        total_bytes += path.stat().st_size
    (root / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")

    if args.tar:
        tarball = args.out / f"{name}.tar.gz"
        with tarfile.open(tarball, "w:gz") as archive:
            archive.add(root, arcname=name)

    withheld_bytes = sum(
        (REPO / p).stat().st_size for p in withheld if (REPO / p).exists()
    )
    # Counted per excluded prefix rather than by three hardcoded names, so a
    # tree added to EXCLUDED reports itself instead of vanishing into a total.
    by_tree = {
        prefix: sum(1 for p in withheld if p.startswith(prefix))
        for prefix in EXCLUDED
    }

    print(f"deposit staged: {root}")
    print(f"  version {version}, commit {commit[:12]}"
          + (", WORKING TREE DIRTY" if dirty else ""))
    print(f"  {len(checksums)} file(s), {human(total_bytes)}")
    for label, (count, total) in sorted(sizes.items()):
        print(f"    {label:26} {count:5} file(s)  {human(total):>8}")
    print(f"    {'capture manifest':26} {len(rows):5} row(s)  "
          f"{human(manifest_path.stat().st_size):>8}")
    print(f"  withheld: {len(withheld)} file(s), {human(withheld_bytes)}")
    for prefix, count in by_tree.items():
        print(f"    {prefix:26} {count:5} file(s)")
    print("  every withheld capture is described in archive/manifest.jsonl")
    if args.tar:
        print(f"  tarball: {args.out / (name + '.tar.gz')}")
    print("nothing was uploaded: this tool has no upload path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
