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
permission question for anything heavier is recorded in BACKLOG.md.

Candidates land in two places: .work/stackernews-candidates.jsonl (the
gitignored raw log) and DISCOVERY.md at the repo root (the tracked intake
queue, shared with discover_reddit.py, so new candidates show up in git
status and in front of anyone working the tree). A candidate is a new item
whose title matches the incident vocabulary; --all reports every new item for
a full manual sweep.

Assessment is the intake agent's job, not this script's: pending DISCOVERY.md
entries are assessed by scripts/agent-discovery-intake.sh (REVIEW_AGENT_BIN,
the same agent pattern as agent-review.sh), which registers relevant threads
in sources.toml, first-captures them with `just capture-one`, and records
every verdict in DISCOVERY.md. Entries whose
thread reaches sources.toml by any route drop out of Pending on the next run.
Nothing here writes to archive/: capture.py remains the only writer.

Zero dependencies: stdlib only, Python 3.11+ for tomllib.
"""

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.toml"
WORK = ROOT / ".work"
STATE = WORK / "stackernews-discovery.json"
CANDIDATES = WORK / "stackernews-candidates.jsonl"
INTAKE = ROOT / "DISCOVERY.md"
INTAKE_LOCK = WORK / "agent-discovery-intake" / "intake.lock"

API = "https://stacker.news/api/graphql"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "(+https://github.com/cc-vuln/coldcard-rng-incident; historical preservation)"
)
TIMEOUT = 45
POLITE_DELAY = 1.5
MAX_PAGES = 3  # hard ceiling; deeper backfill is a deliberate one-off, see docstring
SEEN_KEEP = 5000
INTAKE_LOCK_TIMEOUT = 60.0

# Incident vocabulary for title matching. Oblique titles ("Dear podcasters &
# influencers") will not match; --all exists for a full sweep when the feed is
# busy. Title-only by design: fetching every item body would multiply request
# volume for no discovery gain.
KEYWORDS = re.compile(r"|".join([
    r"cold\s?card", r"coinkite", r"\bnvk\b", r"\brng\b", r"entropy",
    r"seed phrase", r"dice", r"drain", r"sweep", r"stolen", r"theft", r"hack",
    r"hardware wallet", r"passphrase", r"slipstream", r"bitkey", r"opensats",
    r"\bbtcrecover\b", r"self.?custody", r"phishing", r"1596|1,?596|1367|1,?367",
]), re.IGNORECASE)

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


def registered_urls() -> set[str]:
    """Canonical URLs of every stacker.news source already in sources.toml."""
    data = tomllib.loads(SOURCES.read_text(encoding="utf-8"))
    urls = set()
    for src in data.get("source", []):
        m = re.search(r"stacker\.news/items/(\d+)", src.get("url", ""))
        if m:
            urls.add(f"https://stacker.news/items/{m.group(1)}")
    return urls


INTAKE_HEADER = """\
# Discovery intake

Candidates found by the Stacker News, Reddit, BitcoinTalk and manual X
discovery commands. The community-thread lanes run every 12 hours through
`discover-community.timer`; X discovery remains manual while its API and
policy gates are proved. That timer neither runs X discovery nor sends queued
X links to the general community agent. An operator may invoke the separate
X-only triage with `--include-x` during probation. Direct `just ingest-x`
capture of a manually supplied permalink does not use this queue.
Eligible pending entries are assessed by the intake agent
(`scripts/agent-discovery-intake.sh`): a relevant community thread is
registered and first-captured. Explicit X triage only recommends or dismisses
permalinks for human review; it cannot capture or register them. Assessed
entries move below with the verdict. To dismiss a candidate by hand, move its
line to Assessed with a one-line reason.

## Pending

## Assessed
"""

LINE_URL_RE = re.compile(r"\((https?://[^)]+)\)")


def intake_line(c: dict) -> str:
    if c.get("platform") == "x":
        return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
                f"({c['label']})")
    return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
            f"by {c['author'] or '?'}, {c['ncomments']} comments ({c['label']})")


def atomic_text(path: Path, text: str) -> None:
    """Replace the shared intake file only after its new body is durable."""
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def acquire_intake_lock(lock_handle, timeout: float = INTAKE_LOCK_TIMEOUT) -> None:
    """Wait briefly for the agent's DISCOVERY.md lock, then fail visibly."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "discovery intake remained busy; checkpoint not advanced"
                )
            time.sleep(0.25)


def update_intake(candidates: list[dict], known_urls: set[str]) -> None:
    """Reconcile DISCOVERY.md: prune registered threads from Pending, append
    new candidates. Assessed entries are the intake agent's (or a human's)
    record and are kept verbatim."""
    INTAKE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with INTAKE_LOCK.open("a+") as lock_handle:
        acquire_intake_lock(lock_handle)
        if INTAKE.exists():
            text = INTAKE.read_text(encoding="utf-8")
        else:
            text = INTAKE_HEADER
        parts = text.split("## Assessed", 1)
        head = parts[0]
        assessed = parts[1] if len(parts) == 2 else "\n"
        pending = [l for l in head.splitlines() if l.startswith("- ")]
        head = [l for l in head.splitlines() if not l.startswith("- ")]

        present = {m.group(1) for l in pending + assessed.splitlines()
                   if (m := LINE_URL_RE.search(l))}
        pending = [l for l in pending
                   if LINE_URL_RE.search(l).group(1) not in known_urls]
        for c in candidates:
            if c["url"] not in present:
                pending.append(intake_line(c))

        out = "\n".join(head).rstrip() + "\n"
        if pending:
            out += "\n" + "\n".join(pending) + "\n"
        out += "\n## Assessed" + assessed.rstrip() + "\n"
        if not INTAKE.exists() or out != text:
            atomic_text(INTAKE, out)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seen": []}


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
    args = ap.parse_args()

    subs = args.subs or ["bitcoin", "security"]
    pages = min(args.pages, MAX_PAGES)
    state = load_state()
    seen = set(state.get("seen", []))
    known = registered_urls()
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
                if iid in seen or url in known:
                    continue
                seen.add(iid)
                title = it.get("title") or "(comment or untitled)"
                if args.all or KEYWORDS.search(title):
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
                        "matched": bool(KEYWORDS.search(title)),
                    })
            if not cursor:
                break

    WORK.mkdir(exist_ok=True)
    if not args.no_state:
        if candidates:
            with CANDIDATES.open("a", encoding="utf-8") as fh:
                for c in candidates:
                    fh.write(json.dumps(c, sort_keys=True) + "\n")
        state["seen"] = sorted(seen)[-SEEN_KEEP:]
        STATE.write_text(json.dumps(state) + "\n", encoding="utf-8")
        update_intake(candidates, known)

    print(f"scanned {fetched} items from ~{', ~'.join(subs)} "
          f"({requests} requests); {len(candidates)} new candidate(s)")
    for c in candidates:
        print(f"  {c['createdAt'][:16]}  {c['id']:>8}  [~{c['sub']}] "
              f"c={c['ncomments']:<3} {c['author'] or '?':<20} {c['title']}")
    if candidates and not args.no_state:
        print(f"appended to {CANDIDATES.relative_to(ROOT)} and DISCOVERY.md; "
              f"the intake agent assesses pending entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
