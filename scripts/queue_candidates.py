#!/usr/bin/env python3
"""Queue operator-dropped candidate URLs into DISCOVERY.md's Pending section.

The discovery lanes find candidates on their own schedules, but sometimes a
person has a list of URLs in hand: the incident-day X posts of 30-31 July
arrived exactly that way on 12 Aug 2026, handed over by another tool, and
hand-editing the intake queue is fiddly and easy to do subtly wrong. The
drop box is

    .work/operator-candidates.txt

one URL per line, `#` comments allowed. Both intake drivers call this script
at the top of every run, so a dropped URL joins the ordinary queue on the
next tick and is hydrated, assessed and verdicted like anything the lanes
found themselves, with one difference: drops go to the HEAD of Pending.
A dropped URL was already judged worth reading by the person dropping it,
which is a stronger signal than the sieve produces, and the 12 Aug 2026
incident-day drops would otherwise have waited days behind the backlog.
X status permalinks become X candidates; the community
platforms' URLs (reddit, stacker.news, bitcointalk, njump) become community
candidates, classified by hydrate_candidates' own patterns so there is one
place that knows what a candidate URL looks like. URLs already present
anywhere in DISCOVERY.md or registered in sources.toml are dropped as
duplicates. Anything unrecognized stays in the drop file for a person.

Runs under the intake lock, before the calling driver takes it. Stdlib only.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discovery_common
import hydrate_candidates

DROP = discovery_common.WORK / "operator-candidates.txt"
SOURCES = ROOT / "sources.toml"

HEADER = ("# One candidate URL per line; `#` lines are comments. The intake "
          "drivers queue what they recognize and leave the rest here.\n")

X_STATUS = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/"
                      r"([^/]+)/status/(\d+)")


def build_line(url: str, today: str) -> str | None:
    """The queue line for a dropped URL, or None when unrecognized."""
    match = X_STATUS.search(url)
    if match:
        handle, _status = match.groups()
        return discovery_common.intake_line({
            "platform": "x",
            "url": url,
            "createdAt": today,
            "title": (f"@{handle} post "
                      "(text available during approved intake)"),
            "label": f"X @{handle}",
        })
    # A bare URL on a line is enough for the community classifier: it matches
    # the platform by the URL pattern, the same as a lane-reported line.
    if hydrate_candidates.classify(url, include_x=False) is not None:
        return discovery_common.intake_line({
            "url": url,
            "createdAt": today,
            "title": "operator-supplied candidate",
            "author": "",
            "ncomments": 0,
            "label": "operator drop",
        })
    return None


def queue(urls: list[str], today: str) -> tuple[list[str], list[str]]:
    """Queue what is new and recognized. Returns (queued, left) URLs."""
    present_text = ""
    if discovery_common.INTAKE.exists():
        present_text = discovery_common.INTAKE.read_text(encoding="utf-8")
    if SOURCES.exists():
        present_text += SOURCES.read_text(encoding="utf-8")
    queued, left = [], []
    for url in urls:
        if url in present_text:
            print(f"queue-candidates: already queued or registered: {url}")
            continue
        line = build_line(url, today)
        if line is None:
            print(f"queue-candidates: not a recognized candidate URL, left "
                  f"in the drop file: {url}", file=sys.stderr)
            left.append(url)
            continue
        queued.append(line)
    if not queued:
        return [], left
    discovery_common.INTAKE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with discovery_common.INTAKE_LOCK.open("a+") as lock_handle:
        discovery_common.acquire_intake_lock(lock_handle)
        text = (discovery_common.INTAKE.read_text(encoding="utf-8")
                if discovery_common.INTAKE.exists()
                else discovery_common.INTAKE_HEADER)
        sections = discovery_common.split_sections(text)
        rebuilt = []
        for heading, lines in sections:
            if heading == discovery_common.PENDING_H:
                body = lines[:]
                while body and not body[0].strip():
                    body.pop(0)
                lines = queued + body
            rebuilt.append((heading, lines))
        discovery_common.atomic_text(
            discovery_common.INTAKE, discovery_common.join_sections(rebuilt))
    return queued, left


def main() -> int:
    if not DROP.exists():
        return 0
    urls = []
    for raw in DROP.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    if not urls:
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    queued, left = queue(urls, today)
    DROP.write_text(HEADER + "".join(f"{url}\n" for url in left),
                    encoding="utf-8")
    if queued:
        print(f"queue-candidates: queued {len(queued)} operator-supplied "
              f"candidate(s) into DISCOVERY.md Pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
