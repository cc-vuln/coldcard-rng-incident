#!/usr/bin/env python3
"""Capture an X conversation rather than a single post.

The archive holds a Reddit, Stacker News or BitcoinTalk thread as a whole
conversation and an X post as one post. This module is the X half of that
model: the focal post, any ancestor it replies to, the author's own
continuation posts, and replies to a declared cap.

Design record: docs/design/x-thread-capture.md

Three facts about X shape everything here, all established by probe on
6 Aug 2026 against a live permalink:

  1. The conversation is present in the DOM on first paint, but X virtualises
     the list. Scrolling evicts the focal post. So a capture accumulates as it
     scrolls and never reads the DOM once at the end, and a post's screenshot
     is taken while that post is still on screen.
  2. Replies render in X's ranked order, not chronologically. Status ids are
     snowflakes and sort chronologically, so the canonical text sorts on the
     id and ranking churn becomes invisible to the differ. This is the same
     fix flatten_reddit_thread applies to comment ids.
  3. document.body.scrollHeight grows on lazy load and shrinks as virtualised
     rows are replaced by estimates, so it cannot signal completion. The end
     of a thread is "no new status id for N consecutive rounds".

Two fields cannot be read the obvious way. An article can carry more than one
<time>, only one of which belongs to that post, and a rendered name block can
disagree with the post's own permalink. Author identity and post time are both
taken from the status link, never from the name block's text.

Stdlib only, per repo policy: the browser is reached over HTTP through the
capture browser daemon (capture-browser/README.md). Every browser call is a
read: navigate, evaluate, screenshot. Nothing is posted, followed or liked.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.request

# How far a capture is willing to go, and what it declares when it stops.
# These are the "declared cap" the canonical text talks about: a thread
# capture never claims completeness X does not offer.
#
# The caps are safety valves, not operating points, and the difference is not
# cosmetic. Measured 6 Aug 2026 on the clay_garrett attribution thread: at a
# 120 cap two captures two minutes apart differed by +113 -99 lines, because
# stopping mid-conversation leaves X's ranking to decide which replies loaded,
# so the capture is a sample rather than a prefix and its diffs are noise.
# Allowed to run to the end, the same thread converged on 146 replies in 34
# rounds with nothing declared. A binding cap therefore means the capture is
# unreliable, which is why `capped` is recorded and declared as a gap.
SCROLL_ROUNDS_MAX = 120
DRY_ROUNDS_TO_STOP = 4
REPLY_CAP = 500
# How long to wait at the bottom of loaded content before believing the
# conversation has ended, and how many mid-page quiet rounds to tolerate
# before declaring the loader stalled rather than the thread finished.
LOADER_GRACE_SECONDS = 4.0
STALL_LIMIT = 8
HYDRATE_SECONDS = 12
SCROLL_SETTLE_SECONDS = 2.0

# Partition order in the canonical text. Sorting by status id within each
# partition keeps a newly landed reply from displacing the author's own chain.
ROLE_ORDER = ("ancestor", "focal", "self-thread", "reply")

STATUS_HREF = re.compile(r"^/([^/]+)/status/(\d+)")
# The one block delimiter the site's parser splits on. A body line that looked
# like one would silently corrupt the parse, so the flattener refuses instead.
BLOCK_START = re.compile(r"(?m)^post: \d+$")


class ThreadCaptureError(Exception):
    """Raised when a capture cannot be taken safely.

    A partial thread must never be written as though it were a whole one, so
    every failure here is loud. capture.py turns it into a skip event.
    """


# --------------------------------------------------------------- extraction

# Returns every article currently in the DOM, in document order, with only the
# six fields the canonical text keeps. Engagement counters, relative
# timestamps, verified badges and the reply-control row are deliberately never
# read: the churn the reddit normalizers exist to suppress does not enter the
# text in the first place. If an x-thread source ever needs a normalizer
# binding, this function is reading too much.
EXTRACT_ALL_JS = r"""
(() => {
  // X appends unrelated recommended posts below the replies. Everything at or
  // after that boundary belongs to the platform, not to this conversation.
  const BOUNDARY = ["Discover more", "More Tweets", "More posts",
                    "Relevant people", "Who to follow"];
  // Only chrome outside an <article> can be a boundary. A reply whose whole
  // text happened to be "More posts" would otherwise truncate the capture
  // silently, which is the worst failure this function could have.
  let boundary = null;
  for (const el of document.querySelectorAll("h2, span, div[role=heading]")) {
    if (el.closest("article")) continue;
    const t = (el.textContent || "").trim();
    if (BOUNDARY.includes(t)) { boundary = el; break; }
  }
  const past = el => boundary
    ? !!(boundary.compareDocumentPosition(el) &
         Node.DOCUMENT_POSITION_FOLLOWING)
    : false;

  const out = [];
  const arts = [...document.querySelectorAll("article")];
  arts.forEach((a, order) => {
    // Identity comes from the permalink, never from the rendered name block:
    // one probed article rendered "@X" while its own link said "0xAnthraX".
    let status = null, handle = null, when = null;
    for (const t of a.querySelectorAll("time")) {
      const href = t.closest("a")?.getAttribute("href") || "";
      const m = href.match(/^\/([^\/]+)\/status\/(\d+)/);
      if (m) {
        handle = m[1]; status = m[2]; when = t.getAttribute("datetime");
        break;
      }
    }
    if (!status) {
      // The focal post on some renders carries no self-link on its <time>.
      // Fall back to any status link inside the article header.
      const link = [...a.querySelectorAll("a[href*='/status/']")]
        .map(x => x.getAttribute("href") || "")
        .find(h => /^\/[^\/]+\/status\/\d+(\/|$|\?)/.test(h));
      if (link) {
        const m = link.match(/^\/([^\/]+)\/status\/(\d+)/);
        handle = m[1]; status = m[2];
        const t = a.querySelector("time");
        when = t ? t.getAttribute("datetime") : null;
      }
    }
    if (!status) return;

    const nameEl = a.querySelector("[data-testid=User-Name]");
    const name = nameEl
      ? (nameEl.innerText || "").split("\n").map(s => s.trim())
          .filter(Boolean)[0] || null
      : null;

    // Long-form posts carry no tweetText node; their body is a
    // longformRichTextComponent with a separate title element.
    let body = a.querySelector("[data-testid=tweetText]");
    let title = null;
    if (!body) {
      body = a.querySelector("[data-testid=longformRichTextComponent]");
      const te = a.querySelector("[data-testid=twitter-article-title]");
      title = te ? te.innerText : null;
    }
    const text = body
      ? (title ? title + "\n\n" + body.innerText : body.innerText)
      : "";

    out.push({
      order,
      status,
      author: handle,
      name,
      created: when || null,
      media: a.querySelectorAll("[data-testid=tweetPhoto]").length,
      text,
      beyondConversation: past(a),
    });
  });

  // A post's own truncation expander is not a gap: expand_truncated() clears
  // those before extraction, so anything still here loads more conversation.
  const controls = [...document.querySelectorAll("button, div[role=button]")]
    .filter(b => b.getAttribute("data-testid") !== "tweet-text-show-more-link")
    .map(b => (b.innerText || "").trim())
    .filter(s => /^(Show more replies|Show additional replies|Show probably offensive replies|Show more)$/i.test(s));

  // Whether the viewport has actually reached the end of what is loaded. A
  // round that yields nothing while still mid-page means the loader has not
  // caught up, which is not the same fact as the conversation having ended.
  const atBottom =
    window.innerHeight + window.scrollY >= document.body.scrollHeight - 200;

  return JSON.stringify({
    articles: out,
    controls: [...new Set(controls)],
    scrollY: window.scrollY,
    atBottom: atBottom,
  });
})()
"""

# Substituted with .replace(), not %-formatting: this JS contains literal
# percent signs in its CSS and %-formatting silently breaks on them.
# Pins one article by status id so the screenshot step has a stable selector,
# then isolates a rendered clone at the document origin. Cloning keeps X's
# responsive reflow out of the clip and keeps session chrome (nav, account,
# trends) from leaking into a published repo. Same technique as ingest-x.py.
ISOLATE_ONE_JS = r"""
(async () => {
  const status = "__STATUS__";
  document.getElementById("cc-thread-overlay")?.remove();
  const art = [...document.querySelectorAll("article")].find(a =>
    [...a.querySelectorAll("a[href*='/status/']")]
      .some(x => (x.getAttribute("href") || "").includes("/status/" + status)));
  if (!art) return JSON.stringify({found: false, reason: "not in DOM"});
  const r = art.getBoundingClientRect();
  const overlay = document.createElement("div");
  overlay.id = "cc-thread-overlay";
  overlay.style.cssText = [
    "position:absolute", "left:0", "top:0", `width:${r.width}px`,
    "z-index:2147483647", "overflow:hidden", "pointer-events:none",
    `background:${getComputedStyle(document.body).backgroundColor}`,
  ].join(";");
  const clone = art.cloneNode(true);
  clone.removeAttribute("id");
  clone.style.width = "100%";
  clone.style.margin = "0";
  overlay.appendChild(clone);
  document.body.appendChild(overlay);
  const imgs = [...clone.querySelectorAll("[data-testid=tweetPhoto] img")];
  await Promise.all(imgs.map(async img => {
    if (img.complete && img.naturalWidth > 0) return;
    try {
      await Promise.race([
        img.decode(),
        new Promise((_, rej) => setTimeout(() => rej(new Error("t")), 5000)),
      ]);
    } catch (_) { /* the readiness check below decides */ }
  }));
  if (imgs.some(img => !img.complete || img.naturalWidth === 0)) {
    overlay.remove();
    return JSON.stringify({found: false, reason: "media not decoded"});
  }
  // rAF never fires in a background tab; a timer does.
  await new Promise(res => setTimeout(res, 250));
  return JSON.stringify({found: true, w: overlay.offsetWidth,
                         h: overlay.offsetHeight});
})()
"""

CLEAR_OVERLAY_JS = 'document.getElementById("cc-thread-overlay")?.remove(); 1'

# X serves a long post truncated. Probed 6 Aug 2026: post 2 of the
# clay_garrett attribution thread held 275 characters in textContent with no
# CSS clamp, and expanding it produced 397 characters ending "no evidence that
# the provider knowingly participated in or facilitated the suspected theft."
# The archive had the truncated form. Capturing a body that stops mid-sentence
# and presenting it as what someone said is the failure this project exists to
# avoid, so expansion is mandatory before extraction, not an option.
#
# Expanding stays inside the daemon's read-only contract. It is deliberately
# not a general click primitive: this clicks exactly one data-testid, it asks
# the platform for more of a post already on screen, and the probe confirmed
# location.href and the article count are unchanged by it. Do not widen it.
EXPAND_TRUNCATED_JS = r"""
(() => {
  const btns = [...document.querySelectorAll(
    "[data-testid=tweet-text-show-more-link]")];
  btns.forEach(b => { try { b.click(); } catch (_) {} });
  return JSON.stringify({clicked: btns.length});
})()
"""


def parse_status_href(href: str) -> tuple[str, str] | None:
    """(handle, status id) from a /<handle>/status/<id> path, or None."""
    m = STATUS_HREF.match(href or "")
    return (m.group(1), m.group(2)) if m else None


def normalise_articles(raw: list[dict]) -> list[dict]:
    """Validate one extraction pass into posts, in document order.

    Articles without a resolvable status id, and everything at or beyond the
    platform's recommended-content boundary, are dropped here rather than
    downstream: a recommendation is not a reply and must never reach the
    canonical text.
    """
    posts = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("beyondConversation"):
            continue
        status = str(item.get("status") or "")
        if not status.isdigit():
            continue
        author = item.get("author")
        if not isinstance(author, str) or not author:
            continue
        posts.append({
            "status": status,
            "author": author,
            "name": item.get("name") or None,
            "created": normalise_created(item.get("created")),
            "media": int(item.get("media") or 0),
            "text": item.get("text") or "",
        })
    return posts


def normalise_created(value) -> str | None:
    """X's datetime attribute to the archive's second-resolution UTC form."""
    if not isinstance(value, str) or not value:
        return None
    return value.replace(".000Z", "Z")


def assign_roles(posts: list[dict], focal_status: str,
                 focal_author: str) -> dict[str, str]:
    """Role per status id, from the un-scrolled head of the conversation.

    Roles are assigned once, in the first pass, while the head is intact:
    after scrolling the focal post itself is evicted and document order stops
    meaning anything. Everything discovered later can only be a reply.

    The one heuristic in this design. A self-thread is the focal author's own
    posts contiguously below the focal post; the first article by anyone else
    ends it, so an author replying again further down the conversation is a
    reply, which is what it is.
    """
    index = next((n for n, p in enumerate(posts)
                  if p["status"] == focal_status), None)
    if index is None:
        raise ThreadCaptureError(
            f"focal post {focal_status} not found in the rendered conversation "
            "(deleted, protected, or the page did not hydrate)"
        )
    roles = {p["status"]: "reply" for p in posts}
    for p in posts[:index]:
        roles[p["status"]] = "ancestor"
    roles[focal_status] = "focal"
    wanted = focal_author.lower()
    for p in posts[index + 1:]:
        if p["author"].lower() != wanted:
            break
        roles[p["status"]] = "self-thread"
    return roles


def new_thread(focal_status: str, url: str, focal_author: str) -> dict:
    return {
        "thread": focal_status,
        "url": url,
        "author": focal_author,
        "posts": {},
        "roles": {},
        "gaps": [],
        "role_changes": [],
    }


def merge_posts(thread: dict, posts: list[dict],
                roles: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Fold one extraction pass into the accumulating thread.

    Returns the status ids that are new and the ones whose text moved, which
    is what the screenshot pass needs: a post is shot on first sight and
    re-shot only when its text changes, so a poll of a fifty-reply thread does
    not rewrite fifty images it already holds.
    """
    added: list[str] = []
    changed: list[str] = []
    for post in posts:
        status = post["status"]
        role = (roles or {}).get(status)
        held = thread["posts"].get(status)
        if held is None:
            thread["posts"][status] = post
            thread["roles"][status] = role or "reply"
            added.append(status)
            continue
        if role and thread["roles"].get(status) != role:
            # Role is stable by construction, so a change is a signal that X
            # moved something under us. Record it rather than rewrite quietly.
            thread["role_changes"].append({
                "status": status,
                "from": thread["roles"].get(status),
                "to": role,
            })
        if post["text"] != held["text"]:
            thread["posts"][status] = post
            changed.append(status)
    return {"added": added, "changed": changed}


def replies(thread: dict) -> list[str]:
    return [s for s, r in thread["roles"].items() if r == "reply"]


def sorted_statuses(thread: dict) -> list[str]:
    """Partition by role, then status id ascending inside each partition."""
    def key(status: str) -> tuple[int, int]:
        role = thread["roles"].get(status, "reply")
        rank = ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER)
        return (rank, int(status))
    return sorted(thread["posts"], key=key)


def flatten_thread(thread: dict) -> str:
    """Canonical text for one captured conversation.

    Deterministic by construction, in the shape flatten_reddit_thread
    established. Counts, scroll rounds and cap values are deliberately absent:
    they are facts about this project's collection, they move on every capture,
    and putting them here would make every poll report a change. They live in
    meta.json. The gap lines carry the qualitative statement a reader of a
    40-line excerpt needs.
    """
    lines = [
        f"thread: {thread['thread']}",
        f"url: {thread['url']}",
        f"author: {thread['author']}",
    ]
    for status in sorted_statuses(thread):
        post = thread["posts"][status]
        text = post["text"]
        if BLOCK_START.search(text):
            # Would split into a phantom post on read-back. Refuse rather than
            # write a capture the site would parse into something else.
            raise ThreadCaptureError(
                f"post {status} contains a line matching the block delimiter; "
                "refusing to write an ambiguous canonical text"
            )
        lines += [
            "",
            f"post: {status}",
            f"role: {thread['roles'].get(status, 'reply')}",
            f"author: {post['author']}",
            f"name: {post['name'] or ''}",
            f"created: {post['created'] or ''}",
            f"media: {post['media']}",
            "body:",
            text,
        ]
    for gap in thread["gaps"]:
        lines += ["", f"gap: {gap}"]
    return "\n".join(lines) + "\n"


def structured_record(thread: dict, meta: dict) -> dict:
    """The held artefact beside the canonical text.

    Carries what the text deliberately leaves out: the depth this capture
    reached, so a later reviewer can tell a reply that was deleted from one
    that ranking pushed below the cap. Absence is not deletion, and this is
    the field that lets someone say which it was.
    """
    return {
        "schema": 1,
        "thread": thread["thread"],
        "url": thread["url"],
        "author": thread["author"],
        "posts": [
            {**thread["posts"][s], "role": thread["roles"].get(s, "reply")}
            for s in sorted_statuses(thread)
        ],
        "gaps": list(thread["gaps"]),
        "role_changes": list(thread["role_changes"]),
        "depth": meta,
    }


# ------------------------------------------------------------------ browser

def make_bridge(daemon: str, session: str, token: str = ""):
    """A callable that speaks the capture browser's protocol.

    Injected everywhere below so the capture loop is testable without a
    browser: a test passes a fake that replays recorded extraction passes.

    `token` is the capture browser's shared secret where one is configured;
    an empty string sends no header, which is what an install predating the
    token does. See capture-browser/webbridge.py.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Bridge-Token"] = token

    def bridge(action: str, args: dict, fatal: bool = True):
        body = json.dumps(
            {"action": action, "args": args, "session": session}
        ).encode()
        req = urllib.request.Request(daemon, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.load(resp)
        if not payload.get("ok"):
            if fatal:
                raise ThreadCaptureError(f"webbridge {action} failed: {payload}")
            return None
        return payload["data"]
    return bridge


def expand_truncated(bridge) -> int:
    """Expand every truncated post currently on screen. Returns how many.

    Called before each extraction pass, because a post scrolled into view
    arrives truncated like any other.
    """
    raw = bridge("evaluate", {"code": EXPAND_TRUNCATED_JS}, fatal=False)
    if raw is None:
        return 0
    try:
        return int(json.loads(raw["value"]).get("clicked", 0))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0


def extract_pass(bridge) -> dict:
    raw = bridge("evaluate", {"code": EXTRACT_ALL_JS})
    try:
        return json.loads(raw["value"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ThreadCaptureError(f"extraction returned no usable value: {exc}")


def screenshot_post(bridge, status: str) -> bytes | None:
    """Element-only PNG of one post, or None if it could not be framed.

    A post that will not settle is skipped, never approximated: a mis-framed
    screenshot attributed to someone is worse than no screenshot.
    """
    raw = bridge("evaluate",
                 {"code": ISOLATE_ONE_JS.replace("__STATUS__", status)},
                 fatal=False)
    if raw is None:
        return None
    try:
        isolated = json.loads(raw["value"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isolated.get("found"):
        return None
    shot = bridge("cdp", {"method": "Page.captureScreenshot", "params": {
        "format": "png",
        "captureBeyondViewport": True,
        "clip": {"x": 0, "y": 0, "width": isolated["w"],
                 "height": isolated["h"], "scale": 1},
    }}, fatal=False)
    bridge("evaluate", {"code": CLEAR_OVERLAY_JS}, fatal=False)
    if shot is None or "data" not in shot:
        return None
    return base64.b64decode(shot["data"])


def capture_thread(
    url: str,
    focal_status: str,
    focal_author: str,
    *,
    bridge,
    held_statuses: frozenset[str] = frozenset(),
    scroll_rounds: int = SCROLL_ROUNDS_MAX,
    dry_rounds_to_stop: int = DRY_ROUNDS_TO_STOP,
    reply_cap: int = REPLY_CAP,
    sleep=time.sleep,
    hydrate_seconds: float = HYDRATE_SECONDS,
    settle_seconds: float = SCROLL_SETTLE_SECONDS,
    loader_grace_seconds: float = LOADER_GRACE_SECONDS,
    stall_limit: int = STALL_LIMIT,
    want_screenshots: bool = True,
) -> tuple[dict, dict, dict[str, bytes]]:
    """Drive one conversation capture. Returns (thread, depth, screenshots).

    `held_statuses` are ids whose screenshot this archive already holds, so an
    unchanged reply is not re-shot on every poll.

    No archive write happens here and no lock is taken: browser work runs for
    a minute or more on first sight, and the one writer must not be blocked on
    it. The caller writes.
    """
    bridge("navigate", {"url": url, "newTab": True,
                        "group_title": "COLDCARD archive thread capture"})
    sleep(hydrate_seconds)

    expanded = expand_truncated(bridge)
    if expanded:
        sleep(settle_seconds)
    first = extract_pass(bridge)
    posts = normalise_articles(first.get("articles", []))
    if not posts:
        raise ThreadCaptureError("no articles rendered; page did not hydrate")
    roles = assign_roles(posts, focal_status, focal_author)

    thread = new_thread(focal_status, url, focal_author)
    delta = merge_posts(thread, posts, roles)
    shots: dict[str, bytes] = {}
    controls = set(first.get("controls") or [])

    def shoot(added: list[str], changed: list[str]) -> None:
        if not want_screenshots:
            return
        for status in added + changed:
            if status in shots:
                continue
            # Shot on first sight, re-shot only when the text moved. Without
            # this a tier-3 poll of a fifty-reply thread writes 200 PNGs a day
            # of images the archive already holds.
            if status in held_statuses and status not in changed:
                continue
            png = screenshot_post(bridge, status)
            if png:
                shots[status] = png

    shoot(delta["added"], delta["changed"])

    rounds = 0
    dry = 0
    stalls = 0
    capped = False
    while rounds < scroll_rounds and dry < dry_rounds_to_stop:
        if len(replies(thread)) >= reply_cap:
            capped = True
            break
        bridge("evaluate",
               {"code": "window.scrollBy(0, window.innerHeight*0.85); 1"})
        sleep(settle_seconds)
        rounds += 1
        # A post scrolled into view arrives truncated like any other, so this
        # runs every round, not just at the top of the page.
        clicked = expand_truncated(bridge)
        if clicked:
            expanded += clicked
            sleep(settle_seconds)
        seen = extract_pass(bridge)
        controls |= set(seen.get("controls") or [])
        delta = merge_posts(thread, normalise_articles(seen.get("articles", [])))
        if delta["added"] or delta["changed"]:
            dry = 0
            stalls = 0
            shoot(delta["added"], delta["changed"])
            continue
        # Nothing new this round. Whether that means the conversation ended
        # depends entirely on where we are. Measured 6 Aug 2026: counting any
        # quiet round as convergence ended one capture of a 146-reply thread
        # at 45 replies with nothing declared, which is the worst outcome
        # available here because it reads exactly like 88 deleted replies.
        if not seen.get("atBottom"):
            # Mid-page and quiet means the loader has not caught up, which is
            # not the same fact as the conversation having ended.
            stalls += 1
            if stalls > stall_limit:
                thread["gaps"].append(
                    "loading stalled before the end of the conversation")
                break
            sleep(loader_grace_seconds)
            continue
        # At the bottom of what is loaded, which is where X appends more. Give
        # the loader real time before believing the quiet.
        stalls = 0
        sleep(loader_grace_seconds)
        after = extract_pass(bridge)
        controls |= set(after.get("controls") or [])
        late = merge_posts(thread,
                           normalise_articles(after.get("articles", [])))
        if late["added"] or late["changed"]:
            dry = 0
            shoot(late["added"], late["changed"])
        else:
            dry += 1

    # Declared gaps. A capture states where it stopped rather than implying it
    # reached the end, because X ranking decides what loads and no scroll
    # depth guarantees completeness.
    if capped:
        thread["gaps"].append(
            "reply cap reached; X ranking governs which replies loaded")
    # "Stopped before it stopped yielding" is only true if the dry streak never
    # reached convergence. Comparing against the caller's own stop value alone
    # gets this wrong when that value is raised: a run that went eighteen dry
    # rounds has converged, whatever it was told to stop at.
    converged = dry >= min(dry_rounds_to_stop, DRY_ROUNDS_TO_STOP)
    if rounds >= scroll_rounds and not converged:
        thread["gaps"].append(
            "scroll limit reached before the conversation stopped growing")
    for control in sorted(controls):
        thread["gaps"].append(f"{control!r} control present, not expanded")

    depth = {
        "scroll_rounds": rounds,
        "dry_rounds": dry,
        "reply_cap": reply_cap,
        "capped": capped,
        "posts_expanded": expanded,
        "posts_observed": len(thread["posts"]),
        "replies_observed": len(replies(thread)),
        "screenshots_taken": len(shots),
    }
    return thread, depth, shots
