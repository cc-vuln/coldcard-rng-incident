#!/usr/bin/env python3
"""Immutable discovery transactions and deterministic working projections.

Canonical discovery history is one immutable JSON transaction per logical
batch. Candidate JSON, Markdown views, ``state.json`` and root ``DISCOVERY.md``
are generated projections. A committed transaction is the only state change;
an interrupted projection render is detected and can be repaired from it.

The module is stdlib-only and deliberately rejects symlinks, unexpected files,
unknown fields and unsafe path components. Agents can read the projections but
cannot write this tree; operator-side drivers hold one shared lock.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import tomllib
import unicodedata
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
URL_RE = re.compile(r"\((https?://[^)]+)\)")
X_RE = re.compile(r"/(?:[^/]+)/status/(\d+)")
REDDIT_RE = re.compile(r"/comments/([0-9a-z]+)(?:/|$)", re.I)
NOSTR_RE = re.compile(r"/(note1[023456789acdefghjklmnpqrstuvwxyz]+)", re.I)
LEGACY_HEADING_RE = re.compile(r"^## (.+)$")
LEGACY_REGISTERED_RE = re.compile(
    r"(?:->|,)\s+registered as ([A-Za-z0-9][A-Za-z0-9._-]*).*?"
    r"\((\d{8}T\d{6}Z)\)(?=;| ->|$)")
LEGACY_ALREADY_RE = re.compile(
    r"-> already registered as ([A-Za-z0-9][A-Za-z0-9._-]*).*?"
    r"\((\d{8}T\d{6}Z)\)(?=;| ->|$)")
LEGACY_DISMISSED_RE = re.compile(
    r"-> dismissed:\s*(.*?) \((\d{8}T\d{6}Z)\)(?=;| ->|$)")
LEGACY_RETRY_RE = re.compile(
    r"-> Pending:\s*(.*?) \((\d{8}T\d{6}Z)\)(?=;| ->|$)")
LEGACY_SOURCE_DATE_RE = re.compile(r"^- (\d{4})-(\d{2})-(\d{2}) ")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
TX_FILE_RE = re.compile(r"^(\d{8})-([0-9a-f]{64})\.json$")

STATES = frozenset({"pending", "deferred", "assessed", "human-review"})
OBSERVATION_STATES = frozenset({"pending", "deferred", "human-review"})
VERDICTS = frozenset({"registered", "dismissed", "already-registered"})
EVENT_TYPES = frozenset({"observation", "retry", "verdict", "state"})
MAX_TRANSACTION_BYTES = 8 * 1024 * 1024
MAX_EVENTS_PER_TRANSACTION = 2000
VIEW_PAGE_ITEMS = 100

STORE_README = """# Structured discovery record

Discovery history is stored as immutable, hash-chained JSON transaction
batches under `transactions/YYYY-MM/`. A transaction records observations,
retries, verdicts and explicit supersessions; it is the canonical history.
Its sequence and hash-chain position record insertion order; its `at` value
and month directory follow the latest event in that batch. Git history is the
wall-clock record of when a transaction entered this repository.

Everything under `candidates/` and `views/`, `state.json`, and the repository's
root `DISCOVERY.md` is generated from that chain. Candidate JSON is the easiest
place to inspect one item's complete observation and decision history. The
paged Markdown views are navigation aids, not another ledger.

`migration-v1/legacy/` retains every pre-cutover queue and rotated-history file
byte for byte. `migration-v1/occurrence-semantics.json` maps each exact legacy
bullet and transition to the immutable event or events that preserve it.
`migration-v1/manifest.json` records both files' hashes, every deliberate
repair, the baseline semantic root and a bundle root bound into each migration
transaction. Validation independently reparses the held bullets and checks
those event bindings. This makes later alteration or parser drift detectable
without pretending that the old Markdown was itself append-only.

Writers serialize on `.work/locks/discovery.lock`. Do not edit transactions,
candidate projections, views or the root index directly. Use the discovery
writer APIs, then validate or regenerate with:

```bash
just discovery-check
.venv/bin/python scripts/discovery_store.py render
```

An interrupted transaction remains authoritative and a later render repairs
its projections. The one-time installer additionally journals directory
activation under `.work/` so a restart either restores the legacy inputs or
finishes the validated new tree.

The operator workflow and placement guide is `../docs/DISCOVERY.md`.

The published transaction and candidate JSON Schemas describe each object's
structural envelope. They do not express hash recomputation, transition rules,
cross-file inventory or projection equality. `discovery_store.py validate` is
the normative validator for those contracts and for the migration manifest,
occurrence table and `state.json` formats.
"""

# Only metadata already represented in the public queue may leave .work/. Raw
# X snippets, complete nostr events, relay lists and hydrated bodies are
# intentionally absent. Migration-only fields preserve exact public legacy
# provenance and are equally bounded.
PUBLIC_OBSERVATION_FIELDS = frozenset({
    "url", "createdAt", "foundAt", "id", "label", "platform", "relation",
    "tier", "title", "author", "ncomments", "sub", "relayCount", "priority",
    "display_line", "legacy_line", "legacy_candidate_line", "legacy_path",
    "legacy_line_number", "legacy_section", "legacy_event_time_basis",
    "legacy_queue_rank",
})
INTEGER_OBSERVATION_FIELDS = frozenset({
    "ncomments", "relayCount", "legacy_line_number", "legacy_queue_rank",
})
FIELD_LIMITS = {
    "url": 8192,
    "display_line": 16384,
    "legacy_line": 16384,
    "legacy_candidate_line": 16384,
    "title": 4096,
    "author": 1024,
    "label": 1024,
    "legacy_path": 1024,
}
MAX_REASON_CHARS = 16384
SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")

HASH_SCHEMA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
STAMP_SCHEMA = {"type": "string", "pattern": "^[0-9]{8}T[0-9]{6}Z$"}
IDENTITY_SCHEMA = {
    "type": "string",
    "maxLength": 257,
    "pattern": "^[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*$",
}
OBSERVATION_PROPERTIES = {
    key: ({"type": "integer", "minimum": 0}
          if key in INTEGER_OBSERVATION_FIELDS else {"type": "string"})
    for key in sorted(PUBLIC_OBSERVATION_FIELDS)
}
for _field, _schema in OBSERVATION_PROPERTIES.items():
    if _schema.get("type") == "string":
        _schema["maxLength"] = FIELD_LIMITS.get(_field, 2048)
OBSERVATION_PROPERTIES["url"]["minLength"] = 1
OBSERVATION_PROPERTIES["observation_id"] = HASH_SCHEMA
OBSERVATION_SCHEMA = {
    "type": "object",
    "required": ["observation_id", "url"],
    "properties": OBSERVATION_PROPERTIES,
    "additionalProperties": False,
}
VERDICT_SCHEMA = {
    "type": "object",
    "required": ["kind", "reason", "event_id", "at"],
    "properties": {
        "kind": {"enum": sorted(VERDICTS)},
        "reason": {"type": "string", "minLength": 1,
                   "maxLength": MAX_REASON_CHARS},
        "source_id": {"type": "string", "pattern": SOURCE_ID_RE.pattern,
                      "maxLength": 256},
        "supersedes": HASH_SCHEMA,
        "event_id": HASH_SCHEMA,
        "at": STAMP_SCHEMA,
    },
    "additionalProperties": False,
}
RETRY_SCHEMA = {
    "type": "object",
    "required": ["reason", "event_id", "at"],
    "properties": {
        "reason": {"type": "string", "minLength": 1,
                   "maxLength": MAX_REASON_CHARS},
        "event_id": HASH_SCHEMA,
        "at": STAMP_SCHEMA,
    },
    "additionalProperties": False,
}
EVENT_HISTORY_SCHEMA = {
    "type": "object",
    "required": ["ordinal", "type", "event_id", "at", "resulting_state"],
    "properties": {
        "ordinal": {"type": "integer", "minimum": 1},
        "type": {"enum": sorted(EVENT_TYPES)},
        "event_id": HASH_SCHEMA,
        "at": STAMP_SCHEMA,
        "resulting_state": {"enum": sorted(STATES)},
        "observation_id": HASH_SCHEMA,
        "state": {"enum": sorted(STATES)},
        "verdict": {"enum": sorted(VERDICTS)},
        "reason": {"type": "string", "minLength": 1,
                   "maxLength": MAX_REASON_CHARS},
        "source_id": {"type": "string", "pattern": SOURCE_ID_RE.pattern},
        "supersedes": HASH_SCHEMA,
        "expected_head": HASH_SCHEMA,
    },
    "additionalProperties": False,
}

TRANSACTION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "transaction-v1.schema.json",
    "title": "Coldcard incident discovery transaction",
    "type": "object",
    "required": ["schema", "sequence", "previous", "at", "kind",
                 "operation_id", "events", "transaction_id"],
    "properties": {
        "schema": {"const": 1},
        "sequence": {"type": "integer", "minimum": 1},
        "previous": {"oneOf": [{"type": "null"}, HASH_SCHEMA]},
        "at": STAMP_SCHEMA,
        "kind": {"type": "string", "minLength": 1},
        "operation_id": {"type": "string", "minLength": 1,
                         "maxLength": 512},
        "events": {
            "type": "array", "minItems": 1,
            "maxItems": MAX_EVENTS_PER_TRANSACTION,
            "items": {
                "type": "object",
                "required": ["event_id", "candidate", "type", "at", "payload"],
                "properties": {
                    "event_id": HASH_SCHEMA,
                    "candidate": IDENTITY_SCHEMA,
                    "type": {"enum": sorted(EVENT_TYPES)},
                    "at": STAMP_SCHEMA,
                    "payload": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        "transaction_id": HASH_SCHEMA,
    },
    "additionalProperties": False,
}
CANDIDATE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "candidate-v1.schema.json",
    "title": "Coldcard incident discovery candidate projection",
    "type": "object",
    "required": ["schema", "identity", "platform", "native_id", "url",
                 "state", "head", "first_recorded", "last_recorded",
                 "first_ordinal", "last_ordinal", "observations",
                 "retry_history", "verdict_history", "event_history"],
    "properties": {
        "schema": {"const": 1},
        "identity": IDENTITY_SCHEMA,
        "platform": {"type": "string", "maxLength": 128,
                     "pattern": "^[a-z0-9][a-z0-9._-]*$"},
        "native_id": {"type": "string", "maxLength": 128,
                      "pattern": "^[a-z0-9][a-z0-9._-]*$"},
        "url": {"type": "string", "minLength": 1,
                "maxLength": FIELD_LIMITS["url"]},
        "state": {"enum": sorted(STATES)},
        "head": HASH_SCHEMA,
        "first_recorded": STAMP_SCHEMA,
        "last_recorded": STAMP_SCHEMA,
        "first_ordinal": {"type": "integer", "minimum": 1},
        "last_ordinal": {"type": "integer", "minimum": 1},
        "observations": {"type": "array", "items": OBSERVATION_SCHEMA},
        "retry_history": {"type": "array", "items": RETRY_SCHEMA},
        "verdict_history": {"type": "array", "items": VERDICT_SCHEMA},
        "event_history": {"type": "array", "items": EVENT_HISTORY_SCHEMA},
        "retry": RETRY_SCHEMA,
        "verdict": VERDICT_SCHEMA,
        "state_reason": {
            "type": "object",
            "required": ["reason", "event_id", "at"],
            "properties": {
                "reason": {"type": "string", "minLength": 1,
                           "maxLength": MAX_REASON_CHARS},
                "event_id": HASH_SCHEMA,
                "at": STAMP_SCHEMA,
                "supersedes": HASH_SCHEMA,
            },
            "additionalProperties": False,
        },
        "priority": {"type": "string"},
        "queue_rank": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def pretty_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def markdown_text(value: object) -> str:
    """Render untrusted one-line text without creating Markdown structure."""
    text = re.sub(r"[\r\n]+", " ", str(value)).strip()
    text = "".join(
        "\N{REPLACEMENT CHARACTER}"
        if unicodedata.category(character).startswith("C") else character
        for character in text
    )
    text = re.sub(r"([\\`*~_[\]{}()<>#+.!|])", r"\\\1", text)
    return re.sub(
        r"(?i)\b(https?|ftp)://", lambda match: match.group(1) + "&#58;//",
        text)


def unsafe_url_text(value: object) -> bool:
    text = str(value)
    return (any(unicodedata.category(character).startswith("C")
                for character in text)
            or bool(re.search(r"[\s<>\\`\"']", text)))


def markdown_url(value: object) -> str | None:
    """Return a safe angle-bracket Markdown destination, or no link."""
    text = str(value)
    parsed = urlsplit(text)
    if unsafe_url_text(text) or parsed.scheme not in {"http", "https"} \
            or not parsed.hostname:
        return None
    return text


def markdown_suffix(value: object) -> str:
    """Preserve readable inline metadata without allowing Markdown markup."""
    text = re.sub(r"[\r\n]+", " ", str(value)).strip()
    text = "".join(
        "\N{REPLACEMENT CHARACTER}"
        if unicodedata.category(character).startswith("C") else character
        for character in text
    )
    # Parentheses and punctuation are useful in legacy author/comment
    # suffixes and cannot form links once brackets, markup and HTML delimiters
    # are escaped.
    text = re.sub(r"([\\`*~_[\]{}<>#!|])", r"\\\1", text)
    return re.sub(
        r"(?i)\b(https?|ftp)://", lambda match: match.group(1) + "&#58;//",
        text)


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_stamp(value: str) -> str:
    value = str(value).strip()
    if re.fullmatch(r"\d{8}T\d{6}Z", value):
        datetime.strptime(value, "%Y%m%dT%H%M%SZ")
        return value
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        value += "T00:00:00+00:00"
    elif value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid discovery timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def month_of(stamp: str) -> str:
    stamp = normalize_stamp(stamp)
    return f"{stamp[:4]}-{stamp[4:6]}"


def safe_part(value: str) -> str:
    if not isinstance(value, str) or len(value) > 128 \
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value):
        raise ValueError(f"unsafe discovery path part: {value!r}")
    return value


def identity_parts(platform: str, native_id: str) -> tuple[str, str]:
    return safe_part(platform), safe_part(native_id)


def url_identity(url: str, *, strict: bool = False) -> tuple[str, str]:
    """Return a stable platform/native identity for one public candidate."""
    if strict and (not isinstance(url, str) or unsafe_url_text(url)):
        raise ValueError(f"not a safe discovery candidate URL: {url!r}")
    if url.startswith("urn:coldcard-discovery:legacy:"):
        if strict:
            raise ValueError(
                "legacy discovery identities are import-only, not candidates")
        return identity_parts("legacy", url.rsplit(":", 1)[-1])
    parsed = urlsplit(url)
    if strict and (parsed.scheme not in {"http", "https"}
                   or parsed.username is not None
                   or parsed.password is not None):
        raise ValueError(f"not a safe discovery candidate URL: {url}")
    host = (parsed.hostname or "").lower()
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        match = (re.fullmatch(
            r"/(?:[^/]+|i/web)/status/(\d+)/?", parsed.path)
            if strict else X_RE.search(parsed.path))
        if match:
            return identity_parts("x", match.group(1))
    if host == "redd.it":
        if strict:
            short = re.fullmatch(r"/([0-9a-z]+)/?", parsed.path, re.I)
            native_id = short.group(1).lower() if short else ""
        else:
            native_id = parsed.path.strip("/").split("/", 1)[0].lower()
        if re.fullmatch(r"[0-9a-z]+", native_id):
            return identity_parts("reddit", native_id)
    if host == "reddit.com" or host.endswith(".reddit.com"):
        match = (re.fullmatch(
            r"/(?:r/[^/]+/)?comments/([0-9a-z]+)(?:/[^/]+)?/?",
            parsed.path, re.I) if strict else REDDIT_RE.search(parsed.path))
        if match:
            return identity_parts("reddit", match.group(1).lower())
    if host == "stacker.news":
        match = (re.fullmatch(r"/items/(\d+)/?", parsed.path)
                 if strict else re.search(r"/items/(\d+)", parsed.path))
        if match:
            return identity_parts("stackernews", match.group(1))
    if host == "bitcointalk.org":
        raw_topic = parse_qs(parsed.query).get("topic", [""])[0]
        if (not strict or parsed.path == "/index.php") \
                and (match := re.fullmatch(r"(\d+)(?:\.\d+)?", raw_topic)):
            return identity_parts("bitcointalk", match.group(1))
    if host == "njump.me":
        match = (re.fullmatch(
            r"/(note1[023456789acdefghjklmnpqrstuvwxyz]+)/?",
            parsed.path, re.I) if strict else NOSTR_RE.search(parsed.path))
        if match:
            return identity_parts("nostr", match.group(1).lower())
    if strict:
        raise ValueError(f"not a recognised discovery candidate URL: {url}")
    platform = re.sub(r"[^a-z0-9]+", "-", host).strip("-") or "unknown"
    return identity_parts(
        platform, hashlib.sha256(url.encode("utf-8")).hexdigest()[:20])


def candidate_key(platform: str, native_id: str) -> str:
    platform, native_id = identity_parts(platform, native_id)
    return f"{platform}:{native_id}"


def audit_legacy_line_semantics(line: str, section: str, queue_rank: int,
                                path: str, line_number: int) -> dict:
    """Independently parse one held legacy bullet for migration validation.

    This deliberately does not call the installer's parser. The cutover
    records its interpretation in an occurrence table; validation reparses
    the byte-exact held line here and compares both interpretations before
    accepting the event bindings.
    """
    actions: list[tuple[int, dict]] = []
    for match in LEGACY_RETRY_RE.finditer(line):
        actions.append((match.start(), {
            "action": "retry", "reason": match.group(1).strip(),
            "at": match.group(2),
        }))
    for match in LEGACY_DISMISSED_RE.finditer(line):
        actions.append((match.start(), {
            "action": "dismissed", "reason": match.group(1).strip(),
            "at": match.group(2),
        }))
    for match in LEGACY_ALREADY_RE.finditer(line):
        source_id = match.group(1)
        actions.append((match.start(), {
            "action": "already-registered", "source_id": source_id,
            "reason": f"already registered as {source_id}",
            "at": match.group(2),
        }))
    for match in LEGACY_REGISTERED_RE.finditer(line):
        source_id = match.group(1)
        actions.append((match.start(), {
            "action": "registered", "source_id": source_id,
            "reason": f"registered as {source_id}",
            "at": match.group(2),
        }))
    actions.sort(key=lambda item: item[0])
    parsed_actions = [value for _position, value in actions]

    url_match = URL_RE.search(line)
    if url_match:
        url = url_match.group(1)
        platform, native_id = url_identity(url)
    else:
        native_id = hashlib.sha256(line.encode("utf-8")).hexdigest()[:20]
        platform = "legacy"
        url = f"urn:coldcard-discovery:legacy:{native_id}"
    identity = candidate_key(platform, native_id)
    section_state = {
        "Pending": "pending",
        "Deferred": "deferred",
        "Assessed": "assessed",
        "Link review, held for a human decision": "human-review",
    }.get(section, "human-review")
    initial_state = (
        "pending" if section_state == "assessed" and parsed_actions
        else "human-review" if section_state == "assessed"
        else section_state
    )
    date = LEGACY_SOURCE_DATE_RE.match(line)
    if parsed_actions:
        event_at = parsed_actions[0]["at"]
        time_basis = "verdict-stamp"
    elif date:
        event_at = "".join(date.groups()) + "T000000Z"
        time_basis = "source-display-date"
    else:
        event_at = "19700101T000000Z"
        time_basis = "unknown-placeholder"
    return {
        "path": path,
        "line_number": line_number,
        "queue_rank": queue_rank,
        "section": section,
        "identity": identity,
        "url": url,
        "initial_state": initial_state,
        "event_at": event_at,
        "time_basis": time_basis,
        "actions": parsed_actions,
    }


def _new_parents(path: Path) -> list[Path]:
    missing: list[Path] = []
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        missing.append(parent)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        os.chmod(created, 0o755)
        parent_fd = os.open(created.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            # Persist each new directory entry, not only the eventual file in
            # the deepest directory. Otherwise a first YYYY-MM transaction can
            # be acknowledged yet lose its month directory on power failure.
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    return missing


def atomic_bytes(path: Path, value: bytes, *, mode: int | None = None) -> None:
    """Durably replace a generated file without following a symlink."""
    if path.is_symlink():
        raise ValueError(f"refusing to replace symlink: {path}")
    _new_parents(path)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        selected_mode = mode
        if selected_mode is None:
            selected_mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
        os.chmod(tmp, selected_mode)
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Durably replace generated UTF-8 text without following a symlink."""
    atomic_bytes(path, text.encode("utf-8"), mode=mode)


def immutable_json(path: Path, value: dict, *, temp_dir: Path | None = None,
                   max_bytes: int = MAX_TRANSACTION_BYTES) -> None:
    """Publish one immutable transaction with an exclusive atomic link."""
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    _new_parents(path)
    encoded = pretty_json(value).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"discovery transaction exceeds {max_bytes} bytes: {len(encoded)}")
    staging = temp_dir or path.parent
    if temp_dir is None:
        staging.mkdir(parents=True, exist_ok=True)
    if staging.is_symlink() or not staging.is_dir():
        raise ValueError(f"discovery transaction staging is unsafe: {staging}")
    fd, raw = tempfile.mkstemp(prefix="transaction-", dir=staging)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.link(tmp, path, follow_symlinks=False)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path: Path, *, max_bytes: int = MAX_TRANSACTION_BYTES,
              canonical: bool = False) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"discovery JSON is not a regular file: {path}")
    if path.stat().st_size > max_bytes:
        raise ValueError(f"discovery JSON exceeds {max_bytes} bytes: {path}")

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid discovery JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"discovery JSON is not an object: {path}")
    if canonical and raw != pretty_json(value).encode("utf-8"):
        raise ValueError(f"discovery JSON is not canonical: {path}")
    return value


def known_source_ids(root: Path) -> set[str] | None:
    """Return live and quarantined source ids when a registry is available."""
    legacy = Path(root) / "sources.toml"
    if not legacy.is_file():
        return None
    try:
        from registry_store import load as load_registry
        registry = load_registry(root)
    except (ImportError, OSError, ValueError) as exc:
        raise ValueError(f"cannot validate discovery source references: {exc}") from exc
    ids = {
        record["id"]
        for table in ("source", "x_post", "nostr_post")
        for record in registry.get(table, [])
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }
    quarantine = Path(root) / "quarantine"
    if quarantine.exists():
        if quarantine.is_symlink() or not quarantine.is_dir():
            raise ValueError("discovery source-reference quarantine is unsafe")
        for path in sorted(quarantine.glob("registry-????-??.toml")):
            if path.is_symlink() or not path.is_file():
                raise ValueError(
                    f"discovery source-reference quarantine is unsafe: {path}")
            try:
                value = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
                raise ValueError(
                    f"cannot validate quarantined source references: {path}: {exc}") \
                    from exc
            ids.update(
                record["id"]
                for table in ("source", "x_post", "nostr_post")
                for record in value.get(table, [])
                if isinstance(record, dict)
                and isinstance(record.get("id"), str)
            )
    return ids


def registration_source_ids(events: list[dict]) -> set[str]:
    return {
        event["payload"]["source_id"]
        for event in events
        if event.get("type") == "verdict"
        and event.get("payload", {}).get("kind") in {
            "registered", "already-registered"}
        and isinstance(event.get("payload", {}).get("source_id"), str)
    }


def public_observation(raw: dict) -> dict:
    """Return the bounded public subset of a driver-side observation."""
    if not isinstance(raw, dict):
        raise ValueError("discovery observation is not an object")
    value = {key: raw[key] for key in PUBLIC_OBSERVATION_FIELDS if key in raw}
    url = value.get("url")
    if not isinstance(url, str) or not url or len(url) > FIELD_LIMITS["url"]:
        raise ValueError("discovery observation has no bounded URL")
    for key, item in value.items():
        if key in INTEGER_OBSERVATION_FIELDS:
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise ValueError(f"discovery observation {key} is not a non-negative integer")
            continue
        if not isinstance(item, str):
            raise ValueError(f"discovery observation {key} is not text")
        limit = FIELD_LIMITS.get(key, 2048)
        if len(item) > limit or "\x00" in item:
            raise ValueError(f"discovery observation {key} exceeds its public bound")
        if key == "url" and unsafe_url_text(item):
            raise ValueError("discovery observation URL contains unsafe characters")
        if key in {"display_line", "legacy_candidate_line", "legacy_line"} \
                and ("\n" in item or "\r" in item):
            raise ValueError(
                f"discovery observation {key} must be one line")
    return value


def _event(kind: str, identity: str, at: str, payload: dict) -> dict:
    core = {
        "candidate": identity,
        "type": kind,
        "at": normalize_stamp(at),
        "payload": payload,
    }
    return {"event_id": digest(core), **core}


class DiscoveryStore:
    def __init__(self, root: Path = ROOT, *, bootstrap: bool = False):
        self.root = Path(root)
        self.bootstrap = bootstrap
        self.discovery = self.root / "discovery"
        self.transactions = self.discovery / "transactions"
        self.candidates = self.discovery / "candidates"
        self.views = self.discovery / "views"
        self.state_path = self.discovery / "state.json"
        self.marker = self.discovery / "migration-v1" / "manifest.json"
        self.lock_path = self.root / ".work" / "locks" / "discovery.lock"

    def _require_active(self) -> None:
        if self.bootstrap:
            return
        if self.discovery.is_symlink() or not self.discovery.is_dir():
            raise ValueError(
                "structured discovery namespace is not a regular directory")
        if self.marker.is_symlink() or not self.marker.is_file():
            raise ValueError(
                "structured discovery migration is not active; refusing to "
                "interpret or replace legacy DISCOVERY.md")
        manifest = read_json(
            self.marker, max_bytes=2 * 1024 * 1024, canonical=True)
        if manifest.get("schema") != 1:
            raise ValueError("structured discovery migration marker is invalid")

    def _open_lock_directory(self) -> int:
        """Open/create .work/locks without following either path component."""
        root_fd = os.open(
            self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        work_fd: int | None = None
        try:
            work_created = False
            try:
                os.mkdir(".work", 0o775, dir_fd=root_fd)
                work_created = True
            except FileExistsError:
                pass
            if work_created:
                os.fsync(root_fd)
            work_fd = os.open(
                ".work", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd)
        finally:
            os.close(root_fd)
        try:
            locks_created = False
            try:
                os.mkdir("locks", 0o700, dir_fd=work_fd)
                locks_created = True
            except FileExistsError:
                pass
            if locks_created:
                os.fsync(work_fd)
            lock_dir_fd = os.open(
                "locks", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=work_fd)
        finally:
            os.close(work_fd)
        os.fchmod(lock_dir_fd, 0o700)
        return lock_dir_fd

    def _transaction_staging(self) -> Path:
        """Prepare the private temp directory through a no-follow dir fd."""
        lock_dir_fd = self._open_lock_directory()
        staging_fd: int | None = None
        try:
            staging_created = False
            try:
                os.mkdir(".discovery-transactions", 0o700,
                         dir_fd=lock_dir_fd)
                staging_created = True
            except FileExistsError:
                pass
            if staging_created:
                os.fsync(lock_dir_fd)
            staging_fd = os.open(
                ".discovery-transactions",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=lock_dir_fd)
            os.fchmod(staging_fd, 0o700)
        finally:
            if staging_fd is not None:
                os.close(staging_fd)
            os.close(lock_dir_fd)
        return self.lock_path.parent / ".discovery-transactions"

    @contextmanager
    def locked(self):
        lock_dir_fd: int | None = None
        fd: int | None = None
        try:
            lock_dir_fd = self._open_lock_directory()
            fd = os.open(
                self.lock_path.name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600, dir_fd=lock_dir_fd)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "a+", encoding="utf-8") as handle:
                fd = None
                fcntl.flock(handle, fcntl.LOCK_EX)
                yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if lock_dir_fd is not None:
                try:
                    os.close(lock_dir_fd)
                except OSError:
                    pass

    def _lock_context(self, lock_held: bool):
        return nullcontext() if lock_held else self.locked()

    def candidate_path(self, identity: str) -> Path:
        platform, separator, native_id = identity.partition(":")
        if not separator:
            raise ValueError("identity must be <platform>:<native-id>")
        return (self.candidates / safe_part(platform) /
                f"{safe_part(native_id)}.json")

    def _transaction_paths(self) -> list[tuple[int, Path, str]]:
        if not self.transactions.exists():
            return []
        if self.transactions.is_symlink() or not self.transactions.is_dir():
            raise ValueError("discovery/transactions is not a regular directory")
        rows: list[tuple[int, Path, str]] = []
        for month in sorted(self.transactions.iterdir()):
            if month.is_symlink() or not month.is_dir() or not MONTH_RE.fullmatch(month.name):
                raise ValueError(f"unexpected discovery transaction entry: {month}")
            for path in sorted(month.iterdir()):
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"unexpected discovery transaction entry: {path}")
                match = TX_FILE_RE.fullmatch(path.name)
                if not match:
                    raise ValueError(f"unexpected discovery transaction filename: {path}")
                rows.append((int(match.group(1)), path, match.group(2)))
        rows.sort(key=lambda row: row[0])
        return rows

    @staticmethod
    def _validate_event(event: dict) -> None:
        if not isinstance(event, dict):
            raise ValueError("discovery event is not an object")
        required = {"event_id", "candidate", "type", "at", "payload"}
        if set(event) != required:
            raise ValueError("discovery event has unsupported fields")
        if event["type"] not in EVENT_TYPES or not isinstance(event["payload"], dict):
            raise ValueError("discovery event has an invalid type or payload")
        if not isinstance(event["at"], str) \
                or event["at"] != normalize_stamp(event["at"]):
            raise ValueError("discovery event timestamp is not canonical")
        platform, separator, native_id = str(event["candidate"]).partition(":")
        if not separator:
            raise ValueError("discovery event has an invalid candidate identity")
        safe_part(platform)
        safe_part(native_id)
        core = {key: event[key] for key in ("candidate", "type", "at", "payload")}
        if event["event_id"] != digest(core):
            raise ValueError("discovery event id does not match its payload")

    @classmethod
    def _validate_transaction(cls, tx: dict, sequence: int,
                              filename_hash: str, previous: str | None,
                              path: Path) -> None:
        required = {"schema", "sequence", "previous", "at", "kind",
                    "operation_id", "events", "transaction_id"}
        if set(tx) != required or type(tx.get("schema")) is not int \
                or tx["schema"] != 1:
            raise ValueError(f"unsupported discovery transaction schema: {path}")
        if type(tx["sequence"]) is not int or tx["sequence"] != sequence \
                or tx["previous"] != previous:
            raise ValueError(f"broken discovery transaction chain at {path}")
        if not isinstance(tx["kind"], str) or not tx["kind"] \
                or not isinstance(tx["operation_id"], str) \
                or not 1 <= len(tx["operation_id"]) <= 512:
            raise ValueError(f"invalid discovery transaction metadata: {path}")
        if not isinstance(tx["at"], str) \
                or tx["at"] != normalize_stamp(tx["at"]):
            raise ValueError(
                f"discovery transaction timestamp is not canonical: {path}")
        events = tx["events"]
        if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS_PER_TRANSACTION:
            raise ValueError(f"invalid discovery transaction event count: {path}")
        for event in events:
            cls._validate_event(event)
        if tx["at"] != max(event["at"] for event in events):
            raise ValueError(
                f"discovery transaction time is not its latest event: {path}")
        core = {key: tx[key] for key in required - {"transaction_id"}}
        expected = digest(core)
        if tx["transaction_id"] != expected or filename_hash != expected:
            raise ValueError(f"discovery transaction hash mismatch: {path}")
        if month_of(tx["at"]) != path.parent.name:
            raise ValueError(f"discovery transaction is in the wrong month: {path}")

    def load_transactions(self) -> list[dict]:
        transactions: list[dict] = []
        previous: str | None = None
        operations: set[str] = set()
        event_ids: set[str] = set()
        paths = self._transaction_paths()
        for expected_sequence, (sequence, path, filename_hash) in enumerate(paths, 1):
            if sequence != expected_sequence:
                raise ValueError("discovery transaction sequence is not contiguous")
            tx = read_json(path, canonical=True)
            self._validate_transaction(tx, sequence, filename_hash, previous, path)
            if tx["operation_id"] in operations:
                raise ValueError(f"duplicate discovery operation id: {tx['operation_id']}")
            operations.add(tx["operation_id"])
            for event in tx["events"]:
                event_id = event["event_id"]
                if event_id in event_ids:
                    raise ValueError(f"duplicate discovery event id: {event_id}")
                event_ids.add(event_id)
            transactions.append(tx)
            previous = tx["transaction_id"]
        return transactions

    @staticmethod
    def _apply_event(candidate: dict | None, event: dict, ordinal: int) -> dict:
        kind, payload = event["type"], event["payload"]
        if kind == "observation":
            required = {"platform", "native_id", "url", "state", "observation"}
            if set(payload) != required \
                    or payload["state"] not in OBSERVATION_STATES:
                raise ValueError("observation event has an invalid payload")
            observation = payload["observation"]
            if not isinstance(observation, dict):
                raise ValueError("observation event lacks its public observation")
            oid = observation.get("observation_id")
            bare = {key: value for key, value in observation.items()
                    if key != "observation_id"}
            if public_observation(bare) != bare or oid != digest(bare):
                raise ValueError("observation id or public schema is invalid")
            expected = candidate_key(payload["platform"], payload["native_id"])
            if expected != event["candidate"]:
                raise ValueError("observation identity disagrees with candidate")
            url = payload["url"]
            parsed_url = urlsplit(url) if isinstance(url, str) else None
            archival_urn = isinstance(url, str) and url.startswith(
                "urn:coldcard-discovery:legacy:")
            if not isinstance(url, str) or url != observation.get("url") \
                    or (not archival_urn and (
                        parsed_url.scheme not in {"http", "https"}
                        or not parsed_url.hostname)) \
                    or candidate_key(*url_identity(url)) != event["candidate"]:
                raise ValueError(
                    "observation URL disagrees with its payload or candidate")
            if candidate is None:
                candidate = {
                    "schema": 1,
                    "identity": expected,
                    "platform": payload["platform"],
                    "native_id": payload["native_id"],
                    "url": payload["url"],
                    "state": payload["state"],
                    "first_recorded": event["at"],
                    "last_recorded": event["at"],
                    "first_ordinal": ordinal,
                    "observations": [],
                    "retry_history": [],
                    "verdict_history": [],
                    "event_history": [],
                }
            if candidate["identity"] != expected:
                raise ValueError("candidate projection identity changed")
            if all(row.get("observation_id") != oid
                   for row in candidate["observations"]):
                candidate["observations"].append(dict(observation))
            candidate["url"] = payload["url"]
            if observation.get("priority") == "operator":
                candidate["priority"] = "operator"
            if isinstance(observation.get("legacy_queue_rank"), int):
                candidate["queue_rank"] = min(
                    candidate.get("queue_rank", 10**12),
                    observation["legacy_queue_rank"])
            current = candidate["state"]
            requested = payload["state"]
            if current == "deferred" and requested == "pending":
                candidate["state"] = "pending"
            elif current not in {"assessed", "human-review", "pending"}:
                candidate["state"] = requested
            if candidate["state"] != current:
                candidate.pop("state_reason", None)
                if candidate["state"] != "pending":
                    candidate.pop("retry", None)
        elif kind == "retry":
            if candidate is None or candidate["state"] != "pending":
                raise ValueError("retry does not follow a pending candidate")
            if set(payload) - {"reason", "expected_head"} \
                    or "reason" not in payload \
                    or not isinstance(payload["reason"], str) \
                    or not payload["reason"].strip() \
                    or len(payload["reason"]) > MAX_REASON_CHARS \
                    or any(character in payload["reason"]
                           for character in ("\x00", "\r", "\n")):
                raise ValueError("retry event has an invalid reason")
            expected_head = payload.get("expected_head")
            if expected_head is not None and (
                    not isinstance(expected_head, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_head)
                    or expected_head != candidate.get("head")):
                raise ValueError("retry expected head does not match candidate")
            retry = {"event_id": event["event_id"], "at": event["at"],
                     "reason": payload["reason"]}
            candidate["retry"] = retry
            candidate["retry_history"].append(retry)
        elif kind == "verdict":
            if candidate is None:
                raise ValueError("verdict precedes candidate observation")
            allowed = {"kind", "reason", "source_id", "supersedes", "expected_head"}
            if set(payload) - allowed or payload.get("kind") not in VERDICTS \
                    or not isinstance(payload.get("reason"), str) \
                    or not payload["reason"].strip() \
                    or len(payload["reason"]) > MAX_REASON_CHARS \
                    or any(character in payload["reason"]
                           for character in ("\x00", "\r", "\n")):
                raise ValueError("verdict event has an invalid payload")
            source_id = payload.get("source_id")
            if source_id is not None and (
                    not isinstance(source_id, str)
                    or not SOURCE_ID_RE.fullmatch(source_id)):
                raise ValueError("verdict event has an invalid source id")
            if payload["kind"] in {"registered", "already-registered"} \
                    and source_id is None:
                raise ValueError("registration verdict has no source id")
            if payload["kind"] == "dismissed" and source_id is not None:
                raise ValueError("dismissed verdict unexpectedly has a source id")
            expected_head = payload.get("expected_head")
            if expected_head is not None and (
                    not isinstance(expected_head, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_head)
                    or expected_head != candidate.get("head")):
                raise ValueError("verdict expected head does not match candidate")
            current = candidate.get("verdict")
            supersedes = payload.get("supersedes")
            if supersedes is not None and (
                    not isinstance(supersedes, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", supersedes)):
                raise ValueError("verdict supersedes is not an event hash")
            if current:
                if supersedes != current.get("event_id"):
                    raise ValueError("replacement verdict does not supersede current verdict")
            elif supersedes is not None or candidate["state"] not in {
                    "pending", "deferred", "human-review"}:
                raise ValueError("verdict does not follow an open candidate")
            verdict = {key: value for key, value in payload.items()
                       if key != "expected_head" and value is not None}
            verdict.update({"event_id": event["event_id"], "at": event["at"]})
            candidate["verdict"] = verdict
            candidate["verdict_history"].append(verdict)
            candidate["state"] = "assessed"
            candidate.pop("retry", None)
            candidate.pop("state_reason", None)
        else:
            if candidate is None:
                raise ValueError("state event precedes candidate observation")
            if set(payload) - {"state", "reason", "supersedes"} \
                    or not {"state", "reason"} <= set(payload) \
                    or payload["state"] not in {
                    "pending", "deferred", "human-review"} \
                    or not isinstance(payload["reason"], str) \
                    or not payload["reason"].strip() \
                    or len(payload["reason"]) > MAX_REASON_CHARS \
                    or any(character in payload["reason"]
                           for character in ("\x00", "\r", "\n")):
                raise ValueError("state event has an invalid payload")
            supersedes = payload.get("supersedes")
            if candidate["state"] == "assessed":
                current = candidate.get("verdict")
                if payload["state"] != "pending" or not isinstance(current, dict) \
                        or supersedes != current.get("event_id"):
                    raise ValueError(
                        "state event cannot reopen an assessed candidate "
                        "without superseding its verdict")
                candidate.pop("verdict", None)
            elif supersedes is not None:
                raise ValueError(
                    "state event supersedes a verdict on an open candidate")
            candidate["state"] = payload["state"]
            candidate.pop("retry", None)
            candidate["state_reason"] = {
                "event_id": event["event_id"], "at": event["at"],
                "reason": payload["reason"],
                **({"supersedes": supersedes} if supersedes else {}),
            }
        if (candidate["state"] == "assessed") != \
                isinstance(candidate.get("verdict"), dict):
            raise ValueError(
                "candidate assessed state and current verdict disagree")
        if candidate.get("retry") is not None \
                and candidate["state"] != "pending":
            raise ValueError("candidate has a current retry outside pending")
        history = {
            "ordinal": ordinal,
            "type": kind,
            "event_id": event["event_id"],
            "at": event["at"],
            "resulting_state": candidate["state"],
        }
        if kind == "observation":
            history.update({
                "observation_id": payload["observation"]["observation_id"],
                "state": payload["state"],
            })
        elif kind == "verdict":
            history["verdict"] = payload["kind"]
            for key in ("reason", "source_id", "supersedes", "expected_head"):
                if payload.get(key) is not None:
                    history[key] = payload[key]
        else:
            history["state"] = ("pending" if kind == "retry"
                                else payload["state"])
            for key in ("reason", "supersedes", "expected_head"):
                if payload.get(key) is not None:
                    history[key] = payload[key]
        candidate.setdefault("event_history", []).append(history)
        candidate["last_recorded"] = event["at"]
        candidate["head"] = event["event_id"]
        candidate["last_ordinal"] = ordinal
        return candidate

    @classmethod
    def project(cls, transactions: list[dict]) -> dict[str, dict]:
        state: dict[str, dict] = {}
        ordinal = 0
        for tx in transactions:
            for event in tx["events"]:
                ordinal += 1
                identity = event["candidate"]
                state[identity] = cls._apply_event(state.get(identity), event, ordinal)
        return state

    def _loaded(self) -> tuple[list[dict], dict[str, dict]]:
        self._require_active()
        transactions = self.load_transactions()
        return transactions, self.project(transactions)

    @staticmethod
    def _sort_key(candidate: dict) -> tuple:
        return (
            0 if candidate.get("priority") == "operator" else 1,
            candidate.get("queue_rank", 10**12),
            candidate.get("first_ordinal", 10**12),
            candidate["identity"],
        )

    def list_candidates(self, state: str | None = None,
                        platform: str | None = None, *, lane: str | None = None,
                        lock_held: bool = False) -> list[dict]:
        with self._lock_context(lock_held):
            _transactions, projected = self._loaded()
        rows = []
        for candidate in projected.values():
            if state and candidate["state"] != state:
                continue
            if platform and candidate["platform"] != platform:
                continue
            if lane == "x" and candidate["platform"] != "x":
                continue
            if lane == "community" and candidate["platform"] == "x":
                continue
            rows.append(candidate)
        return sorted(rows, key=self._sort_key)

    def count(self, state: str | None = None,
              platform: str | None = None, *, lane: str | None = None,
              lock_held: bool = False) -> int:
        return len(self.list_candidates(state, platform, lane=lane,
                                        lock_held=lock_held))

    def load_candidate(self, identity: str, *, lock_held: bool = False) -> dict | None:
        with self._lock_context(lock_held):
            _transactions, projected = self._loaded()
        return projected.get(identity)

    @staticmethod
    def _observation_event(raw: dict, state: str, at: str, *,
                           strict_identity: bool) -> dict:
        observation = public_observation(raw)
        platform, native_id = url_identity(observation["url"], strict=strict_identity)
        identity = candidate_key(platform, native_id)
        observation = {"observation_id": digest(observation), **observation}
        return _event("observation", identity, at, {
            "platform": platform,
            "native_id": native_id,
            "url": observation["url"],
            "state": state,
            "observation": observation,
        })

    @staticmethod
    def _verdict_event(identity: str, kind: str, reason: str, at: str, *,
                       source_id: str | None = None,
                       supersedes: str | None = None,
                       expected_head: str | None = None) -> dict:
        if kind not in VERDICTS:
            raise ValueError(f"unknown discovery verdict: {kind}")
        payload = {"kind": kind, "reason": reason}
        if source_id:
            payload["source_id"] = source_id
        if supersedes:
            payload["supersedes"] = supersedes
        if expected_head:
            payload["expected_head"] = expected_head
        return _event("verdict", identity, at, payload)

    @staticmethod
    def _retry_event(identity: str, reason: str, at: str, *,
                     expected_head: str | None = None) -> dict:
        payload = {"reason": reason}
        if expected_head:
            payload["expected_head"] = expected_head
        return _event("retry", identity, at, payload)

    def _commit_unlocked(self, events: list[dict], *, kind: str, at: str,
                         operation_id: str | None = None) -> tuple[dict, dict[str, dict]]:
        if not events or len(events) > MAX_EVENTS_PER_TRANSACTION:
            raise ValueError("a discovery transaction needs 1-2000 events")
        if kind == "migration-v1" and self.marker.exists():
            raise ValueError("the one-time discovery migration is already sealed")
        for event in events:
            self._validate_event(event)
        if kind != "migration-v1":
            known = known_source_ids(self.root)
            referenced = registration_source_ids(events)
            if known is not None and not referenced <= known:
                raise ValueError(
                    "discovery verdict references unknown source id(s): "
                    + ", ".join(sorted(referenced - known)))
        transaction_at = normalize_stamp(at)
        if transaction_at != max(event["at"] for event in events):
            raise ValueError("discovery transaction time must equal its latest event")
        transactions, before = self._loaded()
        operation_id = operation_id or digest({"kind": kind, "events": events})
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 512 \
                or "\n" in operation_id or "\r" in operation_id:
            raise ValueError("invalid discovery operation id")
        existing = next((tx for tx in transactions
                         if tx["operation_id"] == operation_id), None)
        if existing:
            if existing["kind"] != kind or existing["events"] != events:
                raise ValueError("discovery operation id was reused with different content")
            self._write_projections_unlocked(transactions, before)
            return existing, before
        prior_event_ids = {
            event["event_id"]
            for transaction in transactions
            for event in transaction["events"]
        }
        new_event_ids = [event["event_id"] for event in events]
        if len(new_event_ids) != len(set(new_event_ids)) \
                or prior_event_ids & set(new_event_ids):
            raise ValueError("discovery event ids must be globally unique")
        trial = copy.deepcopy(before)
        ordinal = sum(len(tx["events"]) for tx in transactions)
        for event in events:
            ordinal += 1
            identity = event["candidate"]
            trial[identity] = self._apply_event(trial.get(identity), event, ordinal)
        sequence = len(transactions) + 1
        core = {
            "schema": 1,
            "sequence": sequence,
            "previous": transactions[-1]["transaction_id"] if transactions else None,
            "at": transaction_at,
            "kind": kind,
            "operation_id": operation_id,
            "events": events,
        }
        tx = {**core, "transaction_id": digest(core)}
        path = (self.transactions / month_of(tx["at"]) /
                f"{sequence:08d}-{tx['transaction_id']}.json")
        immutable_json(
            path, tx,
            temp_dir=self._transaction_staging())
        transactions.append(tx)
        self._write_projections_unlocked(transactions, trial)
        return tx, trial

    def commit_events(self, events: list[dict], *, kind: str,
                      at: str | None = None, operation_id: str | None = None,
                      lock_held: bool = False) -> dict:
        with self._lock_context(lock_held):
            tx, _state = self._commit_unlocked(
                events, kind=kind, at=at or stamp_now(), operation_id=operation_id)
            return tx

    def record_observation(self, observation: dict, *, state: str = "pending",
                           event_at: str | None = None,
                           strict_identity: bool = True,
                           operation_id: str | None = None,
                           lock_held: bool = False) -> dict:
        if state not in OBSERVATION_STATES:
            raise ValueError(f"unknown candidate state: {state}")
        at = normalize_stamp(event_at or observation.get("foundAt") or stamp_now())
        event = self._observation_event(observation, state, at,
                                        strict_identity=strict_identity)
        with self._lock_context(lock_held):
            _tx, projected = self._commit_unlocked(
                [event], kind="observation", at=at, operation_id=operation_id)
            return projected[event["candidate"]]

    def reconcile_observations(self, observations: list[dict], *,
                               known_urls: set[str] | dict[str, str] | None = None,
                               operation_id: str | None = None,
                               lock_held: bool = False) -> list[dict]:
        with self._lock_context(lock_held):
            if known_urls and not isinstance(known_urls, dict):
                raise ValueError(
                    "registered discovery URLs require a URL-to-source-id mapping")
            transactions, projected = self._loaded()
            known: dict[str, str | None] = {}
            by_url = known_urls if isinstance(known_urls, dict) else {}
            for url in known_urls or set():
                try:
                    known[candidate_key(*url_identity(url))] = by_url.get(url)
                except ValueError:
                    continue
            events: list[dict] = []
            working = copy.deepcopy(projected)
            ordinal = sum(len(tx["events"]) for tx in transactions)
            now = stamp_now()

            def add(event: dict) -> None:
                nonlocal ordinal
                events.append(event)
                ordinal += 1
                identity = event["candidate"]
                working[identity] = self._apply_event(
                    working.get(identity), event, ordinal)

            for raw in observations:
                item = dict(raw)
                state = item.pop("state", "pending")
                if state not in OBSERVATION_STATES:
                    raise ValueError(f"unknown candidate state: {state}")
                at = normalize_stamp(item.get("foundAt") or now)
                event = self._observation_event(item, state, at,
                                                strict_identity=True)
                identity = event["candidate"]
                current = working.get(identity)
                oid = event["payload"]["observation"]["observation_id"]
                if current is None or all(
                        row.get("observation_id") != oid
                        for row in current.get("observations", [])):
                    add(event)
            # Observations belong before settlement in this batch. Otherwise a
            # newly seen, earlier-dated observation can become the candidate's
            # ordinal head after an already-registered verdict (an assessed
            # record whose apparent head and last_recorded point backwards).
            for identity, source_id in sorted(known.items()):
                candidate = working.get(identity)
                if candidate and candidate["state"] in {"pending", "deferred"}:
                    verdict_at = max(now, candidate["last_recorded"])
                    add(self._verdict_event(
                        identity, "already-registered",
                        "registered outside discovery intake", verdict_at,
                        source_id=source_id))
            if not events:
                self._write_projections_unlocked(transactions, projected)
                return [working[candidate_key(*url_identity(item["url"]))]
                        for item in observations]
            tx_at = max(event["at"] for event in events)
            tx, final = self._commit_unlocked(
                events, kind="discovery-batch", at=tx_at,
                operation_id=operation_id)
            return [final[candidate_key(*url_identity(item["url"]))]
                    for item in observations]

    def apply_actions(self, actions: list[dict], *, at: str | None = None,
                      operation_id: str | None = None,
                      lock_held: bool = False) -> list[dict]:
        default_at = normalize_stamp(at or stamp_now())
        events: list[dict] = []
        seen: set[str] = set()
        for action in actions:
            identity = action.get("candidate_id")
            kind = action.get("action")
            reason = action.get("reason")
            event_at = normalize_stamp(action.get("at") or default_at)
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError("intake actions need unique candidate ids")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"intake action needs a reason: {identity}")
            seen.add(identity)
            if kind == "retry":
                events.append(self._retry_event(
                    identity, reason, event_at,
                    expected_head=action.get("expected_head")))
            else:
                events.append(self._verdict_event(
                    identity, kind, reason, event_at,
                    source_id=action.get("source_id"),
                    expected_head=action.get("expected_head")))
        with self._lock_context(lock_held):
            transactions, projected = self._loaded()
            existing = next((tx for tx in transactions
                             if tx["operation_id"] == operation_id), None) \
                if operation_id else None
            if existing:
                if existing["kind"] != "intake-batch" or existing["events"] != events:
                    raise ValueError("intake operation id was reused with different content")
                self._write_projections_unlocked(transactions, projected)
                return [projected[action["candidate_id"]] for action in actions]
            for action in actions:
                candidate = projected.get(action["candidate_id"])
                if candidate is None or candidate["state"] != "pending":
                    raise ValueError(f"candidate is not pending: {action['candidate_id']}")
                expected = action.get("expected_head")
                if not isinstance(expected, str) or expected != candidate.get("head"):
                    raise ValueError(f"candidate head changed: {action['candidate_id']}")
            tx_at = max((event["at"] for event in events), default=default_at)
            _tx, final = self._commit_unlocked(
                events, kind="intake-batch", at=tx_at,
                operation_id=operation_id)
            return [final[action["candidate_id"]] for action in actions]

    def record_retry(self, identity: str, reason: str, *, at: str | None = None,
                     operation_id: str | None = None,
                     lock_held: bool = False) -> dict:
        event_at = normalize_stamp(at or stamp_now())
        event = self._retry_event(identity, reason, event_at)
        with self._lock_context(lock_held):
            _tx, projected = self._commit_unlocked(
                [event], kind="retry", at=event_at, operation_id=operation_id)
            return projected[identity]

    def record_verdict(self, identity: str, verdict: str, *, reason: str,
                       at: str | None = None, source_id: str | None = None,
                       supersedes: str | None = None,
                       operation_id: str | None = None,
                       lock_held: bool = False, **_legacy) -> dict:
        event_at = normalize_stamp(at or stamp_now())
        event = self._verdict_event(identity, verdict, reason, event_at,
                                    source_id=source_id,
                                    supersedes=supersedes)
        with self._lock_context(lock_held):
            _tx, projected = self._commit_unlocked(
                [event], kind="verdict", at=event_at, operation_id=operation_id)
            return projected[identity]

    def set_state(self, identity: str, state: str, *, reason: str,
                  at: str | None = None, operation_id: str | None = None,
                  supersedes: str | None = None,
                  lock_held: bool = False) -> dict:
        if state not in {"pending", "deferred", "human-review"}:
            raise ValueError("manual state must be pending, deferred or human-review")
        event_at = normalize_stamp(at or stamp_now())
        payload = {"state": state, "reason": reason}
        if supersedes:
            payload["supersedes"] = supersedes
        event = _event("state", identity, event_at, payload)
        with self._lock_context(lock_held):
            _tx, projected = self._commit_unlocked(
                [event], kind="state", at=event_at, operation_id=operation_id)
            return projected[identity]

    @staticmethod
    def display_line(candidate: dict) -> str:
        observations = candidate.get("observations") or []
        latest = observations[-1] if observations else {}
        legacy = latest.get("legacy_line")
        legacy_suffix = ""
        if legacy:
            candidate_line = str(
                latest.get("legacy_candidate_line")
                or str(legacy).split(" -> ", 1)[0])
            date_match = re.match(
                r"^- (\d{4}-\d{2}-\d{2}) ", candidate_line)
            date = date_match.group(1) if date_match else "unknown"
            url_text = str(candidate["url"])
            destination = re.search(
                r"\]\(<?" + re.escape(url_text) + r">?\)", candidate_line)
            title_start = candidate_line.find(
                "[", date_match.end() if date_match else 0)
            if destination and 0 <= title_start < destination.start():
                title = candidate_line[title_start + 1:destination.start()]
                legacy_suffix = markdown_suffix(
                    candidate_line[destination.end():])
            else:
                # The migration has one deliberately retained row whose
                # original bullet has no URL. Preserve its useful words as
                # plain text instead of exposing only an opaque legacy id.
                start = date_match.end() if date_match else 2
                title = candidate_line[start:].strip() or candidate["identity"]
        else:
            title = latest.get("title") or candidate["identity"]
            created = str(latest.get("createdAt") or "")[:10]
            date = created if re.fullmatch(r"\d{4}-\d{2}-\d{2}", created) else ""
        if not date:
            compact = candidate.get("first_recorded", "")[:8]
            date = (f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
                    if len(compact) == 8 and compact.isdigit()
                    else "unknown")
        escaped_title = markdown_text(title)
        url = markdown_url(candidate["url"])
        if url is None or url.startswith("urn:coldcard-discovery:legacy:"):
            line = f"- {date} {escaped_title}"
        else:
            line = f"- {date} [{escaped_title}](<{url}>)"
        if legacy:
            if legacy_suffix:
                line += f" {legacy_suffix}"
            return line
        platform = candidate["platform"]
        if platform == "x":
            label = latest.get("label") or latest.get("author")
            if label:
                line += f" ({markdown_text(label)})"
        elif platform == "nostr":
            if latest.get("author"):
                line += f" by {markdown_text(latest['author'])}"
            if isinstance(latest.get("relayCount"), int):
                noun = "relay" if latest["relayCount"] == 1 else "relays"
                line += f" ({latest['relayCount']} known {noun})"
            if latest.get("label"):
                line += f" ({markdown_text(latest['label'])})"
        else:
            if latest.get("author"):
                line += f" by {markdown_text(latest['author'])}"
            if isinstance(latest.get("ncomments"), int):
                line += f", {latest['ncomments']} comments"
            if latest.get("label"):
                line += f" ({markdown_text(latest['label'])})"
            if latest.get("tier") and latest["tier"] != "strong":
                line += f" [{markdown_text(latest['tier'])}]"
        return line

    @classmethod
    def verdict_line(cls, candidate: dict) -> str:
        verdict = candidate.get("verdict") or {}
        kind, source_id = verdict.get("kind"), verdict.get("source_id")
        if kind == "registered":
            suffix = f"registered as {source_id}" if source_id else "registered"
        elif kind == "already-registered":
            suffix = (f"already registered as {source_id}" if source_id
                      else "already registered")
        else:
            suffix = f"dismissed: {markdown_text(verdict.get('reason', ''))}".rstrip()
        return f"{cls.display_line(candidate)} -> {suffix} ({verdict.get('at', '')})"

    @staticmethod
    def _page(rows: list[dict], title: str, line, *, stem: str,
              record_prefix: str) -> list[tuple[int, str]]:
        pages = []
        page_count = (len(rows) + VIEW_PAGE_ITEMS - 1) // VIEW_PAGE_ITEMS
        for offset in range(0, len(rows), VIEW_PAGE_ITEMS):
            number = offset // VIEW_PAGE_ITEMS + 1
            body = [f"# {title} — page {number}", "",
                    "Generated from immutable discovery transactions; do not edit.", ""]
            navigation = ["[Index](./index.md)"]
            if number > 1:
                navigation.append(f"[Previous](./{stem}-{number - 1:03d}.md)")
            if number < page_count:
                navigation.append(f"[Next](./{stem}-{number + 1:03d}.md)")
            body.extend([" · ".join(navigation), ""])
            for candidate in rows[offset:offset + VIEW_PAGE_ITEMS]:
                record = (f"{record_prefix}/{safe_part(candidate['platform'])}/"
                          f"{safe_part(candidate['native_id'])}.json")
                body.append(f"{line(candidate)} · [record/history]({record})")
            body.extend(["", " · ".join(navigation)])
            pages.append((number, "\n".join(body).rstrip() + "\n"))
        return pages

    def _render_files(self, transactions: list[dict],
                      projected: dict[str, dict]) -> dict[str, str]:
        files: dict[str, str] = {}
        candidates = sorted(projected.values(), key=self._sort_key)
        for candidate in candidates:
            rel = self.candidate_path(candidate["identity"]).relative_to(self.root).as_posix()
            files[rel] = pretty_json(candidate)

        for state in ("pending", "deferred", "human-review"):
            groups: dict[str, list[dict]] = {}
            for candidate in candidates:
                if candidate["state"] == state:
                    groups.setdefault(candidate["platform"], []).append(candidate)
            index = [f"# Discovery {state.replace('-', ' ').title()}", "",
                     "Generated from immutable discovery transactions; do not edit.", ""]
            for platform, rows in sorted(groups.items()):
                pages = self._page(
                    rows, f"Discovery {state}: {platform}", self.display_line,
                    stem=platform, record_prefix="../../candidates")
                for number, text in pages:
                    name = f"{platform}-{number:03d}.md"
                    files[f"discovery/views/{state}/{name}"] = text
                if pages:
                    links = ", ".join(
                        f"[{number}](./{platform}-{number:03d}.md)"
                        for number, _text in pages)
                    index.append(f"- {platform}: {len(rows)} — pages {links}")
            files[f"discovery/views/{state}/index.md"] = "\n".join(index).rstrip() + "\n"

        assessed: dict[str, dict[str, list[dict]]] = {}
        for candidate in candidates:
            if candidate["state"] == "assessed":
                if not isinstance(candidate.get("verdict"), dict):
                    raise ValueError(
                        f"assessed candidate has no verdict: {candidate['identity']}")
                month = month_of(candidate["verdict"]["at"])
                assessed.setdefault(month, {}).setdefault(candidate["platform"], []).append(candidate)
        assessed_index = ["# Discovery Assessed", "",
                          "Generated from immutable discovery transactions; do not edit.", ""]
        for month, groups in sorted(assessed.items()):
            month_index = [f"# Discovery verdicts — {month}", "",
                           "Generated from immutable discovery transactions; do not edit.", ""]
            total = 0
            for platform, rows in sorted(groups.items()):
                total += len(rows)
                pages = self._page(
                    rows, f"Discovery verdicts: {month} / {platform}",
                    self.verdict_line, stem=platform,
                    record_prefix="../../../candidates")
                for number, text in pages:
                    name = f"{platform}-{number:03d}.md"
                    files[f"discovery/views/assessed/{month}/{name}"] = text
                if pages:
                    links = ", ".join(
                        f"[{number}](./{platform}-{number:03d}.md)"
                        for number, _text in pages)
                    month_index.append(
                        f"- {platform}: {len(rows)} — pages {links}")
            files[f"discovery/views/assessed/{month}/index.md"] = \
                "\n".join(month_index).rstrip() + "\n"
            assessed_index.append(f"- [{month}](./{month}/index.md): {total}")
        files["discovery/views/assessed/index.md"] = \
            "\n".join(assessed_index).rstrip() + "\n"

        counts = {state: sum(1 for candidate in candidates
                             if candidate["state"] == state)
                  for state in sorted(STATES)}
        platforms: dict[str, int] = {}
        for candidate in candidates:
            platforms[candidate["platform"]] = platforms.get(candidate["platform"], 0) + 1
        state = {
            "schema": 1,
            "transaction_count": len(transactions),
            "transaction_head": transactions[-1]["transaction_id"] if transactions else None,
            "candidate_count": len(candidates),
            "candidate_semantic_root": digest(candidates),
            "states": counts,
            "platforms": dict(sorted(platforms.items())),
        }
        files["discovery/state.json"] = pretty_json(state)
        root = ["# Discovery record", "",
                "The canonical history is organised as immutable transactions and",
                "one generated JSON projection per candidate. Markdown below is a",
                "generated working view, not a second record.", "",
                "- [Store format and recovery](discovery/README.md)",
                "- [Operator workflow and placement guide](docs/DISCOVERY.md)",
                f"- [Pending](discovery/views/pending/index.md): {counts['pending']}",
                f"- [Deferred](discovery/views/deferred/index.md): {counts['deferred']}",
                f"- [Human review](discovery/views/human-review/index.md): {counts['human-review']}",
                f"- [Assessed](discovery/views/assessed/index.md): {counts['assessed']}",
                "", "## Inventory", ""]
        root.extend(f"- {platform}: {count}" for platform, count in sorted(platforms.items()))
        root.extend(["", f"Total candidates: {len(candidates)}",
                     f"Transactions: {len(transactions)}"])
        files["DISCOVERY.md"] = "\n".join(root) + "\n"
        return files

    def _generated_actual(self) -> set[str]:
        paths: set[str] = set()
        for base in (self.candidates, self.views):
            if base.exists():
                if base.is_symlink() or not base.is_dir():
                    raise ValueError(f"generated discovery path is not a directory: {base}")
                for path in base.rglob("*"):
                    if path.is_symlink():
                        raise ValueError(f"generated discovery path is a symlink: {path}")
                    if path.is_file():
                        paths.add(path.relative_to(self.root).as_posix())
                    elif not path.is_dir():
                        raise ValueError(
                            f"generated discovery path has an unusual entry: {path}")
        if self.state_path.exists():
            if self.state_path.is_symlink() or not self.state_path.is_file():
                raise ValueError(
                    f"generated discovery path is not a regular file: "
                    f"{self.state_path}")
            paths.add("discovery/state.json")
        if (self.root / "DISCOVERY.md").exists():
            if (self.root / "DISCOVERY.md").is_symlink() \
                    or not (self.root / "DISCOVERY.md").is_file():
                raise ValueError(
                    "generated discovery index is not a regular file")
            paths.add("DISCOVERY.md")
        return paths

    def _write_projections_unlocked(self, transactions: list[dict],
                                    projected: dict[str, dict]) -> None:
        expected = self._render_files(transactions, projected)
        expected_paths = set(expected)
        # Inspect the complete existing projection tree before the first
        # write.  atomic_text() refuses a symlink at its final path, but a
        # symlinked platform/state ancestor would otherwise redirect a new
        # file outside the repository before the later inventory pass noticed
        # it.  Writers hold the operator-only discovery lock during this pass.
        actual_paths = self._generated_actual()
        for rel, text in sorted(expected.items()):
            mode = 0o640 if rel == "DISCOVERY.md" else 0o644
            path = self.root / rel
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"generated discovery path is not a regular file: {path}")
                if path.read_text(encoding="utf-8") == text:
                    if path.stat().st_mode & 0o777 != mode:
                        os.chmod(path, mode)
                    continue
            atomic_text(path, text, mode=mode)
        for rel in sorted(actual_paths - expected_paths, reverse=True):
            (self.root / rel).unlink()
        for base in (self.candidates, self.views):
            if base.exists():
                for directory in sorted((p for p in base.rglob("*") if p.is_dir()),
                                        key=lambda p: len(p.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass

    def render_all(self, *, lock_held: bool = False) -> dict[str, str]:
        with self._lock_context(lock_held):
            transactions, projected = self._loaded()
            self._write_projections_unlocked(transactions, projected)
            return self._render_files(transactions, projected)

    def projection_errors(self, *, lock_held: bool = False) -> list[str]:
        with self._lock_context(lock_held):
            transactions, projected = self._loaded()
            expected = self._render_files(transactions, projected)
            errors = []
            actual_paths = self._generated_actual()
            for rel in sorted(set(expected) - actual_paths):
                errors.append(f"missing generated discovery file: {rel}")
            for rel in sorted(actual_paths - set(expected)):
                errors.append(f"stale generated discovery file: {rel}")
            for rel in sorted(set(expected) & actual_paths):
                path = self.root / rel
                if path.is_symlink() or path.read_text(encoding="utf-8") != expected[rel]:
                    errors.append(f"generated discovery file differs: {rel}")
            return errors

    def check_projections(self, *, lock_held: bool = False) -> list[str]:
        return self.projection_errors(lock_held=lock_held)

    def export_legacy(self, *, lock_held: bool = False) -> str:
        candidates = self.list_candidates(lock_held=lock_held)
        sections = {state: [] for state in STATES}
        for candidate in candidates:
            line = (self.verdict_line(candidate) if candidate["state"] == "assessed"
                    else self.display_line(candidate))
            sections[candidate["state"]].append(line)
        body = ["# Discovery intake (structured-store compatibility export)", "",
                "## Pending", "", *sections["pending"], "", "## Assessed", "",
                *sections["assessed"], "", "## Deferred", "", *sections["deferred"],
                "", "## Link review, held for a human decision", "",
                *sections["human-review"]]
        return "\n".join(body).rstrip() + "\n"


def _schema_text(value: dict) -> str:
    return pretty_json(value)


def schema_files() -> dict[str, str]:
    return {
        "discovery/README.md": STORE_README,
        "discovery/schema/transaction-v1.schema.json": _schema_text(TRANSACTION_SCHEMA),
        "discovery/schema/candidate-v1.schema.json": _schema_text(CANDIDATE_SCHEMA),
    }


def _validate_migration_unlocked(store: DiscoveryStore, transactions: list[dict],
                                 projected: dict[str, dict]) -> dict:
    manifest = read_json(
        store.marker, max_bytes=2 * 1024 * 1024, canonical=True)
    required = {"schema", "created_at", "source_head_at_cutover", "source_files",
                "legacy_entries", "legacy_lines_sha256", "migration_transactions",
                "migration_semantic_root", "states", "platforms", "repairs",
                "source_references", "occurrence_semantics",
                "migration_bundle_root"}
    if set(manifest) != required or type(manifest["schema"]) is not int \
            or manifest["schema"] != 1:
        raise ValueError("unsupported discovery migration manifest")
    if not isinstance(manifest["created_at"], str) \
            or manifest["created_at"] != normalize_stamp(manifest["created_at"]):
        raise ValueError("discovery migration timestamp is not canonical")
    if not isinstance(manifest["source_head_at_cutover"], str) \
            or not manifest["source_head_at_cutover"]:
        raise ValueError("discovery migration has no source head at cutover")
    bundle_root = manifest["migration_bundle_root"]
    if not isinstance(bundle_root, str) \
            or not re.fullmatch(r"[0-9a-f]{64}", bundle_root):
        raise ValueError("discovery migration has an invalid bundle root")
    descriptor = {
        key: value for key, value in manifest.items()
        if key not in {"migration_transactions", "migration_bundle_root"}
    }
    if digest(descriptor) != bundle_root:
        raise ValueError("discovery migration bundle root changed")
    references = manifest["source_references"]
    reference_keys = {"referenced", "live", "quarantined", "unresolved"}
    if not isinstance(references, dict) or set(references) != reference_keys \
            or any(not isinstance(references[key], list)
                   for key in reference_keys) \
            or any(not isinstance(value, str) or not SOURCE_ID_RE.fullmatch(value)
                   for key in reference_keys for value in references[key]) \
            or any(references[key] != sorted(set(references[key]))
                   for key in reference_keys) \
            or set(references["live"]) & set(references["quarantined"]) \
            or set(references["live"]) & set(references["unresolved"]) \
            or set(references["quarantined"]) & set(references["unresolved"]) \
            or set(references["referenced"]) != (
                set(references["live"]) |
                set(references["quarantined"]) |
                set(references["unresolved"])):
        raise ValueError("discovery migration source-reference summary is invalid")
    repairs = manifest["repairs"]
    repair_kinds = {
        "assessed_retry_only_reopened_pending",
        "assessed_without_transition_held_for_human_review",
        "missing_url_given_stable_legacy_identity",
        "multi_transition_lines_preserved",
        "verdict_supersessions_made_explicit",
        "verdict_reopened_by_later_retry",
    }
    if not isinstance(repairs, dict) or set(repairs) != repair_kinds \
            or any(not isinstance(rows, list) \
                   or any(not isinstance(row, dict) for row in rows)
                   for rows in repairs.values()):
        raise ValueError("discovery migration repair inventory is invalid")
    occurrence_meta = manifest["occurrence_semantics"]
    occurrence_copy = "discovery/migration-v1/occurrence-semantics.json"
    if not isinstance(occurrence_meta, dict) or set(occurrence_meta) != {
            "copy", "bytes", "sha256", "entries"} \
            or occurrence_meta["copy"] != occurrence_copy \
            or type(occurrence_meta["bytes"]) is not int \
            or occurrence_meta["bytes"] < 1 \
            or type(occurrence_meta["entries"]) is not int \
            or occurrence_meta["entries"] < 1 \
            or not isinstance(occurrence_meta["sha256"], str) \
            or not re.fullmatch(r"[0-9a-f]{64}", occurrence_meta["sha256"]):
        raise ValueError("discovery migration occurrence metadata is invalid")
    occurrence_path = store.root / occurrence_copy
    if occurrence_path.is_symlink() or not occurrence_path.is_file():
        raise ValueError("discovery migration occurrence table is missing")
    occurrence_raw = occurrence_path.read_bytes()
    if len(occurrence_raw) != occurrence_meta["bytes"] \
            or sha256_bytes(occurrence_raw) != occurrence_meta["sha256"]:
        raise ValueError("discovery migration occurrence table changed")
    occurrence_value = read_json(
        occurrence_path, max_bytes=8 * 1024 * 1024, canonical=True)
    if set(occurrence_value) != {"schema", "occurrences"} \
            or type(occurrence_value["schema"]) is not int \
            or occurrence_value["schema"] != 1 \
            or not isinstance(occurrence_value["occurrences"], list) \
            or len(occurrence_value["occurrences"]) != occurrence_meta["entries"]:
        raise ValueError("discovery migration occurrence table is invalid")
    stored_semantics = occurrence_value["occurrences"]
    if type(manifest["legacy_entries"]) is not int \
            or manifest["legacy_entries"] < 1:
        raise ValueError("discovery migration has an invalid entry count")
    for name, counts in (("states", manifest["states"]),
                         ("platforms", manifest["platforms"])):
        if not isinstance(counts, dict) or any(
                not isinstance(key, str) or type(value) is not int or value < 0
                for key, value in counts.items()):
            raise ValueError(
                f"discovery migration {name} inventory is invalid")
    sources = manifest["source_files"]
    if not isinstance(sources, list) or not sources:
        raise ValueError("discovery migration has no legacy sources")
    source_order: dict[str, int] = {}
    source_occurrences: dict[tuple[str, int], str] = {}
    source_lines: list[str] = []
    audited_semantics: list[dict] = []
    queue_rank = 0
    expected_entries = 0
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or set(source) != {
                "path", "copy", "bytes", "sha256", "entries"}:
            raise ValueError("invalid discovery migration source entry")
        original, copy = source["path"], source["copy"]
        if not isinstance(original, str) or not isinstance(copy, str) \
                or Path(original).is_absolute() or ".." in Path(original).parts \
                or copy != "discovery/migration-v1/legacy/" + original \
                or original in source_order:
            raise ValueError("invalid discovery migration source path")
        path = store.root / copy
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"legacy migration copy is missing: {copy}")
        raw = path.read_bytes()
        if type(source["bytes"]) is not int \
                or type(source["entries"]) is not int \
                or source["entries"] < 0 \
                or not isinstance(source["sha256"], str) \
                or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) \
                or len(raw) != source["bytes"] \
                or sha256_bytes(raw) != source["sha256"]:
            raise ValueError(f"legacy migration copy changed: {copy}")
        held: list[tuple[int, str]] = []
        section = None if original == "DISCOVERY.md" else "Assessed"
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if match := LEGACY_HEADING_RE.fullmatch(line):
                section = match.group(1)
            elif line.startswith("- "):
                held.append((number, line))
                queue_rank += 1
                audited_semantics.append(audit_legacy_line_semantics(
                    line, section or "Assessed", queue_rank,
                    original, number))
        if len(held) != source["entries"]:
            raise ValueError(f"legacy migration entry count changed: {copy}")
        for number, line in held:
            source_occurrences[(original, number)] = line
            source_lines.append(line)
        source_order[original] = index
        expected_entries += source["entries"]
    source_paths = list(source_order)
    expected_source_paths = [
        "DISCOVERY.md",
        *sorted(path for path in source_paths if path != "DISCOVERY.md"),
    ]
    if source_paths != expected_source_paths \
            or any(not re.fullmatch(
                r"discovery/assessed-\d{4}-\d{2}\.md", path)
                   for path in source_paths[1:]):
        raise ValueError("legacy migration source order is not canonical")
    migration_files: set[str] = set()
    migration_root = store.marker.parent
    if migration_root.is_symlink() or not migration_root.is_dir():
        raise ValueError("discovery migration bundle is not a regular directory")
    for path in migration_root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"discovery migration bundle has a symlink: {path}")
        if path.is_file():
            migration_files.add(path.relative_to(store.root).as_posix())
        elif not path.is_dir():
            raise ValueError(
                f"discovery migration bundle has an unusual entry: {path}")
    expected_migration_files = {
        "discovery/migration-v1/manifest.json",
        occurrence_copy,
        *(source["copy"] for source in sources),
    }
    if migration_files != expected_migration_files:
        raise ValueError(
            "discovery migration bundle file set changed: "
            f"missing={sorted(expected_migration_files - migration_files)[:3]}, "
            f"unexpected={sorted(migration_files - expected_migration_files)[:3]}")
    if expected_entries != manifest["legacy_entries"]:
        raise ValueError("legacy migration source counts disagree")
    if occurrence_meta["entries"] != manifest["legacy_entries"]:
        raise ValueError("migration occurrence count disagrees with legacy inputs")
    if digest(source_lines) != manifest["legacy_lines_sha256"]:
        raise ValueError("legacy migration line digest disagrees with held bytes")
    if len(stored_semantics) != len(audited_semantics):
        raise ValueError("migration occurrence table does not cover held lines")
    semantic_keys = {
        "path", "line_number", "queue_rank", "section", "identity",
        "url", "initial_state", "event_at", "time_basis", "actions",
        "observation_event_id",
    }
    for stored, audited in zip(stored_semantics, audited_semantics):
        if not isinstance(stored, dict) or set(stored) != semantic_keys \
                or type(stored["line_number"]) is not int \
                or type(stored["queue_rank"]) is not int \
                or not isinstance(stored["observation_event_id"], str) \
                or not re.fullmatch(
                    r"[0-9a-f]{64}", stored["observation_event_id"]):
            raise ValueError("migration occurrence row is invalid")
        normalized = {
            key: stored[key] for key in semantic_keys
            if key not in {"actions", "observation_event_id"}
        }
        actions = stored["actions"]
        if not isinstance(actions, list) \
                or len(actions) != len(audited["actions"]):
            raise ValueError(
                "migration occurrence actions disagree with held line")
        normalized_actions = []
        for stored_action, audited_action in zip(actions, audited["actions"]):
            if not isinstance(stored_action, dict) \
                    or set(stored_action) != set(audited_action) | {"event_ids"} \
                    or not isinstance(stored_action["event_ids"], list) \
                    or not stored_action["event_ids"] \
                    or any(not isinstance(event_id, str)
                           or not re.fullmatch(r"[0-9a-f]{64}", event_id)
                           for event_id in stored_action["event_ids"]):
                raise ValueError("migration occurrence action binding is invalid")
            normalized_actions.append({
                key: stored_action[key] for key in audited_action
            })
        normalized["actions"] = normalized_actions
        if canonical_json(normalized) != canonical_json(audited):
            raise ValueError(
                "migration occurrence semantics disagree with held line")
    expected_repairs: dict[str, list[dict]] = {
        key: [] for key in repair_kinds
    }
    for stored, audited in zip(stored_semantics, audited_semantics):
        reference = {
            "path": audited["path"],
            "line_number": audited["line_number"],
            "candidate": audited["identity"],
        }
        actions = audited["actions"]
        if audited["url"].startswith("urn:coldcard-discovery:legacy:"):
            expected_repairs[
                "missing_url_given_stable_legacy_identity"].append(reference)
        if audited["section"] == "Assessed" and not actions:
            expected_repairs[
                "assessed_without_transition_held_for_human_review"].append(
                    reference)
        if len(actions) > 1:
            expected_repairs["multi_transition_lines_preserved"].append({
                **reference,
                "transitions": [action["action"] for action in actions],
            })
        if audited["section"] == "Assessed" and actions \
                and actions[-1]["action"] == "retry":
            expected_repairs[
                "assessed_retry_only_reopened_pending"].append(reference)
    tx_ids = manifest["migration_transactions"]
    if not isinstance(tx_ids, list) or not tx_ids \
            or len(tx_ids) > len(transactions) \
            or any(not isinstance(value, str) for value in tx_ids) \
            or [tx["transaction_id"] for tx in transactions[:len(tx_ids)]] != tx_ids:
        raise ValueError("discovery migration transaction prefix changed")
    if any(tx["kind"] != "migration-v1"
           for tx in transactions[:len(tx_ids)]):
        raise ValueError("discovery migration prefix has a non-migration transaction")
    for number, tx in enumerate(transactions[:len(tx_ids)], 1):
        if tx["operation_id"] != f"migration-v1:{bundle_root}:{number:04d}":
            raise ValueError("discovery migration transaction is not bundle-bound")
    migration_events = [
        event for transaction in transactions[:len(tx_ids)]
        for event in transaction["events"]
    ]
    migration_event_map = {
        event["event_id"]: event for event in migration_events
    }
    migration_event_position = {
        event["event_id"]: position
        for position, event in enumerate(migration_events)
    }
    chronological_supersessions: list[tuple[int, dict]] = []
    chronological_reopens: list[tuple[int, dict]] = []
    bound_event_ids: set[str] = set()
    for stored, audited in zip(stored_semantics, audited_semantics):
        observation_id = stored["observation_event_id"]
        observation = migration_event_map.get(observation_id)
        if observation_id in bound_event_ids or observation is None \
                or observation["type"] != "observation" \
                or observation["candidate"] != audited["identity"] \
                or observation["at"] != audited["event_at"]:
            raise ValueError("legacy observation event binding is invalid")
        payload = observation["payload"]
        public = payload.get("observation", {})
        if payload.get("state") != audited["initial_state"] \
                or payload.get("url") != audited["url"] \
                or public.get("url") != audited["url"] \
                or public.get("legacy_path") != audited["path"] \
                or public.get("legacy_line_number") != audited["line_number"] \
                or public.get("legacy_queue_rank") != audited["queue_rank"] \
                or public.get("legacy_section") != audited["section"] \
                or public.get("legacy_event_time_basis") != audited["time_basis"]:
            raise ValueError("legacy observation provenance binding is invalid")
        bound_event_ids.add(observation_id)

        for stored_action, audited_action in zip(
                stored["actions"], audited["actions"]):
            event_ids = stored_action["event_ids"]
            if len(event_ids) not in {1, 2} \
                    or any(event_id in bound_event_ids
                           or event_id not in migration_event_map
                           for event_id in event_ids):
                raise ValueError("legacy action event binding is invalid")
            bound_events = [migration_event_map[event_id]
                            for event_id in event_ids]
            if audited_action["action"] == "retry":
                if len(bound_events) == 2:
                    reopen = bound_events[0]
                    if reopen["type"] != "state" \
                            or reopen["candidate"] != audited["identity"] \
                            or reopen["at"] != audited_action["at"] \
                            or reopen["payload"].get("state") != "pending" \
                            or reopen["payload"].get("reason") != \
                            "later legacy retry reopened pending":
                        raise ValueError("legacy retry reopen binding is invalid")
                    chronological_reopens.append((
                        migration_event_position[reopen["event_id"]], {
                            "path": audited["path"],
                            "line_number": audited["line_number"],
                            "candidate": audited["identity"],
                            "supersedes": reopen["payload"]["supersedes"],
                            "reopen": reopen["event_id"],
                        }))
                retry = bound_events[-1]
                if retry["type"] != "retry" \
                        or retry["candidate"] != audited["identity"] \
                        or retry["at"] != audited_action["at"] \
                        or retry["payload"].get("reason") != \
                        audited_action["reason"]:
                    raise ValueError("legacy retry event binding is invalid")
            else:
                if len(bound_events) != 1:
                    raise ValueError("legacy verdict has multiple event bindings")
                verdict = bound_events[0]
                verdict_payload = verdict["payload"]
                if verdict["type"] != "verdict" \
                        or verdict["candidate"] != audited["identity"] \
                        or verdict["at"] != audited_action["at"] \
                        or verdict_payload.get("kind") != audited_action["action"] \
                        or verdict_payload.get("reason") != audited_action["reason"] \
                        or verdict_payload.get("source_id") != \
                        audited_action.get("source_id"):
                    raise ValueError("legacy verdict event binding is invalid")
                if verdict_payload.get("supersedes"):
                    chronological_supersessions.append((
                        migration_event_position[verdict["event_id"]], {
                            "path": audited["path"],
                            "line_number": audited["line_number"],
                            "candidate": audited["identity"],
                            "supersedes": verdict_payload["supersedes"],
                            "replacement": verdict["event_id"],
                        }))
            bound_event_ids.update(event_ids)
    if bound_event_ids != set(migration_event_map):
        raise ValueError("migration has events not bound to legacy occurrences")
    expected_event_order: list[tuple[str, int, int, int, str]] = []
    for stored, audited in zip(stored_semantics, audited_semantics):
        rank = audited["queue_rank"]
        expected_event_order.append((
            audited["event_at"], rank, 0, 0,
            stored["observation_event_id"]))
        for transition, (stored_action, audited_action) in enumerate(
                zip(stored["actions"], audited["actions"]), 1):
            for within_action, event_id in enumerate(
                    stored_action["event_ids"]):
                expected_event_order.append((
                    audited_action["at"], rank, transition,
                    within_action, event_id))
    expected_ids = [row[-1] for row in sorted(expected_event_order)]
    if [event["event_id"] for event in migration_events] != expected_ids:
        raise ValueError(
            "migration events are not in legacy chronological replay order")
    expected_repairs["verdict_supersessions_made_explicit"] = [
        row for _position, row in sorted(chronological_supersessions)
    ]
    expected_repairs["verdict_reopened_by_later_retry"] = [
        row for _position, row in sorted(chronological_reopens)
    ]
    if canonical_json(repairs) != canonical_json(expected_repairs):
        raise ValueError(
            "discovery migration repair inventory disagrees with held history")
    baseline = DiscoveryStore.project(transactions[:len(tx_ids)])
    baseline_rows = sorted(baseline.values(), key=DiscoveryStore._sort_key)
    if digest(baseline_rows) != manifest["migration_semantic_root"]:
        raise ValueError("discovery migration semantic root changed")
    baseline_source_ids = sorted({
        event["payload"]["source_id"]
        for transaction in transactions[:len(tx_ids)]
        for event in transaction["events"]
        if event["type"] == "verdict"
        and isinstance(event["payload"].get("source_id"), str)
    })
    if references["referenced"] != baseline_source_ids:
        raise ValueError(
            "discovery migration source references disagree with baseline events")
    occurrences: dict[tuple[str, int], str] = {}
    ranks: dict[tuple[str, int], int] = {}
    observation_count = 0
    for candidate in baseline_rows:
        for observation in candidate.get("observations", []):
            observation_count += 1
            path = observation.get("legacy_path")
            number = observation.get("legacy_line_number")
            line = observation.get("legacy_line")
            if path not in source_order:
                continue
            if not isinstance(number, int) or not isinstance(line, str):
                raise ValueError("legacy observation lacks exact provenance")
            key = (path, number)
            if key in occurrences:
                raise ValueError(f"duplicate legacy occurrence mapping: {path}:{number}")
            occurrences[key] = line
            rank = observation.get("legacy_queue_rank")
            if not isinstance(rank, int) or isinstance(rank, bool):
                raise ValueError("legacy observation lacks its queue rank")
            ranks[key] = rank
    if observation_count != manifest["legacy_entries"] \
            or len(occurrences) != manifest["legacy_entries"]:
        raise ValueError("structured store does not preserve every legacy occurrence")
    if occurrences != source_occurrences:
        raise ValueError("structured legacy occurrences disagree with held bytes")
    ordered = [line for (_key, line) in sorted(
        occurrences.items(), key=lambda item: (source_order[item[0][0]], item[0][1]))]
    if digest(ordered) != manifest["legacy_lines_sha256"]:
        raise ValueError("legacy discovery line hash changed")
    ordered_keys = [key for key, _line in sorted(
        occurrences.items(), key=lambda item: (source_order[item[0][0]], item[0][1]))]
    if [ranks[key] for key in ordered_keys] != list(
            range(1, manifest["legacy_entries"] + 1)):
        raise ValueError("legacy discovery queue order changed")
    states = {state: sum(1 for candidate in baseline_rows
                         if candidate["state"] == state)
              for state in sorted(STATES)}
    states = {key: value for key, value in states.items() if value}
    platforms: dict[str, int] = {}
    for candidate in baseline_rows:
        platforms[candidate["platform"]] = platforms.get(candidate["platform"], 0) + 1
    if canonical_json(manifest["states"]) != canonical_json(states) \
            or canonical_json(manifest["platforms"]) != canonical_json(
                dict(sorted(platforms.items()))):
        raise ValueError("discovery migration inventory disagrees with its baseline")
    return manifest


def validate_migration(root: Path = ROOT, *, lock_held: bool = False) -> dict:
    store = DiscoveryStore(root)
    with store._lock_context(lock_held):
        transactions, projected = store._loaded()
        return _validate_migration_unlocked(store, transactions, projected)


def validate_store(root: Path = ROOT, *, lock_held: bool = False) -> dict:
    store = DiscoveryStore(root)
    with store._lock_context(lock_held):
        expected_top = {
            "README.md", "candidates", "migration-v1", "schema",
            "state.json", "transactions", "views",
        }
        if store.discovery.is_symlink() or not store.discovery.is_dir():
            raise ValueError("discovery namespace is not a regular directory")
        actual_top = {path.name for path in store.discovery.iterdir()}
        if actual_top != expected_top:
            raise ValueError(
                "discovery namespace differs: "
                f"missing={sorted(expected_top - actual_top)}, "
                f"unexpected={sorted(actual_top - expected_top)}")
        for name in ("candidates", "migration-v1", "schema",
                     "transactions", "views"):
            path = store.discovery / name
            if path.is_symlink() or not path.is_dir():
                raise ValueError(
                    f"discovery namespace directory is unsafe: {path}")
        for name in ("README.md", "state.json"):
            path = store.discovery / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(
                    f"discovery namespace file is unsafe: {path}")
        expected_schema = {
            Path(rel).name for rel in schema_files()
            if rel.startswith("discovery/schema/")
        }
        schema_entries = list((store.discovery / "schema").iterdir())
        if {path.name for path in schema_entries} != expected_schema \
                or any(path.is_symlink() or not path.is_file()
                       for path in schema_entries):
            raise ValueError("discovery schema namespace differs")
        for rel, text in schema_files().items():
            path = store.root / rel
            if path.is_symlink() or not path.is_file() \
                    or path.read_text(encoding="utf-8") != text:
                raise ValueError(
                    f"discovery format support file missing or changed: {rel}")
        transactions, projected = store._loaded()
        manifest = _validate_migration_unlocked(store, transactions, projected)
        migration_count = len(manifest["migration_transactions"])
        if any(transaction["kind"] == "migration-v1"
               for transaction in transactions[migration_count:]):
            raise ValueError("discovery migration transaction appears after cutover")
        known = known_source_ids(store.root)
        if known is not None:
            runtime_references = registration_source_ids([
                event
                for transaction in transactions[migration_count:]
                for event in transaction["events"]
            ])
            if not runtime_references <= known:
                raise ValueError(
                    "discovery history references unknown runtime source id(s): "
                    + ", ".join(sorted(runtime_references - known)))
        expected = store._render_files(transactions, projected)
        actual = store._generated_actual()
        if actual != set(expected):
            missing = sorted(set(expected) - actual)
            stale = sorted(actual - set(expected))
            raise ValueError("generated discovery file set differs: "
                             f"missing={missing[:3]}, stale={stale[:3]}")
        for rel, text in expected.items():
            path = store.root / rel
            if path.is_symlink() or path.read_text(encoding="utf-8") != text:
                raise ValueError(f"generated discovery file differs: {rel}")
        return {
            "transactions": len(transactions),
            "transaction_head": transactions[-1]["transaction_id"] if transactions else None,
            "candidates": len(projected),
            "migration": manifest,
        }


def load_intake_verdict_lines(root: Path = ROOT, *,
                              lock_held: bool = False) -> list[str]:
    store = DiscoveryStore(root)
    return [store.verdict_line(candidate) for candidate in
            store.list_candidates(state="assessed", lock_held=lock_held)]


def verdict_facts(root: Path = ROOT, *, lock_held: bool = False,
                  final_only: bool = True) -> list[dict]:
    store = DiscoveryStore(root)
    rows = []
    for candidate in store.list_candidates(lock_held=lock_held):
        verdicts = ([candidate["verdict"]]
                    if final_only and candidate.get("verdict")
                    else ([] if final_only
                          else candidate.get("verdict_history", [])))
        for verdict in verdicts:
            rows.append({"candidate_id": candidate["identity"], **verdict})
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--lock-held", action="store_true",
                        help="caller already holds .work/locks/discovery.lock")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--state", choices=sorted(STATES))
    listing.add_argument("--platform")
    listing.add_argument("--lane", choices=("community", "x"))
    listing.add_argument(
        "--format", choices=("id", "line", "intake-json", "json"),
        default="id",
        help=("id or generated Markdown for people; intake-json is the "
              "bounded JSONL handoff used by intake drivers; json emits the "
              "complete candidate projection"),
    )
    listing.add_argument("--limit", type=int)
    counting = sub.add_parser("count")
    counting.add_argument("--state", choices=sorted(STATES))
    counting.add_argument("--platform")
    counting.add_argument("--lane", choices=("community", "x"))
    show = sub.add_parser("show")
    show.add_argument("identity")
    verdict = sub.add_parser("record-verdict")
    verdict.add_argument("identity")
    verdict.add_argument("verdict", choices=sorted(VERDICTS))
    verdict.add_argument("--reason", required=True)
    verdict.add_argument("--source-id")
    verdict.add_argument("--supersedes")
    state = sub.add_parser("set-state")
    state.add_argument("identity")
    state.add_argument("state", choices=("pending", "deferred", "human-review"))
    state.add_argument("--reason", required=True)
    state.add_argument("--supersedes")
    sub.add_parser("render")
    sub.add_parser("validate")
    exporting = sub.add_parser("export-legacy")
    exporting.add_argument("--out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = DiscoveryStore(args.root)
    try:
        if args.command == "list":
            candidates = store.list_candidates(
                args.state, args.platform, lane=args.lane,
                lock_held=args.lock_held)
            if args.limit is not None:
                if args.limit < 0:
                    raise ValueError("--limit cannot be negative")
                candidates = candidates[:args.limit]
            for candidate in candidates:
                if args.format == "line":
                    print(store.display_line(candidate))
                elif args.format == "intake-json":
                    print(canonical_json({
                        "schema": 1,
                        "candidate_id": candidate["identity"],
                        "candidate_head": candidate["head"],
                        "url": candidate["url"],
                        # This remains untrusted presentation text. Machine
                        # identity above never gets parsed back out of it.
                        "queue_line": store.display_line(candidate),
                    }))
                elif args.format == "json":
                    print(canonical_json(candidate))
                else:
                    print(candidate["identity"])
        elif args.command == "count":
            print(store.count(args.state, args.platform, lane=args.lane,
                              lock_held=args.lock_held))
        elif args.command == "show":
            candidate = store.load_candidate(args.identity,
                                             lock_held=args.lock_held)
            if candidate is None:
                raise ValueError(f"unknown candidate: {args.identity}")
            print(pretty_json(candidate), end="")
        elif args.command == "record-verdict":
            store.record_verdict(
                args.identity, args.verdict, reason=args.reason,
                source_id=args.source_id, supersedes=args.supersedes,
                lock_held=args.lock_held)
        elif args.command == "set-state":
            store.set_state(args.identity, args.state, reason=args.reason,
                            supersedes=args.supersedes,
                            lock_held=args.lock_held)
        elif args.command == "render":
            store.render_all(lock_held=args.lock_held)
        elif args.command == "validate":
            result = validate_store(args.root, lock_held=args.lock_held)
            print(f"valid: {result['transactions']} transactions; "
                  f"{result['candidates']} candidates; head "
                  f"{result['transaction_head']}")
        elif args.command == "export-legacy":
            text = store.export_legacy(lock_held=args.lock_held)
            if args.out:
                atomic_text(args.out, text)
            else:
                print(text, end="")
    except (OSError, ValueError, KeyError) as exc:
        print(f"discovery-store: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
