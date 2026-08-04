#!/usr/bin/env python3
"""Discover new incident threads on BitcoinTalk, gently.

The BitcoinTalk twin of discover_stackernews.py and discover_reddit.py.
The forum is plain SMF 1.1 and answers anonymous requests from this host
(robots.txt carries only a sitemap line), so this script fetches board index
pages directly, no browser: one board page per run, 1.5s apart, fired twice
a day by discover-community.timer.

Two routes worth knowing, found 4 Aug 2026:

- board index (index.php?board=N.0) lists topics with title, author, reply
  count and last-post time; that is the discovery surface. Board 37 (Wallet
  software) is the incident's home board
- the print view (index.php?action=printpage;topic=T.0) renders a whole
  thread as stable text ("Title: / Post by: X on <date>") with no live user
  counters, while the ?topic=T.0;all view is Cloudflare-challenged from this
  host. Registered threads therefore capture the print view via fetch_url.
  --show prints one thread's print-view text for the intake agent

Volume discipline matches the other lanes: one page per board per run, twice
a day. Do not page deeper to backfill; that is a one-off enumeration job.

Zero dependencies: stdlib only (imports discover_stackernews.py for the
keyword list and intake file handling).
"""

import argparse
import html
import json
import re
import sys
import time
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discover_stackernews import (  # noqa: E402
    KEYWORDS, SOURCES, WORK, update_intake,
)

STATE = WORK / "bitcointalk-discovery.json"
CANDIDATES = WORK / "bitcointalk-candidates.jsonl"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "(+https://github.com/cc-vuln/coldcard-rng-incident; historical preservation)"
)
TIMEOUT = 45
POLITE_DELAY = 1.5
SEEN_KEEP = 5000
DEFAULT_BOARDS = ["37", "1"]  # 37 = Wallet software; 1 = Bitcoin Discussion
                              # (the incident megathread, topic 5589927, is
                              # pinned in Bitcoin Discussion)

BASE = "https://bitcointalk.org/index.php"
TOPIC_URL_RE = re.compile(r"bitcointalk\.org/index\.php\?topic=(\d+)")
ROW_RE = re.compile(
    r'\?topic=(\d+)\.0">(.*?)</a></span>.*?'
    r'profile;u=\d+"[^>]*>([^<]+)</a>.*?'
    r'align="center">\s*(\d+)\s*</td>\s*'
    r'<td[^>]*align="center">\s*(\d+)\s*</td>.*?'
    r'([A-Z][a-z]+ \d{2}, \d{4}, \d{2}:\d{2}:\d{2} [AP]M'
    r'|<b>Today</b> at \d{2}:\d{2}:\d{2} [AP]M)<br',
    re.S)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_board(page: str) -> list[dict]:
    """Topic rows from an SMF board index page."""
    topics = []
    for chunk in page.split("<tr"):
        if "index.php?topic=" not in chunk or "</span>" not in chunk:
            continue
        m = ROW_RE.search(chunk)
        if not m:
            continue
        tid, title, author, replies, _views, lastpost = m.groups()
        try:
            if "Today" in lastpost:
                clock = datetime.strptime(lastpost, "<b>Today</b> at %I:%M:%S %p")
                today = datetime.now(timezone.utc)
                last_dt = today.replace(hour=clock.hour, minute=clock.minute,
                                        second=clock.second, microsecond=0)
            else:
                last_dt = datetime.strptime(lastpost, "%B %d, %Y, %I:%M:%S %p")
        except ValueError:
            continue
        topics.append({
            "id": tid,
            "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
            "author": html.unescape(author).strip(),
            "replies": int(replies),
            # Guest times are UTC; the board shows last-post time, not the
            # topic's start time, so that is what a candidate can carry.
            "lastPostAt": last_dt.replace(tzinfo=timezone.utc).isoformat(),
        })
    return topics


def registered_urls() -> set[str]:
    """Canonical topic URLs of every bitcointalk source in sources.toml."""
    data = tomllib.loads(SOURCES.read_text(encoding="utf-8"))
    urls = set()
    for src in data.get("source", []):
        m = TOPIC_URL_RE.search(src.get("url", ""))
        if m:
            urls.add(f"{BASE}?topic={m.group(1)}.0")
    return urls


def print_view_text(topic_id: str) -> str:
    """One thread's print view as plain text (the capture surface, and
    what --show hands the intake agent)."""
    page = fetch(f"{BASE}?action=printpage;topic={topic_id}.0")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seen": []}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", action="append", dest="boards",
                    help="SMF board number to scan (default: 37, Wallet software)")
    ap.add_argument("--all", action="store_true",
                    help="report every new topic, not just keyword matches")
    ap.add_argument("--no-state", action="store_true",
                    help="do not update the seen state (a look, not a sweep)")
    ap.add_argument("--show", metavar="TOPIC_ID",
                    help="print one thread's print-view text and exit (for the intake agent)")
    args = ap.parse_args()

    if args.show:
        print(print_view_text(args.show)[:6000])
        return 0

    boards = args.boards or DEFAULT_BOARDS
    state = load_state()
    seen = set(state.get("seen", []))
    known = registered_urls()
    now = datetime.now(timezone.utc)

    candidates = []
    fetched = 0
    requests = 0
    for board in boards:
        if requests:
            time.sleep(POLITE_DELAY)
        try:
            page = fetch(f"{BASE}?board={board}.0")
        except Exception as exc:
            print(f"error fetching board {board}: {exc}", file=sys.stderr)
            return 1
        requests += 1
        for t in parse_board(page):
            fetched += 1
            url = f"{BASE}?topic={t['id']}.0"
            if t["id"] in seen or url in known:
                continue
            seen.add(t["id"])
            if args.all or KEYWORDS.search(t["title"]):
                candidates.append({
                    "id": t["id"],
                    "url": url,
                    "sub": board,
                    "label": f"bct/{board}",
                    "title": t["title"],
                    "author": t["author"],
                    "createdAt": t["lastPostAt"],
                    "ncomments": t["replies"],
                    "foundAt": now.strftime("%Y%m%dT%H%M%SZ"),
                    "matched": bool(KEYWORDS.search(t["title"])),
                })

    WORK.mkdir(exist_ok=True)
    if not args.no_state:
        if candidates:
            with CANDIDATES.open("a", encoding="utf-8") as fh:
                for c in candidates:
                    fh.write(json.dumps(c, sort_keys=True) + "\n")
        state["seen"] = sorted(seen)[-SEEN_KEEP:]
        STATE.write_text(json.dumps(state) + "\n", encoding="utf-8")
        update_intake(candidates, known)

    print(f"scanned {fetched} topics from board(s) {', '.join(boards)} "
          f"({requests} requests); {len(candidates)} new candidate(s)")
    for c in candidates:
        print(f"  {c['createdAt'][:16]}  {c['id']:>8}  [bct/{c['sub']}] "
              f"r={c['ncomments']!s:<3} {c['author'] or '?':<20} {c['title']}")
    if candidates and not args.no_state:
        print(f"appended to {CANDIDATES.relative_to(ROOT)} and DISCOVERY.md; "
              f"the intake agent assesses pending entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
