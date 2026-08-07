#!/usr/bin/env python3
"""Discover new incident threads on Stacker News, gently.

Capture of registered threads is generic (sources.toml fetch_post against the
GraphQL API); what was missing is discovery: the site's keyword search does
not index recent items, so new incident threads have to be found by reading
the territory recent feeds. That is all this script does.

Volume discipline, because the site is small and we are guests:

- default run is TWO requests: one 100-item page of ~bitcoin recent, one of
  ~security recent
- 1.5s between requests, the same POLITE_DELAY capture.py uses
- the identifying project user agent, never a bare library default
- run it at most every few hours; the feeds move slowly enough that more is
  waste. Do not raise --pages/--limit to backfill: that is a one-off
  enumeration job, not this script's

stacker.news serves no robots.txt (the path returns the application shell),
so there is no published crawl policy. Two requests per run is well inside
what the site already answers anonymously for any reader, but the open
permission question for anything heavier remains a standing limit in
AGENTS.md.

Candidates land in two places: .work/stackernews-candidates.jsonl (the
gitignored raw log) and DISCOVERY.md at the repo root (the tracked intake
queue, shared with every other discovery lane, so new candidates show up in
git status and in front of anyone working the tree). A candidate is a new item
whose title matches the incident vocabulary; --all reports every new item for
a full manual sweep.

Assessment is the intake agent's job, not this script's: pending DISCOVERY.md
entries are assessed by scripts/agent-discovery-intake.sh (REVIEW_AGENT_BIN,
the same agent pattern as agent-review.sh), which registers relevant threads
in sources.toml, first-captures them with `just capture-one`, and records
every verdict in DISCOVERY.md. Entries whose
thread reaches sources.toml by any route drop out of Pending on the next run.
Nothing here writes to archive/: capture.py remains the only writer.

Zero dependencies: stdlib only (imports discovery_common.py for the keyword
list and intake file handling), Python 3.11+ for tomllib.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discovery_common import (  # noqa: E402
    POLITE_DELAY, TIMEOUT, UA, WORK,
    deferred_urls, load_state, match_tier, persist_run, queue_mark,
    registered_urls, report_queued,
)

STATE = WORK / "stackernews-discovery.json"
CANDIDATES = WORK / "stackernews-candidates.jsonl"

API = "https://stacker.news/api/graphql"
MAX_PAGES = 3  # hard ceiling; deeper backfill is a deliberate one-off, see docstring

ITEM_URL_RE = re.compile(r"stacker\.news/items/(\d+)")

ITEMS_QUERY = """
query ($sub: String!, $cursor: String) {
  items(sub: $sub, sort: "recent", limit: %d, cursor: $cursor) {
    cursor
    items { id title createdAt user { name } ncomments }
  }
}
"""


def fetch_page(sub: str, limit: int, cursor: str | None) -> dict:
    body = json.dumps({
        "query": ITEMS_QUERY % limit,
        "variables": {"sub": sub, "cursor": cursor},
    }).encode("utf-8")
    req = urllib.request.Request(API, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


ITEM_QUERY = "{ item(id: %d) { title text ncomments createdAt user { name } } }"


def fetch_item_body(item_id: str) -> None:
    """Print one thread's item fields for the intake agent (--show).

    The intake prompt used to hand the agent a curl command for this. It no
    longer does: the agent is deprivileged and has no business making network
    requests of its own, so scripts/hydrate_candidates.py calls this on its
    behalf and puts the result in the prompt. Same two fields, same one
    request, fetched by a process an injection is not steering.
    """
    body = json.dumps({"query": ITEM_QUERY % int(item_id)}).encode("utf-8")
    req = urllib.request.Request(API, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.load(resp)
    item = (payload.get("data") or {}).get("item")
    if not item:
        raise SystemExit(f"stacker.news item {item_id}: no such item")
    print(json.dumps(item, indent=2, sort_keys=True))


def registered_urls_sn() -> set[str]:
    """Canonical URLs of every stacker.news source already in sources.toml."""
    def canonical(url: str) -> str | None:
        m = ITEM_URL_RE.search(url)
        return f"https://stacker.news/items/{m.group(1)}" if m else None
    return registered_urls(canonical)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sub", action="append", dest="subs",
                    help="territory to scan (default: bitcoin, security)")
    ap.add_argument("--limit", type=int, default=100, help="items per page")
    ap.add_argument("--pages", type=int, default=1,
                    help=f"pages per territory (max {MAX_PAGES})")
    ap.add_argument("--all", action="store_true",
                    help="report every new item, not just keyword matches")
    ap.add_argument("--no-state", action="store_true",
                    help="do not update the seen state (a look, not a sweep)")
    ap.add_argument("--show", metavar="ITEM_ID",
                    help="print one thread's item body and exit (for hydration)")
    args = ap.parse_args()

    if args.show:
        fetch_item_body(args.show)
        return 0

    subs = args.subs or ["bitcoin", "security"]
    pages = min(args.pages, MAX_PAGES)
    state = load_state(STATE)
    seen = set(state.get("seen", []))
    known = registered_urls_sn()
    deferred = deferred_urls()
    cutoff = datetime.now(timezone.utc)

    candidates = []
    fetched = 0
    requests = 0
    for sub in subs:
        cursor = None
        for _ in range(pages):
            if requests:
                time.sleep(POLITE_DELAY)
            try:
                data = fetch_page(sub, args.limit, cursor)
            except Exception as exc:
                print(f"error fetching ~{sub}: {exc}", file=sys.stderr)
                return 1
            requests += 1
            payload = (data.get("data") or {}).get("items") or {}
            items = payload.get("items") or []
            cursor = payload.get("cursor")
            if not items:
                break
            for it in items:
                fetched += 1
                iid = str(it.get("id"))
                url = f"https://stacker.news/items/{iid}"
                if url in known:
                    continue
                # A deferred candidate is re-reported while it is still in
                # the listing window, so it can promote itself once the
                # thread grows.
                if iid in seen and url not in deferred:
                    continue
                seen.add(iid)
                title = it.get("title") or "(comment or untitled)"
                tier = match_tier(title)
                if args.all or tier:
                    candidates.append({
                        "id": iid,
                        "url": url,
                        "sub": sub,
                        "label": f"~{sub}",
                        "title": title,
                        "author": (it.get("user") or {}).get("name"),
                        "createdAt": it.get("createdAt"),
                        "ncomments": it.get("ncomments"),
                        "foundAt": cutoff.strftime("%Y%m%dT%H%M%SZ"),
                        "matched": bool(tier),
                        "tier": tier,
                    })
            if not cursor:
                break

    persist_run(state=state, seen=seen, candidates=candidates, known=known,
                state_path=STATE, candidates_path=CANDIDATES,
                save=not args.no_state)

    print(f"scanned {fetched} items from ~{', ~'.join(subs)} "
          f"({requests} requests); {len(candidates)} new candidate(s)")
    for c in candidates:
        print(f"  {c['createdAt'][:16]}  {c['id']:>8}  [~{c['sub']}] "
              f"c={c['ncomments']:<3} {queue_mark(c):<9} "
              f"{c['author'] or '?':<20} {c['title']}")
    report_queued(candidates, CANDIDATES, not args.no_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
