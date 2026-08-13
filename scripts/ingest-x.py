#!/usr/bin/env python3
"""Ingest one X post into archive/x/ and register it in sources.toml.

One command, three artefacts:

  1. <post-id>/<TS>/post.png   element-only screenshot of the tweet article. A
                           rendered clone is isolated at the document origin,
                           then captured through CDP beyond the viewport. This
                           keeps X's responsive reflow out of the clip and
                           avoids leaking session chrome (nav, account, trends)
                           into a published repo.
  2. <post-id>/<TS>/post.txt   verbatim text + provenance sidecar.
  3. a [[x_post]] block in sources.toml (skipped if the id or URL is
                           already registered, or --no-register is given).

With --thread, a fourth: the first capture of the conversation around the
post. That is a different kind of artefact and it is written by a different
tool. A thread-enabled post is a polled source under this same id, so its
conversation belongs in the snapshot, diff, review and change-feed contract
that capture.py owns; this script registers the entry and then hands the
capture to `capture.py capture --id <slug>`, which is the same poll its tier's
timer will run from then on. See docs/design/x-thread-capture.md.

Uses the capture browser (http://127.0.0.1:10086), which holds the
project's signed-in sessions. READ-ONLY on X: navigate, evaluate,
screenshot. Nothing is posted, followed or liked. Complements the
gallery-dl path (capture-x.sh), which only fetches attached media: this
is the tool that captures the rendered post itself.

Stdlib only, per repo policy. Run through the venv:

  .venv/bin/python scripts/ingest-x.py <url> --id <slug> [--tag t] [--why "..."]
  .venv/bin/python scripts/ingest-x.py <url> --id <slug> --thread --tier 3

or via just:

  just ingest-x <url> <slug>
  just ingest-x <url> <slug> "" "" --thread --tier 3
  just capture-thread <slug>      # re-capture the conversation later
"""

import argparse
import base64
import json
import tomllib
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from archive_lock import ArchiveLockBusy, archive_lock
from capture import X_STATUS_URL, load_sources, wb_token
from migrate_registry import refresh_if_installed
from x_thread import expand_truncated

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "archive" / "x"
SOURCES = ROOT / "sources.toml"
DAEMON = "http://127.0.0.1:" + os.environ.get("WEBBRIDGE_PORT", "10086") + "/command"
SESSION = os.environ.get("CAPTURE_BROWSER_SESSION", "coldcard-archive-x")
GROUP_TITLE = "COLDCARD archive ingest"
# Attached images load lazily and can still be empty frames after the tweet text
# has settled. A post whose evidence IS the screenshot is worth the extra wait:
# capturing the caption without the artefact it refers to is worse than slow.
HYDRATE_SECONDS = 12


def bridge(action: str, args: dict, fatal: bool = True) -> dict | None:
    body = json.dumps({"action": action, "args": args, "session": SESSION}).encode()
    headers = {"Content-Type": "application/json"}
    # The capture browser's shared secret, where the operator has set one up.
    # It lives in .capture-browser/ at mode 700, so the account the unattended
    # agents run as cannot read it: reaching the port is not enough to drive a
    # signed-in browser. See capture-browser/webbridge.py.
    token = wb_token()
    if token:
        headers["X-Bridge-Token"] = token
    req = urllib.request.Request(DAEMON, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    if not payload.get("ok"):
        if fatal:
            sys.exit(f"webbridge {action} failed: {payload}")
        return None
    return payload["data"]


def now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_tweet(url: str) -> tuple[str, str]:
    m = re.search(r"x\.com/([^/]+)/status/(\d+)", url)
    if not m:
        sys.exit(f"not an X status URL: {url}")
    return m.group(1), m.group(2)


EXTRACT_JS = r"""
(() => {
  const handle = "%s".toLowerCase();
  const tweetId = "%s";
  document.querySelectorAll("#cc-ingest-target").forEach(e => {
    e.removeAttribute("id");
  });
  const arts = [...document.querySelectorAll("article")];
  const targetTime = a => [...a.querySelectorAll("time")].find(t => {
    const href = t.closest("a")?.getAttribute("href");
    return href && href.includes("/status/" + tweetId);
  });
  const el = arts.find(a => {
    const u = a.querySelector("[data-testid=User-Name]");
    return u && u.innerText.toLowerCase().includes("@" + handle) && targetTime(a);
  });
  if (!el) return JSON.stringify({found: false});
  const u = el.querySelector("[data-testid=User-Name]");
  const t = targetTime(el);
  // Long-form posts (X articles) carry no tweetText node: their body is a
  // longformRichTextComponent with a separate title element. Fall back so a
  // long-form post yields verbatim text instead of a hard "not found".
  let txt = el.querySelector("[data-testid=tweetText]");
  let longformTitle = null;
  if (!txt) {
    txt = el.querySelector("[data-testid=longformRichTextComponent]");
    const titleEl = el.querySelector("[data-testid=twitter-article-title]");
    longformTitle = titleEl ? titleEl.innerText : null;
  }
  const mediaSlots = [...el.querySelectorAll("[data-testid=tweetPhoto]")];
  const mediaImages = mediaSlots.flatMap(slot => [...slot.querySelectorAll("img")]);
  // A photo slot can hold a video: the <video> carries its poster as an
  // attribute and the slot contains no <img>, so an images-only count never
  // reaches the slot count and the readiness gate would never pass.
  const mediaVideos = mediaSlots.flatMap(slot => [...slot.querySelectorAll("video")]);
  const videos = mediaVideos.map(v => ({
    poster: v.poster || null,
    readyState: v.readyState,
  }));
  const media = mediaImages.map(img => ({
    url: img.currentSrc || img.src || null,
    complete: img.complete,
    width: img.naturalWidth,
    height: img.naturalHeight,
  }));
  const mediaReady = mediaSlots.length === 0 || (
    mediaImages.length + mediaVideos.length >= mediaSlots.length &&
    media.every(img => img.complete && img.width > 0 && img.height > 0) &&
    videos.every(v => v.poster)
  );
  // Reply context: the article directly above this one, if any.
  const i = arts.indexOf(el);
  let replyTo = null;
  if (i > 0) {
    const pu = arts[i-1].querySelector("[data-testid=User-Name]");
    const pt = arts[i-1].querySelector("[data-testid=tweetText]");
    replyTo = {
      user: pu ? pu.innerText : null,
      text: pt ? pt.innerText.slice(0, 280) : null,
    };
  }
  // Pin the element so the screenshot step has a stable, unique selector.
  // No scrolling: the CDP clip is computed in document coordinates and
  // captures beyond the viewport.
  el.id = "cc-ingest-target";
  return JSON.stringify({
    found: true,
    // Set while any of this post's text is still behind a show-more control.
    // The capture refuses rather than record a body that stops mid-sentence.
    truncated: !!el.querySelector("[data-testid=tweet-text-show-more-link]"),
    user: u ? u.innerText : null,
    time: t ? t.getAttribute("datetime") : null,
    timeLink: t && t.closest("a") ? t.closest("a").getAttribute("href") : null,
    text: txt ? (longformTitle ? longformTitle + "\n\n" + txt.innerText
                               : txt.innerText) : null,
    height: el.offsetHeight,
    mediaSlots: mediaSlots.length,
    mediaReady: mediaReady,
    media: media,
    videos: videos,
    replyTo: replyTo,
  });
})()
"""


RECT_JS = r"""
(() => {
  const el = document.getElementById("cc-ingest-target");
  if (!el || !el.isConnected) return JSON.stringify({found: false});
  const r = el.getBoundingClientRect();
  return JSON.stringify({x: r.x, y: r.y + window.scrollY,
                         w: r.width, h: r.height, dpr: devicePixelRatio,
                         scrollY: window.scrollY});
})()
"""


ISOLATE_JS = r"""
(async () => {
  document.getElementById("cc-ingest-overlay")?.remove();
  const el = document.getElementById("cc-ingest-target");
  if (!el || !el.isConnected) return JSON.stringify({found: false});
  const r = el.getBoundingClientRect();
  const overlay = document.createElement("div");
  overlay.id = "cc-ingest-overlay";
  overlay.style.cssText = [
    "position:absolute", "left:0", "top:0", `width:${r.width}px`,
    "z-index:2147483647", "overflow:hidden", "pointer-events:none",
    `background:${getComputedStyle(document.body).backgroundColor}`,
  ].join(";");
  const clone = el.cloneNode(true);
  clone.removeAttribute("id");
  clone.style.width = "100%";
  clone.style.margin = "0";
  overlay.appendChild(clone);
  document.body.appendChild(overlay);
  const mediaImages = [...clone.querySelectorAll("[data-testid=tweetPhoto] img")];
  await Promise.all(mediaImages.map(async img => {
    if (img.complete && img.naturalWidth > 0) return;
    try {
      await Promise.race([
        img.decode(),
        new Promise((_, reject) => setTimeout(
          () => reject(new Error("media decode timeout")), 5000
        )),
      ]);
    } catch (_) {
      // The readiness check below decides whether capture may proceed.
    }
  }));
  if (mediaImages.some(img => !img.complete || img.naturalWidth === 0)) {
    overlay.remove();
    return JSON.stringify({found: false, reason: "media not decoded"});
  }
  // rAF never fires in a background tab; a timer does (throttled, but fires).
  await new Promise(resolve => setTimeout(resolve, 250));
  return JSON.stringify({found: true, w: overlay.offsetWidth,
                         h: overlay.offsetHeight});
})()
"""


TS_DIR = re.compile(r"^\d{8}T\d{6}Z$")
BODY_MARK = "--- post text (verbatim) ---"


def newest_held_body(slug: str) -> str | None:
    """Post text from the newest held capture of this post, if any.

    Used by --skip-unchanged so a repair pass writes a capture only where the
    text actually differs. A directory whose name is not a timestamp is
    rejected explicitly: "undated" sorts after digits, so a plain max() would
    pick it.
    """
    directory = OUT / slug
    if not directory.is_dir():
        return None
    stamps = sorted(p for p in directory.iterdir()
                    if p.is_dir() and TS_DIR.match(p.name))
    for capture in reversed(stamps):
        sidecar = capture / "post.txt"
        if not sidecar.is_file():
            continue
        text = sidecar.read_text(encoding="utf-8", errors="replace")
        if BODY_MARK in text:
            return text.split(BODY_MARK, 1)[1].strip()
    return None


def registered_x_posts() -> list[dict]:
    """Every [[x_post]] block, or an empty list if the registry will not parse.

    Deliberately tolerant, and deliberately not load_sources(): this is read
    before anything is written, to decide where a capture goes, and a registry
    that is mid-edit should not stop a capture from being taken.
    """
    registered_now = False
    try:
        cfg = tomllib.loads(SOURCES.read_text())
    except Exception:
        return []
    return [p for p in cfg.get("x_post", []) if isinstance(p, dict)]


def status_in(url: str) -> str | None:
    """The status id a registered URL names, or None if it names none.

    Compared exactly, never as a substring. `tweet_id in url` reads like the
    same test and is not: X ids vary in length, so a shorter id can appear
    inside a longer one and resolve this post to somebody else's registry
    entry. A capture filed under the wrong id is the failure the id
    resolution here exists to prevent.
    """
    match = X_STATUS_URL.search(url or "")
    return match.group(2) if match else None


def registered_post(tweet_id: str) -> dict | None:
    """The [[x_post]] block this status is already registered as, if any."""
    for post in registered_x_posts():
        if status_in(post.get("url", "")) == tweet_id:
            return post
    return None


def registered_id(tweet_id: str) -> str | None:
    """The id this status is already registered under, if any.

    Captures live under the registered id so that the site, which iterates the
    registry, looks in the directory the capture actually wrote. A slug derived
    from the handle would be right often enough to be dangerous.
    """
    post = registered_post(tweet_id)
    return post.get("id") if post else None


def thread_enabled(slug: str) -> bool:
    """Whether sources.toml holds a thread-enabled [[x_post]] under this id.

    Checked after the write phase rather than predicted before it. The thread
    capture polls by id, and an id that resolves to something other than this
    post's conversation would file a capture under the wrong source.
    """
    return any(p.get("id") == slug and p.get("thread") is True
               for p in registered_x_posts())


def already_registered(tweet_id: str, slug: str) -> bool:
    cfg = load_sources()
    for p in cfg.get("source", []) + cfg.get("x_post", []):
        if p.get("id") == slug or status_in(p.get("url", "")) == tweet_id:
            return True
    return False


def register(slug: str, url: str, handle: str, posted: str, tag: str | None,
             why: str, thread: bool = False, tier: int | None = None) -> None:
    block = f'''
[[x_post]]
id = "{slug}"
url = "{url}"
author = "{handle}"
posted = "{posted}"
'''
    if tag:
        block += f'tag = "{tag}"\n'
    if thread:
        # thread = true makes this post's conversation a polled source under
        # the same id; tier states its cadence, and validate_sources requires
        # the pair. See docs/design/x-thread-capture.md section 4.
        block += f"thread = true\ntier = {int(tier)}\n"
    block += f'why = """\n{why.rstrip()}\n"""\n'
    with open(SOURCES, "a") as fh:
        fh.write(block)


TIERS = (1, 2, 3)
# capture.py's "a healthy run found a change" exit. See README, Usage.
CHANGE_EXIT = 10


def resolve_tier(want_thread: bool, tier: int | None, existing: dict | None,
                 no_register: bool) -> int | None:
    """The tier this run should register, or None when nothing is registered.

    Raises ValueError with the operator's next step. Turning threading on for a
    post that is already registered is a registry semantics change and stays a
    human edit of sources.toml: this script appends blocks, it does not rewrite
    them, and quietly appending a second block for the same post would give one
    conversation two entries.
    """
    if not want_thread:
        if tier is not None:
            raise ValueError(
                "--tier applies only to a polled thread; add --thread or drop "
                "--tier"
            )
        return None
    if tier is not None and tier not in TIERS:
        raise ValueError(f"--tier must be 1, 2 or 3, got {tier}")
    if existing is None:
        if no_register:
            raise ValueError(
                "--thread has nothing to poll with --no-register: the "
                "conversation is captured as a registered source, under this "
                "post's id"
            )
        if tier is None:
            raise ValueError(
                "--thread requires --tier 1, 2 or 3, so the conversation's "
                "polling cadence is stated rather than assumed"
            )
        return tier
    if existing.get("thread") is not True:
        raise ValueError(
            f"{existing.get('id')!r} is registered as a single post. Add\n"
            "    thread = true\n"
            "    tier = 3\n"
            "to its [[x_post]] block in sources.toml, then re-run. Enabling a "
            "poll is a registry edit, not something an ingest run should do "
            "on its own"
        )
    held = existing.get("tier")
    if tier is not None and tier != held:
        raise ValueError(
            f"{existing.get('id')!r} is registered at tier {held!r}; --tier "
            f"{tier} would disagree with the registry. Edit sources.toml or "
            "drop --tier"
        )
    return held


def capture_thread_now(slug: str) -> int:
    """Take the first conversation capture, through capture.py's poll path.

    Not a second write path, on purpose. capture.py owns snapshot writing,
    change detection, the diff, the index.jsonl event and the run record, and a
    manual first capture that wrote its own would be a second implementation of
    the one thing this repo must not get wrong. It is a separate process
    because the archive writer lock is not reentrant and this script has just
    released it; the browser work then happens under capture.py's lock, exactly
    as it does on every scheduled poll of the same source.
    """
    if not thread_enabled(slug):
        print(f"thread: {slug!r} is not a thread-enabled [[x_post]] in "
              "sources.toml, so there is no conversation source to poll; "
              "the focal post was captured and the conversation was not",
              file=sys.stderr)
        return 2
    cmd = [sys.executable, str(ROOT / "scripts" / "capture.py"), "capture",
           "--id", slug, "--kind", "social-thread"]
    print(f"thread: capturing the conversation as source {slug!r}")
    code = subprocess.run(cmd, cwd=ROOT).returncode
    # capture.py exits 10 when a healthy run found a change, and a first
    # capture of a conversation is a change by definition: reporting the
    # outcome this command was run for as a non-zero exit would make every
    # successful ingest look like a failure. Everything else passes through
    # unchanged, so 20 still means the poll was incomplete and 21 that another
    # writer holds the lock. Same mapping as `just capture-gate`, for the same
    # reason.
    return 0 if code == CHANGE_EXIT else code


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--id", dest="slug",
                    help="archive slug; default <handle>-<tweet-id>")
    ap.add_argument("--tag")
    ap.add_argument("--why", default=None,
                    help="why this post matters; default: first 200 chars")
    ap.add_argument("--no-register", action="store_true",
                    help="artefacts only, do not touch sources.toml")
    ap.add_argument("--keep-tab", action="store_true")
    ap.add_argument("--skip-unchanged", action="store_true",
                    help="write nothing if the post text matches the newest "
                         "held capture; for repair passes over many posts")
    ap.add_argument("--thread", action="store_true",
                    help="also capture the conversation around the post, as a "
                         "polled source under the same id")
    ap.add_argument("--tier", type=int,
                    help="polling cadence 1-3 for --thread; required when the "
                         "post is being registered now")
    a = ap.parse_args()

    handle, tweet_id = parse_tweet(a.url)
    # An explicit --id wins, then the id this status is already registered
    # under, then a slug from the handle for a post being registered now.
    existing = registered_post(tweet_id)
    slug = a.slug or (existing.get("id") if existing else None) \
        or f"{handle.lower()}-{tweet_id}"
    # Settled before any browser work, so a run that cannot end in a thread
    # capture says so before it spends two minutes finding out.
    try:
        tier = resolve_tier(a.thread, a.tier, existing, a.no_register)
    except ValueError as exc:
        sys.exit(str(exc))

    print(f"navigating: {a.url}")
    nav = bridge("navigate", {"url": a.url, "newTab": True,
                              "group_title": GROUP_TITLE}, fatal=False)
    if nav is None:
        # The daemon remembers the session's last tab; if it was closed
        # behind our back, navigate trips over the stale id. Reset the
        # session bookkeeping and retry once.
        bridge("close_session", {}, fatal=False)
        nav = bridge("navigate", {"url": a.url, "newTab": True,
                                  "group_title": GROUP_TITLE}, fatal=False)
        if nav is None:
            sys.exit("navigate failed after resetting the WebBridge session")
    time.sleep(HYDRATE_SECONDS)

    # X serves a long post cut off, with the remainder behind a show-more
    # control and genuinely absent from the DOM. Until 6 Aug 2026 this tool
    # read the truncated body and filed it as the post: a probed post held 275
    # characters where the full text is 397, and the missing clause was the
    # one its registry entry cites. Expand before reading, every time.
    expanded = expand_truncated(bridge)
    if expanded:
        time.sleep(2)

    # X hydrates slowly; poll until the article renders.
    info = {}
    for _ in range(10):
        raw = bridge("evaluate", {"code": EXTRACT_JS % (handle, tweet_id)})
        info = json.loads(raw["value"])
        if (info.get("found")
                and (info.get("text") or info.get("mediaSlots"))
                and info.get("mediaReady") and not info.get("truncated")):
            break
        # A post that scrolled or re-rendered can come back truncated again.
        expanded += expand_truncated(bridge)
        time.sleep(2)
    if not info.get("found"):
        sys.exit(f"tweet article not found for @{handle} (deleted? wrong URL?)")
    if not info.get("text") and not info.get("mediaSlots"):
        sys.exit(f"tweet article for @{handle} has neither text nor media; "
                 "refusing an empty capture")
    if not info.get("text"):
        # An image-only post carries no tweetText node: the attached image IS
        # the post. Normalise the absent body to empty so the sidecar and
        # registry paths below can assume a string.
        info["text"] = ""
    if not info.get("mediaReady"):
        sys.exit(f"tweet media did not hydrate for @{handle}; refusing a blank "
                 "attachment capture")
    if info.get("truncated"):
        # Capturing a body that stops mid-sentence and presenting it as what
        # someone said is worse than capturing nothing.
        sys.exit(f"tweet text stayed truncated for @{handle} after "
                 f"{expanded} expansion attempts; refusing a partial capture")

    # Checked before the screenshot pass, which is the expensive part.
    if a.skip_unchanged:
        held = newest_held_body(slug)
        if held is not None and held == info["text"].strip():
            print(f"{slug}: unchanged since the newest held capture; "
                  "nothing written")
            if not a.keep_tab:
                bridge("close_tab", {})
            # The focal post has not moved, but the conversation around it is
            # a separate source with its own history and moves on its own, so
            # --thread still runs.
            if a.thread:
                sys.exit(capture_thread_now(slug))
            return

    # X permalink pages keep shifting while replies and media hydrate. Retag
    # the exact status if X re-renders it, then isolate a static rendered clone
    # before capture so later thread movement cannot change the artefact.
    def measure() -> dict:
        return json.loads(bridge("evaluate", {"code": RECT_JS})["value"])

    shot = None
    for attempt in range(10):
        # Background tabs throttle paint and rAF; pulling the tab forward makes
        # compositing reliable (without it the author header can be missing).
        bridge("cdp", {"method": "Page.bringToFront", "params": {}}, fatal=False)
        if attempt:
            # A re-render collapses an expanded post again, and the screenshot
            # must show the same text the sidecar records.
            expand_truncated(bridge)
            raw = bridge("evaluate", {"code": EXTRACT_JS % (handle, tweet_id)})
            refreshed = json.loads(raw["value"])
            if (not refreshed.get("found") or not refreshed.get("mediaReady")
                    or refreshed.get("truncated")):
                time.sleep(1)
                continue
        time.sleep(1.5)
        rect = measure()
        if not rect or rect.get("found") is False:
            continue
        print(f"clip rect: {rect}")
        isolated = json.loads(
            bridge("evaluate", {"code": ISOLATE_JS})["value"]
        )
        if not isolated.get("found"):
            continue
        shot = bridge("cdp", {"method": "Page.captureScreenshot", "params": {
            "format": "png",
            "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0,
                     "width": isolated["w"], "height": isolated["h"],
                     "scale": 1},
        }})
        break
    else:
        sys.exit("tweet article would not settle; not capturing a potentially "
                 "mis-framed screenshot")
    captured = now_z()
    user_lines = [
        line.strip() for line in (info.get("user") or "").splitlines()
        if line.strip()
    ]
    lines = [
        f"url:      {a.url}",
        f"author:   {user_lines[0]}" if user_lines else "author:   ?",
        f"posted:   {info['time']} (from the page's <time> element)",
        f"captured: {captured} via the capture browser (authenticated,",
        "          read-only. Element-only screenshot; no session chrome.",
    ]
    if expanded:
        lines.append("expanded: this post was served truncated; its show-more "
                     "control was")
        lines.append("          expanded before reading, so the text below is "
                     "the whole post")
    if len(user_lines) > 1:
        lines.insert(2, f"handle:   {user_lines[1]}")
    media = info.get("media") or []
    lines.append(f"media:    {len(media)} attached image(s)")
    for index, item in enumerate(media, start=1):
        lines.append(
            f"media-{index}: {item.get('url')} "
            f"({item.get('width')} x {item.get('height')} pixels)"
        )
    videos = info.get("videos") or []
    if videos:
        # The element screenshot holds the player with its poster frame; the
        # moving image itself is gallery-dl territory (capture-x.sh).
        lines.append(f"videos:   {len(videos)} attached video(s), poster URL below;")
        lines.append("          the video itself is not captured by this tool")
        for index, item in enumerate(videos, start=1):
            lines.append(f"video-{index}: poster {item.get('poster')}")
    if info.get("replyTo") and info["replyTo"].get("user"):
        rt = info["replyTo"]
        lines.append(f"reply-to: {rt['user']} -- {rt['text']!r}")
    if not info["text"]:
        lines.append("note:     no text body; the attached image is the whole post")
    lines += ["", "--- post text (verbatim) ---", "", info["text"], ""]
    try:
        with archive_lock("ingest-x"):
            # A capture is a directory. Re-capturing writes a new one beside
            # the old, so nothing is ever overwritten and the append-only rule
            # is enforced by the layout rather than by remembering it.
            capture_dir = OUT / slug / captured
            capture_dir.mkdir(parents=True, exist_ok=True)
            png, txt = capture_dir / "post.png", capture_dir / "post.txt"
            png.write_bytes(base64.b64decode(shot["data"]))
            txt.write_text("\n".join(lines))
            print(f"screenshot: {png}")
            print(f"sidecar:    {txt}")

            if a.no_register or already_registered(tweet_id, slug):
                print("sources.toml: already registered (or --no-register), untouched")
            else:
                posted = (info["time"] or "").replace(".000Z", "Z")
                first_line = (info["text"].splitlines()[0][:200]
                              if info["text"]
                              else f"image-only post by @{handle}")
                why = a.why or first_line + " (TODO: expand)"
                register(slug, a.url, handle, posted, a.tag, why,
                         thread=a.thread, tier=tier)
                registered_now = True
                suffix = f", thread = true, tier = {tier}" if a.thread else ""
                print(f"sources.toml: registered [[x_post]] id = \"{slug}\""
                      f"{suffix}")
    except ArchiveLockBusy as exc:
        sys.exit(f"archive writer lock busy: {exc}")

    if registered_now:
        try:
            refresh_if_installed(SOURCES)
        except (OSError, ValueError) as exc:
            sys.exit(f"registry projection refresh failed after registering "
                     f"{slug!r}: {exc}")

    if not a.keep_tab:
        bridge("close_tab", {})

    # Last, and outside the lock: the conversation capture takes the archive
    # writer lock itself and opens its own tab in its own browser session.
    if a.thread:
        sys.exit(capture_thread_now(slug))


if __name__ == "__main__":
    main()
