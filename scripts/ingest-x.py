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

Uses the capture browser (http://127.0.0.1:10086), which holds the
project's signed-in sessions. READ-ONLY on X: navigate, evaluate,
screenshot. Nothing is posted, followed or liked. Complements the
gallery-dl path (capture-x.sh), which only fetches attached media: this
is the tool that captures the rendered post itself.

Stdlib only, per repo policy. Run through the venv:

  .venv/bin/python scripts/ingest-x.py <url> --id <slug> [--tag t] [--why "..."]

or via just:

  just ingest-x <url> <slug>
"""

import argparse
import base64
import json
import tomllib
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from archive_lock import ArchiveLockBusy, archive_lock
from capture import load_sources, wb_token
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


def registered_id(tweet_id: str) -> str | None:
    """The id this status is already registered under, if any.

    Captures live under the registered id so that the site, which iterates the
    registry, looks in the directory the capture actually wrote. A slug derived
    from the handle would be right often enough to be dangerous.
    """
    try:
        cfg = tomllib.loads(SOURCES.read_text())
    except Exception:
        return None
    for post in cfg.get("x_post", []):
        if tweet_id in post.get("url", ""):
            return post.get("id")
    return None


def already_registered(tweet_id: str, slug: str) -> bool:
    cfg = load_sources()
    for p in cfg.get("source", []) + cfg.get("x_post", []):
        if p.get("id") == slug or tweet_id in p.get("url", ""):
            return True
    return False


def register(slug: str, url: str, handle: str, posted: str, tag: str | None,
             why: str) -> None:
    block = f'''
[[x_post]]
id = "{slug}"
url = "{url}"
author = "{handle}"
posted = "{posted}"
'''
    if tag:
        block += f'tag = "{tag}"\n'
    block += f'why = """\n{why.rstrip()}\n"""\n'
    with open(SOURCES, "a") as fh:
        fh.write(block)


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
    a = ap.parse_args()

    handle, tweet_id = parse_tweet(a.url)
    # An explicit --id wins, then the id this status is already registered
    # under, then a slug from the handle for a post being registered now.
    slug = a.slug or registered_id(tweet_id) or f"{handle.lower()}-{tweet_id}"

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
        if (info.get("found") and info.get("text")
                and info.get("mediaReady") and not info.get("truncated")):
            break
        # A post that scrolled or re-rendered can come back truncated again.
        expanded += expand_truncated(bridge)
        time.sleep(2)
    if not info.get("found") or not info.get("text"):
        sys.exit(f"tweet article not found for @{handle} (deleted? wrong URL?)")
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
                why = a.why or info["text"].splitlines()[0][:200] + " (TODO: expand)"
                register(slug, a.url, handle, posted, a.tag, why)
                print(f"sources.toml: registered [[x_post]] id = \"{slug}\"")
    except ArchiveLockBusy as exc:
        sys.exit(f"archive writer lock busy: {exc}")

    if not a.keep_tab:
        bridge("close_tab", {})


if __name__ == "__main__":
    main()
