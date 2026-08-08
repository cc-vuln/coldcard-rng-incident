#!/usr/bin/env python3
"""Discover new posts from a small registry of incident-relevant X accounts.

DEPRECATED 8 Aug 2026: the official-API lane was retired with the operator's
policy reversal; live watched-account discovery now runs through the capture
browser in scripts/discover_x_browser.py, and X promotion through the
registering xintake lane. This script remains as the fallback X hydration
path for hydrate_candidates.py wherever the old credential is still present.

This is discovery, not capture. It uses the official X API to read shallow
public user timelines, keeps ID-only metadata in .work/, and queues new
permalinks in DISCOVERY.md for the existing intake agent. A relevant post is
captured later with ingest-x.py, after explicit operator-approved assessment.

Account-safety boundaries:

- opt-in: live reads require X_DISCOVERY_ENABLED=true
- official API only: no browser scripting, cookie reuse, home feed or search
- read-only: the bearer token is used only for documented GET endpoints
- bounded: at most 10 profiles and 30 posts per profile, with fixed spacing
- fail closed: rate limits, quota, stale credentials and access errors stop the
  whole run and create a persistent cooldown
- first contact is a baseline: existing posts are marked seen but are not
  queued unless --queue-initial is deliberately supplied
- overflow is an error: a later run never checkpoints past a full result page

Everything in this watcher is stdlib Python. It is deliberately absent from
discover-community.service while request volume and intake quality are reviewed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.toml"
WORK = ROOT / ".work"
STATE = WORK / "x-discovery.json"
CANDIDATES = WORK / "x-candidates.jsonl"
LOCK = WORK / "x-discovery.lock"

API_ROOT = "https://api.x.com/2"
USER_AGENT = (
    "cc-vuln.org-primary-source-archive/1.0 "
    "(+https://cc-vuln.org/methodology/)"
)
HTTP_TIMEOUT = 45
MAX_RESPONSE_BYTES = 5_000_000
DEFAULT_MAX_WATCHES = 6
HARD_MAX_WATCHES = 10
DEFAULT_MAX_POSTS = 20
MIN_MAX_POSTS = 5
HARD_MAX_POSTS = 30
DEFAULT_PROFILE_DELAY = 30.0
MIN_PROFILE_DELAY = 10.0
DEFAULT_COOLDOWN_HOURS = 24.0
STATE_VERSION = 1
SEEN_KEEP = 20_000

HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
STATUS_RE = re.compile(r"https?://(?:www\.)?x\.com/([^/]+)/status/(\d+)", re.I)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STOP_ALL_STATUSES = frozenset({
    "rate-limited",
    "quota-exhausted",
    "auth-stale",
    "api-access-denied",
    "transient-error",
})


class ConfigError(ValueError):
    """A local registry or option is unsafe or malformed."""


class ApiError(RuntimeError):
    """A sanitized X API failure that never contains the bearer token."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Watch:
    handle: str
    why: str
    org: str | None = None
    since: str | None = None
    include_replies: bool = True
    max_posts: int = DEFAULT_MAX_POSTS
    active: bool = True


@dataclass(frozen=True)
class ResolvedUser:
    id: str
    username: str


@dataclass(frozen=True)
class FetchResult:
    status: str
    posts: tuple[dict, ...] = ()
    detail: str = ""

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"

    @property
    def stop_all(self) -> bool:
        return self.status in STOP_ALL_STATUSES


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def compact_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_compact_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def atomic_json(path: Path, data: dict) -> None:
    """Write private state without leaving a half-written checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_state(path: Path = STATE) -> dict:
    if not path.exists():
        return {"version": STATE_VERSION, "seen": [], "watches": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"refusing live reads with unreadable state: {exc}") from exc
    if data.get("version") != STATE_VERSION:
        raise ConfigError(
            f"unsupported X discovery state version {data.get('version')!r}"
        )
    if not isinstance(data.get("seen"), list) or not isinstance(
        data.get("watches"), dict
    ):
        raise ConfigError("X discovery state has an invalid shape")
    if any(not str(value).isdigit() for value in data["seen"]):
        raise ConfigError("X discovery state contains a non-numeric status id")
    return data


def load_registry(path: Path = SOURCES) -> tuple[list[Watch], set[str], set[str]]:
    """Return active watches, registered status IDs and registered X URLs."""
    try:
        with path.open("rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    watches: list[Watch] = []
    seen_handles: set[str] = set()
    for pos, item in enumerate(cfg.get("x_watch", []), 1):
        if not isinstance(item, dict):
            raise ConfigError(f"x_watch[{pos}] must be a table")
        handle = item.get("handle")
        if not isinstance(handle, str) or not HANDLE_RE.fullmatch(handle):
            raise ConfigError(f"x_watch[{pos}] has invalid handle {handle!r}")
        key = handle.casefold()
        if key in seen_handles:
            raise ConfigError(f"duplicate x_watch handle @{handle}")
        seen_handles.add(key)

        why = item.get("why")
        if not isinstance(why, str) or not why.strip():
            raise ConfigError(f"x_watch @{handle} needs a non-empty why")
        org = item.get("org")
        if org is not None and (not isinstance(org, str) or not org.strip()):
            raise ConfigError(f"x_watch @{handle}: org must be a non-empty string")
        since = item.get("since")
        if since is not None:
            if not isinstance(since, str) or not DATE_RE.fullmatch(since):
                raise ConfigError(f"x_watch @{handle}: since must be YYYY-MM-DD")
            try:
                datetime.strptime(since, "%Y-%m-%d")
            except ValueError as exc:
                raise ConfigError(
                    f"x_watch @{handle}: since is not a calendar date"
                ) from exc
        include_replies = item.get("include_replies", True)
        active = item.get("active", True)
        if type(include_replies) is not bool:
            raise ConfigError(f"x_watch @{handle}: include_replies must be boolean")
        if type(active) is not bool:
            raise ConfigError(f"x_watch @{handle}: active must be boolean")
        max_posts = item.get("max_posts", DEFAULT_MAX_POSTS)
        if (
            type(max_posts) is not int
            or not MIN_MAX_POSTS <= max_posts <= HARD_MAX_POSTS
        ):
            raise ConfigError(
                f"x_watch @{handle}: max_posts must be "
                f"{MIN_MAX_POSTS}..{HARD_MAX_POSTS}"
            )
        watches.append(Watch(
            handle=handle,
            why=why.strip(),
            org=org.strip() if isinstance(org, str) else None,
            since=since,
            include_replies=include_replies,
            max_posts=max_posts,
            active=active,
        ))

    registered_ids: set[str] = set()
    registered_urls: set[str] = set()
    for post in cfg.get("x_post", []):
        url = post.get("url", "")
        if not isinstance(url, str):
            continue
        match = STATUS_RE.search(url)
        if match:
            registered_ids.add(match.group(2))
            registered_urls.add(
                f"https://x.com/{match.group(1)}/status/{match.group(2)}"
            )
    return [watch for watch in watches if watch.active], registered_ids, registered_urls


def bearer_token() -> str:
    raw = os.environ.get("X_API_BEARER_TOKEN", "")
    token = raw.strip()
    if not token:
        raise ConfigError(
            "X_API_BEARER_TOKEN is required; watched-account discovery uses "
            "only the official X API"
        )
    if any(ord(char) < 33 or ord(char) == 127 for char in token):
        raise ConfigError("X_API_BEARER_TOKEN contains invalid whitespace")
    if raw != token:
        raise ConfigError("X_API_BEARER_TOKEN has leading or trailing whitespace")
    return token


def _clean_detail(value: str, limit: int = 600) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    return clean[:limit]


def api_error_detail(body: bytes, fallback: str) -> str:
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return _clean_detail(fallback)
    details: list[str] = []
    if isinstance(payload, dict):
        for key in ("title", "detail", "type"):
            if isinstance(payload.get(key), str):
                details.append(payload[key])
        errors = payload.get("errors")
        if isinstance(errors, list):
            for error in errors:
                if not isinstance(error, dict):
                    continue
                parts = [
                    error.get(key) for key in ("title", "detail", "message")
                    if isinstance(error.get(key), str)
                ]
                if parts:
                    details.append(": ".join(parts))
    return _clean_detail("; ".join(details) or fallback)


def api_get(
    path: str,
    params: dict[str, str],
    token: str,
    *,
    opener: Callable = urllib.request.urlopen,
) -> dict:
    """Perform one documented read-only X API GET and return its JSON object."""
    query = urllib.parse.urlencode(params)
    url = API_ROOT + path + ("?" + query if query else "")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with opener(request, timeout=HTTP_TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES + 1)
        raise ApiError(
            exc.code,
            api_error_detail(body, f"X API returned HTTP {exc.code}"),
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(0, _clean_detail(f"X API request failed: {exc}")) from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ApiError(0, "X API response exceeded the local size limit")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(0, f"X API returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ApiError(0, "X API returned a non-object JSON document")
    return payload


def classify_api_failure(status: int, detail: str) -> str:
    text = detail.casefold()
    if status == 429 or "rate limit" in text:
        return "rate-limited"
    if status == 402 or any(word in text for word in ("credits", "quota", "usage cap")):
        return "quota-exhausted"
    if status == 401 or "unauthorized" in text or "invalid token" in text:
        return "auth-stale"
    if "protected" in text or "approved followers" in text:
        return "profile-protected"
    if "suspended" in text:
        return "profile-suspended"
    if status == 404 or "could not find user" in text or "not found" in text:
        return "profile-unavailable"
    if status == 403:
        return "api-access-denied"
    if status == 0 or status >= 500:
        return "transient-error"
    return "api-error"


Requester = Callable[[str, dict[str, str], str], dict]


def resolve_users(
    watches: list[Watch],
    state: dict,
    token: str,
    *,
    requester: Requester = api_get,
) -> tuple[dict[str, ResolvedUser], dict[str, FetchResult]]:
    """Resolve uncached handles in one official API request."""
    resolved: dict[str, ResolvedUser] = {}
    failures: dict[str, FetchResult] = {}
    missing: list[Watch] = []
    watch_state = state.setdefault("watches", {})

    for watch in watches:
        key = watch.handle.casefold()
        prior = watch_state.get(key, {})
        user_id = str(prior.get("user_id", ""))
        if user_id.isdigit():
            resolved[key] = ResolvedUser(
                user_id, str(prior.get("resolved_handle") or watch.handle)
            )
        else:
            missing.append(watch)

    if not missing:
        return resolved, failures
    try:
        payload = requester(
            "/users/by",
            {
                "usernames": ",".join(watch.handle for watch in missing),
                "user.fields": "protected,withheld",
            },
            token,
        )
    except ApiError as exc:
        result = FetchResult(
            classify_api_failure(exc.status, exc.detail), detail=exc.detail
        )
        return resolved, {watch.handle.casefold(): result for watch in missing}

    returned: dict[str, dict] = {}
    for user in payload.get("data", []):
        if not isinstance(user, dict):
            continue
        username = user.get("username")
        user_id = user.get("id")
        if isinstance(username, str) and str(user_id).isdigit():
            returned[username.casefold()] = user

    errors = payload.get("errors")
    error_detail = api_error_detail(
        json.dumps({"errors": errors}).encode(), "user lookup returned no data"
    ) if errors else "user lookup returned no data"
    error_statuses = {
        _as_int(error.get("status"))
        for error in errors or []
        if isinstance(error, dict) and _as_int(error.get("status"))
    }
    error_status = max(error_statuses) if error_statuses else 404
    for watch in missing:
        key = watch.handle.casefold()
        user = returned.get(key)
        if user:
            resolved_user = ResolvedUser(str(user["id"]), str(user["username"]))
            resolved[key] = resolved_user
            prior = dict(watch_state.get(key, {}))
            prior.update({
                "user_id": resolved_user.id,
                "resolved_handle": resolved_user.username,
            })
            watch_state[key] = prior
            continue
        detail = _clean_detail(f"@{watch.handle}: {error_detail}")
        failures[key] = FetchResult(
            classify_api_failure(error_status, detail), detail=detail
        )
    return resolved, failures


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def timeline_params(watch: Watch, since_id: str | None) -> dict[str, str]:
    params = {
        "max_results": str(watch.max_posts),
        "tweet.fields": "author_id,created_at,referenced_tweets",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    if since_id and since_id.isdigit():
        params["since_id"] = since_id
    elif watch.since:
        params["start_time"] = watch.since + "T00:00:00Z"
    if not watch.include_replies:
        params["exclude"] = "replies"
    return params


def _post_text(post: dict) -> str:
    note = post.get("note_tweet")
    if isinstance(note, dict) and isinstance(note.get("text"), str):
        return note["text"]
    return post.get("text") if isinstance(post.get("text"), str) else ""


def normalize_api_post(
    post: dict,
    watch: Watch,
    user: ResolvedUser,
    referenced: dict[str, dict],
    usernames: dict[str, str],
) -> dict | None:
    status_id = str(post.get("id", ""))
    if not status_id.isdigit() or str(post.get("author_id", "")) != user.id:
        return None

    references = {
        str(item.get("type")): str(item.get("id"))
        for item in post.get("referenced_tweets", [])
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    relation = (
        "repost" if "retweeted" in references else
        "reply" if "replied_to" in references else
        "quote" if "quoted" in references else
        "post"
    )
    source_id = references.get("retweeted", status_id)
    content_post = referenced.get(source_id, post) if relation == "repost" else post
    original_author = user.username
    if relation == "repost":
        original_author = usernames.get(
            str(content_post.get("author_id", "")), "unknown"
        )
    metrics = post.get("public_metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    reply_user_id = str(post.get("in_reply_to_user_id", ""))

    return {
        "id": status_id,
        "url": f"https://x.com/{user.username}/status/{status_id}",
        "platform": "x",
        "actor": user.username,
        "author": user.username,
        "originalAuthor": original_author,
        "org": watch.org,
        "relation": relation,
        "content": _post_text(content_post),
        "createdAt": post.get("created_at") or "",
        "replyTo": usernames.get(reply_user_id) or None,
        "replyId": references.get("replied_to"),
        "quoteId": references.get("quoted"),
        "sourceTweetId": source_id,
        "replyCount": _as_int(metrics.get("reply_count")),
        "repostCount": _as_int(metrics.get("retweet_count")),
        "quoteCount": _as_int(metrics.get("quote_count")),
        "withheld": post.get("withheld"),
        "watchWhy": watch.why,
        "label": f"X @{user.username}",
    }


def parse_timeline(
    payload: dict,
    watch: Watch,
    user: ResolvedUser,
    *,
    incremental: bool,
) -> FetchResult:
    data = payload.get("data", [])
    if data is None:
        data = []
    if not isinstance(data, list):
        return FetchResult("api-error", detail="X API timeline data is not a list")
    if not data and payload.get("errors"):
        detail = api_error_detail(
            json.dumps({"errors": payload["errors"]}).encode(),
            "X API timeline returned errors",
        )
        return FetchResult(classify_api_failure(0, detail), detail=detail)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if incremental and meta.get("next_token"):
        return FetchResult(
            "result-window-exceeded",
            detail=(
                f"more than {watch.max_posts} new posts are available; "
                "checkpoint not advanced"
            ),
        )

    includes = payload.get("includes")
    if not isinstance(includes, dict):
        includes = {}
    referenced = {
        str(item.get("id")): item
        for item in includes.get("tweets", [])
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    usernames = {
        str(item.get("id")): str(item.get("username"))
        for item in includes.get("users", [])
        if isinstance(item, dict)
        and str(item.get("id", "")).isdigit()
        and isinstance(item.get("username"), str)
    }
    effective_user = ResolvedUser(user.id, usernames.get(user.id, user.username))
    posts: dict[str, dict] = {}
    for raw_post in data[:watch.max_posts]:
        if not isinstance(raw_post, dict):
            continue
        post = normalize_api_post(
            raw_post, watch, effective_user, referenced, usernames
        )
        if post:
            posts[post["id"]] = post
    if data and not posts:
        return FetchResult(
            "api-error",
            detail="X API returned posts that did not belong to the resolved user",
        )
    ordered = tuple(sorted(
        posts.values(), key=lambda item: int(item["id"]), reverse=True
    ))
    return FetchResult("healthy", posts=ordered)


def fetch_watch(
    watch: Watch,
    user: ResolvedUser,
    token: str,
    since_id: str | None,
    *,
    requester: Requester = api_get,
) -> FetchResult:
    try:
        payload = requester(
            f"/users/{urllib.parse.quote(user.id, safe='')}/tweets",
            timeline_params(watch, since_id),
            token,
        )
    except ApiError as exc:
        return FetchResult(
            classify_api_failure(exc.status, exc.detail), detail=exc.detail
        )
    return parse_timeline(payload, watch, user, incremental=bool(since_id))


def candidate_title(post: dict) -> str:
    return (
        f"@{post['actor']} {post['relation']} "
        "(text available during approved intake)"
    )


def candidate_for_intake(post: dict, found_at: str) -> dict:
    return {
        "id": post["id"],
        "url": post["url"],
        "platform": "x",
        "actor": post["actor"],
        "org": post.get("org"),
        "relation": post["relation"],
        "createdAt": post["createdAt"],
        "replyId": post.get("replyId"),
        "quoteId": post.get("quoteId"),
        "sourceTweetId": post.get("sourceTweetId"),
        "watchWhy": post.get("watchWhy"),
        "label": post["label"],
        "foundAt": found_at,
        "title": candidate_title(post),
    }


def append_candidates(candidates: list[dict], path: Path = CANDIDATES) -> None:
    if not candidates:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for candidate in candidates:
            fh.write(json.dumps(candidate, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def show_candidate(post_id: str, path: Path = CANDIDATES) -> int:
    if not path.exists():
        print(f"no X candidate log at {path}", file=sys.stderr)
        return 1
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(candidate.get("id")) == post_id:
            found = candidate
    if found is None:
        print(f"X candidate {post_id} not found", file=sys.stderr)
        return 1
    print(json.dumps(found, indent=2, sort_keys=True))
    return 0


def load_candidate(post_id: str, path: Path = CANDIDATES) -> dict:
    if not path.exists():
        raise ConfigError(f"no X candidate log at {path}")
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(candidate.get("id")) == post_id:
            found = candidate
    if found is None:
        raise ConfigError(f"X candidate {post_id} not found")
    return found


def hydrate_candidate(
    post_id: str,
    token: str,
    *,
    path: Path = CANDIDATES,
    requester: Requester = api_get,
) -> int:
    """Fetch one candidate for immediate assessment without persisting text."""
    if not post_id.isdigit():
        raise ConfigError("X candidate id must be numeric")
    candidate = load_candidate(post_id, path)
    params = {
        "tweet.fields": (
            "author_id,created_at,in_reply_to_user_id,note_tweet,"
            "public_metrics,referenced_tweets,withheld"
        ),
        "expansions": (
            "author_id,referenced_tweets.id,"
            "referenced_tweets.id.author_id,in_reply_to_user_id"
        ),
        "user.fields": "username,name",
    }
    try:
        payload = requester(
            f"/tweets/{urllib.parse.quote(post_id, safe='')}", params, token
        )
    except ApiError as exc:
        print(f"X API: {exc.detail}", file=sys.stderr)
        return 1
    data = payload.get("data")
    if not isinstance(data, dict):
        detail = api_error_detail(
            json.dumps(payload).encode(), "X API returned no post"
        )
        print(f"X API: {detail}", file=sys.stderr)
        return 1
    author_id = str(data.get("author_id", ""))
    if not author_id.isdigit():
        print("X API: post has no valid author id", file=sys.stderr)
        return 1
    includes = payload.get("includes")
    users = includes.get("users", []) if isinstance(includes, dict) else []
    username = next(
        (
            str(user.get("username"))
            for user in users
            if isinstance(user, dict)
            and str(user.get("id", "")) == author_id
            and isinstance(user.get("username"), str)
        ),
        str(candidate.get("actor") or "unknown"),
    )
    watch = Watch(
        handle=username,
        why=str(candidate.get("watchWhy") or "watched incident actor"),
        org=candidate.get("org") if isinstance(candidate.get("org"), str) else None,
    )
    parsed = parse_timeline(
        {**payload, "data": [data]},
        watch,
        ResolvedUser(author_id, username),
        incremental=False,
    )
    if not parsed.healthy or not parsed.posts:
        print(
            f"X API: {parsed.detail or 'post could not be normalized'}",
            file=sys.stderr,
        )
        return 1
    hydrated = dict(candidate)
    hydrated.update(parsed.posts[0])
    print(json.dumps(hydrated, indent=2, sort_keys=True))
    return 0


def choose_watches(
    watches: list[Watch], state: dict, handles: list[str], limit: int
) -> list[Watch]:
    requested = {handle.casefold() for handle in handles}
    if requested:
        available = {watch.handle.casefold() for watch in watches}
        unknown = requested - available
        if unknown:
            raise ConfigError(
                "unknown or inactive X watch: " + ", ".join(sorted(unknown))
            )
        watches = [
            watch for watch in watches if watch.handle.casefold() in requested
        ]

    watch_state = state.get("watches", {})
    return sorted(
        watches,
        key=lambda watch: (
            watch_state.get(watch.handle.casefold(), {}).get("last_attempt", ""),
            watch.handle.casefold(),
        ),
    )[:limit]


def _after_since(post: dict, since: str | None) -> bool:
    if not since:
        return True
    created = post.get("createdAt") or ""
    return created[:10] >= since


def discover(
    selected: list[Watch],
    state: dict,
    registered_ids: set[str],
    registered_urls: set[str],
    *,
    queue_initial: bool,
    no_state: bool,
    profile_delay: float,
    cooldown_hours: float,
    clock: Callable[[], datetime] = now_utc,
    fetcher: Callable[[Watch], FetchResult],
    state_path: Path = STATE,
    candidates_path: Path = CANDIDATES,
) -> tuple[int, int, int]:
    """Return profiles attempted, candidates queued and failed profiles."""
    from discovery_common import update_intake

    seen = {str(value) for value in state.get("seen", [])} - registered_ids
    watch_state = state.setdefault("watches", {})
    total_candidates = 0
    attempted = 0
    failures = 0

    for index, watch in enumerate(selected):
        if index:
            time.sleep(profile_delay)
        attempted += 1
        stamp_dt = clock()
        stamp = compact_ts(stamp_dt)
        key = watch.handle.casefold()
        prior = watch_state.get(key, {})
        baseline = not prior.get("last_success")

        result = fetcher(watch)
        current = dict(prior)
        current.update({
            "handle": watch.handle,
            "last_attempt": stamp,
            "status": result.status,
        })
        if result.detail:
            current["detail"] = result.detail
        else:
            current.pop("detail", None)
        watch_state[key] = current

        if not result.healthy:
            failures += 1
            print(f"@{watch.handle}: {result.status}: {result.detail}", file=sys.stderr)
            if result.stop_all:
                until = stamp_dt + timedelta(hours=cooldown_hours)
                state["cooldown_until"] = compact_ts(until)
                state["cooldown_reason"] = result.status
                print(
                    f"stopping all X discovery; cooldown until {compact_ts(until)}",
                    file=sys.stderr,
                )
                if not no_state:
                    state["seen"] = sorted(seen, key=int)[-SEEN_KEEP:]
                    atomic_json(state_path, state)
                break
            if not no_state:
                state["seen"] = sorted(seen, key=int)[-SEEN_KEEP:]
                atomic_json(state_path, state)
            continue

        fetched_ids = {post["id"] for post in result.posts}
        candidates = [
            candidate_for_intake(post, stamp)
            for post in result.posts
            if post["id"] not in seen
            and post["id"] not in registered_ids
            and _after_since(post, watch.since)
            and (queue_initial or not baseline)
        ]
        if not no_state:
            if candidates:
                append_candidates(candidates, candidates_path)
                update_intake(candidates, registered_urls)
            seen.update(fetched_ids)
            current["last_success"] = stamp
            if result.posts:
                current["resolved_handle"] = result.posts[0]["actor"]
            if fetched_ids:
                current["newest_id"] = max(fetched_ids, key=int)
            if baseline:
                current["baseline_count"] = len(fetched_ids)
            state["seen"] = sorted(seen, key=int)[-SEEN_KEEP:]
            state.pop("cooldown_until", None)
            state.pop("cooldown_reason", None)
            atomic_json(state_path, state)

        total_candidates += len(candidates)
        action = "baselined" if baseline and not queue_initial else "scanned"
        print(
            f"@{watch.handle}: {action} {len(result.posts)} post(s); "
            f"{len(candidates)} new candidate(s)"
        )
        for candidate in candidates:
            print(
                f"  {candidate['createdAt'][:16]} {candidate['url']} "
                f"{candidate['title']}"
            )

    return attempted, total_candidates, failures


def parse_number_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be numeric") from exc


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
        "--handle", action="append", default=[],
        help="scan only this registered handle (repeatable)",
    )
    parser.add_argument(
        "--max-watches", type=int,
        default=parse_int_env("X_DISCOVERY_MAX_WATCHES", DEFAULT_MAX_WATCHES),
        help=(
            f"profiles this run (default {DEFAULT_MAX_WATCHES}, "
            f"hard max {HARD_MAX_WATCHES})"
        ),
    )
    parser.add_argument(
        "--profile-delay", type=float,
        default=parse_number_env(
            "X_DISCOVERY_PROFILE_DELAY", DEFAULT_PROFILE_DELAY
        ),
        help=f"fixed seconds between profiles (minimum {MIN_PROFILE_DELAY:g})",
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
        "--list", action="store_true",
        help="list configured watches without touching X",
    )
    parser.add_argument(
        "--check-auth", action="store_true",
        help="check the local API credential without touching X",
    )
    parser.add_argument(
        "--clear-cooldown", action="store_true",
        help="clear the local stop state; performs no X request",
    )
    parser.add_argument(
        "--show", metavar="POST_ID",
        help="hydrate one queued candidate for assessment; one official API GET",
    )
    parser.add_argument(
        "--show-local", metavar="POST_ID",
        help="print one queued candidate's ID-only metadata; no X request",
    )
    args = parser.parse_args()

    try:
        if args.show_local:
            return show_candidate(args.show_local)
        if args.show:
            if not enabled(os.environ.get("X_DISCOVERY_ENABLED")):
                raise ConfigError("live X discovery is disabled")
            return hydrate_candidate(args.show, bearer_token())
        watches, registered_ids, registered_urls = load_registry()
        if args.list:
            for watch in watches:
                mode = "posts + replies" if watch.include_replies else "posts"
                print(
                    f"@{watch.handle:<18} {mode:<15} "
                    f"{watch.max_posts:>2} max  {watch.why}"
                )
            return 0
        if args.check_auth:
            bearer_token()
            print("X API credential preflight healthy: bearer token is present")
            return 0

        WORK.mkdir(exist_ok=True)
        lock_handle = LOCK.open("a+")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another X discovery run holds the lock; skipping", file=sys.stderr)
            return 1

        state = load_state()
        state["seen"] = [
            value for value in state.get("seen", [])
            if str(value) not in registered_ids
        ]
        if args.clear_cooldown:
            state.pop("cooldown_until", None)
            state.pop("cooldown_reason", None)
            atomic_json(STATE, state)
            print("cleared local X discovery cooldown; no X request made")
            return 0
        if not enabled(os.environ.get("X_DISCOVERY_ENABLED")):
            raise ConfigError(
                "live X discovery is disabled; set X_DISCOVERY_ENABLED=true "
                "after reading docs/design/discovery-and-x-watch.md"
            )
        if not 1 <= args.max_watches <= HARD_MAX_WATCHES:
            raise ConfigError(f"--max-watches must be 1..{HARD_MAX_WATCHES}")
        if args.profile_delay < MIN_PROFILE_DELAY and args.max_watches > 1:
            raise ConfigError(
                f"--profile-delay must be at least {MIN_PROFILE_DELAY:g} seconds"
            )
        cooldown_hours = parse_number_env(
            "X_DISCOVERY_COOLDOWN_HOURS", DEFAULT_COOLDOWN_HOURS
        )
        if cooldown_hours < 1:
            raise ConfigError("X_DISCOVERY_COOLDOWN_HOURS must be at least 1")
        raw_cooldown = state.get("cooldown_until")
        cooldown = parse_compact_ts(raw_cooldown)
        if raw_cooldown and cooldown is None:
            raise ConfigError("X discovery cooldown state is malformed")
        if cooldown and cooldown > now_utc():
            raise ConfigError(
                f"X discovery cooldown is active until {compact_ts(cooldown)} "
                f"({state.get('cooldown_reason', 'safety stop')})"
            )

        token = bearer_token()
        selected = choose_watches(watches, state, args.handle, args.max_watches)
        if not selected:
            raise ConfigError("no active X watches selected")
        resolved, resolution_failures = resolve_users(selected, state, token)

        def fetch_selected(watch: Watch) -> FetchResult:
            key = watch.handle.casefold()
            if key in resolution_failures:
                return resolution_failures[key]
            user = resolved[key]
            prior = state.get("watches", {}).get(key, {})
            since_id = str(prior.get("newest_id") or "") or None
            return fetch_watch(watch, user, token, since_id)

        attempted, queued, failures = discover(
            selected,
            state,
            registered_ids,
            registered_urls,
            queue_initial=args.queue_initial,
            no_state=args.no_state,
            profile_delay=args.profile_delay,
            cooldown_hours=cooldown_hours,
            fetcher=fetch_selected,
        )
        outcome = "incomplete" if failures else "complete"
        print(
            f"X discovery {outcome}: {attempted} profile(s), "
            f"{queued} candidate(s), {failures} failure(s)"
        )
        return 1 if failures else 0
    except (ConfigError, TimeoutError) as exc:
        print(f"discover-x: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
