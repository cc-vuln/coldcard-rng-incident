#!/usr/bin/env python3
"""Ingest one nostr note and its replies into archive/nostr/.

Nostr events are self-authenticating signed artefacts: the signature is
verified by nak on receipt, so there is no browser session to borrow and no
screenshot provenance question. The capture is the event JSON itself, plus a
flattened text rendering and a tool sidecar. nak is the sanctioned binary
beside gallery-dl for social capture.

The registry entry is a [[nostr_post]] block in sources.toml, written by the
intake agent or by hand; this tool never edits sources.toml. A capture lands
under the registered id so the site, which iterates the registry, looks in
the directory the capture actually wrote. With a slug argument and no
registry entry this is an operator first-capture: it writes under
<slug>-<hexprefix8> and prints the exact block to register. Re-capturing
writes a new <TS> directory beside the old one, so the append-only rule is a
property of the layout rather than something a writer has to remember.

Stdlib only, per repo policy. Run through the venv:

  .venv/bin/python scripts/ingest_nostr.py <note1|nevent1|hex> [slug] [tag] [why]

or via just:

  just ingest-nostr <note1...> <slug>

Exit 2: input invalid, or the note is not registered and no slug was given.
Exit 1: no relay returned the event, or the returned id did not match.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from archive_lock import ArchiveLockBusy, archive_lock
from nostr_common import bech32_decode, bech32_encode, decode_event_ref

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "archive" / "nostr"
SOURCES = ROOT / "sources.toml"
NAK = shutil.which("nak") or str(Path.home() / ".local" / "bin" / "nak")
# relay.nostr.band is unreachable from the capture host; these answer.
RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    # A search relay; sometimes needs one connect retry.
    "wss://search.nos.today",
]
REPLY_CAP = 200
NAK_TIMEOUT = 90
HEX64 = re.compile(r"^[0-9a-f]{64}$")

# bech32/nip19 helpers come from nostr_common (the one copy); the names stay
# importable from here because test_ingest_nostr.py exercises them through
# this module.


# --- registry ---------------------------------------------------------------


def find_registration(hexid: str, sources_path: Path = SOURCES) -> dict | None:
    """The [[nostr_post]] this event is registered under, if any.

    Matches on the note id in bech32 or hex form inside the entry's URL, or
    on the id's first 8 hex chars ending the entry's slug (the registry's
    own id convention).
    """
    try:
        cfg = tomllib.loads(sources_path.read_text())
    except Exception:
        return None
    note1 = bech32_encode("note", bytes.fromhex(hexid))
    for post in cfg.get("nostr_post", []):
        url = post.get("url", "")
        if hexid in url or note1 in url:
            return post
        if post.get("id", "").endswith(hexid[:8]):
            return post
    return None


REGISTRATION_HINT = """\
not registered in sources.toml. The intake agent registers incident-relevant
notes as:

[[nostr_post]]
id = "<slug>-<first 8 hex of event id>"
title = "..."
url = "https://njump.me/note1..."
author = "npub1..."
org = "nostr"
posted = "YYYY-MM-DD"
tag = "community"
why = "..."

For an operator first-capture, pass a slug:
  ingest_nostr.py <note> <slug> [tag] [why]
and add the block this tool prints afterwards."""


# --- nak --------------------------------------------------------------------


def nak_version() -> str:
    try:
        proc = subprocess.run(
            [NAK, "--version"], capture_output=True, text=True, timeout=15
        )
        return proc.stdout.strip() or proc.stderr.strip()
    except Exception:
        return "unknown"


def _run_nak(args: list[str], allow_empty: bool = False,
             ) -> subprocess.CompletedProcess | None:
    """One nak invocation, with one retry: relays occasionally refuse a first
    connect and answer the second. A reply query that answers with zero
    events is a successful empty result, not a failure."""
    for _ in range(2):
        try:
            proc = subprocess.run(
                [NAK, *args],
                capture_output=True,
                text=True,
                timeout=NAK_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            continue
        if proc.returncode == 0 and (allow_empty or proc.stdout.strip()):
            return proc
    return None


def _events_from_stdout(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and HEX64.match(str(event.get("id", ""))):
            events.append(event)
    return events


def fetch_event(hexid: str, bech32_ref: str | None) -> tuple[dict, str]:
    """The signed event, or sys.exit(1). Fail closed: a relay returning a
    different id than requested is a fetch failure, not a capture."""
    if bech32_ref:
        proc = _run_nak(["fetch", bech32_ref])
        if proc:
            for event in _events_from_stdout(proc.stdout):
                if event["id"] == hexid:
                    return event, "nak fetch (relay hints / author outbox)"
    note1 = bech32_encode("note", bytes.fromhex(hexid))
    for relay in RELAYS:
        proc = _run_nak(["req", "-i", hexid, relay])
        if proc:
            for event in _events_from_stdout(proc.stdout):
                if event["id"] == hexid:
                    return event, f"nak req -i from {relay}"
    sys.exit(
        f"no relay returned event {hexid} ({note1}); refusing to capture "
        "nothing"
    )


def dedupe_events(events: list[dict], exclude_id: str | None = None
                  ) -> list[dict]:
    """First occurrence of each event id wins; the note itself is excluded."""
    seen: dict[str, dict] = {}
    for event in events:
        if event["id"] == exclude_id:
            continue
        seen.setdefault(event["id"], event)
    return list(seen.values())


def fetch_replies(hexid: str) -> tuple[list[dict], list[str]]:
    """Kind-1 events e-tagging the note, per relay, deduped by event id.

    nak verifies event signatures on receipt by default, so what lands here
    is already authenticated against its declared author.
    """
    collected = []
    answered = []
    for relay in RELAYS:
        proc = _run_nak(["req", "-k", "1", "-t", f"e={hexid}", relay],
                        allow_empty=True)
        if proc is None:
            continue
        answered.append(relay)
        collected.extend(_events_from_stdout(proc.stdout))
    return dedupe_events(collected, exclude_id=hexid), answered


def normalize_replies(replies: list[dict], cap: int = REPLY_CAP,
                      ) -> tuple[list[dict], bool]:
    """Oldest first, ties broken by id, capped. Returns (replies, truncated)."""
    ordered = sorted(replies, key=lambda e: (e.get("created_at", 0), e["id"]))
    return ordered[:cap], len(ordered) > cap


# --- rendering ----------------------------------------------------------------


def iso_utc(unix_ts) -> str:
    try:
        moment = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown-time"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def flatten_event_text(event: dict, replies: list[dict], note1: str,
                       captured: str, provenance: str, version: str,
                       truncated: bool) -> str:
    """Canonical flattened text: header, the content verbatim, then replies
    oldest-first. Reply bodies are single-lined here; replies.json keeps them
    verbatim."""
    author_npub = bech32_encode("npub", bytes.fromhex(event["pubkey"]))
    lines = [
        f"url:      https://njump.me/{note1}",
        f"event id: {event['id']}",
        f"author:   {author_npub}",
        f"posted:   {iso_utc(event.get('created_at'))} "
        f"(created_at {event.get('created_at')})",
        f"captured: {captured} via {version}; {provenance}.",
        "          nostr events are self-authenticating signed artefacts;",
        "          nak verified the signature on receipt.",
        "",
        "--- note text (verbatim) ---",
        "",
        event.get("content", ""),
        "",
    ]
    if replies:
        header = f"--- replies ({len(replies)}) ---"
        if truncated:
            header += f" [truncated at {REPLY_CAP}]"
        lines += [header, ""]
        for reply in replies:
            npub = bech32_encode("npub", bytes.fromhex(reply["pubkey"]))
            short = npub[:16] + "…"
            body = " ".join(str(reply.get("content", "")).splitlines())
            lines.append(f"[{iso_utc(reply.get('created_at'))}] {short}: {body}")
        lines.append("")
    else:
        lines += ["--- replies (0) ---", ""]
    return "\n".join(lines)


def now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ref", help="note1, nevent1 or 64-char hex event id")
    ap.add_argument("slug", nargs="?", help="operator first-capture slug; "
                    "ignored when the note is already registered")
    ap.add_argument("tag", nargs="?", default="community")
    ap.add_argument("why", nargs="?", default=None)
    a = ap.parse_args()

    try:
        hexid = decode_event_ref(a.ref)
    except ValueError as exc:
        sys.exit(str(exc))

    entry = find_registration(hexid)
    if entry is None and not a.slug:
        sys.exit(REGISTRATION_HINT)
    if entry is not None and a.slug and a.slug != entry.get("id"):
        print(f"note: already registered as {entry.get('id')!r}; "
              f"ignoring slug {a.slug!r}")

    bech32_ref = None if HEX64.match(a.ref.strip()) else a.ref.strip().lower()
    version = nak_version()
    event, provenance = fetch_event(hexid, bech32_ref)
    raw_replies, reply_relays = fetch_replies(hexid)
    replies, truncated = normalize_replies(raw_replies)

    note1 = bech32_encode("note", bytes.fromhex(hexid))
    author_npub = bech32_encode("npub", bytes.fromhex(event["pubkey"]))
    if entry is not None:
        archive_id = entry["id"]
    else:
        archive_id = f"{a.slug}-{hexid[:8]}"

    captured = now_z()
    meta = {
        "tool": "ingest_nostr.py",
        "ts": captured,
        "requested": a.ref,
        "event_id": hexid,
        "note1": note1,
        "archive_id": archive_id,
        "provenance": provenance,
        "reply_relays_answered": reply_relays,
        "reply_count": len(replies),
        "replies_truncated": truncated,
        "nak": version,
    }

    try:
        with archive_lock("ingest-nostr"):
            # A capture is a directory. Re-capturing writes a new one beside
            # the old, so nothing is ever overwritten.
            capture_dir = OUT / archive_id / captured
            capture_dir.mkdir(parents=True, exist_ok=True)
            (capture_dir / "event.json").write_text(
                json.dumps(event, indent=2, ensure_ascii=False) + "\n"
            )
            if replies:
                (capture_dir / "replies.json").write_text(
                    json.dumps(replies, indent=2, ensure_ascii=False) + "\n"
                )
            (capture_dir / "event.txt").write_text(
                flatten_event_text(event, replies, note1, captured,
                                   provenance, version, truncated)
            )
            (capture_dir / "meta.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
            )
    except ArchiveLockBusy as exc:
        sys.exit(f"archive writer lock busy: {exc}")

    print(f"capture:    {capture_dir}")
    print(f"note1:      {note1}")
    print(f"replies:    {len(replies)}"
          + (f" (truncated at {REPLY_CAP})" if truncated else ""))
    if entry is None:
        title = " ".join(str(event.get("content", "")).split())[:60]
        why = a.why or "TODO: why this note matters to the incident record"
        print("sources.toml: NOT registered; add this block:\n")
        print(f'''[[nostr_post]]
id = "{archive_id}"
title = "{title}"
url = "https://njump.me/{note1}"
author = "{author_npub}"
org = "nostr"
posted = "{iso_utc(event.get('created_at'))[:10]}"
tag = "{a.tag}"
why = """
{why}
"""''')


if __name__ == "__main__":
    main()
