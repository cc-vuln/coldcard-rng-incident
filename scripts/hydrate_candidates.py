#!/usr/bin/env python3
"""Fetch each discovery candidate's body, so the intake agent never has to.

The intake prompt used to hand the agent a curl command and three `--show`
invocations and ask it to fetch one body per candidate. That made the agent a
network client, which is the one thing an injected agent most wants to be: the
same command that fetches a Reddit thread will happily fetch an attacker's
collector with the interesting parts of this repository in the query string.

The review agent has never had that problem, because render_review_packets.py
puts the evidence in the prompt before the agent starts. This is the same
pattern for intake. The driver runs it as the operator account, before
dropping privilege; the agent receives text and no reason to reach the
network at all.

Every body is untrusted, so every body is fenced with a per-run nonce that the
trusted part of the prompt names. The nonce is not a security boundary by
itself, and it is not claimed as one: it is there so that content which
imitates the prompt's own structure cannot silently become part of it. Any
occurrence of the marker inside a body is neutralised before fencing, which is
the part that actually matters.

One request per candidate, POLITE_DELAY apart, no crawling: the same volume
discipline the discovery scripts keep.

Usage:
    hydrate_candidates.py --nonce <hex> [--max-chars N] < candidate-lines
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv/bin/python")
POLITE_DELAY = 1.5
# The X watcher's own spacing between profile reads, kept for post lookups so
# hydration cannot become the reason an account-health signal trips.
X_DELAY = 1.5
TIMEOUT = 240

# Platform, in the order the intake queue mixes them. Each entry is the
# pattern that recognises a candidate URL and the reader that fetches it.
PLATFORMS = (
    ("stackernews", re.compile(r"stacker\.news/items/(\d+)"),
     "discover_stackernews.py"),
    ("reddit", re.compile(r"reddit\.com/r/[^/]+/comments/([0-9a-z]+)"),
     "discover_reddit.py"),
    ("bitcointalk", re.compile(r"bitcointalk\.org/index\.php\?topic=(\d+)"),
     "discover_bitcointalk.py"),
    ("nostr", re.compile(r"njump\.me/(note1[023456789acdefghjklmnpqrstuvwxyz]+)"),
     "discover_nostr.py"),
)

# X candidates are a separate lane: a different agent, a different prompt, and
# its own driver (scripts/agent-x-intake.sh) rather than the 12-hourly
# community timer. The hydration is the same idea, and the gain is larger,
# because reading a post needs the capture browser's signed-in session.
# Pre-hydrating means the session stays in the driver and the agent never
# reaches the browser at all.
X_PLATFORM = ("x", re.compile(r"x\.com/[^/]+/status/(\d+)"), "x_browser.py")

X_URL = re.compile(r"https://x\.com/[^/]+/status/\d+")

# The browser lane's client. X hydration fails closed if it is unavailable;
# the deprecated API lane and its bearer credential are never a fallback.
try:
    import x_browser
except ImportError:
    x_browser = None

# Own webbridge session name, same convention as discover_x_browser.py: the
# daemon keys one tab per session, and reusing ingest-x.py's session would
# close a tab another lane is reading.
X_SESSION = "coldcard-archive-x-hydrate"

# Counted only when the text read comes back empty: an attached photo, video
# or card is the difference between a media-only post (hydrate it, with the
# absence stated, so the agent can judge it) and a post that is genuinely not
# there (leave it Pending). Until 13 Aug 2026 a media-only post failed the
# read outright, and enough of them accumulated at the head of the queue to
# stall every batch.
MEDIA_JS = r"""
(() => {
  const arts = [...document.querySelectorAll("article")];
  const tweetId = "%s";
  const article = arts.find(a => [...a.querySelectorAll("time")].some(t => {
    const href = t.closest("a")?.getAttribute("href");
    return href && href.includes("/status/" + tweetId);
  })) || arts[0];
  if (!article) return JSON.stringify({media: 0});
  const media = article.querySelectorAll(
    '[data-testid="tweetPhoto"], [data-testid="videoPlayer"],' +
    ' video, [data-testid="card.wrapper"],' +
    ' [data-testid="article-cover-image"], article article').length;
  // X's newer quote layout drops every semantic testid; the only marker
  // left is a bare "Quote" label div. A quote comment may not be readable
  // under this markup, which the hydration note then says.
  const quote = [...article.querySelectorAll("div, span")].some(
    d => d.innerText.trim() === "Quote");
  return JSON.stringify({media: media + (quote ? 1 : 0), quote: quote});
})()
"""

# Read-only extraction, a reduced form of ingest-x.py's EXTRACT_JS: the focal
# article is the one whose timestamp link names the status id, and its
# tweetText is the post. No clicking, no scrolling, no show-more expansion;
# a truncated or absent body fails the read and the candidate stays Pending.
X_EXTRACT_JS = r"""
(() => {
  const tweetId = "%s";
  const arts = [...document.querySelectorAll("article")];
  const el = arts.find(a => [...a.querySelectorAll("time")].some(t => {
    const href = t.closest("a")?.getAttribute("href");
    return href && href.includes("/status/" + tweetId);
  }));
  if (!el) return {found: false};
  const u = el.querySelector("[data-testid=User-Name]");
  const t = [...el.querySelectorAll("time")].find(x => {
    const href = x.closest("a")?.getAttribute("href");
    return href && href.includes("/status/" + tweetId);
  });
  const txt = el.querySelector("[data-testid=tweetText]");
  const truncated = !!el.querySelector(
    "[data-testid=tweet-text-show-more-link]");
  return {found: true,
          user: u ? u.innerText : null,
          time: t ? t.getAttribute("datetime") : null,
          text: txt ? txt.innerText : null,
          truncated: truncated};
})()
"""


def fetch_x_browser(url: str, ident: str) -> tuple[bool, str]:
    """One post read through the capture browser, driver-side.

    An active discovery cooldown stops the read before the browser is
    touched: a sick session is not pushed harder by a sibling lane. After
    navigating, the page is classified with x_browser's own probe; anything
    but "ok" (login-wall, challenge, rate-limit) fails the read, and the
    candidate stays Pending for the next run rather than being assessed
    blind.
    """
    try:
        cooldown = x_browser.read_cooldown()
    except x_browser.XBrowserConfigError as exc:
        return False, str(exc)
    if cooldown is not None:
        return False, (f"X browser lane is cooling down "
                       f"({cooldown['class']} until {cooldown['until']})")
    try:
        x_browser.navigate(url, X_SESSION)
        # X's SPA mounts its content after domcontentloaded; the same settle
        # x_browser.probe_health keeps before reading the page.
        time.sleep(6)
        probe = json.loads(
            x_browser.evaluate(x_browser.HEALTH_JS, X_SESSION)["value"])
        health = x_browser.classify_session(probe)
        if health != "ok":
            return False, f"capture browser session is not healthy ({health})"
        raw = x_browser.evaluate(X_EXTRACT_JS % ident, X_SESSION)
        # The bridge deserializes object return values itself; a string comes
        # back verbatim. Accept both rather than pinning one representation.
        info = raw["value"]
        if isinstance(info, str):
            info = json.loads(info)
        media_count = 0
        if isinstance(info, dict) and info.get("found") and not info.get("text"):
            raw_media = x_browser.evaluate(MEDIA_JS % ident, X_SESSION)
            media_info = raw_media["value"]
            if isinstance(media_info, str):
                media_info = json.loads(media_info)
            media_count = int(media_info.get("media", 0))
    except (x_browser.BridgeError, x_browser.XBrowserConfigError,
            KeyError, ValueError) as exc:
        return False, f"capture browser read failed ({exc})"
    finally:
        try:
            x_browser.close_session(X_SESSION)
        except x_browser.XBrowserConfigError:
            # No readable token means no session was ever opened; closing is
            # best effort, same posture as x_browser.close_session itself.
            pass
    if not isinstance(info, dict) or not info.get("found"):
        return False, "tweet article not found (deleted? wrong URL?)"
    if info.get("truncated"):
        return False, ("post was served truncated; not hydrating a body that "
                       "stops mid-sentence")
    if not info.get("text"):
        if not media_count:
            return False, "tweet article has neither text nor media"
        user = (info.get("user") or "?").replace("\n", " ")
        return True, (f"author: {user}\n"
                      f"posted: {info.get('time') or '?'} "
                      f"(from the page's <time> element)\n"
                      "note: no text body of its own; an attached image, "
                      "video or quoted card is the whole post. Under X's "
                      "newer quote markup a quote comment is not readable "
                      "by this reader\n"
                      "\n--- post text (verbatim) ---\n\n")
    user = (info.get("user") or "?").replace("\n", " ")
    return True, (f"author: {user}\n"
                  f"posted: {info.get('time') or '?'} "
                  f"(from the page's <time> element)\n"
                  f"\n--- post text (verbatim) ---\n\n{info['text']}")


def fetch_x(script: str, ident: str, line: str) -> tuple[bool, str]:
    """X hydration through the capture browser, with no credential fallback."""
    url = X_URL.search(line)
    if x_browser is not None and url:
        return fetch_x_browser(url.group(0), ident)
    return False, "capture-browser X client unavailable; candidate stays pending"


def classify(line: str, include_x: bool) -> tuple[str, str, str] | None:
    platforms = (*PLATFORMS, X_PLATFORM) if include_x else PLATFORMS
    for name, pattern, script in platforms:
        match = pattern.search(line)
        if match:
            return name, match.group(1), script
    return None


def fetch(script: str, ident: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [PY, str(ROOT / "scripts" / script), "--show", ident],
            cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT}s"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, (detail[-1] if detail else f"exit {result.returncode}")
    return True, result.stdout


PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]{2,})\}")


def neutralise(text: str, nonce: str) -> str:
    """Stop a body from closing the fence it is inside, or filling a slot.

    A candidate body is text somebody else wrote, and the marker is printed
    in the prompt above it, so a body can contain the marker. Replacing it
    with a visibly mangled form keeps the fence unambiguous and keeps the
    tampering visible to a reader of the prompt. The same applies to anything
    shaped like one of render_agent_prompt.py's placeholders.
    """
    text = text.replace(nonce, f"{nonce[:4]}-REMOVED-BY-HYDRATION")
    return PLACEHOLDER.sub(lambda m: f"(placeholder {m.group(1)} removed)", text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nonce", required=True,
                        help="per-run fence marker, generated by the driver")
    parser.add_argument("--max-chars", type=int, default=8000,
                        help="per-candidate ceiling on hydrated text")
    parser.add_argument("--delay", type=float, default=POLITE_DELAY)
    parser.add_argument("--include-x", action="store_true",
                        help="also hydrate X permalinks, for the X intake "
                             "lane (scripts/agent-x-intake.sh)")
    args = parser.parse_args()
    if args.include_x:
        args.delay = max(args.delay, X_DELAY)

    lines = [line.rstrip("\n") for line in sys.stdin if line.strip()]
    open_fence = f"<<<UNTRUSTED-{args.nonce}"
    close_fence = f"UNTRUSTED-{args.nonce}>>>"

    fetched = 0
    for index, line in enumerate(lines, 1):
        print(f"### Candidate {index}")
        print(f"Queue line: {neutralise(line, args.nonce)}")
        target = classify(line, args.include_x)
        if target is None:
            reason = ("an X permalink, assessed in its own lane"
                      if X_PLATFORM[1].search(line)
                      else "no recognised platform URL")
            print(f"Body: not hydrated ({reason})")
            print()
            continue
        platform, ident, script = target
        print(f"Platform: {platform} (id {ident})")
        if fetched:
            time.sleep(args.delay)
        if platform == "x":
            ok, payload = fetch_x(script, ident, line)
        else:
            ok, payload = fetch(script, ident)
        fetched += 1
        if not ok:
            print(f"Body: fetch failed ({payload})")
            print("Leave this candidate Pending and report the failure.")
            print()
            continue
        body = neutralise(payload, args.nonce)
        truncated = len(body) > args.max_chars
        if truncated:
            body = body[:args.max_chars]
        print(f"Body: hydrated, {'truncated' if truncated else 'complete'}")
        print(open_fence)
        print(body.rstrip())
        print(close_fence)
        print()

    print(f"Hydrated {fetched} of {len(lines)} candidate(s) with one request "
          f"each.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
