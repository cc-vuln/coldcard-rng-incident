#!/usr/bin/env python3
"""Shared plumbing for the discovery lanes. Imported, never run.

Five scripts find candidate threads and queue them for the intake agent:
discover_stackernews.py, discover_reddit.py, discover_bitcointalk.py,
discover_nostr.py and discover_x.py. They disagree about almost everything
worth disagreeing about (the transport, the request budget, the shape of a
listing) and agree completely about what happens to a candidate once found:
it goes in the lane's JSONL log, its id goes in the lane's seen set, and its
line goes in DISCOVERY.md under the intake lock.

That shared half used to live in discover_stackernews.py, because Stacker
News was the first lane and the second one imported from it rather than from
anywhere neutral. Four lanes later the Stacker News module held the intake
header describing nostr, and an intake_line() branching on X and nostr
candidates, none of which it has any business knowing about. The plumbing
lives here now and every lane imports it as a peer.

Two rules survive the move and are the reason this file is worth reading:

- DISCOVERY.md is shared with the intake agent, which rewrites it while a
  discovery run may be appending. Every write goes through update_intake(),
  which takes the agent's own lock and replaces the file atomically. Do not
  write DISCOVERY.md any other way
- Assessed entries are the agent's record, or a human's. update_intake()
  keeps them verbatim and only ever prunes Pending. A lane that loses its
  seen state should re-queue candidates, never re-open assessed ones

Zero dependencies: stdlib only, Python 3.11+ for tomllib.
"""

import fcntl
import json
import os
import re
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.toml"
WORK = ROOT / ".work"
INTAKE = ROOT / "DISCOVERY.md"
INTAKE_LOCK = WORK / "agent-discovery-intake" / "intake.lock"

# The identifying project user agent, never a bare library default. Lanes that
# talk to a site directly use this; the X lane identifies itself to an
# authenticated API and has its own.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "(+https://github.com/cc-vuln/coldcard-rng-incident; historical preservation)"
)
TIMEOUT = 45
POLITE_DELAY = 1.5
SEEN_KEEP = 5000
INTAKE_LOCK_TIMEOUT = 60.0

# Incident vocabulary for title matching. Oblique titles ("Dear podcasters &
# influencers") will not match; every lane's --all exists for a full sweep
# when the feed is busy. Title-only by design in the listing lanes: fetching
# every item body would multiply request volume for no discovery gain.
KEYWORDS = re.compile(r"|".join([
    r"cold\s?card", r"coinkite", r"\bnvk\b", r"\brng\b", r"entropy",
    r"seed phrase", r"dice", r"drain", r"sweep", r"stolen", r"theft", r"hack",
    r"hardware wallet", r"passphrase", r"slipstream", r"bitkey", r"opensats",
    r"\bbtcrecover\b", r"self.?custody", r"phishing", r"1596|1,?596|1367|1,?367",
]), re.IGNORECASE)

INTAKE_HEADER = """\
# Discovery intake

Candidates found by the Stacker News, Reddit, BitcoinTalk, nostr and manual X
discovery commands. The community-thread lanes run every 12 hours through
`discover-community.timer`; X and nostr discovery remain manual while their
API, relay and policy gates are proved. That timer runs neither of them, nor
does it send queued X links to the general community agent. An operator may
invoke the separate X-only triage with `--include-x` during probation; nostr
candidates (njump.me links) go to the standard intake agent, which registers
them as [[nostr_post]] blocks and first-captures with `just ingest-nostr`.
Direct `just ingest-x` capture of a manually supplied permalink does not use
this queue.
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
    """One DISCOVERY.md Pending line for a candidate.

    The community lanes report a comment count; the X and nostr lanes have
    nothing comparable to report, so they say what they do have.
    """
    if c.get("platform") == "x":
        return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
                f"({c['label']})")
    if c.get("platform") == "nostr":
        relays = "relay" if c["relayCount"] == 1 else "relays"
        return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
                f"by {c['author']} ({c['relayCount']} known {relays}) "
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


def load_state(path: Path) -> dict:
    """A lane's seen state, or an empty one on first run."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"seen": []}


def registered_urls(canonical: Callable[[str], str | None],
                    table: str = "source") -> set[str]:
    """Canonical URLs this lane has already registered in sources.toml.

    `canonical` maps a registered URL to the lane's canonical form of it, or
    to None if the entry belongs to another lane. The lanes differ in how
    much normalising that takes: a Reddit permalink is registered in the form
    the listing reports, while a BitcoinTalk topic can be registered as a
    link to one post inside it and has to be rebuilt from its topic id.

    `table` is the sources.toml array to read. Every lane but nostr registers
    into [[source]]; nostr posts are their own array and are never polled.
    """
    data = tomllib.loads(SOURCES.read_text(encoding="utf-8"))
    urls = set()
    for entry in data.get(table, []):
        url = entry.get("url", "")
        if not isinstance(url, str) or not url:
            continue
        if (found := canonical(url)) is not None:
            urls.add(found)
    return urls


def persist_run(*, state: dict, seen: set, candidates: list[dict],
                known: set[str], state_path: Path, candidates_path: Path,
                save: bool = True) -> None:
    """Commit a lane's run: candidate log, seen checkpoint, intake queue.

    The order matters on a crash. The JSONL log is appended first because it
    is the lane's own raw record and a duplicate line there is harmless; the
    seen checkpoint advances next, so a crash before it re-queues rather than
    drops; DISCOVERY.md is reconciled last, under the intake lock.

    `save` is each lane's --no-state: look at the feed without spending the
    checkpoint, so the next real run still reports what it found.
    """
    WORK.mkdir(exist_ok=True)
    if not save:
        return
    if candidates:
        with candidates_path.open("a", encoding="utf-8") as fh:
            for c in candidates:
                fh.write(json.dumps(c, sort_keys=True) + "\n")
    state["seen"] = sorted(seen)[-SEEN_KEEP:]
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    update_intake(candidates, known)


def report_queued(candidates: list[dict], candidates_path: Path,
                  save: bool = True) -> None:
    """Tell the operator where a run's candidates went, if anywhere."""
    if candidates and save:
        print(f"appended to {candidates_path.relative_to(ROOT)} and "
              f"DISCOVERY.md; the intake agent assesses pending entries")
