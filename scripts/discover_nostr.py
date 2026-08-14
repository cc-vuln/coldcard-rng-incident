#!/usr/bin/env python3
"""Discover new incident discussion on nostr, gently.

The nostr twin of discover_stackernews.py: it asks NIP-50 search relays for
recent kind-1 notes matching a few fixed incident queries, sieves the results
through the same keyword list the other community discoverers use, and records
new candidate observations in the shared structured discovery store for the
intake agent (scripts/agent-discovery-intake.sh). Each saved run commits an
immutable observation batch and regenerates the candidate JSON and sharded
Markdown views; root DISCOVERY.md is only their index. Capture of registered
posts is a separate lane (scripts/ingest_nostr.py); nothing here writes to
archive/.

Volume discipline, because public relays are shared infrastructure:

- at most 12 requests per run: one NIP-50 REQ per query per relay, 50 events
  per query, 7 days back
- 1.5s between requests, the same POLITE_DELAY capture.py uses
- one retry per relay on connect failure, then that relay is done for the run
- opt-in: live reads require NOSTR_DISCOVERY_ENABLED=true (exactly); without
  it the script prints how to enable and exits 0 with no network activity
- manual during probation: do not add it to a systemd timer until the
  probation gate in docs/design/discovery-and-x-watch.md is extended to nostr

Search relays come from NOSTR_SEARCH_RELAYS (comma-separated wss URLs,
default DEFAULT_RELAYS below). Relay notes from this host, 6 Aug 2026:
wss://search.nos.today answers NIP-50 (occasionally needs one retry on
connect); wss://nostrja-kari-nip50.heguro.com, wss://antiprimal.net,
wss://relay.ditto.pub and wss://nostr.wine also return results (found via
NIP-66 kind-30166 monitor events, tested with a live "coldcard" query each);
relay.nostr.band times out on TCP; relay.noswhere.com, relay.nostrcheck.me,
relay.vertexlab.io, filter.nostr.wine, relay.orly.dev and relay.mleku.dev
connect but return nothing.

`--show <note1-or-hex>` fetches one event by id from wss://search.nos.today
and wss://relay.damus.io and prints the raw JSON, a flattened text view and
up to 20 reply snippets. It exists for the intake agent and counts as that
candidate's one body fetch. `--check` prints the local configuration without
touching the network.

nak (the nostr CLI) does the relay protocol work; bech32 note1/npub coding
comes from nostr_common.py because nak v0.20.2 has no `encode note` target.
Zero other dependencies: stdlib only (imports discovery_common.py for the
keyword list and structured discovery handling, nostr_common.py for nip19
coding).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from discovery_common import (  # noqa: E402
    KEYWORDS, POLITE_DELAY, WORK,
    load_state, persist_run, registered_urls, report_queued,
)
from nostr_common import bech32_encode, decode_event_ref  # noqa: E402

STATE = WORK / "nostr-discovery.json"
CANDIDATES = WORK / "nostr-candidates.jsonl"

NAK = shutil.which("nak")
# Working NIP-50 search relays from this host, broadest index first (see the
# docstring for the 6 Aug 2026 relay survey).
DEFAULT_RELAYS = [
    "wss://search.nos.today",
    "wss://nostrja-kari-nip50.heguro.com",
    "wss://antiprimal.net",
    "wss://relay.ditto.pub",
    "wss://nostr.wine",
]
SHOW_RELAYS = ["wss://search.nos.today", "wss://relay.damus.io"]

# Tight fixed queries in the incident vocabulary. Single terms: this relay
# answers "coldcard" and "coinkite" but returns nothing for multi-word
# queries like "coldcard rng" (verified 6 Aug 2026), so the recall/precision
# split is broad query here, KEYWORDS sieve client-side.
QUERIES = ["coldcard", "coinkite"]
LOOKBACK_DAYS = 7
LIMIT = 50
MAX_REQUESTS = 12  # hard ceiling per run, retries included; covers 5 relays x 2 queries
RETRY_BACKOFF = 3.0
SEARCH_TIMEOUT = 90
SHOW_TIMEOUT = 60
SNIPPET_LEN = 60
MAX_REPLIES = 20

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"https?://\S+")


class RelayError(RuntimeError):
    """One nak relay interaction failed."""


# bech32/nip19 coding (note1/npub encode, note1/nevent1/hex decode) comes
# from nostr_common, the one copy: nak v0.20.2 has no `encode note` target.


# --- nak subprocess ----------------------------------------------------------


def run_nak(args: list[str], timeout: int) -> list[dict]:
    """Run nak and parse one JSON event per stdout line (stderr is chatter)."""
    if NAK is None:
        raise RelayError("nak not found on PATH")
    try:
        proc = subprocess.run(
            [NAK, *args], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RelayError(f"nak timed out after {timeout}s") from exc
    except OSError as exc:
        raise RelayError(f"nak failed to start: {exc}") from exc
    if proc.returncode != 0:
        detail = re.sub(r"\s+", " ", proc.stderr).strip()[:300]
        raise RelayError(detail or f"nak exited {proc.returncode}")
    events = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("id"), str):
            events.append(event)
    return events


def search_relay(relay: str, query: str, since: int) -> list[dict]:
    """One NIP-50 REQ; one retry on failure, then RelayError."""
    args = ["req", "--search", query, "-k", "1", "-l", str(LIMIT),
            "--since", str(since), relay]
    try:
        return run_nak(args, SEARCH_TIMEOUT)
    except RelayError as first:
        print(f"{relay} search {query!r} failed ({first}); one retry",
              file=sys.stderr)
        time.sleep(RETRY_BACKOFF)
        try:
            return run_nak(args, SEARCH_TIMEOUT)
        except RelayError as second:
            raise RelayError(f"{first}; retry: {second}") from second


# --- registry, state, candidates --------------------------------------------


def registered_urls_nostr() -> dict[str, str]:
    """URLs of every nostr post already registered in sources.toml.

    Registrations are [[nostr_post]] blocks, not [[source]]: they are
    validated by capture.py but never polled, so they live in their own array.
    """
    return registered_urls(lambda url: url, table="nostr_post")


def sanitize_snippet(content: str, limit: int = SNIPPET_LEN) -> str:
    """One-line intake-safe snippet: no URLs (an x.com link would misroute
    the intake splitter) and no bracket characters that would break the
    line's markdown-ish shape."""
    text = URL_RE.sub("", content)
    text = re.sub(r"[\[\]()]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text or "(no text)"


def candidate_for_event(event: dict, relays: set[str], found_at: str) -> dict:
    npub = bech32_encode("npub", bytes.fromhex(event["pubkey"]))
    note = bech32_encode("note", bytes.fromhex(event["id"]))
    created = datetime.fromtimestamp(
        int(event.get("created_at", 0)), timezone.utc)
    return {
        "id": event["id"],
        "url": f"https://njump.me/{note}",
        "platform": "nostr",
        "label": "nostr",
        "title": sanitize_snippet(event.get("content") or ""),
        "author": f"{npub[:17]}…",
        "npub": npub,
        "note": note,
        "relayCount": len(relays),
        "relays": sorted(relays),
        "createdAt": created.isoformat(),
        "foundAt": found_at,
        "matched": True,
        "event": event,
    }


# --- modes -------------------------------------------------------------------


def check_config() -> int:
    print(f"nak: {NAK or 'NOT FOUND on PATH'}", end="")
    if NAK:
        try:
            proc = subprocess.run([NAK, "--version"], capture_output=True,
                                  text=True, timeout=15)
            print(f" ({proc.stdout.strip() or proc.stderr.strip()})")
        except (subprocess.TimeoutExpired, OSError):
            print(" (version probe failed)")
    else:
        print()
    on = os.environ.get("NOSTR_DISCOVERY_ENABLED", "").strip() == "true"
    print(f"NOSTR_DISCOVERY_ENABLED: "
          f"{'true (live reads allowed)' if on else 'not exactly true (disabled)'}")
    print(f"search relays: {', '.join(search_relays())}")
    print(f"queries: {', '.join(repr(q) for q in QUERIES)} "
          f"({LOOKBACK_DAYS}d back, limit {LIMIT}, max {MAX_REQUESTS} requests)")
    state = load_state(STATE)
    print(f"seen state: {len(state.get('seen', []))} event ids "
          f"({STATE.relative_to(ROOT)})")
    print("--check makes no network requests")
    return 0 if NAK else 1


def search_relays() -> list[str]:
    raw = os.environ.get("NOSTR_SEARCH_RELAYS", "").strip()
    relays = [r.strip() for r in raw.split(",") if r.strip()] or DEFAULT_RELAYS
    good = []
    for relay in relays:
        if relay.startswith("wss://"):
            good.append(relay)
        else:
            print(f"ignoring non-wss relay {relay!r}", file=sys.stderr)
    return good or DEFAULT_RELAYS


def show_event(ref: str) -> int:
    try:
        hex_id = decode_event_ref(ref)
    except ValueError as exc:
        print(f"bad event reference: {exc}", file=sys.stderr)
        return 2
    try:
        events = run_nak(["req", "-i", hex_id, *SHOW_RELAYS], SHOW_TIMEOUT)
    except RelayError as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    if not events:
        print(f"event {hex_id[:16]}… not found on "
              f"{', '.join(SHOW_RELAYS)}", file=sys.stderr)
        return 1
    event = events[0]
    npub = bech32_encode("npub", bytes.fromhex(event["pubkey"]))
    note = bech32_encode("note", bytes.fromhex(event["id"]))
    created = datetime.fromtimestamp(
        int(event.get("created_at", 0)), timezone.utc)
    print(json.dumps(event, indent=2, sort_keys=True))
    print("\n--- flattened ---")
    print(f"note:     {note}")
    print(f"author:   {npub}")
    print(f"created:  {created.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"content:  {event.get('content') or ''}")
    try:
        replies = run_nak(["req", "-k", "1", "-t", f"e={hex_id}",
                           "-l", str(MAX_REPLIES), *SHOW_RELAYS],
                          SHOW_TIMEOUT)
    except RelayError as exc:
        print(f"\n--- replies unavailable: {exc} ---")
        return 0
    print(f"\n--- replies ({len(replies)} fetched, up to {MAX_REPLIES} shown) ---")
    for reply in replies[:MAX_REPLIES]:
        when = datetime.fromtimestamp(
            int(reply.get("created_at", 0)), timezone.utc)
        who = bech32_encode("npub", bytes.fromhex(reply["pubkey"]))[:17]
        snippet = re.sub(r"\s+", " ", reply.get("content") or "")[:120]
        print(f"  {when.strftime('%Y-%m-%d %H:%M')} {who}…  {snippet}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="print config and relay status; no network activity")
    ap.add_argument("--no-state", action="store_true",
                    help="do not update the seen state (a look, not a sweep)")
    ap.add_argument("--show", metavar="NOTE1_OR_HEX",
                    help="print one event's raw JSON and text view "
                         "(for the intake agent)")
    args = ap.parse_args()

    if args.check:
        return check_config()

    if os.environ.get("NOSTR_DISCOVERY_ENABLED", "").strip() != "true":
        print("live nostr discovery is disabled; set "
              "NOSTR_DISCOVERY_ENABLED=true to enable it.\n"
              "nostr discovery is manual during probation; see the docstring "
              "of scripts/discover_nostr.py.")
        if args.show:
            return 2
        return 0

    if args.show:
        return show_event(args.show)

    if NAK is None:
        print("nak not found on PATH; cannot query relays", file=sys.stderr)
        return 1

    relays = search_relays()
    state = load_state(STATE)
    seen = set(state.get("seen", []))
    known = registered_urls_nostr()
    now = datetime.now(timezone.utc)
    since = int(now.timestamp()) - LOOKBACK_DAYS * 86400

    events: dict[str, dict] = {}
    event_relays: dict[str, set[str]] = {}
    fetched = 0
    requests = 0
    failures = 0
    for relay in relays:
        for query in QUERIES:
            if requests >= MAX_REQUESTS:
                break
            if requests:
                time.sleep(POLITE_DELAY)
            try:
                found = search_relay(relay, query, since)
            except RelayError as exc:
                # search_relay already retried once; this relay is done
                requests += 2
                failures += 1
                print(f"{relay} done for this run: {exc}", file=sys.stderr)
                break
            requests += 1
            for event in found:
                fetched += 1
                eid = event["id"]
                events.setdefault(eid, event)
                event_relays.setdefault(eid, set()).add(relay)

    candidates = []
    for eid, event in sorted(events.items(),
                             key=lambda kv: (int(kv[1].get("created_at", 0)),
                                             kv[0])):
        if not HEX64_RE.fullmatch(eid) or \
                not HEX64_RE.fullmatch(str(event.get("pubkey") or "")):
            continue
        note_url = f"https://njump.me/{bech32_encode('note', bytes.fromhex(eid))}"
        if eid in seen or note_url in known:
            continue
        seen.add(eid)
        content = event.get("content") or ""
        # Client-side sieve: relays return plenty of near-miss fuzz.
        if KEYWORDS.search(content):
            candidates.append(
                candidate_for_event(event, event_relays[eid],
                                    now.strftime("%Y%m%dT%H%M%SZ")))

    persist_run(state=state, seen=seen, candidates=candidates, known=known,
                state_path=STATE, candidates_path=CANDIDATES,
                save=not args.no_state)

    print(f"scanned {fetched} events from {', '.join(relays)} "
          f"({requests} requests, {failures} relay failure(s)); "
          f"{len(candidates)} new candidate(s)")
    for c in candidates:
        print(f"  {c['createdAt'][:16]}  {c['id'][:8]}  "
              f"{c['author']:<20} {c['title']}")
    report_queued(candidates, CANDIDATES, not args.no_state)
    return 1 if failures and not events else 0


if __name__ == "__main__":
    sys.exit(main())
