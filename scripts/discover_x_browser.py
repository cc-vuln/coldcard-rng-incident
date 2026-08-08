#!/usr/bin/env python3
"""Discover new incident posts on X through the capture browser.

This is the browser lane that replaced the deprecated official-API lane
(scripts/discover_x.py) when the operator reversed the API-only policy on
8 Aug 2026. It reads the home timeline and the watched [[x_watch]] profiles
through the webbridge daemon, driver-side only, as the operator account, and
queues new permalinks in DISCOVERY.md for the intake agent. A relevant post
is captured later through ingest-x.py, after assessment.

Account-safety boundaries, carried over from the API lane:

- opt-in: live reads require X_BROWSER_DISCOVERY_ENABLED=true exactly
- read-only: navigate and evaluate only. Nothing posts, follows, likes,
  submits a form or clicks a login button; a dead session stops the lane
  for a person, it never triggers an automated sign-in
- bounded: at most 12 timeline scroll passes and 10 profiles per run, with
  fixed spacing between browser actions
- fail closed: a login wall, a challenge and a rate limit are distinct
  session-health classes; any of them stops the run, writes a persistent
  24h cooldown and raises an x-session-health alert instead of pushing
  through. A login wall needs a person to renew the session
- first contact is a baseline: a profile's existing posts are marked seen
  but not queued unless --queue-initial is deliberately supplied
- overflow is an error: if a profile's window fills with new posts without
  reaching the previous checkpoint, nothing is queued and the checkpoint
  does not advance past posts that may not have been seen

Reading a signed-in home timeline carries X's automation-rule suspension
risk (non-API website scripting can lead to permanent account suspension).
The operator accepted that risk in writing on 8 Aug 2026. The fixed spacing,
hard bounds and fail-closed cooldown above are the whole mitigation.

The agent never reaches this lane: the driver reads and hydrates, the agent
receives text. Stdlib only, per repo policy.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import x_browser  # noqa: E402
from discover_x import (  # noqa: E402
    ConfigError, Watch, append_candidates, atomic_json, choose_watches,
    compact_ts, load_registry, now_utc,
)
from discovery_common import match_tier, update_intake  # noqa: E402

WORK = ROOT / ".work"
STATE = WORK / "x-browser-discovery.json"
# Same candidate log the API lane wrote: one record per queued post, with the
# short snippet kept here and only here. DISCOVERY.md lines stay text-free,
# exactly as the API lane queued them.
CANDIDATES = WORK / "x-candidates.jsonl"
LOCK = WORK / "x-browser-discovery.lock"
COOLDOWN = x_browser.COOLDOWN

# Own webbridge session name: the daemon keys one tab per session and this
# lane can run while a live poll or the Reddit lane is mid-read, so sharing
# a session would close a tab someone else is reading.
SESSION = "coldcard-archive-x-discover"
HOME_URL = "https://x.com/home"

STATE_VERSION = 1
SEEN_KEEP = 20_000
SNIPPET_MAX = 200
DEFAULT_TIMELINE_PASSES = 6
HARD_MAX_TIMELINE_PASSES = 12
DEFAULT_MAX_WATCHES = 6
HARD_MAX_WATCHES = 10
# Fixed waits, not adaptive polling: predictability beats cleverness for an
# account the project would rather not lose.
PROBE_SETTLE = 6.0
PROFILE_SETTLE = 5.0
SCROLL_SETTLE = 3.0

PROFILE_CLASSES = ("ok", "protected", "suspended", "unavailable")
SESSION_CLASSES = ("login-wall", "challenge", "rate-limit")

PROFILE_DETAIL = {
    "protected": "posts are protected; only approved followers can read them",
    "suspended": "account is suspended",
    "unavailable": "account does not exist (renamed or deleted?)",
}

# --------------------------------------------------------------------- page JS
#
# x.com markup is obfuscated, so extraction anchors on semantic structure:
# article elements, the <time> inside the status permalink, links matching
# /status/<id>, and the stable data-testid hooks (User-Name, tweetText,
# socialContext, placementTracking). Class names are never used.

EXTRACT_JS = r"""
(() => {
  const posts = [];
  const seen = new Set();
  for (const a of document.querySelectorAll("article")) {
    const t = a.querySelector("time");
    let href = t && t.closest("a") ? t.closest("a").getAttribute("href") : null;
    if (!href || !/\/status\/\d+/.test(href)) {
      href = [...a.querySelectorAll('a[href*="/status/"]')]
        .map(x => x.getAttribute("href"))
        .find(h => h && /\/status\/\d+$/.test(h)) || null;
    }
    if (!href) continue;
    const idm = href.match(/\/status\/(\d+)/);
    if (!idm || seen.has(idm[1])) continue;
    seen.add(idm[1]);
    let handle = null;
    const u = a.querySelector('[data-testid="User-Name"]');
    if (u) {
      const m = (u.innerText || "").match(/@([A-Za-z0-9_]{1,15})/);
      if (m) handle = m[1];
      if (!handle) {
        const hl = [...u.querySelectorAll('a[href^="/"]')]
          .map(x => x.getAttribute("href"))
          .find(h => /^\/[A-Za-z0-9_]{1,15}$/.test(h));
        if (hl) handle = hl.slice(1);
      }
    }
    if (!handle) {
      const hm = href.match(/^\/([A-Za-z0-9_]{1,15})\/status\/\d+/);
      if (hm) handle = hm[1];
    }
    const te = a.querySelector('[data-testid="tweetText"]');
    const snippet = te
      ? (te.innerText || "").replace(/\s+/g, " ").trim().slice(0, 280) : "";
    const sc = a.querySelector('[data-testid="socialContext"]');
    posts.push({
      id: idm[1],
      href: href.split("?")[0],
      handle: handle,
      snippet: snippet,
      time: t ? t.getAttribute("datetime") : null,
      context: sc ? (sc.innerText || "") : "",
      ad: !!a.querySelector('[data-testid="placementTracking"]')
    });
  }
  return JSON.stringify({posts: posts, articles:
    document.querySelectorAll("article").length});
})()
"""

SCROLL_JS = r"""
(() => { window.scrollBy(0, 1600); return "scrolled"; })()
"""

PROFILE_PROBE_JS = r"""
(() => {
  const text = ((document.body && document.body.innerText) || "").slice(0, 5000);
  const noArticle = !document.querySelector("article");
  return JSON.stringify({
    url: location.href,
    loginForm: !!document.querySelector(
      'form[action*="login" i], [data-testid="LoginForm"],' +
      ' input[autocomplete="username"]'),
    wallText: noArticle &&
      /sign in to (x|twitter)|don.?t miss what.?s happening|log in to (x|twitter)/i
        .test(text),
    arkose: !!document.querySelector(
      'iframe[src*="arkose" i], iframe[src*="funcaptcha" i],' +
      ' div[id*="arkose" i], [data-testid*="captcha" i]'),
    challengeText:
      /verify you are (human|a person)|unusual activity|confirm you.?re not a robot|security check|authenticate your account/i
        .test(text),
    rateText:
      /rate limit|too many requests|cannot retrieve posts at this time|please wait a few moments/i
        .test(text),
    suspended: /account suspended/i.test(text),
    protected: /these posts are protected|only approved followers/i.test(text),
    nonexistent: /this account doesn.?t exist/i.test(text),
    articles: document.querySelectorAll("article").length
  });
})()
"""


# ---------------------------------------------------------- pure, tested logic


def classify_profile(probe: dict) -> str:
    """One profile probe to a per-profile outcome.

    Session classes take precedence over profile classes: a login wall or
    challenge masks whatever the profile itself would have said. Protected,
    suspended and unavailable stay distinct per-profile results; an empty
    successful read is a healthy "ok".
    """
    url = str(probe.get("url") or "")
    if (
        probe.get("loginForm")
        or probe.get("wallText")
        or "/login" in url
        or "/i/flow/" in url
    ):
        return "login-wall"
    if probe.get("arkose") or probe.get("challengeText"):
        return "challenge"
    if probe.get("rateText"):
        return "rate-limit"
    if probe.get("suspended"):
        return "suspended"
    if probe.get("protected"):
        return "protected"
    if probe.get("nonexistent"):
        return "unavailable"
    return "ok"


def clean_snippet(value: object) -> str:
    """Whitespace-collapsed, bounded snippet. Full post text is never kept."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:SNIPPET_MAX]


def normalize_post(raw: dict, *, default_actor: str | None = None) -> dict | None:
    """One extracted article to a normalized post, or None if unusable.

    Ads are dropped here: a promoted post has no authorial intent behind its
    placement, and its status id is not an incident artefact.
    """
    if not isinstance(raw, dict) or raw.get("ad"):
        return None
    status_id = str(raw.get("id") or "")
    if not status_id.isdigit():
        return None
    handle = str(raw.get("handle") or "").strip().lstrip("@")
    href = str(raw.get("href") or "")
    if not handle:
        match = re.match(r"/?([A-Za-z0-9_]{1,15})/status/\d+", href)
        if match:
            handle = match.group(1)
    if not handle:
        return None
    context = str(raw.get("context") or "")
    # "repost" is only attributable when the reader knows the reposter: on a
    # watched profile it is the watched actor. On the home timeline the
    # reposter is a third party the DOM context names only loosely, so the
    # post keeps its own author's "post" relation there.
    relation = (
        "repost" if default_actor and "repost" in context.casefold()
        else "post"
    )
    return {
        "id": status_id,
        # The permalink is rebuilt from the author handle so case and query
        # junk from the DOM never reach the queue.
        "url": f"https://x.com/{handle}/status/{status_id}",
        "author": handle,
        "actor": default_actor or handle,
        "relation": relation,
        "snippet": clean_snippet(raw.get("snippet")),
        "createdAt": str(raw.get("time") or ""),
    }


def queue_decision(post: dict, watched: set[str]) -> str | None:
    """Why this timeline post queues, or None.

    A post by a watched handle queues unfiltered: the watch registry exists
    because the post that matters is the one that does not say the keyword.
    Everything else passes discovery_common's two-tier keyword sieve on its
    snippet.
    """
    watched = {handle.casefold() for handle in watched}
    if (
        post["author"].casefold() in watched
        or post["actor"].casefold() in watched
    ):
        return "watch"
    return match_tier(post["snippet"])


def candidate_title(post: dict) -> str:
    # Identical to the API lane's: the queue line stays text-free.
    return (
        f"@{post['actor']} {post['relation']} "
        "(text available during approved intake)"
    )


def candidate_for_intake(
    post: dict,
    found_at: str,
    *,
    source: str,
    tier: str,
    watch: Watch | None = None,
) -> dict:
    created = post["createdAt"] or (
        f"{found_at[:4]}-{found_at[4:6]}-{found_at[6:8]}"
    )
    return {
        "id": post["id"],
        "url": post["url"],
        "platform": "x",
        "actor": post["actor"],
        "org": watch.org if watch else None,
        "relation": post["relation"],
        "createdAt": created,
        "watchWhy": watch.why if watch else None,
        "label": f"X @{post['actor']}",
        "foundAt": found_at,
        "title": candidate_title(post),
        # The snippet lives only in this JSONL record; the DISCOVERY.md line
        # built from it carries no text, same posture as the API lane.
        "snippet": post["snippet"],
        "source": source,
        "tier": tier,
    }


def _after_since(post: dict, since: str | None) -> bool:
    if not since:
        return True
    return (post.get("createdAt") or "")[:10] >= since


def decide_watch(
    posts: list[dict],
    prior: dict,
    watch: Watch,
    seen: set[str],
    registered_ids: set[str],
    *,
    queue_initial: bool,
    overflow: bool,
) -> tuple[list[dict], bool]:
    """Which of a profile's posts queue, and whether this was first contact.

    Baseline runs queue nothing without --queue-initial. Overflow queues
    nothing either: the window never reached the previous checkpoint, so
    posts between it and the window may have been missed, and checkpointing
    past them would lose them silently.
    """
    baseline = not prior.get("last_success")
    queueable: list[dict] = []
    if not overflow and (queue_initial or not baseline):
        for post in posts:
            if post["id"] in seen or post["id"] in registered_ids:
                continue
            if not _after_since(post, watch.since):
                continue
            queueable.append(post)
    return queueable, baseline


def window_overflow(posts: list[dict], prior: dict, *, exhausted: bool) -> bool:
    """Did the window fill with new posts without reaching the checkpoint?"""
    if not prior.get("last_success"):
        return False
    newest = str(prior.get("newest_id") or "")
    if not newest or not posts or not exhausted:
        return False
    return newest not in {post["id"] for post in posts}


def advance_watch(
    prior: dict,
    watch: Watch,
    posts: list[dict],
    stamp: str,
    status: str,
    detail: str = "",
) -> dict:
    """The per-profile checkpoint after one attempt, atomically written later.

    The newest_id checkpoint advances only on a healthy, non-overflow read,
    so an error never moves the lane past posts it may not have seen.
    """
    current = dict(prior)
    current.update({
        "handle": watch.handle,
        "last_attempt": stamp,
        "status": status,
    })
    if detail:
        current["detail"] = detail
    else:
        current.pop("detail", None)
    if status == "ok":
        current["last_success"] = stamp
        fetched = {post["id"] for post in posts}
        if fetched:
            previous = str(prior.get("newest_id") or "")
            pool = fetched | ({previous} if previous else set())
            current["newest_id"] = max(pool, key=int)
        if not prior.get("last_success"):
            current["baseline_count"] = len(fetched)
    return current


# ------------------------------------------------------------------ state file


def load_state(path: Path = STATE) -> dict:
    if not path.exists():
        return {"version": STATE_VERSION, "seen": [], "watches": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"refusing live reads with unreadable state: {exc}"
        ) from exc
    if data.get("version") != STATE_VERSION:
        raise ConfigError(
            f"unsupported X browser discovery state version {data.get('version')!r}"
        )
    if not isinstance(data.get("seen"), list) or not isinstance(
        data.get("watches"), dict
    ):
        raise ConfigError("X browser discovery state has an invalid shape")
    if any(not str(value).isdigit() for value in data["seen"]):
        raise ConfigError("X browser discovery state has a non-numeric status id")
    return data


# ------------------------------------------------------------------ live reads


def extract_posts(session: str) -> list[dict]:
    raw = x_browser.evaluate(EXTRACT_JS, session)
    return json.loads(raw["value"])["posts"]


def read_timeline(session: str, passes: int) -> tuple[list[dict], bool, int]:
    """Scroll the home timeline. Returns (posts, exhausted, passes run).

    Each pass extracts what the DOM currently holds, then scrolls once and
    waits a fixed settle. Two consecutive passes with no new ids stop the
    read early; using the whole budget without that stop is "exhausted".
    """
    collected: dict[str, dict] = {}
    stagnant = 0
    exhausted = True
    run = 0
    for index in range(passes):
        run += 1
        new = 0
        for post in extract_posts(session):
            if post["id"] not in collected:
                collected[post["id"]] = post
                new += 1
        stagnant = stagnant + 1 if not new else 0
        if stagnant >= 2:
            exhausted = False
            break
        if index + 1 < passes:
            x_browser.evaluate(SCROLL_JS, session)
            time.sleep(SCROLL_SETTLE)
    return list(collected.values()), exhausted, run


def read_profile(session: str, watch: Watch) -> tuple[str, list[dict], str]:
    """One watched profile: (outcome, posts, detail).

    One bounded scroll pass: read the settled viewport, scroll once, wait a
    fixed settle, read again. Deeper paging to backfill is a one-off
    enumeration job, not this lane's.
    """
    navigate_url = f"https://x.com/{watch.handle}"
    x_browser.navigate(navigate_url, session)
    time.sleep(PROFILE_SETTLE)
    probe = json.loads(x_browser.evaluate(PROFILE_PROBE_JS, session)["value"])
    outcome = classify_profile(probe)
    if outcome != "ok":
        return outcome, [], PROFILE_DETAIL.get(outcome, "")
    collected: dict[str, dict] = {}
    for post in extract_posts(session):
        collected.setdefault(post["id"], post)
    x_browser.evaluate(SCROLL_JS, session)
    time.sleep(SCROLL_SETTLE)
    for post in extract_posts(session):
        collected.setdefault(post["id"], post)
    return "ok", list(collected.values()), ""


def emit_health_alert(health_class: str) -> None:
    """Raise the operator alert; a failure here never fails the run.

    This is the `|| true` tolerance the driver shell would give: the cooldown
    file is the stop, the alert is the notification, and a broken alert path
    must not turn a clean session stop into a lane failure.
    """
    date = now_utc().strftime("%Y-%m-%d")
    python = ROOT / ".venv" / "bin" / "python"
    summary = {
        "login-wall": (
            "X discovery stopped: the capture browser was served a login "
            "wall, so the signed-in session is dead. A person must renew it; "
            "the lane never logs itself in. Cooldown written."
        ),
        "challenge": (
            "X discovery stopped: the capture browser hit a challenge page. "
            "24h cooldown written; it may clear on its own."
        ),
        "rate-limit": (
            "X discovery stopped: the capture browser hit a rate-limit page. "
            "24h cooldown written; do not push through it."
        ),
    }.get(health_class, f"X discovery stopped: session health {health_class}")
    cmd = [
        str(python if python.exists() else sys.executable),
        str(ROOT / "scripts" / "alert.py"),
        "emit",
        "--kind", "x-session-health",
        "--severity", "urgent",
        "--key", f"x-session-{health_class}-{date}",
        "--summary", summary,
    ]
    try:
        subprocess.run(
            cmd, cwd=ROOT, check=False, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def session_stop(health_class: str, *, clock=now_utc) -> int:
    """Write the cooldown, alert, and report a clean stop. Always exits 0."""
    state = x_browser.write_cooldown(COOLDOWN, health_class, clock=clock)
    emit_health_alert(health_class)
    print(
        f"session health: {health_class}; discovery stopped, cooldown until "
        f"{state['until']} (x-session-health alert raised)",
        file=sys.stderr,
    )
    return 0


# ------------------------------------------------------------------------- CLI


def parse_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--timeline-only", action="store_true",
        help="read only the home timeline, not the watched profiles",
    )
    parser.add_argument(
        "--watches-only", action="store_true",
        help="read only the watched profiles, not the home timeline",
    )
    parser.add_argument(
        "--handle", action="append", default=[],
        help="scan only this registered watch (repeatable); implies "
             "--watches-only",
    )
    parser.add_argument(
        "--timeline-passes", type=int,
        default=parse_int_env(
            "X_BROWSER_DISCOVERY_TIMELINE_PASSES", DEFAULT_TIMELINE_PASSES
        ),
        help=(
            f"timeline scroll-and-wait passes (default "
            f"{DEFAULT_TIMELINE_PASSES}, hard max {HARD_MAX_TIMELINE_PASSES})"
        ),
    )
    parser.add_argument(
        "--max-watches", type=int,
        default=parse_int_env(
            "X_BROWSER_DISCOVERY_MAX_WATCHES", DEFAULT_MAX_WATCHES
        ),
        help=(
            f"profiles this run (default {DEFAULT_MAX_WATCHES}, hard max "
            f"{HARD_MAX_WATCHES})"
        ),
    )
    parser.add_argument(
        "--queue-initial", action="store_true",
        help="queue first-contact history instead of only baselining it",
    )
    parser.add_argument(
        "--no-state", action="store_true",
        help="diagnostic live read; do not queue or update checkpoints",
    )
    parser.add_argument(
        "--clear-cooldown", action="store_true",
        help="clear the local session-health cooldown; performs no request",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list configured watches without touching the browser",
    )
    args = parser.parse_args()

    try:
        if args.clear_cooldown:
            existed = x_browser.clear_cooldown()
            print("cleared the X browser session cooldown; no request made"
                  if existed else "no X browser session cooldown was set")
            return 0

        watches, registered_ids, registered_urls = load_registry()
        if args.list:
            watched = {watch.handle.casefold() for watch in watches}
            for watch in watches:
                print(f"@{watch.handle:<18} since {watch.since or '-':<10} "
                      f"{watch.why}")
            print(f"{len(watches)} active watch(es); the home timeline is "
                  "also read, keyword-sieved, plus any post by a watched "
                  f"handle ({len(watched)} watched)")
            return 0

        if args.timeline_only and args.watches_only:
            raise ConfigError("--timeline-only and --watches-only exclude "
                              "each other")
        if not 1 <= args.timeline_passes <= HARD_MAX_TIMELINE_PASSES:
            raise ConfigError(
                f"--timeline-passes must be 1..{HARD_MAX_TIMELINE_PASSES}"
            )
        if not 1 <= args.max_watches <= HARD_MAX_WATCHES:
            raise ConfigError(f"--max-watches must be 1..{HARD_MAX_WATCHES}")

        # An active cooldown exits before the browser is touched. A malformed
        # one fails closed as a config error rather than being assumed away.
        cooldown = x_browser.read_cooldown()
        if cooldown:
            print(
                f"X browser session cooldown is active until "
                f"{cooldown['until']} ({cooldown['class']}); skipping",
                file=sys.stderr,
            )
            return 0

        # The kill switch is exact on purpose: the API lane accepted several
        # truthy spellings, and this lane's risk budget is tighter.
        if os.environ.get("X_BROWSER_DISCOVERY_ENABLED", "") != "true":
            raise ConfigError(
                "live X browser discovery is disabled; set "
                "X_BROWSER_DISCOVERY_ENABLED=true after reading "
                "docs/design/discovery-and-x-watch.md"
            )

        WORK.mkdir(exist_ok=True)
        lock_handle = LOCK.open("a+")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another X browser discovery run holds the lock; skipping",
                  file=sys.stderr)
            return 0

        # The token read doubles as the operator-account check: only the
        # operator can read it, so an agent context fails here, loudly.
        x_browser.bridge_token()

        # Session health first, before any timeline or profile work. A non-ok
        # class stops the run visibly but is not a unit failure.
        try:
            health = x_browser.probe_health(SESSION, HOME_URL)
        except x_browser.BridgeError as exc:
            print(f"discover-x-browser: {exc}", file=sys.stderr)
            return 1
        print(f"session health probe: {health}")
        if health != "ok":
            x_browser.close_session(SESSION)
            return session_stop(health)

        state = load_state()
        seen = {str(value) for value in state.get("seen", [])} - registered_ids
        watch_state = state.setdefault("watches", {})
        watched_handles = {watch.handle.casefold() for watch in watches}

        stamp = compact_ts(now_utc())
        candidates: list[dict] = []
        fetched_ids: set[str] = set()
        failures = 0

        def queue_timeline() -> None:
            posts, exhausted, run = read_timeline(SESSION, args.timeline_passes)
            normalized = [
                post for post in
                (normalize_post(raw) for raw in posts)
                if post is not None
            ]
            fetched_ids.update(post["id"] for post in normalized)
            queued = 0
            for post in normalized:
                tier = queue_decision(post, watched_handles)
                if not tier:
                    continue
                if post["id"] in seen or post["id"] in registered_ids:
                    continue
                if post["url"] in registered_urls:
                    continue
                candidates.append(candidate_for_intake(
                    post, stamp, source="home-timeline", tier=tier,
                ))
                queued += 1
            state["timeline"] = {
                "last_attempt": stamp,
                "last_success": stamp,
                "passes": run,
                "posts": len(normalized),
            }
            print(f"home timeline: {len(normalized)} post(s) in {run} "
                  f"pass(es){' (budget exhausted)' if exhausted else ''}; "
                  f"{queued} new candidate(s)")

        def queue_watches() -> int:
            nonlocal failures
            selected = choose_watches(
                watches, state, args.handle, args.max_watches
            )
            if not selected:
                raise ConfigError("no active X watches selected")
            for index, watch in enumerate(selected):
                key = watch.handle.casefold()
                prior = watch_state.get(key, {})
                try:
                    outcome, raw_posts, detail = read_profile(SESSION, watch)
                except x_browser.BridgeError as exc:
                    print(f"@{watch.handle}: bridge error: {exc}",
                          file=sys.stderr)
                    failures += 1
                    watch_state[key] = advance_watch(
                        prior, watch, [], stamp, "bridge-error", str(exc)[:200]
                    )
                    continue
                if outcome in SESSION_CLASSES:
                    # The session died mid-run. Cool down and stop; what was
                    # already found is still persisted below.
                    watch_state[key] = advance_watch(
                        prior, watch, [], stamp, outcome,
                        "session failure during profile read",
                    )
                    failures += 1
                    return session_stop(outcome)
                posts = [
                    post for post in (
                        normalize_post(raw, default_actor=watch.handle)
                        for raw in raw_posts
                    )
                    if post is not None
                ]
                fetched_ids.update(post["id"] for post in posts)
                if outcome != "ok":
                    failures += 1
                    watch_state[key] = advance_watch(
                        prior, watch, [], stamp, outcome, detail
                    )
                    print(f"@{watch.handle}: {outcome}: {detail}",
                          file=sys.stderr)
                    continue
                overflow = window_overflow(
                    posts, prior, exhausted=True
                )
                queueable, baseline = decide_watch(
                    posts, prior, watch, seen, registered_ids,
                    queue_initial=args.queue_initial, overflow=overflow,
                )
                status = "window-exceeded" if overflow else "ok"
                detail = (
                    "the window filled with new posts without reaching the "
                    "previous checkpoint; nothing queued, checkpoint held"
                ) if overflow else ""
                watch_state[key] = advance_watch(
                    prior, watch, [] if overflow else posts, stamp,
                    status, detail,
                )
                if overflow:
                    failures += 1
                    print(f"@{watch.handle}: window-exceeded: {detail}",
                          file=sys.stderr)
                    continue
                for post in queueable:
                    candidates.append(candidate_for_intake(
                        post, stamp, source=f"watch:{watch.handle}",
                        tier="watch", watch=watch,
                    ))
                action = (
                    "baselined" if baseline and not args.queue_initial
                    else "scanned"
                )
                print(f"@{watch.handle}: {action} {len(posts)} post(s); "
                      f"{len(queueable)} new candidate(s)")
            return 0

        stop_code = 0
        if not args.watches_only and not args.handle:
            queue_timeline()
        if not args.timeline_only:
            stop_code = queue_watches()

        if not args.no_state:
            if candidates:
                append_candidates(candidates, CANDIDATES)
                update_intake(candidates, registered_urls)
            # The checkpoint advances only after the intake queue accepted
            # the candidates, so a crash before this point safely re-queues.
            seen.update(fetched_ids)
            state["seen"] = sorted(seen, key=int)[-SEEN_KEEP:]
            atomic_json(STATE, state)
            if candidates:
                print(f"appended {len(candidates)} candidate(s) to "
                      f"{CANDIDATES.relative_to(ROOT)} and DISCOVERY.md")
        else:
            print("--no-state: nothing queued, no checkpoint advanced")
            for candidate in candidates:
                print(f"  {candidate['createdAt'][:16]} {candidate['url']} "
                      f"{candidate['title']} [{candidate['tier']}]")

        x_browser.close_session(SESSION)
        outcome = "incomplete" if failures else "complete"
        print(f"X browser discovery {outcome}: "
              f"{len(candidates)} candidate(s), {failures} failure(s)")
        return stop_code
    except (ConfigError, x_browser.XBrowserConfigError) as exc:
        print(f"discover-x-browser: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
