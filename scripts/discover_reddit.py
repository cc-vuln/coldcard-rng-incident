#!/usr/bin/env python3
"""Discover new incident threads on Reddit, gently.

The Reddit twin of discover_stackernews.py: capture of registered threads is
generic (sources.toml capture = "reddit-json"); what was missing is discovery.
This script reads each watched subreddit's /new listing and queues
keyword-matched threads in DISCOVERY.md, the shared intake file, for the
intake agent (scripts/agent-discovery-intake.sh).

The route is the capture browser, not a direct request: Reddit refuses
anonymous JSON from this host with a 403 challenge, so listings are read
through the webbridge session (fetch_json), exactly as thread capture does.
Two consequences:

- the session is signed in to Reddit as the project account; this script only
  ever reads listing and thread JSON, the same read-only vocabulary capture
  uses
- the session is shared with live polls. Two listing reads every 12 hours
  keep the collision window tiny; the known dry-run overlap issue is BACKLOG
  section 2

Volume discipline: one 100-post page per subreddit per run, 1.5s apart,
fired twice a day by discover-community.timer. Do not page deeper to
backfill: that is a one-off enumeration job, not this script's.

`--show <post-id>` fetches one thread's post body through the same route.
It exists for the intake agent, which cannot reach Reddit directly either.

Zero dependencies: stdlib only (imports capture.py for the webbridge protocol
and discovery_common.py for the keyword list and intake file handling).
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from capture import BrowserUnavailable, wb_available, wb_cmd  # noqa: E402
from discovery_common import (  # noqa: E402
    KEYWORDS, POLITE_DELAY, WORK,
    load_state, persist_run, registered_urls, report_queued,
)

STATE = WORK / "reddit-discovery.json"
CANDIDATES = WORK / "reddit-candidates.jsonl"

DEFAULT_SUBS = ["coldcard", "Bitcoin"]

POST_URL_RE = re.compile(r"reddit\.com/r/[^/]+/comments/([a-z0-9]+)")


def registered_urls_reddit() -> set[str]:
    """URLs of every reddit source already in sources.toml.

    Reddit permalinks are registered in the form the listing reports them, so
    a matched URL is already canonical.
    """
    return registered_urls(lambda url: url if POST_URL_RE.search(url) else None)


def fetch_new(sub: str, limit: int) -> list[dict]:
    url = f"https://www.reddit.com/r/{sub}/new.json?limit={limit}&raw_json=1"
    try:
        data = wb_cmd("fetch_json", {"url": url}, timeout=180)
    finally:
        try:
            wb_cmd("close_tab")
        except Exception:
            pass
    if not data.get("json_ok"):
        raise BrowserUnavailable(
            f"fetch_json r/{sub}: status {data.get('status')}")
    return json.loads(data["body"])["data"]["children"]


def fetch_post_body(post_id: str) -> None:
    """Print one thread's post fields for the intake agent (--show).

    The trailing slash matters: /comments/<id>.json without it returns a
    comments-only listing; with it Reddit redirects to the canonical thread
    JSON, the two-element [post, comments] listing."""
    url = f"https://www.reddit.com/comments/{post_id}/.json?raw_json=1"
    try:
        data = wb_cmd("fetch_json", {"url": url}, timeout=180)
    finally:
        try:
            wb_cmd("close_tab")
        except Exception:
            pass
    if not data.get("json_ok"):
        raise BrowserUnavailable(
            f"fetch_json post {post_id}: status {data.get('status')}")
    body = json.loads(data["body"])
    if not isinstance(body, list):
        raise BrowserUnavailable(
            f"fetch_json post {post_id}: unexpected payload shape")
    post = body[0]["data"]["children"][0]["data"]
    print(json.dumps({
        "id": post.get("id"),
        "subreddit": post.get("subreddit"),
        "title": post.get("title"),
        "author": post.get("author"),
        "created_utc": post.get("created_utc"),
        "num_comments": post.get("num_comments"),
        "selftext": post.get("selftext") or "",
        "permalink": "https://www.reddit.com" + post.get("permalink", ""),
    }, indent=2, sort_keys=True))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sub", action="append", dest="subs",
                    help="subreddit to scan (default: coldcard, Bitcoin)")
    ap.add_argument("--limit", type=int, default=100, help="posts per listing")
    ap.add_argument("--all", action="store_true",
                    help="report every new post, not just keyword matches")
    ap.add_argument("--no-state", action="store_true",
                    help="do not update the seen state (a look, not a sweep)")
    ap.add_argument("--show", metavar="POST_ID",
                    help="print one thread's post body and exit (for the intake agent)")
    args = ap.parse_args()

    if args.show:
        fetch_post_body(args.show)
        return 0

    if not wb_available():
        print("capture browser not reachable; skipping (a recorded gap)",
              file=sys.stderr)
        return 1

    subs = args.subs or DEFAULT_SUBS
    state = load_state(STATE)
    seen = set(state.get("seen", []))
    known = registered_urls_reddit()
    now = datetime.now(timezone.utc)

    candidates = []
    fetched = 0
    requests = 0
    for sub in subs:
        if requests:
            time.sleep(POLITE_DELAY)
        try:
            children = fetch_new(sub, args.limit)
        except Exception as exc:
            print(f"error fetching r/{sub}: {exc}", file=sys.stderr)
            return 1
        requests += 1
        for ch in children:
            d = ch.get("data", {})
            pid = str(d.get("id", ""))
            if not pid:
                continue
            fetched += 1
            url = "https://www.reddit.com" + d.get("permalink", "")
            if pid in seen or url in known:
                continue
            seen.add(pid)
            title = d.get("title") or "(untitled)"
            selftext = d.get("selftext") or ""
            haystack = title + "\n" + selftext
            # r/coldcard is low-volume and, since the incident, on-topic by
            # default: every new post there is worth the agent's assessment.
            # Larger subs need the keyword sieve.
            if args.all or sub.lower() == "coldcard" or KEYWORDS.search(haystack):
                candidates.append({
                    "id": pid,
                    "url": url,
                    "sub": sub,
                    "label": f"r/{sub}",
                    "title": title,
                    "author": d.get("author"),
                    "createdAt": datetime.fromtimestamp(
                        d.get("created_utc", 0), timezone.utc).isoformat(),
                    "ncomments": d.get("num_comments"),
                    "foundAt": now.strftime("%Y%m%dT%H%M%SZ"),
                    "matched": bool(KEYWORDS.search(haystack)),
                })

    persist_run(state=state, seen=seen, candidates=candidates, known=known,
                state_path=STATE, candidates_path=CANDIDATES,
                save=not args.no_state)

    print(f"scanned {fetched} posts from r/{', r/'.join(subs)} "
          f"({requests} requests); {len(candidates)} new candidate(s)")
    for c in candidates:
        print(f"  {c['createdAt'][:16]}  {c['id']:>8}  [r/{c['sub']}] "
              f"c={c['ncomments']!s:<3} {c['author'] or '?':<20} {c['title']}")
    report_queued(candidates, CANDIDATES, not args.no_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
