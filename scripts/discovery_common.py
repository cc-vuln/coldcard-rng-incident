#!/usr/bin/env python3
"""Shared plumbing for the discovery lanes. Imported, never run.

Five scripts find candidate threads and queue them for the intake agent:
discover_stackernews.py, discover_reddit.py, discover_bitcointalk.py,
discover_nostr.py and discover_x.py. They disagree about almost everything
worth disagreeing about (the transport, the request budget, the shape of a
listing) and agree completely about what happens to a candidate once found:
it goes in the lane's JSONL log, its id goes in the lane's seen set, and its
complete observation goes into the structured discovery store.

That shared half used to live in discover_stackernews.py, because Stacker
News was the first lane and the second one imported from it rather than from
anywhere neutral. Four lanes later the Stacker News module held the intake
header describing nostr, and an intake_line() branching on X and nostr
candidates, none of which it has any business knowing about. The plumbing
lives here now and every lane imports it as a peer.

Two rules are the reason this file is worth reading:

- every store write holds `.work/locks/discovery.lock`; agents receive generated
  packets and submit JSONL decisions, but cannot write canonical discovery
  files or their generated Markdown views
- a repeated observation updates discoverable candidate history without
  reopening an assessed verdict. A lane that loses its seen checkpoint may
  replay safely

Zero dependencies: stdlib only, Python 3.11+ for tomllib.
"""

import json
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path
from typing import Callable

from discovery_store import DiscoveryStore

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.toml"
WORK = ROOT / ".work"

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


def intake_line(c: dict) -> str:
    """One compact human rendering for a structured candidate.

    The community lanes report a comment count; the X and nostr lanes have
    nothing comparable to report, so they say what they do have. A tier that
    is not "strong" is named at the end of the line, because it is the reason
    a deferred entry is where it is.
    """
    if c.get("platform") == "x":
        return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
                f"({c['label']})")
    if c.get("platform") == "nostr" and isinstance(c.get("relayCount"), int):
        relays = "relay" if c["relayCount"] == 1 else "relays"
        return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
                f"by {c['author']} ({c['relayCount']} known {relays}) "
                f"({c['label']})")
    tier = c.get("tier")
    suffix = f" [{tier}]" if tier and tier != "strong" else ""
    return (f"- {c['createdAt'][:10]} [{c['title']}]({c['url']}) "
            f"by {c['author'] or '?'}, {c['ncomments']} comments "
            f"({c['label']}){suffix}")


def atomic_text(path: Path, text: str) -> None:
    """Replace an ignored lane checkpoint only after its body is durable."""
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            # mkstemp makes the replacement 0600. Carry the mode of an
            # existing ignored checkpoint or operator drop file so a routine
            # atomic update does not unexpectedly change who may inspect it.
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def update_intake(candidates: list[dict],
                  known_urls: set[str] | dict[str, str]) -> None:
    """Reconcile observations into the canonical candidate store.

    Deferral is reversible: a later observation with more discussion promotes
    the candidate. Settled candidates retain their verdict while still gaining
    the new observation. Registered URLs receive an explicit terminal event
    instead of disappearing from the queue without an explanation.
    """
    observations = []
    for candidate in candidates:
        observation = dict(candidate)
        observation["display_line"] = intake_line(candidate)
        observation["state"] = ("deferred" if should_defer(candidate)
                                else "pending")
        observations.append(observation)
    DiscoveryStore(ROOT).reconcile_observations(
        observations, known_urls=known_urls)


def queue_mark(c: dict) -> str:
    """Where this candidate would wait, for a lane's own run output. Empty for
    the ordinary case: a named title going straight to the agent."""
    if should_defer(c):
        return "deferred"
    tier = c.get("tier")
    return tier if tier and tier != "strong" else ""


def deferred_urls() -> set[str]:
    """URLs currently held in Deferred, which a lane re-reports rather than
    skipping as seen. This projection read is a hint; the worst a concurrent
    store update can cost is one run's promotion.
    """
    return {candidate["url"] for candidate in
            DiscoveryStore(ROOT).list_candidates(state="deferred")}


def load_state(path: Path) -> dict:
    """A lane's seen state, or an empty one on first run."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"seen": []}


def registered_urls(canonical: Callable[[str], str | None],
                    table: str = "source") -> dict[str, str]:
    """Map canonical registered URLs to their exact source ids.

    `canonical` maps a registered URL to the lane's canonical form of it, or
    to None if the entry belongs to another lane. The lanes differ in how
    much normalising that takes: a Reddit permalink is registered in the form
    the listing reports, while a BitcoinTalk topic can be registered as a
    link to one post inside it and has to be rebuilt from its topic id.

    `table` is the sources.toml array to read. Every lane but nostr registers
    into [[source]]; nostr posts are their own array and are never polled.
    """
    # Keep the queue/atomic-write helpers importable without the optional
    # sharded registry adapter. Tests and callers that replace SOURCES with a
    # small explicit fixture also retain direct-file semantics (those fixtures
    # predate the registry's [meta] validation contract).
    if SOURCES == ROOT / "sources.toml":
        import registry_store
        data = registry_store.load(ROOT)
    else:
        with SOURCES.open("rb") as handle:
            data = tomllib.load(handle)
    urls: dict[str, str] = {}
    for entry in data.get(table, []):
        url, source_id = entry.get("url", ""), entry.get("id")
        if not isinstance(url, str) or not url \
                or not isinstance(source_id, str) or not source_id:
            continue
        if (found := canonical(url)) is not None:
            urls.setdefault(found, source_id)
    return urls


def persist_run(*, state: dict, seen: set, candidates: list[dict],
                known: set[str] | dict[str, str], state_path: Path,
                candidates_path: Path,
                save: bool = True) -> None:
    """Commit a lane's run: structured store, raw log, seen checkpoint.

    The order matters on a crash. The store is reconciled first, under its
    lock, because it is the durable work queue. The ignored JSONL log follows; a
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
            # The ignored lane log is diagnostic rather than canonical, but
            # it must become durable before the seen checkpoint advances. A
            # replay can add a harmless duplicate log row; a checkpoint with
            # no corresponding raw observation would be silent loss.
            fh.flush()
            os.fsync(fh.fileno())
    state["seen"] = sorted(seen)[-SEEN_KEEP:]
    atomic_text(state_path, json.dumps(state) + "\n")


def report_queued(candidates: list[dict], candidates_path: Path,
                  save: bool = True) -> None:
    """Tell the operator where a run's candidates went, if anywhere."""
    if candidates and save:
        print(f"appended to {candidates_path.relative_to(ROOT)} and "
              f"the discovery store; the intake agent assesses pending entries")
