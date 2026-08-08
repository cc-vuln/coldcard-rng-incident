#!/usr/bin/env python3
"""Shared capture-browser client helpers for X work.

Driver-side only, ever run as the operator account. The bridge token lives in
.capture-browser/token at mode 600, readable by the operator and by nothing
the unattended agents run as; this module is one of the few things meant to
hold it. An agent never imports this file and never reaches the bridge's
evaluate/cdp actions: the driver reads and hydrates, the agent receives text.

The vocabulary used here is read-only in the sense capture-x.sh is: navigate
and evaluate. Nothing in this module submits a form, clicks a login button,
posts, follows or likes. Session renewal is a person's job, deliberately: a
login wall stops the lane with a cooldown and an alert, it never triggers an
automated sign-in.

Stdlib only, per repo policy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".work"
TOKEN_FILE = ROOT / ".capture-browser" / "token"
COOLDOWN = WORK / "x-browser-cooldown.json"
DAEMON_URL = (
    "http://127.0.0.1:" + os.environ.get("WEBBRIDGE_PORT", "10086") + "/command"
)

# Fixed polite spacing between browser actions. X's automation rules warn that
# non-API website scripting can lead to permanent account suspension; the
# operator accepted that risk in writing on 8 Aug 2026. Constant spacing plus
# hard per-run bounds is the whole mitigation this lane has, so the gap is a
# floor enforced inside post_command rather than a sleep callers remember.
POLITE_GAP = 2.0
COOLDOWN_HOURS = 24.0
HTTP_TIMEOUT = 60

HEALTH_OK = "ok"
HEALTH_CLASSES = ("ok", "login-wall", "challenge", "rate-limit")


class XBrowserConfigError(ValueError):
    """Local configuration makes a browser read unsafe or impossible."""


class BridgeError(RuntimeError):
    """The webbridge daemon rejected a command or could not be reached."""


def bridge_token(path: Path = TOKEN_FILE) -> str:
    """The webbridge shared secret, or a loud refusal.

    capture.py's wb_token() returns "" when the file is absent so installs
    that predate token auth keep working. X discovery makes the stricter
    choice: a session this sensitive is never driven tokenless, so an
    unreadable or empty token stops the run before the browser is touched.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise XBrowserConfigError(
            f"cannot read the webbridge token at {path}: {exc.strerror or exc}. "
            "It is created by scripts/agent-permissions.sh at mode 600, owned "
            "by the operator account"
        ) from exc
    token = raw.strip()
    if not token:
        raise XBrowserConfigError(
            f"the webbridge token at {path} is empty; the signed-in session "
            "is never driven without it"
        )
    return token


_last_action = 0.0


def _space(gap: float) -> None:
    """Keep at least `gap` seconds between browser commands, module-wide."""
    global _last_action
    if gap <= 0:
        return
    wait = _last_action + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_action = time.monotonic()


def post_command(
    action: str,
    args: dict,
    session: str,
    *,
    token: str | None = None,
    url: str = DAEMON_URL,
    timeout: int = HTTP_TIMEOUT,
    gap: float = POLITE_GAP,
) -> dict:
    """One webbridge command. Returns the payload's data or raises BridgeError.

    The fixed gap is enforced here so every caller inherits the same spacing
    no matter the order of navigate/evaluate calls.
    """
    _space(gap)
    if token is None:
        token = bridge_token()
    body = json.dumps(
        {"action": action, "args": args or {}, "session": session}
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Bridge-Token": token,
    }
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # 403 with a token we hold means the daemon was restarted under a
        # different secret, which is an operator problem, not a retryable one.
        raise BridgeError(
            f"webbridge {action}: HTTP {exc.code} (bad or missing token?)"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeError(f"webbridge {action} unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BridgeError(f"webbridge {action}: invalid JSON reply: {exc}") from exc
    if not payload.get("ok"):
        raise BridgeError(
            f"webbridge {action} rejected: {payload.get('error', payload)!r}"[:300]
        )
    data = payload.get("data", {})
    if not data.get("success", True):
        raise BridgeError(f"webbridge {action} unsuccessful: {data!r}"[:300])
    return data


def navigate(url: str, session: str, **kwargs) -> dict:
    return post_command("navigate", {"url": url}, session, **kwargs)


def evaluate(code: str, session: str, **kwargs) -> dict:
    return post_command("evaluate", {"code": code}, session, **kwargs)


def close_session(session: str, **kwargs) -> dict | None:
    """Best effort: a wedged browser must not mask the real outcome."""
    try:
        return post_command("close_session", {}, session, **kwargs)
    except BridgeError:
        return None


# ---------------------------------------------------------------- session health
#
# The load-bearing classification. After navigating an x.com URL, HEALTH_JS
# gathers structural signals and classify_session() maps them to exactly one
# of the four classes. login-wall is the urgent one: the signed-in session
# died and only a person can renew it. challenge and rate-limit may clear with
# the cooldown. An unrecognized empty page is treated as a challenge, fail
# closed: scanning a page that rendered nothing and reporting it as a healthy
# empty read is the failure this lane exists to avoid.

HEALTH_JS = r"""
(() => {
  const text = (document.body && document.body.innerText) || "";
  const head = text.slice(0, 6000);
  const loginForm = !!document.querySelector(
    'form[action*="login" i], [data-testid="LoginForm"],' +
    ' input[autocomplete="username"], [data-testid="loginButton"]');
  const wallText =
    /sign in to (x|twitter)|don.?t miss what.?s happening|log in to (x|twitter)|something went wrong.? try reloading|join (x|twitter)/i
      .test(head) && !document.querySelector("article");
  const arkose = !!document.querySelector(
    'iframe[src*="arkose" i], iframe[src*="funcaptcha" i],' +
    ' div[id*="arkose" i], [data-testid*="captcha" i]');
  const challengeText =
    /verify you are (human|a person)|unusual activity|confirm you.?re not a robot|security check|authenticate your account/i
      .test(head);
  const rateText =
    /rate limit|too many requests|cannot retrieve posts at this time|please wait a few moments/i
      .test(head);
  const articles = document.querySelectorAll("article").length;
  const chrome = !!document.querySelector(
    '[data-testid="primaryColumn"], [data-testid="SideNav_AccountSwitcher_Button"],' +
    ' [data-testid="AppTabBar_Home_Link"], nav[role="navigation"]');
  return JSON.stringify({
    url: location.href,
    loginForm: loginForm,
    wallText: wallText,
    arkose: arkose,
    challengeText: challengeText,
    rateText: rateText,
    articles: articles,
    chrome: chrome,
    title: document.title || ""
  });
})()
"""


def classify_session(probe: dict) -> str:
    """Map a HEALTH_JS probe to one of the four session-health classes.

    Precedence is login wall, then challenge, then rate limit: the first is
    the one only a person can repair, so it must never be masked by a
    secondary symptom. A page with articles or signed-in chrome is healthy.
    Anything else unrecognizable fails closed as a challenge.
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
    if probe.get("articles") or probe.get("chrome"):
        return "ok"
    return "challenge"


def probe_health(session: str, url: str = "https://x.com/home", **kwargs) -> str:
    """Navigate an x.com URL and classify what the session was served."""
    navigate(url, session, **kwargs)
    # The navigate action waits for domcontentloaded plus one second; X's SPA
    # mounts its real content after that. A fixed settle keeps the probe from
    # reading the boot screen.
    time.sleep(6)
    raw = evaluate(HEALTH_JS, session, **kwargs)
    return classify_session(json.loads(raw["value"]))


# ------------------------------------------------------------------ cooldown
#
# Any non-ok health class stops the lane and writes a persistent cooldown:
# pushing a sick session harder is how accounts get flagged. The cooldown is
# local state only; --clear-cooldown clears it without touching the browser,
# and clearing it does not repair a session.


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def compact_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_compact_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_cooldown(
    path: Path = COOLDOWN,
    health_class: str = "challenge",
    *,
    hours: float = COOLDOWN_HOURS,
    clock=now_utc,
) -> dict:
    if health_class == HEALTH_OK or health_class not in HEALTH_CLASSES:
        raise XBrowserConfigError(
            f"refusing to cool down on health class {health_class!r}"
        )
    moment = clock()
    state = {
        "class": health_class,
        "set_at": compact_ts(moment),
        "until": compact_ts(moment + timedelta(hours=hours)),
    }
    atomic_json(path, state)
    return state


def read_cooldown(path: Path = COOLDOWN, *, clock=now_utc) -> dict | None:
    """The active cooldown, or None. A malformed file fails closed and loud."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise XBrowserConfigError(
            f"X browser cooldown state at {path} is unreadable: {exc}"
        ) from exc
    until = parse_compact_ts(data.get("until"))
    if (
        data.get("class") not in HEALTH_CLASSES
        or data.get("class") == HEALTH_OK
        or until is None
    ):
        raise XBrowserConfigError(
            f"X browser cooldown state at {path} is malformed"
        )
    if until <= clock():
        return None
    return {"class": data["class"], "set_at": data.get("set_at"),
            "until": data["until"]}


def clear_cooldown(path: Path = COOLDOWN) -> bool:
    """Remove the cooldown file without any browser request."""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--clear-cooldown", action="store_true",
        help="clear the local X browser cooldown; performs no request",
    )
    args = parser.parse_args()
    if args.clear_cooldown:
        existed = clear_cooldown()
        print("cleared the X browser cooldown; no browser request made"
              if existed else "no X browser cooldown was set")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
