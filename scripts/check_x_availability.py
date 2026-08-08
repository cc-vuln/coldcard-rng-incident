#!/usr/bin/env python3
"""Re-check that registered X posts are still observable, through the capture browser.

The archive exists because parties edit and delete: a captured post that
vanishes from X is the social-media equivalent of the Mk3 advisory revision.
This lane navigates each registered [[x_post]] permalink, classifies what X
served, and records the outcome. It never edits the registry and never marks
anything gone; it observes, records and alerts.

Absence is not deletion (docs/design/x-thread-capture.md section 6). A single
`deleted` or `unavailable-other` observation is recorded and alerted at info;
only two consecutive observations, from separate runs, escalate to a warning
("held post unavailable twice; likely withdrawn"). Account-state outcomes
(suspended, protected, restricted) are recorded and alerted at info on
transition; they are the account's state, not the post's withdrawal.

Account-safety boundaries, same posture as discover_x_browser.py:

- opt-in: live reads require X_BROWSER_AVAILABILITY_ENABLED=true exactly
- read-only: navigate and evaluate only. Nothing posts, follows, likes,
  submits a form or clicks a login button; a dead session stops the lane
  for a person, it never triggers an automated sign-in
- bounded: at most 25 posts per run (hard max 50), each post at most once
  per cadence window (default 24h), with x_browser's fixed spacing between
  browser actions
- fail closed: a login wall, a challenge and a rate limit are session-health
  classes. Any of them stops the whole run mid-iteration, records no
  per-post outcome for the post being read, writes the shared 24h cooldown
  and raises an x-session-health alert. An active cooldown is never pushed

Driver-side only, ever run as the operator account. Stdlib only, per repo
policy. Exit 0 always, except usage error.
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
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import x_browser  # noqa: E402

SOURCES = ROOT / "sources.toml"
WORK = ROOT / ".work"
STATE = WORK / "x-availability.json"
LOCK = WORK / "x-availability.lock"
COOLDOWN = x_browser.COOLDOWN

# Own webbridge session name, same reasoning as the discovery lane: one tab
# per session, so a concurrent poll or discovery run never has its tab closed
# from under it.
SESSION = "coldcard-archive-x-availability"

STATE_VERSION = 1
DEFAULT_MAX_POSTS = 25
HARD_MAX_POSTS = 50
DEFAULT_CADENCE_HOURS = 24.0
# Fixed settle after navigate, as in x_browser.probe_health: X's SPA mounts
# its real content after domcontentloaded, and a probe that reads the boot
# screen misclassifies a healthy post as unavailable.
NAV_SETTLE = 6.0
# A dead daemon fails every navigation; a bounded strike count keeps one
# broken bridge from being mistaken for 25 withdrawn posts.
BRIDGE_ERROR_LIMIT = 3

STATUS_URL = re.compile(r"https?://(?:www\.)?x\.com/([^/]+)/status/(\d+)", re.I)

SESSION_CLASSES = ("login-wall", "challenge", "rate-limit")
ABSENCE_CLASSES = ("deleted", "unavailable-other")
ACCOUNT_CLASSES = ("suspended", "protected", "restricted")
OUTCOME_CLASSES = (
    "ok",
    "deleted",
    "suspended",
    "protected",
    "restricted",
    "unavailable-other",
)


class ConfigError(ValueError):
    """Local configuration makes an availability read unsafe or impossible."""


# --------------------------------------------------------------------- page JS
#
# x.com markup is obfuscated, so the probe anchors on semantic structure and
# X's own notice strings: the article whose permalink carries this status id,
# the [data-testid="errorDetail"] error cell, and the notice text X prints for
# a deleted post, a suspended account, a protected account and a withheld or
# age-restricted post. Class names are never used. The session-health signals
# mirror x_browser.HEALTH_JS so the two lanes agree on what a dead session
# looks like.

PROBE_JS = r"""
(() => {
  const statusId = "%s";
  const text = ((document.body && document.body.innerText) || "").slice(0, 8000);
  const noArticle = !document.querySelector("article");
  const articles = [...document.querySelectorAll("article")];
  const target = articles.some(a =>
    [...a.querySelectorAll('a[href*="/status/"]')].some(l =>
      ((l.getAttribute("href") || "").split("?")[0]).endsWith("/status/" + statusId)));
  return JSON.stringify({
    url: location.href,
    loginForm: !!document.querySelector(
      'form[action*="login" i], [data-testid="LoginForm"],' +
      ' input[autocomplete="username"], [data-testid="loginButton"]'),
    wallText: noArticle &&
      /sign in to (x|twitter)|don.?t miss what.?s happening|log in to (x|twitter)|join (x|twitter)/i
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
    suspended:
      /account suspended|suspends accounts which violate/i.test(text),
    protected:
      /these posts are protected|only approved followers can see/i.test(text),
    restricted:
      /age-restricted|has been withheld in|withheld in response to a legal demand|not available in your (country|region)/i
        .test(text),
    deletedNotice:
      /this page doesn.?t exist|this post (was deleted|is unavailable)|from an account that no longer exists|don.?t exist.? try searching/i
        .test(text),
    errorDetail: !!document.querySelector('[data-testid="errorDetail"]'),
    target: target,
    articles: articles.length,
    chrome: !!document.querySelector(
      '[data-testid="primaryColumn"], [data-testid="SideNav_AccountSwitcher_Button"],' +
      ' [data-testid="AppTabBar_Home_Link"], nav[role="navigation"]')
  });
})()
"""


# ---------------------------------------------------------- pure, tested logic


def classify_availability(probe: dict) -> str:
    """One permalink probe to exactly one outcome class.

    Session classes take precedence over every post-level reading: a login
    wall or challenge masks whatever the post itself would have said, and a
    login wall must never be hidden behind a secondary symptom. Account states
    come before the deleted notice because a suspended account's permalink
    serves both "account suspended" and generic absence text. A rendered page
    with the target article is ok. X's own error cell without a recognized
    notice, and any other rendered page without the post, is
    unavailable-other. A page that rendered nothing at all fails closed as a
    challenge, the same posture x_browser.classify_session takes: reporting
    an empty read as an absence would fabricate deletions.
    """
    url = str(probe.get("url") or "")
    if (
        probe.get("loginForm")
        or probe.get("wallText")
        or "/login" in url
        or "/i/flow/" in url
        or "/logout" in url
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
    if probe.get("restricted"):
        return "restricted"
    if probe.get("deletedNotice"):
        return "deleted"
    if probe.get("target"):
        return "ok"
    if probe.get("errorDetail") or probe.get("articles") or probe.get("chrome"):
        return "unavailable-other"
    return "challenge"


def registered_posts(path: Path = SOURCES) -> list[dict]:
    """Every [[x_post]] block with a parseable permalink, tolerant of a
    registry mid-edit: this lane observes, and a TOML hiccup elsewhere must
    not turn into a run of bogus outcomes."""
    try:
        with path.open("rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    posts = []
    for item in cfg.get("x_post", []):
        if not isinstance(item, dict):
            continue
        slug = item.get("id")
        match = STATUS_URL.search(str(item.get("url") or ""))
        if isinstance(slug, str) and slug and match:
            posts.append({
                "id": slug,
                "handle": match.group(1),
                "status": match.group(2),
                "url": f"https://x.com/{match.group(1)}/status/{match.group(2)}",
            })
    return posts


def select_due(
    posts: list[dict],
    state: dict,
    now: datetime,
    cadence_hours: float,
    limit: int,
) -> list[dict]:
    """The least-recently-checked posts that are due under the cadence.

    A post is due when it has never been checked or its last check is at
    least `cadence_hours` old, so each post is re-checked roughly daily when
    the lane runs twice a day, and never twice inside one window.
    """
    entries = state.get("posts", {})

    def last_check(post: dict) -> datetime:
        stamp = entries.get(post["id"], {}).get("last_check")
        parsed = x_browser.parse_compact_ts(stamp)
        return parsed or datetime.min.replace(tzinfo=timezone.utc)

    due = [
        post for post in posts
        if now - last_check(post) >= timedelta(hours=cadence_hours)
    ]
    return sorted(due, key=lambda post: (last_check(post), post["id"]))[:limit]


def advance_post(prior: dict, outcome: str, stamp: str) -> dict:
    """The per-post state entry after one observation.

    The absence streak counts consecutive absence observations only: an ok
    resets it, an account state neither increments nor carries it (a
    protected account's posts are not withdrawn, and treating the state's
    later lifting as a check would corrupt the streak), and two consecutive
    absences are the escalation the record acts on.
    """
    entry = {"last_check": stamp, "last_outcome": outcome}
    if outcome in ABSENCE_CLASSES:
        streak = prior.get("streak", 0)
        if prior.get("last_outcome") not in ABSENCE_CLASSES:
            streak = 0
        try:
            streak = int(streak)
        except (TypeError, ValueError):
            streak = 0
        entry["streak"] = streak + 1
    else:
        entry["streak"] = 0
    return entry


def alert_decision(prior: dict, entry: dict) -> tuple[str, str] | None:
    """(severity, reason) to alert, or None. One observation is never a
    withdrawal; two consecutive absences are."""
    outcome = entry["last_outcome"]
    if outcome in ABSENCE_CLASSES:
        if entry["streak"] >= 2:
            return (
                "warning",
                f"held post unavailable twice ({outcome} on consecutive "
                "checks); likely withdrawn",
            )
        return (
            "info",
            f"held post not observable ({outcome}); a single absence is "
            "recorded, not called a deletion",
        )
    if outcome in ACCOUNT_CLASSES and prior.get("last_outcome") != outcome:
        return (
            "info",
            {
                "suspended": "post's account reads as suspended",
                "protected": "post's account went protected",
                "restricted": "post reads as withheld or age/region restricted",
            }[outcome],
        )
    return None


# ------------------------------------------------------------------ state file


def load_state(path: Path = STATE) -> dict:
    if not path.exists():
        return {"version": STATE_VERSION, "posts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"refusing live reads with unreadable state: {exc}"
        ) from exc
    if data.get("version") != STATE_VERSION:
        raise ConfigError(
            f"unsupported X availability state version {data.get('version')!r}"
        )
    if not isinstance(data.get("posts"), dict):
        raise ConfigError("X availability state has an invalid shape")
    return data


# --------------------------------------------------------------------- alerts
#
# Every alert goes through alert.py emit with failure tolerance: the cooldown
# file and the state file are the record, the alert is the notification, and
# a broken alert path must never turn an observation into a lane failure.


def _emit(kind: str, severity: str, key: str, summary: str,
          detail: str | None = None) -> None:
    python = ROOT / ".venv" / "bin" / "python"
    cmd = [
        str(python if python.exists() else sys.executable),
        str(ROOT / "scripts" / "alert.py"),
        "emit",
        "--kind", kind,
        "--severity", severity,
        "--key", key,
        "--summary", summary,
    ]
    if detail:
        cmd += ["--detail", detail]
    try:
        subprocess.run(
            cmd, cwd=ROOT, check=False, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def emit_session_alert(health_class: str) -> None:
    date = x_browser.now_utc().strftime("%Y-%m-%d")
    summary = {
        "login-wall": (
            "X availability re-check stopped: the capture browser was served "
            "a login wall, so the signed-in session is dead. A person must "
            "renew it; the lane never logs itself in. Cooldown written."
        ),
        "challenge": (
            "X availability re-check stopped: the capture browser hit a "
            "challenge page. 24h cooldown written; it may clear on its own."
        ),
        "rate-limit": (
            "X availability re-check stopped: the capture browser hit a "
            "rate-limit page. 24h cooldown written; do not push through it."
        ),
    }.get(health_class, f"X availability re-check stopped: {health_class}")
    _emit(
        "x-session-health", "urgent",
        f"x-session-{health_class}-{date}", summary,
    )


def emit_post_alert(post: dict, severity: str, reason: str,
                    date: str) -> None:
    key = (
        f"x-availability-{post['id']}-{date}" if severity == "info"
        else f"x-availability-{post['id']}-withdrawn-{date}"
    )
    _emit(
        "x-availability", severity, key,
        f"{post['id']} ({post['url']}): {reason}",
    )


# ------------------------------------------------------------------ live reads


def probe_post(post: dict, session: str) -> str:
    """Navigate one permalink and classify what the session was served."""
    x_browser.navigate(post["url"], session)
    time.sleep(NAV_SETTLE)
    raw = x_browser.evaluate(PROBE_JS % post["status"], session)
    return classify_availability(json.loads(raw["value"]))


def parse_number_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be numeric") from exc


# ------------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--max", type=int, dest="max_posts",
        default=int(parse_number_env(
            "X_AVAILABILITY_MAX_POSTS", DEFAULT_MAX_POSTS)),
        help=(f"posts this run (default {DEFAULT_MAX_POSTS}, hard max "
              f"{HARD_MAX_POSTS})"),
    )
    parser.add_argument(
        "--cadence-hours", type=float,
        default=parse_number_env(
            "X_AVAILABILITY_CADENCE_HOURS", DEFAULT_CADENCE_HOURS),
        help=(f"minimum hours between checks of the same post (default "
              f"{DEFAULT_CADENCE_HOURS:g})"),
    )
    parser.add_argument(
        "--post", action="append", default=[], metavar="ID",
        help="check only this registered post id (repeatable); ignores "
             "cadence but not the cooldown",
    )
    parser.add_argument(
        "--no-state", action="store_true",
        help="diagnostic live read; do not record outcomes or alert per post",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list registered posts and their recorded state; no browser",
    )
    parser.add_argument(
        "--clear-cooldown", action="store_true",
        help="clear the local X browser cooldown; performs no request",
    )
    args = parser.parse_args()

    try:
        if args.clear_cooldown:
            existed = x_browser.clear_cooldown()
            print("cleared the X browser cooldown; no request made"
                  if existed else "no X browser cooldown was set")
            return 0
        if not 1 <= args.max_posts <= HARD_MAX_POSTS:
            raise ConfigError(f"--max must be 1..{HARD_MAX_POSTS}")
        if args.cadence_hours <= 0:
            raise ConfigError("--cadence-hours must be positive")

        posts = registered_posts()
        state = load_state()

        if args.list:
            entries = state.get("posts", {})
            for post in posts:
                entry = entries.get(post["id"], {})
                stamp = entry.get("last_check") or "never"
                outcome = entry.get("last_outcome") or "-"
                streak = entry.get("streak", 0)
                print(f"{post['id']:<44} {stamp:<17} {outcome}"
                      + (f" (streak {streak})" if streak else ""))
            print(f"{len(posts)} registered post(s), "
                  f"{len(entries)} with recorded state")
            return 0

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

        # The kill switch is exact on purpose, matching the discovery lane.
        if os.environ.get("X_BROWSER_AVAILABILITY_ENABLED", "") != "true":
            raise ConfigError(
                "live X availability re-checks are disabled; set "
                "X_BROWSER_AVAILABILITY_ENABLED=true after reading "
                "docs/design/x-thread-capture.md"
            )

        if args.post:
            wanted = set(args.post)
            unknown = wanted - {post["id"] for post in posts}
            if unknown:
                raise ConfigError(
                    "not registered as [[x_post]]: " + ", ".join(sorted(unknown))
                )
            selected = [post for post in posts if post["id"] in wanted]
        else:
            selected = select_due(
                posts, state, x_browser.now_utc(),
                args.cadence_hours, args.max_posts,
            )
        if not selected:
            print("no registered X post is due under the cadence; nothing to do")
            return 0

        WORK.mkdir(exist_ok=True)
        lock_handle = LOCK.open("a+")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another X availability run holds the lock; skipping",
                  file=sys.stderr)
            return 0

        # The token read doubles as the operator-account check: only the
        # operator can read it, so an agent context fails here, loudly.
        x_browser.bridge_token()

        entries = state.setdefault("posts", {})
        stamp = x_browser.compact_ts(x_browser.now_utc())
        date = stamp[:8]
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        failures = 0
        checked = 0

        for post in selected:
            try:
                outcome = probe_post(post, SESSION)
            except x_browser.BridgeError as exc:
                failures += 1
                print(f"{post['id']}: bridge error: {exc}", file=sys.stderr)
                if failures >= BRIDGE_ERROR_LIMIT:
                    print("stopping: the bridge is failing every navigation",
                          file=sys.stderr)
                    break
                continue
            if outcome in SESSION_CLASSES:
                # The session died. Cool down and stop; no per-post outcome
                # is recorded, because this reading says nothing about the
                # post. What earlier posts recorded still persists below.
                x_browser.close_session(SESSION)
                stop_state = x_browser.write_cooldown(COOLDOWN, outcome)
                emit_session_alert(outcome)
                if not args.no_state and checked:
                    x_browser.atomic_json(STATE, state)
                print(
                    f"session health: {outcome}; availability re-check "
                    f"stopped, cooldown until {stop_state['until']} "
                    "(x-session-health alert raised)",
                    file=sys.stderr,
                )
                return 0
            prior = entries.get(post["id"], {})
            entry = advance_post(prior, outcome, stamp)
            checked += 1
            print(f"{post['id']}: {outcome}"
                  + (f" (absence streak {entry['streak']})"
                     if entry.get("streak") else ""))
            if args.no_state:
                continue
            entries[post["id"]] = entry
            decision = alert_decision(prior, entry)
            if decision:
                severity, reason = decision
                emit_post_alert(post, severity, reason, date)
                print(f"  alert ({severity}): {reason}")

        x_browser.close_session(SESSION)
        if args.no_state:
            print("--no-state: nothing recorded, no alerts raised")
        else:
            x_browser.atomic_json(STATE, state)
        print(f"X availability re-check: {checked} checked, "
              f"{failures} bridge failure(s), {len(selected)} selected")
        return 0
    except (ConfigError, x_browser.XBrowserConfigError) as exc:
        # Exit 0 always except usage error: a disabled kill switch, a
        # malformed registry and a cooldown are expected states, and the
        # stderr line plus the unit journal are the report. A lane that
        # exits non-zero for them would cry unit-failure over a quiet skip.
        print(f"check-x-availability: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
