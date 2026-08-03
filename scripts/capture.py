#!/usr/bin/env python3
"""Snapshot tracked sources and record when their text changes.

Zero dependencies: stdlib only, Python 3.11+ for tomllib.

The point of this tool is not to mirror pages. It is to answer "when did the
advice change, and what did it say before". So a snapshot is written only when
the extracted text differs from the last one held. Every poll is logged either
way, which is what lets you say "unchanged as of 03:00 UTC" with evidence.

One front door, three fetch backends, one record shape. A source declares its
method with `capture = "http"` (the default, a plain scripted fetch),
`capture = "browser"` (rendered through the capture browser,
for JS-challenged or JS-hydrated pages), or gallery-dl for social posts via
scripts/capture-x.sh. Whichever method fetched it, a capture lands as the same
artefacts: <TS>.txt, a rendered artefact (.html or .pdf), <TS>.meta.json with
the method recorded, a diff on change, and an index.jsonl event.

    capture.py capture [--id ID] [--tier N] [--kind KIND]
                       [--exclude-kind KIND] [--dry-run]
    capture.py status
    capture.py audit          # verify every capture against the record contract
    capture.py log [--limit N]
    capture.py show ID
    capture.py import-dir DIR   # absorb an ad-hoc snapshot directory

Exit 0 is a healthy unchanged run, 10 is a healthy run with changes, 20 is an
incomplete run, and 21 is archive-writer lock contention.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from archive_lock import (
    LOCK_BUSY_EXIT,
    ArchiveLockBusy,
    archive_lock,
)
from response_headers import safe_headers, scrub_geo

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.toml"
SNAPSHOTS = ROOT / "archive" / "snapshots"
DIFFS = ROOT / "archive" / "diffs"
INDEX = ROOT / "archive" / "index.jsonl"
RUNS = ROOT / "archive" / "runs"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "(+https://github.com/cc-vuln/coldcard-rng-incident; historical preservation)"
)
TIMEOUT = 45
POLITE_DELAY = 1.5
INCOMPLETE_EXIT = 20
# Snapshot-style UTC stamp, the same shape every capture filename uses.
TS_RE = re.compile(r"\d{8}T\d{6}Z")

# A poll that failed and a record that is stale are different things, and only
# the second should stop a publication. These control the first: a transient
# fault is retried inside the run rather than being reported as a gap.
FETCH_ATTEMPTS = 3
RETRY_BACKOFF = (2.0, 5.0)

# How often each tier is meant to be polled, mirroring the scheduler's jobs.
# Used to judge staleness, which is what the publication gate now asks about.
TIER_INTERVAL_SECONDS = {1: 30 * 60, 2: 6 * 60 * 60, 3: 6 * 60 * 60}
# Missing three consecutive cycles is decay; missing one is weather.
STALE_CYCLES = 3

# A publisher that refuses this collector repeatedly is a fact about the
# publisher, not a bug here. After this many consecutive refusals the archive
# stops pretending the next direct attempt will work and falls back to Wayback.
WAYBACK_AFTER_REFUSALS = 3


class SourceConfigError(ValueError):
    """The source registry is ambiguous or requests an unknown operation."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------- text

class _Text(HTMLParser):
    """Strip markup to readable text so diffs show editorial change, not noise."""

    SKIP = {"script", "style", "noscript", "svg", "head"}
    BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "header", "footer", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        lines = [ln.strip() for ln in raw.split("\n")]
        return "\n".join(ln for ln in lines if ln)


def extract_text(body: bytes, url: str) -> str:
    try:
        s = body.decode("utf-8", errors="replace")
    except Exception:
        s = body.decode("latin-1", errors="replace")
    # Plain-text sources (raw.githubusercontent, .md, .c) need no stripping.
    if not re.search(r"<\s*(html|body|div|p)\b", s[:4000], re.I):
        return "\n".join(ln.rstrip() for ln in s.splitlines() if ln.strip())
    p = _Text()
    try:
        p.feed(s)
    except Exception:
        return s
    return p.text()


def extract_source_text(body: bytes, url: str, src: dict) -> str:
    """Extract the comparison text declared by one registered source."""

    json_html_field = src.get("json_html_field")
    if json_html_field:
        try:
            payload = json.loads(body)
            html = payload[json_html_field]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourceConfigError(
                f"{src.get('id', '<unknown>')}: cannot extract JSON HTML "
                f"field {json_html_field!r}: {exc}"
            ) from exc
        if not isinstance(html, str):
            raise SourceConfigError(
                f"{src.get('id', '<unknown>')}: JSON HTML field "
                f"{json_html_field!r} is not a string"
            )
        parts: list[str] = []
        for field in src.get("json_text_fields", []):
            try:
                value = payload
                for segment in field.split("."):
                    value = value[segment]
            except (KeyError, TypeError) as exc:
                raise SourceConfigError(
                    f"{src.get('id', '<unknown>')}: cannot extract JSON text "
                    f"field {field!r}: {exc}"
                ) from exc
            if not isinstance(value, str):
                raise SourceConfigError(
                    f"{src.get('id', '<unknown>')}: JSON text field "
                    f"{field!r} is not a string"
                )
            parts.append(extract_text(value.encode("utf-8"), url))
        parts.append(extract_text(html.encode("utf-8"), url))
        return "\n".join(part for part in parts if part)
    if src.get("json_pretty"):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SourceConfigError(
                f"{src.get('id', '<unknown>')}: response is not valid JSON: {exc}"
            ) from exc
        return json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    return extract_text(body, url)


# Change detection hashes a source-specific canonical view.  The held text is
# still the text that was actually extracted, so normalisation suppresses
# recurring presentation churn without rewriting or sanitising old evidence.
RELATIVE_TIME = re.compile(
    r"\b(?:just now|\d+\s*(?:seconds?|minutes?|hours?|days?|weeks?|months?|"
    r"years?|[smhdwy])\s+ago)(?=\W|\d|$)",
    re.IGNORECASE,
)


def _normalise_relative_time(text: str) -> str:
    return RELATIVE_TIME.sub("<relative-time>", text)


def _normalise_fiat_values(text: str) -> str:
    return re.sub(
        r"(?m)^\$[0-9][0-9,]*(?:\.[0-9]+)?(?:/BTC)?$",
        "<live-fiat-value>",
        text,
    )


def _normalise_github_repo_counters(text: str) -> str:
    return re.sub(
        r"(?m)^(Fork|Star|Issues|Pull requests|Security and quality)\n"
        r"(?:[0-9][0-9,.]*[kKmM]?)$",
        r"\1\n<repository-count>",
        text,
    )


def _normalise_github_reactions(text: str) -> str:
    """Remove reaction totals and actor lists without hiding comment edits."""
    emoji = r"(?:👍|👎|😄|🎉|😕|❤️?|🚀|👀)"
    return re.sub(
        rf"(?ms)^{emoji}\n\d+\n"
        rf"[^\n]+ reacted with [^\n]+ emoji\n"
        rf"All reactions\n{emoji}\n\d+ reactions?$",
        "<github-reactions>",
        text,
    )


def _normalise_tftc_related(text: str) -> str:
    marker = "\nKeep reading\n"
    return text.split(marker, 1)[0].rstrip() if marker in text else text


def _normalise_theblock_tickers(text: str) -> str:
    return re.sub(
        r"(?m)^(?:BTC|ETH|SOL|PYTH|LINK)USD\$[^\n]+$",
        "<live-market-ticker>",
        text,
    )


def _normalise_theblock_ticker_shell(text: str) -> str:
    """Treat ticker availability and live rows as one presentation region."""
    return re.sub(
        r"(?ms)^NEW\n(?:"
        r"Live\n(?:<live-market-ticker>\n)+|"
        r"No ticker data available\n"
        r")(?=Latest Crypto News$)",
        "NEW\n<live-market-ticker-region>\n",
        text,
    )


def _normalise_rolling_last_update(text: str) -> str:
    """Remove a page-wide date that advances independently of its content."""
    months = (
        "January|February|March|April|May|June|July|August|September|"
        "October|November|December"
    )
    return re.sub(
        rf"(?m)^(Last update:)\n(?:{months}) \d{{1,2}}, \d{{4}}$",
        r"\1\n<rolling-last-update>",
        text,
    )


def _normalise_reddit_engagement(text: str) -> str:
    # Vote totals and collapsed-reply counts change independently of what the
    # victim or commenters wrote.  Standalone numbers inside prose are left
    # alone unless Reddit rendered them as their own line between comments.
    text = re.sub(
        r"(?m)^-?[0-9][0-9,.]*[kKmM]?$", "<engagement-count>", text
    )
    text = re.sub(
        r"(?m)^\d+ more repl(?:y|ies)$", "<more-replies>", text
    )
    return re.sub(
        r"(?m)^\d+Name$", "<engagement-count>\nName", text
    )


def _normalise_reddit_achievement_badges(text: str) -> str:
    # Achievement badges are publisher chrome Reddit recomputes between polls;
    # they appear, disappear and swap label under the same comment. Drop the
    # whole line: masking it in place would still diff when a badge appears
    # where none was rendered before. Nobody writes "Top 1% Commenter" as a
    # standalone comment line. Kept separate from reddit-engagement because
    # held snapshots record that name in meta.json and the archive audit
    # replays them with the function as it was at capture time.
    return re.sub(
        r"(?m)^[ \t]*Top \d+% (?:Commenter|Poster)\n?", "", text
    )


def _normalise_slipstream_live_state(text: str) -> str:
    """Suppress live chain and fee values without hiding portal wording."""

    text = re.sub(
        r"(?m)^(Minimum submission rate|Current mineable rate)\n[^\n]+$",
        r"\1\n<live-fee-rate>",
        text,
    )
    return re.sub(
        r"(?m)^Current Block Height:\s*[0-9][0-9,]*$",
        "Current Block Height: <live-block-height>",
        text,
    )


def _normalise_android_article(text: str) -> str:
    """Compare the historical post body rather than current blog chrome."""

    start = text.find("14 August 2013")
    end = text.find("\nJoin the discussion on", start)
    if start >= 0 and end > start:
        return text[start:end].rstrip()
    return text


def _normalise_unciphered_article(text: str) -> str:
    """Exclude the rotating WordPress footer after the disclosure body."""

    marker = "\nRss\n"
    return text.split(marker, 1)[0].rstrip() if marker in text else text


def _normalise_tracker_footer_live_state(text: str) -> str:
    """Suppress the snapshot clock and rotating mirror in the footer."""

    text = re.sub(
        r"(?m) Wave 3 from cron snapshot \([^)]*\)\.",
        " Wave 3 from cron snapshot (<snapshot-time>).",
        text,
    )
    # The mirror name is masked only when the line marks it as a fallback; a
    # plain "via mempool.space." line still registers a primary-source switch.
    return re.sub(
        r"(?m)^Balances and transactions via \S+ \(fallback mirror\)\.",
        "Balances and transactions via <fallback-mirror> (fallback mirror).",
        text,
    )


def _normalise_cryptonews_chrome(text: str) -> str:
    """Compare the crypto.news article body, not ticker or sidebar chrome."""

    # The live price ticker renders four times: name, $price, signed 24h
    # change, "<X> price" label. Belt and braces in case the slice below
    # loses its markers in a redesign.
    text = re.sub(
        r"(?m)^([^\n]+\([A-Z0-9]+\))\n\$[0-9][0-9,]*(?:\.[0-9]+)?\n"
        r"-?[0-9]+(?:\.[0-9]+)?\n[^\n]+ price$",
        r"\1\n<live-ticker-price>\n<live-ticker-change>\n<ticker-label>",
        text,
    )
    # Keep the article: from the breadcrumb to the end of the disclaimer.
    # Everything after "Read more about" is rotating sidebar cards and footer.
    start = text.find("Home →")
    end = text.find("\nRead more about\n", start if start >= 0 else 0)
    if start >= 0 and end > start:
        return text[start:end].rstrip()
    return text


def _normalise_newsbitcom_sidebar(text: str) -> str:
    """Drop the rotating recommendation chrome after the article body."""

    # The sidebar region runs from the first of these headers to the end of
    # the page. The blocks reorder between polls, so any of them can be first,
    # including the Related articles block whose age labels also churn.
    match = re.search(
        r"(?m)^(?:Related articles|LATEST NEWS|PRESS RELEASES|"
        r"LATEST PODCASTS)$",
        text,
    )
    return text[:match.start()].rstrip() if match else text


def _normalise_substack_engagement(text: str) -> str:
    """Suppress Substack counters and comment age stamps, keep post text."""

    # Like/comment/restack totals: bare number lines directly above Share.
    # Anchored on the Share line so a bare number inside prose is left alone.
    text = re.sub(
        r"(?m)(?:^\d+\n)+(?=Share$)", "<engagement-count>\n", text
    )
    # Comment ages in Substack short form, a bare line under the author name.
    return re.sub(r"(?m)^\d{1,3}[smhd]$", "<comment-age>", text)


NORMALISERS = {
    "relative-time": _normalise_relative_time,
    "fiat-values": _normalise_fiat_values,
    "github-repo-counters": _normalise_github_repo_counters,
    "github-reactions": _normalise_github_reactions,
    "tftc-related": _normalise_tftc_related,
    "theblock-tickers": _normalise_theblock_tickers,
    "theblock-ticker-shell": _normalise_theblock_ticker_shell,
    "rolling-last-update": _normalise_rolling_last_update,
    "reddit-engagement": _normalise_reddit_engagement,
    "reddit-achievement-badges": _normalise_reddit_achievement_badges,
    "slipstream-live-state": _normalise_slipstream_live_state,
    "android-article": _normalise_android_article,
    "unciphered-article": _normalise_unciphered_article,
    "tracker-footer-live-state": _normalise_tracker_footer_live_state,
    "cryptonews-chrome": _normalise_cryptonews_chrome,
    "newsbitcom-sidebar": _normalise_newsbitcom_sidebar,
    "substack-engagement": _normalise_substack_engagement,
}


# Normalizers bound to a source here rather than in sources.toml. capture_one
# merges them into the source's normalizers list before comparison and before
# meta.json is written, so each new snapshot records the full list that was in
# effect and the archive audit replays it exactly as for config-declared ones.
# canonical_text itself only honours src["normalizers"]: the audit rebuilds
# its comparison source from held meta.json, and auto-merging there would
# recompute old change hashes with filters that did not exist at capture time.
SOURCE_NORMALISERS: dict[str, list[str]] = {
    "coldcard-hack-tracker": ["tracker-footer-live-state"],
    "cryptonews-build-error-38m": ["cryptonews-chrome"],
    "newsbitcom-who-lost-who-at-risk": ["newsbitcom-sidebar"],
    "reddit-drained-timeline": ["reddit-achievement-badges"],
    "reddit-ai-discovery-thread": ["reddit-achievement-badges"],
    "btcpp-dettmer-commit-history": ["substack-engagement"],
}


def source_normalizers(src: dict) -> list[str]:
    """Config-declared normalizers plus any bound in code for the source."""
    names = list(src.get("normalizers", []))
    for name in SOURCE_NORMALISERS.get(src.get("id", ""), []):
        if name not in names:
            names.append(name)
    return names


def canonical_text(text: str, src: dict) -> str:
    """Return the stable comparison view declared by a source."""
    for name in src.get("normalizers", []):
        try:
            text = NORMALISERS[name](text)
        except KeyError as exc:
            raise SourceConfigError(
                f"{src.get('id', '<unknown>')}: unknown normalizer {name!r}"
            ) from exc
    return text


# ------------------------------------------------------------------- config

def validate_sources(cfg: dict) -> None:
    seen: dict[str, str] = {}
    for sid, names in SOURCE_NORMALISERS.items():
        unknown = [name for name in names if name not in NORMALISERS]
        if unknown:
            raise SourceConfigError(
                f"source {sid!r}: invalid normalizers {unknown!r}"
            )
    # One post, one identity. The same URL registered twice creates two source
    # pages, splits its captures between them, and reads to a visitor as two
    # independent records of one thing. This happened once, on 2 Aug 2026.
    seen_urls: dict[str, str] = {}
    for section in ("source", "x_post"):
        for pos, item in enumerate(cfg.get(section, []), 1):
            sid = item.get("id")
            if not isinstance(sid, str) or not sid.strip():
                raise SourceConfigError(
                    f"{section}[{pos}] has no non-empty string id"
                )
            if sid in seen:
                raise SourceConfigError(
                    f"duplicate source id {sid!r} in {seen[sid]} and "
                    f"{section}[{pos}]"
                )
            seen[sid] = f"{section}[{pos}]"
            if not isinstance(item.get("url"), str):
                raise SourceConfigError(f"{section} {sid!r} has no URL")
            url = item["url"]
            if url in seen_urls:
                raise SourceConfigError(
                    f"{sid!r} registers the same URL as {seen_urls[url]!r}: {url}"
                )
            seen_urls[url] = sid

            # A source can stop existing. When the origin no longer serves it, the
            # archive is the only remaining copy, which is the whole point of
            # holding captures; it is not a capture failure and must not mark
            # every later run incomplete. Recording it requires saying what was
            # observed and when, so the claim is checkable rather than asserted.
            gone = item.get("gone", False)
            if not isinstance(gone, bool):
                raise SourceConfigError(f"{sid!r}: gone must be true or false")
            if gone:
                since = item.get("gone_since")
                if not isinstance(since, str) or not TS_RE.fullmatch(since):
                    raise SourceConfigError(
                        f"{sid!r}: gone_since must be a UTC timestamp like "
                        "20260803T021200Z"
                    )
                note = item.get("gone_note")
                if not isinstance(note, str) or not note.strip():
                    raise SourceConfigError(
                        f"{sid!r}: gone_note must record what was observed"
                    )
                status = item.get("gone_status")
                if status is not None and not isinstance(status, str):
                    raise SourceConfigError(
                        f"{sid!r}: gone_status must be a string such as \"404\""
                    )
            elif any(k in item for k in ("gone_since", "gone_note", "gone_status")):
                raise SourceConfigError(
                    f"{sid!r}: gone_* fields require gone = true"
                )

            if section == "source":
                tier = item.get("tier")
                if type(tier) is not int or tier not in (1, 2, 3):
                    raise SourceConfigError(
                        f"source {sid!r}: tier must be an integer from 1 to 3"
                    )
                kind = item.get("kind")
                if not isinstance(kind, str) or not kind.strip():
                    raise SourceConfigError(
                        f"source {sid!r}: kind must be a non-empty string"
                    )
                method = item.get("capture", "http")
                if method not in ("http", "browser"):
                    raise SourceConfigError(
                        f"source {sid!r}: unsupported capture method {method!r}"
                    )
                fetch_url = item.get("fetch_url")
                if fetch_url is not None and (
                    not isinstance(fetch_url, str) or not fetch_url.strip()
                ):
                    raise SourceConfigError(
                        f"source {sid!r}: fetch_url must be a non-empty string"
                    )
                if fetch_url is not None and method != "http":
                    raise SourceConfigError(
                        f"source {sid!r}: fetch_url is supported only for http capture"
                    )
                json_html_field = item.get("json_html_field")
                if json_html_field is not None and (
                    not isinstance(json_html_field, str)
                    or not json_html_field.strip()
                ):
                    raise SourceConfigError(
                        f"source {sid!r}: json_html_field must be a non-empty string"
                    )
                json_pretty = item.get("json_pretty", False)
                if type(json_pretty) is not bool:
                    raise SourceConfigError(
                        f"source {sid!r}: json_pretty must be a boolean"
                    )
                if json_html_field is not None and json_pretty:
                    raise SourceConfigError(
                        f"source {sid!r}: choose json_html_field or json_pretty, not both"
                    )
                json_text_fields = item.get("json_text_fields", [])
                if not isinstance(json_text_fields, list) or any(
                    not isinstance(field, str) or not field.strip()
                    for field in json_text_fields
                ):
                    raise SourceConfigError(
                        f"source {sid!r}: json_text_fields must contain "
                        "non-empty strings"
                    )
                if json_text_fields and json_html_field is None:
                    raise SourceConfigError(
                        f"source {sid!r}: json_text_fields requires json_html_field"
                    )
                if method != "http" and (
                    json_html_field is not None or json_pretty or json_text_fields
                ):
                    raise SourceConfigError(
                        f"source {sid!r}: JSON extraction is supported only for http capture"
                    )
                normalizers = item.get("normalizers", [])
                if not isinstance(normalizers, list) or any(
                    name not in NORMALISERS for name in normalizers
                ):
                    raise SourceConfigError(
                        f"source {sid!r}: invalid normalizers {normalizers!r}"
                    )
                required_text = item.get("required_text", [])
                if not isinstance(required_text, list) or any(
                    not isinstance(marker, str) or not marker.strip()
                    for marker in required_text
                ):
                    raise SourceConfigError(
                        f"source {sid!r}: required_text must contain "
                        "non-empty strings"
                    )


def classify_failure(exc: BaseException) -> str:
    """What kind of failure this is, because they deserve different responses.

    `transient` is weather: resets, timeouts, rate limits and server errors,
    worth retrying. `refused` and `absent` are decisions by the origin, and
    retrying them is both pointless and rude. Keeping them apart is what lets
    the gate stop blocking on noise while still blocking on decay.
    """

    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code in (404, 410):
            return "absent"
        if code == 403:
            return "refused"
        if code == 429 or 500 <= code < 600:
            return "transient"
        return "refused"
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError, OSError)):
        return "transient"
    return "transient"


def load_sources() -> dict:
    with SOURCES.open("rb") as fh:
        cfg = tomllib.load(fh)
    validate_sources(cfg)
    return cfg


def snap_dir(sid: str) -> Path:
    return SNAPSHOTS / sid


def latest_snapshot(sid: str) -> tuple[str, str] | None:
    """Return (timestamp, text) of the most recent stored snapshot."""
    d = snap_dir(sid)
    if not d.is_dir():
        return None
    txts = sorted(d.glob("*.txt"))
    if not txts:
        return None
    return txts[-1].stem, txts[-1].read_text(encoding="utf-8")


def append_event(ev: dict) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with INDEX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, sort_keys=True) + "\n")


# ------------------------------------------------------------------ capture

def fetch(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.headers.items()}
        headers["_status"] = str(resp.status)
    # Some origins render the collector's own location into the page. Remove
    # it here, before anything hashes or stores these bytes.
    text = body.decode("utf-8", errors="ignore")
    scrubbed, hits = scrub_geo(text)
    if hits:
        body = scrubbed.encode("utf-8")
    # Narrowed here rather than at write time: a routing header that never
    # enters the process cannot be logged, diffed or persisted by mistake.
    return body, safe_headers(headers)


# ------------------------------------------------------------------- browser
# Some sources only render inside a real browser session: Reddit answers
# scripted fetches with a JS challenge, and JS-hydrated apps return an empty
# shell. For those, capture.py drives the capture browser, a local daemon that
# controls the owner's own browser, over plain HTTP. Stdlib only, read-only by
# construction: navigate, evaluate, save_as_pdf and close_tab are the whole
# vocabulary, so this can never post, follow or like anything.
#
# The rendered artefact is a PDF standing in for raw HTML, and the extracted
# text is <main> innerText, i.e. what a logged-in human would see. If the
# daemon or browser is unavailable the source is skipped with an event, which
# is a recorded gap rather than a corrupted record. The overall run exits 20.

WB_PORT = os.environ.get("WEBBRIDGE_PORT", "10086")
WB_URL = f"http://127.0.0.1:{WB_PORT}/command"
WB_SESSION = "coldcard-archive"
# Ceiling on the wait for required_text to appear, not a fixed delay: a page
# that satisfies its markers returns on the first poll. Client-rendered sources
# that fetch chain data per row can take well over half a minute under a full
# run, and failing them wastes a capture window rather than protecting anything.
BROWSER_READY_TIMEOUT = 90

class BrowserUnavailable(RuntimeError):
    pass


def wb_cmd(action: str, args: dict | None = None, timeout: int = 60) -> dict:
    payload = json.dumps(
        {"action": action, "args": args or {}, "session": WB_SESSION}
    ).encode()
    req = urllib.request.Request(
        WB_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read())
    data = out.get("data", {})
    if not out.get("ok") or not data.get("success", True):
        raise BrowserUnavailable(f"webbridge {action} rejected: {out!r}"[:300])
    return data


def wb_available() -> bool:
    """Is the capture browser reachable?

    Deliberately does not start it. It is a long-lived service (`just
    capture-browser`, or the systemd unit in capture-browser/), and a capture
    run that spawns browsers is a capture run that leaks them. When it is not
    there the source is skipped, which is a recorded gap rather than a
    corrupted record, and the run exits 20.
    """
    try:
        wb_cmd("list_tabs", timeout=10)
        return True
    except Exception:
        return False


def fetch_browser(
    url: str,
    scroll: bool,
    pdf_path: Path | None = None,
    required_text: tuple[str, ...] = (),
) -> tuple[str, int]:
    """Render url in the owner's browser. Returns (visible text, pdf bytes).

    The PDF, when asked for, is saved while the tab is still open: closing
    first leaves the daemon with no current tab and the render fails. Caller
    decides whether to keep the file, so dry runs and unchanged polls pass
    pdf_path=None and no artefact is written.
    """
    if not wb_available():
        raise BrowserUnavailable("webbridge daemon or browser not reachable")
    wb_cmd("navigate", {"url": url, "newTab": True,
                        "group_title": "COLDCARD archive capture"})
    try:
        time.sleep(5)  # let challenges and hydration settle
        deadline = time.monotonic() + BROWSER_READY_TIMEOUT
        text = ""
        while True:
            data = wb_cmd("evaluate", {"code":
                "(() => { const m = document.querySelector('main') || document.body;"
                " return m.innerText; })()"})
            text = data.get("value", "")
            if not required_text or all(marker in text for marker in required_text):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(2)
        if scroll:
            for _ in range(3):
                wb_cmd("evaluate", {"code":
                    "window.scrollTo(0, document.body.scrollHeight); 'scrolled'"})
                time.sleep(2)
            data = wb_cmd("evaluate", {"code":
                "(() => { const m = document.querySelector('main') || document.body;"
                " return m.innerText; })()"})
            text = data.get("value", "")
        pdf_bytes = 0
        if pdf_path is not None:
            wb_cmd("save_as_pdf", {"paper_format": "a4", "print_background": True,
                                   "path": str(pdf_path)}, timeout=180)
            pdf_bytes = pdf_path.stat().st_size
        return text, pdf_bytes
    finally:
        try:
            wb_cmd("close_tab")
        except Exception:
            pass


def normalize_browser_text(text: str) -> str:
    return _normalise_relative_time(text)


_EVENTS_BY_SOURCE: dict[str, list[dict]] | None = None


def events_by_source() -> dict[str, list[dict]]:
    """Every recorded attempt, grouped by source id, in the order written.

    Read once per process. The index is append-only, so a run's own events are
    not in the map it started with, which is what we want: decisions are made
    against the history that existed before this run.
    """

    global _EVENTS_BY_SOURCE
    if _EVENTS_BY_SOURCE is None:
        grouped: dict[str, list[dict]] = {}
        if INDEX.exists():
            with INDEX.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sid = ev.get("id")
                    if isinstance(sid, str):
                        grouped.setdefault(sid, []).append(ev)
        _EVENTS_BY_SOURCE = grouped
    return _EVENTS_BY_SOURCE


SUCCESS_EVENTS = ("unchanged", "changed", "first")


def consecutive_refusals(sid: str) -> int:
    """Refusals since the last time this source was actually captured."""

    count = 0
    for ev in reversed(events_by_source().get(sid, [])):
        if ev.get("event") in SUCCESS_EVENTS:
            break
        if _was_refusal(ev):
            count += 1
            continue
        break
    return count


def _was_refusal(ev: dict) -> bool:
    """Whether a recorded attempt was the origin refusing us.

    Events written before failures were classified carry only the raw error
    string, so a standing refusal recorded last week would otherwise never
    count toward the fallback threshold and the feature would take days to
    become useful on exactly the source it was built for.
    """

    if ev.get("failure"):
        return ev["failure"] == "refused"
    return ev.get("event") == "error" and "403" in str(ev.get("error", ""))


def _try_wayback(src: dict, result: dict, kind: str, dry: bool):
    """Fall back to Wayback for a source whose origin keeps refusing us.

    Only for refusals, and only once a refusal is clearly the standing state
    rather than one bad response. A recovered capture is stored like any other
    but carries `provenance: wayback`, because a reader must always be able to
    tell what this project fetched from the origin itself.
    """

    if kind != "refused" or src.get("capture", "http") != "http":
        return None
    if consecutive_refusals(src["id"]) + 1 < WAYBACK_AFTER_REFUSALS:
        return None

    try:
        import wayback
    except ImportError:
        return None
    snapshot = wayback.newest_snapshot(src["url"])
    if snapshot is None:
        return None
    ts14, body = snapshot
    try:
        text = extract_source_text(body, src["url"], src)
    except Exception:
        return None
    if not text.strip():
        return None

    result.update(
        provenance="wayback",
        wayback_timestamp=wayback.wb_ts_to_ours(ts14),
        failure=kind,
        origin_refused=True,
    )
    print(
        f"  {src['id']:<24} WAYBACK  origin refusing; replayed "
        f"{wayback.wb_ts_to_ours(ts14)}"
    )
    return body, {"_status": "200", "_provenance": "wayback"}, text


def capture_one(src: dict, dry: bool = False) -> dict:
    # Code-bound normalizers (SOURCE_NORMALISERS) merge in here so comparison,
    # diffing and the recorded meta.json all use one effective list.
    src = {**src, "normalizers": source_normalizers(src)}
    sid, url = src["id"], src["url"]
    fetch_url = src.get("fetch_url", url)
    method = src.get("capture", "http")
    ts = now()
    result = {"ts": ts, "id": sid, "url": url, "event": "checked", "method": method}

    body, headers, text = None, {}, ""
    tmp_pdf: Path | None = None
    pdf_bytes = 0
    attempts = 0
    # A transient fault is retried here rather than being recorded as a gap in
    # the record. Refusals and 404s are the origin's decision, so they are taken
    # at face value the first time: retrying them is pointless and impolite.
    while True:
        attempts += 1
        try:
            if method == "browser":
                # Rendered to a temp path first; kept only if the text changed.
                tmp_pdf = Path(tempfile.gettempdir()) / f"coldcard-capture-{sid}-{ts}.pdf"
                text, pdf_bytes = fetch_browser(
                    url,
                    bool(src.get("browser_scroll")),
                    None if dry else tmp_pdf,
                    tuple(src.get("required_text", [])),
                )
                text = normalize_browser_text(text)
            else:
                body, headers = fetch(fetch_url)
                text = extract_source_text(body, fetch_url, src)
            break
        except BrowserUnavailable as e:
            # The browser route is down (daemon off, browser closed, target
            # crashed). A crashed target usually recovers because the shim
            # relaunches, so it is worth one more go before recording the gap.
            if tmp_pdf is not None:
                tmp_pdf.unlink(missing_ok=True)
                tmp_pdf = None
            if attempts < FETCH_ATTEMPTS:
                time.sleep(RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)])
                continue
            result.update(
                event="skipped", failure="unavailable",
                attempts=attempts, error=str(e)[:300],
            )
            print(f"  {sid:<24} SKIPPED  {str(e)[:70]}")
            if not dry:
                append_event(result)
            return result
        except Exception as e:
            if tmp_pdf is not None:
                tmp_pdf.unlink(missing_ok=True)
                tmp_pdf = None
            kind = classify_failure(e)
            if kind == "transient" and attempts < FETCH_ATTEMPTS:
                time.sleep(RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)])
                continue
            if isinstance(e, (urllib.error.URLError, OSError, TimeoutError)):
                detail = str(e)[:300]
            else:
                detail = f"{type(e).__name__}: {e}"[:300]
            recovered = _try_wayback(src, result, kind, dry)
            if recovered is not None:
                body, headers, text = recovered
                break
            result.update(event="error", failure=kind, attempts=attempts, error=detail)
            suffix = f" (after {attempts} attempts)" if attempts > 1 else ""
            print(f"  {sid:<24} ERROR  [{kind}] {detail[:60]}{suffix}")
            if not dry:
                append_event(result)
            return result

    # Some sites answer a scripted fetch with a challenge or consent page that
    # parses fine and says nothing. Storing one would silently corrupt the record,
    # so a source can declare the minimum text it should ever legitimately return.
    floor = src.get("min_chars", 0)
    if floor and len(text) < floor:
        if tmp_pdf is not None:
            tmp_pdf.unlink(missing_ok=True)
        result.update(event="blocked", failure="challenged", chars=len(text), min_chars=floor)
        print(f"  {sid:<24} BLOCKED  {len(text)} chars < {floor} floor, not stored")
        if not dry:
            append_event(result)
        return result

    missing_markers = [
        marker for marker in src.get("required_text", []) if marker not in text
    ]
    if missing_markers:
        if tmp_pdf is not None:
            tmp_pdf.unlink(missing_ok=True)
        result.update(event="blocked", failure="challenged",
                      missing_required_text=missing_markers)
        print(
            f"  {sid:<24} BLOCKED  required rendered content did not load"
        )
        if not dry:
            append_event(result)
        return result

    t_hash = sha256(text.encode())
    stable_text = canonical_text(text, src)
    stable_hash = sha256(stable_text.encode())
    result["text_sha256"] = t_hash
    result["change_sha256"] = stable_hash
    if src.get("normalizers"):
        result["normalizers"] = src["normalizers"]
    if body is not None:
        result.update(raw_sha256=sha256(body), bytes=len(body),
                      http_status=headers.get("_status"))
        if fetch_url != url:
            result["fetch_url"] = fetch_url

    prev = latest_snapshot(sid)
    if prev and sha256(canonical_text(prev[1], src).encode()) == stable_hash:
        if tmp_pdf is not None:
            tmp_pdf.unlink(missing_ok=True)
        result["event"] = "unchanged"
        print(f"  {sid:<24} same   {t_hash[:12]}")
        if not dry:
            append_event(result)
        return result

    result["event"] = "first" if prev is None else "changed"
    diff: list[str] | None = None
    if prev:
        # Calculate the stable-view diff before the dry-run return so a
        # read-only poll reports useful line counts without writing artefacts.
        diff = list(difflib.unified_diff(
            canonical_text(prev[1], src).splitlines(), stable_text.splitlines(),
            fromfile=f"{sid}@{prev[0]}", tofile=f"{sid}@{ts}",
            lineterm="", n=3))
        added = sum(
            1 for line in diff
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = sum(
            1 for line in diff
            if line.startswith("-") and not line.startswith("---")
        )
        result.update(diff_added=added, diff_removed=removed, prev_ts=prev[0])

    if dry:
        delta = (
            f"  +{result['diff_added']} -{result['diff_removed']}"
            if prev else ""
        )
        print(
            f"  {sid:<24} {result['event'].upper()}"
            f" (dry run, not written){delta}"
        )
        return result

    d = snap_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ts}.txt").write_text(text, encoding="utf-8")
    if method == "browser":
        # The rendered PDF stands in for raw HTML, which a browser capture
        # does not otherwise have. Rendered in the same tab session as the
        # text, moved in from the temp path only because the text changed.
        tmp_pdf.replace(d / f"{ts}.pdf")
        result["bytes"] = pdf_bytes
        result["renderer"] = "capture-browser"
    else:
        (d / f"{ts}.html").write_bytes(body)
    (d / f"{ts}.meta.json").write_text(
        json.dumps({**result, "headers": safe_headers(headers)},
                   indent=2, sort_keys=True),
        encoding="utf-8")

    if prev:
        # Diffs use the same stable comparison view as change detection.  Raw
        # extracted text remains in the snapshots on both sides.
        dd = DIFFS / sid
        dd.mkdir(parents=True, exist_ok=True)
        assert diff is not None
        (dd / f"{ts}.diff").write_text("\n".join(diff) + "\n", encoding="utf-8")
        print(
            f"  {sid:<24} CHANGED  +{result['diff_added']}"
            f" -{result['diff_removed']}  -> archive/diffs/{sid}/{ts}.diff"
        )
    else:
        print(f"  {sid:<24} first  {t_hash[:12]}  ({len(text)} chars)")

    append_event(result)
    return result


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def _run_result_path(args, started: str) -> Path | None:
    if args.result_file:
        return Path(args.result_file).expanduser().resolve()
    if args.dry_run:
        return None
    return RUNS / f"{started}-p{os.getpid()}.json"


def _parse_ts(ts: str) -> dt.datetime:
    return dt.datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def freshness(srcs: list[dict], results: list[dict], now_ts: str) -> list[dict]:
    """How far behind each source that did not capture this run has fallen.

    The question that protects a reader is not "did a poll just fail" but "is
    the held record still current enough to publish". A source is allowed to
    miss `STALE_CYCLES` of its own polling interval before that answer changes,
    so one refused request or one rate limit is weather, and a source failing
    every cycle for hours is decay. The second used to be invisible: any single
    failure produced the same undifferentiated signal as sustained silence.
    """

    outcome = {r["id"]: r for r in results}
    now_dt = _parse_ts(now_ts)
    report: list[dict] = []
    for src in srcs:
        sid = src["id"]
        current = outcome.get(sid)
        if current is not None and current["event"] in SUCCESS_EVENTS:
            continue
        if current is None:
            continue

        last_success = None
        for ev in reversed(events_by_source().get(sid, [])):
            if ev.get("event") in SUCCESS_EVENTS and isinstance(ev.get("ts"), str):
                last_success = ev["ts"]
                break

        interval = TIER_INTERVAL_SECONDS.get(src.get("tier"), 6 * 60 * 60)
        budget = interval * STALE_CYCLES
        if last_success is None:
            # Never captured, and it just failed again. There is no history that
            # says this ever worked, so it cannot be called merely behind.
            report.append({
                "id": sid, "failure": current.get("failure"),
                "last_success": None, "age_seconds": None,
                "budget_seconds": budget, "stale": True,
            })
            continue

        age = (now_dt - _parse_ts(last_success)).total_seconds()
        report.append({
            "id": sid, "failure": current.get("failure"),
            "last_success": last_success, "age_seconds": int(age),
            "budget_seconds": budget, "stale": age > budget,
        })
    return report


def _capture_exit(results: list[dict], stale_ids: set[str] | None = None) -> int:
    """Exit 20 means the record is behind, not that a request failed.

    `stale_ids` is the set judged overdue by `freshness`. Passing None keeps the
    conservative reading for callers that have not computed it: any failure
    counts.
    """

    if stale_ids is None:
        if any(r["event"] in ("error", "blocked", "skipped") for r in results):
            return INCOMPLETE_EXIT
    elif stale_ids:
        return INCOMPLETE_EXIT
    if any(r["event"] in ("changed", "first") for r in results):
        return 10
    return 0


def cmd_capture(args) -> int:
    started = now()
    result_path = _run_result_path(args, started)
    try:
        cfg = load_sources()
    except (OSError, tomllib.TOMLDecodeError, SourceConfigError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(f"source registry error: {detail}", file=sys.stderr)
        payload = {
            "schema": 1,
            "command": "capture",
            "started_at": started,
            "finished_at": now(),
            "dry_run": args.dry_run,
            "outcome": "config-error",
            "exit_code": 2,
            "counts": {"config_error": 1},
            "events": [{"event": "config-error", "error": detail}],
        }
        if result_path:
            _write_json_atomic(result_path, payload)
            print(f"run result: {result_path}")
        return 2

    srcs = cfg.get("source", [])
    if args.id:
        srcs = [s for s in srcs if s["id"] == args.id]
        if not srcs:
            selection = {
                "id": args.id,
                "tier": args.tier,
                "kind": args.kind,
                "exclude_kind": args.exclude_kind,
            }
            print(f"no source with id {args.id!r}", file=sys.stderr)
            if result_path:
                _write_json_atomic(result_path, {
                    "schema": 1,
                    "command": "capture",
                    "started_at": started,
                    "finished_at": now(),
                    "dry_run": args.dry_run,
                    "selection": selection,
                    "outcome": "config-error",
                    "exit_code": 2,
                    "counts": {"config_error": 1},
                    "events": [{"event": "config-error",
                                "error": f"unknown source id {args.id!r}"}],
                })
            return 2
    if args.tier is not None:
        srcs = [s for s in srcs if s.get("tier") == args.tier]
    if args.kind:
        srcs = [s for s in srcs if s.get("kind") == args.kind]
    if args.exclude_kind:
        srcs = [s for s in srcs if s.get("kind") != args.exclude_kind]
    if not srcs:
        selection = {
            "id": args.id,
            "tier": args.tier,
            "kind": args.kind,
            "exclude_kind": args.exclude_kind,
        }
        detail = f"source selection matched no sources: {selection!r}"
        print(detail, file=sys.stderr)
        if result_path:
            _write_json_atomic(result_path, {
                "schema": 1,
                "command": "capture",
                "started_at": started,
                "finished_at": now(),
                "dry_run": args.dry_run,
                "selection": selection,
                "outcome": "config-error",
                "exit_code": 2,
                "counts": {"config_error": 1},
                "events": [{"event": "config-error", "error": detail}],
            })
        return 2

    # Sources whose origin no longer serves them are not polled. Continuing to
    # request a URL that has been withdrawn is both rude and pointless, and
    # counting the 404 as a failure would mark every run incomplete for as long
    # as the archive keeps the record, which is meant to be forever.
    retired = [s for s in srcs if s.get("gone")]
    srcs = [s for s in srcs if not s.get("gone")]
    if retired:
        print(f"{len(retired)} source(s) no longer served upstream, not polled:")
        for s in retired:
            status = f" {s['gone_status']}" if s.get("gone_status") else ""
            print(f"  {s['id']}  GONE{status}  since {s['gone_since']}")
        print()

    print(f"capture {started}  ({len(srcs)} sources)")
    results: list[dict] = []
    changed = []
    for i, s in enumerate(srcs):
        r = capture_one(s, dry=args.dry_run)
        results.append(r)
        if r["event"] in ("changed", "first"):
            changed.append(r)
        if i < len(srcs) - 1:
            time.sleep(POLITE_DELAY)

    # Judged before anything is reported, because the failure summary states
    # each source's margin and the exit code depends on the same answer.
    fresh = freshness(srcs, results, started)
    stale_ids = {f["id"] for f in fresh if f["stale"]}

    print()
    if changed:
        print(f"{len(changed)} source(s) changed:")
        for c in changed:
            if c["event"] == "changed":
                print(f"  {c['id']}  +{c.get('diff_added',0)} -{c.get('diff_removed',0)}")
            else:
                print(f"  {c['id']}  (first capture)")
    else:
        print("no changes")

    failures = [
        r for r in results if r["event"] in ("error", "blocked", "skipped")
    ]
    if failures:
        print(f"{len(failures)} source(s) incomplete:")
        for failure in failures:
            if failure.get("error"):
                detail = failure["error"]
            elif failure.get("missing_required_text"):
                detail = "missing " + ", ".join(failure["missing_required_text"])
            elif failure.get("min_chars"):
                detail = f"{failure.get('chars')} chars < {failure['min_chars']} floor"
            else:
                detail = "no detail recorded"
            kind = failure.get("failure")
            label = f"{failure['event'].upper()}" + (f" [{kind}]" if kind else "")
            print(f"  {failure['id']}  {label}  {detail}")

        # Say how far behind each one actually is, so a passing run still shows
        # its margin and a blocking one shows why it blocked.
        for entry in fresh:
            if entry["age_seconds"] is None:
                print(f"    {entry['id']}: never captured — blocks publication")
                continue
            behind = entry["age_seconds"] // 60
            allowed = entry["budget_seconds"] // 60
            verdict = "OVERDUE, blocks publication" if entry["stale"] else "within budget"
            print(
                f"    {entry['id']}: last captured {behind} min ago, "
                f"budget {allowed} min — {verdict}"
            )

    exit_code = _capture_exit(results, stale_ids)
    counts: dict[str, int] = {}
    if retired:
        counts["gone"] = len(retired)
    for result in results:
        event = result["event"]
        counts[event] = counts.get(event, 0) + 1
    outcome = (
        "incomplete"
        if exit_code == INCOMPLETE_EXIT
        else "changed"
        if exit_code == 10
        else "unchanged"
    )
    payload = {
        "schema": 1,
        "command": "capture",
        "started_at": started,
        "finished_at": now(),
        "dry_run": args.dry_run,
        "selection": {
            "id": args.id,
            "tier": args.tier,
            "kind": args.kind,
            "exclude_kind": args.exclude_kind,
        },
        "outcome": outcome,
        "exit_code": exit_code,
        "counts": counts,
        "events": results,
        "freshness": fresh,
        "gone": [
            {
                "id": s["id"],
                "since": s["gone_since"],
                "status": s.get("gone_status"),
                "note": s["gone_note"],
            }
            for s in retired
        ],
    }
    if result_path:
        _write_json_atomic(result_path, payload)
        try:
            shown = result_path.relative_to(ROOT)
        except ValueError:
            shown = result_path
        print(f"run result: {shown}")

    # 10 remains exclusively "healthy run with changes".  An incomplete run
    # exits 20, even if one of the other sources changed; the structured result
    # preserves both facts for the notifier.
    return exit_code


# ------------------------------------------------------------------ reports

def cmd_audit(args) -> int:
    """Check every held capture against the unified record contract.

    A capture is <TS>.txt plus <TS>.meta.json (ts matches filename, recorded
    hash matches the file), plus a rendered artefact (.pdf for browser,
    .html for http), plus a diff whenever the text moved from the previous
    snapshot. index.jsonl changed/first events must have their snapshot.
    Exits 1 if anything is off, so it can gate a publish.
    """
    problems: list[str] = []
    try:
        cfg = load_sources()
        by_id = {source["id"]: source for source in cfg.get("source", [])}
    except (OSError, tomllib.TOMLDecodeError, SourceConfigError) as exc:
        problems.append(f"sources.toml: {type(exc).__name__}: {exc}")
        by_id = {}
    for d in sorted(SNAPSHOTS.iterdir()):
        if not d.is_dir():
            continue
        prev_hash = None
        for t in sorted(d.glob("*.txt")):
            ts = t.stem
            meta_p = d / f"{ts}.meta.json"
            if not meta_p.exists():
                problems.append(f"{d.name}/{ts}: txt without meta.json")
                continue
            meta = json.loads(meta_p.read_text())
            if meta.get("ts") != ts:
                problems.append(f"{d.name}/{ts}: meta ts mismatch")
            actual = sha256(t.read_bytes())
            if meta.get("text_sha256") and meta["text_sha256"] != actual:
                problems.append(f"{d.name}/{ts}: text_sha256 mismatch")
            if meta.get("change_sha256") and d.name in by_id:
                comparison_source = {
                    "id": d.name,
                    "normalizers": meta.get("normalizers", []),
                }
                stable = sha256(
                    canonical_text(
                        t.read_text(encoding="utf-8"), comparison_source
                    ).encode()
                )
                if meta["change_sha256"] != stable:
                    problems.append(f"{d.name}/{ts}: change_sha256 mismatch")
            method = meta.get("method", "http")
            if method not in ("http", "browser", "gallery-dl"):
                problems.append(f"{d.name}/{ts}: nonstandard method {method!r}")
            if method == "browser" or meta.get("renderer"):
                if not (d / f"{ts}.pdf").exists():
                    problems.append(f"{d.name}/{ts}: browser capture without PDF")
            elif method == "http" and not meta.get("imported_from") \
                    and meta.get("provenance") != "wayback":
                if not (d / f"{ts}.html").exists():
                    problems.append(f"{d.name}/{ts}: http capture without HTML")
            if prev_hash and actual != prev_hash \
                    and not (DIFFS / d.name / f"{ts}.diff").exists():
                problems.append(f"{d.name}/{ts}: text moved but no diff")
            prev_hash = actual

    if INDEX.exists():
        for ln, line in enumerate(
                INDEX.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                problems.append(f"index.jsonl line {ln}: invalid JSON")
                continue
            if ev.get("event") in ("changed", "first") and \
                    not (SNAPSHOTS / ev["id"] / f"{ev['ts']}.txt").exists():
                problems.append(
                    f"index.jsonl line {ln}: {ev['event']} without snapshot"
                    f" ({ev['id']}/{ev['ts']})")

    if RUNS.is_dir():
        for run in sorted(RUNS.glob("*.json")):
            try:
                payload = json.loads(run.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"{run.relative_to(ROOT)}: invalid JSON ({exc})")
                continue
            required = {"schema", "command", "started_at", "finished_at",
                        "outcome", "exit_code", "counts", "events"}
            missing = sorted(required - payload.keys())
            if missing:
                problems.append(
                    f"{run.relative_to(ROOT)}: missing {', '.join(missing)}"
                )

    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(" -", p)
        return 1
    print("audit ok: every capture conforms to the unified contract")
    return 0


def cmd_status(args) -> int:
    cfg = load_sources()
    print(f"{'ID':<24} {'TIER':<5} {'SNAPS':<6} {'LAST CHANGE':<18} ORG")
    print("-" * 78)
    for s in cfg.get("source", []):
        d = snap_dir(s["id"])
        snaps = sorted(d.glob("*.txt")) if d.is_dir() else []
        last = snaps[-1].stem if snaps else "never"
        print(f"{s['id']:<24} {s.get('tier','-'):<5} {len(snaps):<6} {last:<18} {s.get('org','')}")
    xs = cfg.get("x_post", [])
    if xs:
        print(f"\n{len(xs)} X posts registered (capture via scripts/capture-x.sh)")
    return 0


def cmd_log(args) -> int:
    if not INDEX.exists():
        print("no events yet")
        return 0
    evs = [json.loads(l) for l in INDEX.read_text(encoding="utf-8").splitlines() if l.strip()]
    evs = [e for e in evs if e.get("event") in ("changed", "first", "error", "blocked", "skipped")]
    for e in evs[-args.limit:]:
        tag = {"changed": "CHANGED", "first": "FIRST  ", "error": "ERROR  ",
               "blocked": "BLOCKED", "skipped": "SKIPPED"}[e["event"]]
        extra = ""
        if e["event"] == "changed":
            extra = f"  +{e.get('diff_added',0)} -{e.get('diff_removed',0)}"
        elif e["event"] == "error":
            extra = f"  {e.get('error','')[:60]}"
        print(f"{e['ts']}  {tag}  {e['id']}{extra}")
    return 0


def cmd_show(args) -> int:
    d = snap_dir(args.id)
    if not d.is_dir():
        print(f"no snapshots for {args.id!r}")
        return 2
    snaps = sorted(d.glob("*.txt"))
    print(f"{args.id}: {len(snaps)} snapshot(s)")
    for s in snaps:
        meta = d / f"{s.stem}.meta.json"
        info = json.loads(meta.read_text()) if meta.exists() else {}
        print(f"  {s.stem}  {info.get('text_sha256','')[:16]}  "
              f"{info.get('bytes','?')} bytes  {info.get('event','')}")
    dd = DIFFS / args.id
    if dd.is_dir():
        for f in sorted(dd.glob("*.diff")):
            print(f"  diff -> {f.relative_to(ROOT)}")
    return 0


def cmd_import_dir(args) -> int:
    """Absorb an ad-hoc capture directory (name.html files) as the first snapshot."""
    src_dir = Path(args.dir).resolve()
    ts = args.ts or src_dir.name
    if not re.fullmatch(r"\d{8}T\d{6}Z", ts):
        print(f"cannot infer timestamp from {ts!r}; pass --ts", file=sys.stderr)
        return 2
    cfg = load_sources()
    by_id = {s["id"]: s for s in cfg.get("source", [])}
    alias = {"coinkite-backgrounder": "coinkite-backgrounder",
             "coinkite-mk3-advisory": "coinkite-mk3-advisory",
             "coinkite-blog-index": "coinkite-blog-index",
             "block-disclosure": "block-disclosure",
             "cc-changelog": "cc-changelog",
             "cc-history-mk3": "cc-history-mk3",
             "cc-history-mk": "cc-history-mk",
             "cc-history-q": "cc-history-q",
             "cc-downloads": "coldcard-downloads"}
    n = 0
    for f in sorted(src_dir.glob("*.html")):
        sid = alias.get(f.stem)
        if not sid or sid not in by_id:
            print(f"  skip {f.name} (no matching source id)")
            continue
        d = snap_dir(sid)
        d.mkdir(parents=True, exist_ok=True)
        if (d / f"{ts}.txt").exists():
            print(f"  {sid}: {ts} already imported")
            continue
        body = f.read_bytes()
        text = extract_text(body, by_id[sid]["url"])
        (d / f"{ts}.txt").write_text(text, encoding="utf-8")
        (d / f"{ts}.html").write_bytes(body)
        (d / f"{ts}.meta.json").write_text(json.dumps({
            "ts": ts, "id": sid, "url": by_id[sid]["url"], "event": "first",
            "text_sha256": sha256(text.encode()), "raw_sha256": sha256(body),
            "bytes": len(body), "imported_from": str(src_dir),
            "note": "backfilled from an ad-hoc emergency capture",
        }, indent=2, sort_keys=True), encoding="utf-8")
        append_event({"ts": ts, "id": sid, "url": by_id[sid]["url"], "event": "first",
                      "text_sha256": sha256(text.encode()), "raw_sha256": sha256(body),
                      "bytes": len(body), "backfilled": True})
        print(f"  {sid:<24} imported {len(text)} chars")
        n += 1
    print(f"\nimported {n} snapshot(s) at {ts}")
    return 0


def cmd_record_run(args) -> int:
    """Append changed events from a structured run to archive/CHANGES.md.

    This is a separate locked write because capture releases the archive lock
    before the notification wrapper formats and delivers its alert.
    """
    path = Path(args.result_file).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read run result {path}: {exc}", file=sys.stderr)
        return 2
    changed = [
        event for event in payload.get("events", [])
        if event.get("event") in ("changed", "first")
    ]
    if not changed:
        return 0
    ts = payload.get("started_at")
    if not isinstance(ts, str):
        print("run result has no started_at", file=sys.stderr)
        return 2
    changes_path = ROOT / "archive" / "CHANGES.md"
    existing = changes_path.read_text(encoding="utf-8") if changes_path.exists() else ""
    heading = f"## {ts}"
    if heading in existing.splitlines():
        print(f"changes already recorded for {ts}")
        return 0
    lines = ["", heading, ""]
    for event in changed:
        if event["event"] == "changed":
            detail = (
                f"{event['id']}  +{event.get('diff_added', 0)} "
                f"-{event.get('diff_removed', 0)}"
            )
        else:
            detail = f"{event['id']}  (first capture)"
        lines.append(f"- {detail}")
    with changes_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"recorded {len(changed)} change(s) in {changes_path.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="poll sources, store on change")
    c.add_argument("--id"); c.add_argument("--tier", type=int)
    c.add_argument("--kind")
    c.add_argument("--exclude-kind")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument(
        "--result-file",
        help="write the structured run result here (default: archive/runs/)",
    )
    c.set_defaults(fn=cmd_capture)

    s = sub.add_parser("status", help="what is tracked and when it last moved")
    s.set_defaults(fn=cmd_status)

    a = sub.add_parser("audit", help="verify every capture against the record contract")
    a.set_defaults(fn=cmd_audit)

    l = sub.add_parser("log", help="chronological change events")
    l.add_argument("--limit", type=int, default=40)
    l.set_defaults(fn=cmd_log)

    sh = sub.add_parser("show", help="snapshot history for one source")
    sh.add_argument("id"); sh.set_defaults(fn=cmd_show)

    im = sub.add_parser("import-dir", help="backfill an ad-hoc capture directory")
    im.add_argument("dir"); im.add_argument("--ts")
    im.set_defaults(fn=cmd_import_dir)

    rr = sub.add_parser("record-run", help="append a run's changes to CHANGES.md")
    rr.add_argument("result_file")
    rr.set_defaults(fn=cmd_record_run)

    args = ap.parse_args()
    exclusive = args.cmd in ("import-dir", "record-run") or (
        args.cmd == "capture" and not args.dry_run
    )
    shared = args.cmd == "audit"
    if os.environ.get("COLDCARD_ARCHIVE_LOCK_HELD") == "1":
        return args.fn(args)
    try:
        if exclusive:
            with archive_lock(f"capture.py {args.cmd}"):
                return args.fn(args)
        if shared:
            with archive_lock(f"capture.py {args.cmd}", shared=True):
                return args.fn(args)
        return args.fn(args)
    except ArchiveLockBusy as exc:
        print(f"archive writer lock busy: {exc}", file=sys.stderr)
        return LOCK_BUSY_EXIT


if __name__ == "__main__":
    sys.exit(main())
