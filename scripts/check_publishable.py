#!/usr/bin/env python3
"""Refuse to commit artefacts the site itself withholds.

A source marked `withhold_text = true` in sources.toml has its captured
bodies held back from the published site. No source is marked today, but the
check stays: a repository is publication too, and a snapshot committed to
it is more permanent than a page, because a clone outlives any later
deletion.

So the same rule that governs the site governs the repository, and this is the
check that enforces it. Everything stays in the local archive, where it still
backs every claim; it simply does not leave.

It also refuses the other direction of leak. Every capture stores the
origin's response headers, and most of a modern response's headers describe
the path from that origin to this collector: which CDN edge answered, which
cache node, which trace id. Several of them name a city. The published site
never shows them, so a gate that reads only the built site cannot see the
problem; the repository publishes the archive itself. `response_headers.KEEP`
is the one policy, applied at capture time and enforced again here.

Run by `just audit`. Exits non-zero and names the offenders.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import registry_store
from response_headers import disallowed, geo_in_body

ROOT = Path(__file__).resolve().parent.parent
# A source opts in with `withhold_text = true`. None does today.

# A bitcoin address in any file that would be committed is worth stopping for,
# whatever its source. Deliberately loose: false positives are cheap here.
ADDRESS = re.compile(r"\b(?:bc1[a-z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b")


def withheld_ids() -> set[str]:
    # Both registry block types, because withhold_text is honoured on both.
    # A thread-enabled [[x_post]] writes snapshot text under its own id, so
    # reading only [[source]] would let a withheld conversation reach a commit.
    cfg = registry_store.load(ROOT)
    return {
        s["id"] for s in (*cfg.get("source", []), *cfg.get("x_post", []))
        if s.get("withhold_text") is True
    }


def committable() -> list[str]:
    """Paths git would include: tracked, plus untracked and not ignored."""
    out = []
    for args in (["git", "ls-files"],
                 ["git", "ls-files", "--others", "--exclude-standard"]):
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        out += [p for p in r.stdout.splitlines() if p]
    return out


def capture_leaks(paths: list[str]) -> list[str]:
    """Capture-side detail that must never be published with the archive.

    Two shapes. A `.meta.json` may carry a header the allowlist does not
    permit, which is the common case and the one that recurs whenever an
    origin moves to a new CDN. A captured body may carry the collector's own
    location, because some origins render the visitor's country or currency
    into the page they serve.
    """
    found: list[str] = []
    for path in paths:
        f = ROOT / path
        if path.endswith(".meta.json"):
            try:
                meta = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            extra = disallowed(meta.get("headers") or {})
            if extra:
                shown = ", ".join(extra[:4])
                more = f" (+{len(extra) - 4} more)" if len(extra) > 4 else ""
                found.append(f"{path}: header(s) not on the allowlist: "
                             f"{shown}{more}")
        elif f.suffix.lower() in {".html", ".txt"}:
            try:
                hits = geo_in_body(f.read_text(errors="ignore"))
            except OSError:
                continue
            if hits:
                found.append(f"{path}: capture-side geolocation in body: "
                             f"{hits[0][:60]}")
    return found


def main() -> int:
    ids = withheld_ids()
    paths = committable()
    problems: list[str] = []

    for path in paths:
        parts = Path(path).parts
        if any(part in ids for part in parts):
            problems.append(f"{path}: artefact of a withheld source")

    problems += capture_leaks(paths)

    if problems:
        print(f"publishable check failed ({len(problems)} problem(s)):",
              file=sys.stderr)
        for p in problems[:15]:
            print(f"  - {p}", file=sys.stderr)
        if len(problems) > 15:
            print(f"  ... and {len(problems) - 15} more", file=sys.stderr)
        print("\nThese must not be committed. A withheld source's artefact "
              "belongs in .gitignore; a stray header or a geolocated body "
              "should be scrubbed from the capture, which changes no hash "
              "the site cites.", file=sys.stderr)
        return 1

    # Advisory, not a failure. Collector and research addresses are published
    # deliberately; a victim's address never is, and the difference is a
    # judgement no pattern can make. Listing them keeps that judgement
    # deliberate instead of accidental.
    carriers: list[str] = []
    for path in paths:
        f = ROOT / path
        if f.suffix.lower() not in {".txt", ".md", ".json", ".jsonl", ".toml"}:
            continue
        try:
            if ADDRESS.search(f.read_text(errors="ignore")):
                carriers.append(path)
        except OSError:
            continue

    metas = sum(1 for p in paths if p.endswith(".meta.json"))
    print(f"publishable check ok: nothing from {len(ids)} withheld source(s) "
          f"would be committed; {metas} capture header set(s) clean")
    if carriers:
        print(f"notice: {len(carriers)} committable file(s) contain bitcoin "
              f"addresses. Confirm each is a collector or published research "
              f"address, never a victim's:")
        for c in carriers[:10]:
            print(f"  - {c}")
        if len(carriers) > 10:
            print(f"  ... and {len(carriers) - 10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
