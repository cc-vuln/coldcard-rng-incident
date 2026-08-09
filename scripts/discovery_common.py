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
import stat
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

# Incident vocabulary, in two tiers. Oblique titles ("Dear podcasters &
# influencers") will not match; every lane's --all exists for a full sweep
# when the feed is busy. Title-only by design in the listing lanes: fetching
# every item body would multiply request volume for no discovery gain.
#
# Tier 1 names this incident and little else. Tier 2 is the bitcoin-security
# vocabulary the incident borrows: on its own it describes the whole subject
# area rather than this event. The split is not a filter. Measured against the
# 425 assessed candidates on 7 Aug 2026, refusing tier-2-only titles would have
# cut 58 dismissals and lost 33 registered sources, which is the wrong trade
# for a preservation record. What the tier is for is deferral: see
# should_defer(), where it composes with the comment count.
STRONG = re.compile(r"|".join([
    r"cold\s?card", r"coinkite", r"\bnvk\b", r"\brng\b", r"slipstream",
    r"\bbtcrecover\b", r"1596|1,?596|1367|1,?367",
]), re.IGNORECASE)

TOPICAL = re.compile(r"|".join([
    r"entropy", r"seed phrase", r"dice", r"drain", r"sweep", r"stolen",
    r"theft", r"hack", r"hardware wallet", r"passphrase", r"bitkey",
    r"opensats", r"self.?custody", r"phishing",
]), re.IGNORECASE)

# The union, and the question every lane still asks first: does this mention
# the subject at all? Kept as one pattern so a lane's sieve call is unchanged.
KEYWORDS = re.compile(f"{STRONG.pattern}|{TOPICAL.pattern}", re.IGNORECASE)

# A candidate whose title only borrows the vocabulary, and which nobody has
# replied to, is the lowest-yield thing the intake agent is asked to read: of
# the 24 such candidates in the assessed corpus, 0 were registered. They are
# deferred rather than dropped, because a thread with no replies today may
# have forty tomorrow, and because this project would rather re-read a dull
# thread than lose a first-hand one.
DEFER_MAX_COMMENTS = 2


def match_tier(title: str, haystack: str | None = None) -> str | None:
    """Which tier queued this candidate, and on what text.

    "strong"  tier-1 vocabulary in the title
    "topical" tier-2 only, in the title
    "body"    neither in the title, but the lane matched on the body it also
              searched (Reddit sieves title and selftext together)
    None      no match anywhere
    """
    if STRONG.search(title):
        return "strong"
    if TOPICAL.search(title):
        return "topical"
    if haystack and KEYWORDS.search(haystack):
        return "body"
    return None


def should_defer(candidate: dict) -> bool:
    """Hold this candidate back from the agent for now.

    Only where both weak signals agree: the title never names the incident,
    and the thread has drawn almost no discussion. A candidate with no comment
    count to read (X, nostr) is never deferred on this rule.
    """
    if candidate.get("tier") == "strong":
        return False
    n = candidate.get("ncomments")
    if not isinstance(n, int):
        return False
    return n <= DEFER_MAX_COMMENTS

INTAKE_HEADER = """\
# Discovery intake

Candidates found by the Stacker News, Reddit, BitcoinTalk, nostr and manual X
discovery commands. The community-thread lanes run every 12 hours through
`discover-community.timer`; nostr discovery remains manual while its relay
gate is proved. That timer runs neither nostr nor X discovery, nor does it
send queued X links to the general community agent. Since 8 Aug 2026, queued
X candidates are assessed by the registering xintake lane
(`scripts/agent-x-intake.sh`, the same REVIEW_AGENT_BIN pattern): a relevant
post is registered as an [[x_post]] block and ingested driver-side
afterwards. The read-only X triage prompt and the `--include-x` admission
flag are retired. nostr
candidates (njump.me links) go to the standard intake agent, which registers
them as [[nostr_post]] blocks and first-captures with `just ingest-nostr`.
Direct `just ingest-x` capture of a manually supplied permalink does not use
this queue.
Eligible pending entries are assessed by the intake agent
(`scripts/agent-discovery-intake.sh`): a relevant community thread is
registered and first-captured. Assessed
entries move below with the verdict. To dismiss a candidate by hand, move its
line to Assessed with a one-line reason.
Candidates whose title never names the incident and which have drawn almost
no discussion wait under `## Deferred` instead. That is a queue and not a
verdict: the lane that found one keeps its comment count current and promotes
it to Pending by itself once the thread grows. Only the lanes write there.
Older verdicts rotate to
discovery/assessed-YYYY-MM.md (`just rotate-discovery`): a line moves
verbatim, and a verdict without a UTC stamp never rotates.

## Pending

## Assessed

## Deferred
"""

DEFERRED_NOTE = (
    "Queued, but held back from the agent: the title never names the incident "
    "and the thread has drawn almost no discussion. Nothing here is dismissed. "
    "Each line carries the last comment count its lane observed, and a lane "
    "promotes an entry to Pending by itself once the thread grows past "
    f"{DEFER_MAX_COMMENTS} comments. To assess one now, move its line to "
    "Pending."
)

LINE_URL_RE = re.compile(r"\((https?://[^)]+)\)")

PENDING_H = "## Pending"
ASSESSED_H = "## Assessed"
DEFERRED_H = "## Deferred"


def intake_line(c: dict) -> str:
    """One DISCOVERY.md queue line for a candidate.

    The community lanes report a comment count; the X and nostr lanes have
    nothing comparable to report, so they say what they do have. A tier that
    is not "strong" is named at the end of the line, because it is the reason
    a deferred entry is where it is.
    """
    if c.get("platform") == "x":
        return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
                f"({c['label']})")
    if c.get("platform") == "nostr":
        relays = "relay" if c["relayCount"] == 1 else "relays"
        return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
                f"by {c['author']} ({c['relayCount']} known {relays}) "
                f"({c['label']})")
    tier = c.get("tier")
    suffix = f" [{tier}]" if tier and tier != "strong" else ""
    return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
            f"by {c['author'] or '?'}, {c['ncomments']} comments "
            f"({c['label']}){suffix}")


def split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """The file as (heading, lines) pairs in file order, preamble first.

    Every reader of this file used to locate a section by what surrounds it:
    the intake driver still takes Pending to be everything between its heading
    and Assessed. That is why a section is addressed by its own heading here
    and why Deferred is written after Assessed rather than between the two.
    """
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((heading, lines))
            heading, lines = line, []
        else:
            lines.append(line)
    sections.append((heading, lines))
    return sections


def join_sections(sections: list[tuple[str | None, list[str]]]) -> str:
    """Rebuild the file, one blank line under each heading."""
    parts = []
    for heading, lines in sections:
        block = "\n".join(lines).strip("\n")
        if heading is None:
            if block:
                parts.append(block)
            continue
        parts.append(f"{heading}\n\n{block}" if block else heading)
    return "\n\n".join(parts) + "\n"


def section(sections: list, heading: str) -> list[str]:
    """The entry lines under one heading, or an empty list if absent."""
    for h, lines in sections:
        if h == heading:
            return [l for l in lines if l.startswith("- ")]
    return []


def atomic_text(path: Path, text: str) -> None:
    """Replace the shared intake file only after its new body is durable."""
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            # mkstemp makes the replacement 0600. Carry the mode of the file
            # being replaced, or every queue rewrite strips the group access
            # the intake agent needs (observed 7 Aug 2026: a lane rewrite
            # left DISCOVERY.md unreadable to the agent account and its run
            # could not record a single verdict).
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
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


def _line_url(line: str) -> str | None:
    m = LINE_URL_RE.search(line)
    return m.group(1) if m else None


def update_intake(candidates: list[dict], known_urls: set[str]) -> None:
    """Reconcile DISCOVERY.md: prune registered threads, route new candidates
    to Pending or Deferred, and promote a deferred thread that has since drawn
    discussion. Assessed entries are the intake agent's (or a human's) record
    and are kept verbatim.

    Deferral is reversible by design. A deferred candidate stays in the file
    with its comment count on show, and the lane that found it re-reports it
    for as long as it is in the listing window, so the count on the line is
    the last one observed rather than the one it was queued with.
    """
    INTAKE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with INTAKE_LOCK.open("a+") as lock_handle:
        acquire_intake_lock(lock_handle)
        if INTAKE.exists():
            text = INTAKE.read_text(encoding="utf-8")
        else:
            text = INTAKE_HEADER
        sections = split_sections(text)

        pending = section(sections, PENDING_H)
        deferred = section(sections, DEFERRED_H)
        assessed_urls = {u for l in section(sections, ASSESSED_H)
                         if (u := _line_url(l))}
        # Anything already written down anywhere, including the human-owned
        # link-review section, is not a new candidate.
        present = {u for h, lines in sections for l in lines
                   if l.startswith("- ") and (u := _line_url(l))}

        pending = [l for l in pending if _line_url(l) not in known_urls]
        deferred = [l for l in deferred if _line_url(l) not in known_urls]
        deferred_urls = {u for l in deferred if (u := _line_url(l))}

        for c in candidates:
            url, line = c["url"], intake_line(c)
            if url in assessed_urls:
                continue
            if url in deferred_urls:
                # Re-reported: refresh the line, and promote it if the thread
                # has grown past the bar since it was set aside.
                deferred = [l for l in deferred if _line_url(l) != url]
                (deferred if should_defer(c) else pending).append(line)
                continue
            if url in present:
                continue
            (deferred if should_defer(c) else pending).append(line)

        rebuilt = []
        for heading, lines in sections:
            if heading == PENDING_H:
                rebuilt.append((heading, pending))
            elif heading == DEFERRED_H:
                rebuilt.append(
                    (heading, ([DEFERRED_NOTE] + deferred) if deferred else []))
            else:
                rebuilt.append((heading, lines))
        if deferred and not any(h == DEFERRED_H for h, _ in rebuilt):
            rebuilt.append((DEFERRED_H, [DEFERRED_NOTE] + deferred))

        out = join_sections(rebuilt)
        if not INTAKE.exists() or out != text:
            atomic_text(INTAKE, out)


def queue_mark(c: dict) -> str:
    """Where this candidate would wait, for a lane's own run output. Empty for
    the ordinary case: a named title going straight to the agent."""
    if should_defer(c):
        return "deferred"
    tier = c.get("tier")
    return tier if tier and tier != "strong" else ""


def deferred_urls() -> set[str]:
    """URLs currently held in Deferred, which a lane re-reports rather than
    skipping as seen. Read without the intake lock: it is a hint, and the
    worst a concurrent rewrite can cost is one run's promotion.
    """
    if not INTAKE.exists():
        return set()
    sections = split_sections(INTAKE.read_text(encoding="utf-8"))
    return {u for l in section(sections, DEFERRED_H) if (u := _line_url(l))}


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
    """Commit a lane's run: intake queue, candidate log, seen checkpoint.

    The order matters on a crash. DISCOVERY.md is reconciled first, under its
    lock, because it is the durable work queue. The JSONL log follows; a
    duplicate line there is harmless. The seen checkpoint advances last and
    atomically, so any earlier failure causes a replay rather than silently
    losing a candidate.

    `save` is each lane's --no-state: look at the feed without spending the
    checkpoint, so the next real run still reports what it found.
    """
    WORK.mkdir(exist_ok=True)
    if not save:
        return
    update_intake(candidates, known)
    if candidates:
        with candidates_path.open("a", encoding="utf-8") as fh:
            for c in candidates:
                fh.write(json.dumps(c, sort_keys=True) + "\n")
    state["seen"] = sorted(seen)[-SEEN_KEEP:]
    atomic_text(state_path, json.dumps(state) + "\n")


def report_queued(candidates: list[dict], candidates_path: Path,
                  save: bool = True) -> None:
    """Tell the operator where a run's candidates went, if anywhere."""
    if candidates and save:
        print(f"appended to {candidates_path.relative_to(ROOT)} and "
              f"DISCOVERY.md; the intake agent assesses pending entries")
