#!/usr/bin/env python3
"""Build a small, deterministic evidence packet for an intake-agent run.

Earlier intake drivers handed an agent the candidate queue twice and the
whole source registry once.  This builder keeps the evidence that changes a
decision while making the transport independent of the eventual registry and
discovery layouts:

* every candidate has a canonical external key and stable store identity;
* exact registry duplicates are resolved mechanically from that key;
* every source with a non-zero historical ``absorbed`` count is retained;
* zero-history registry rows are counted, not copied into every prompt; and
* the queue line occurs once in the Markdown packet, beside its hydrated body.

The JSON is the machine-checkable packet.  The compact Markdown is the only
part intended for an agent prompt, and must still be passed through
``render_agent_prompt.py`` as untrusted evidence.

If ``scripts/registry_store.py`` is present, its ``load(root)`` API is
preferred for the registry.  Discovery is different: the migration marker is
an activation boundary and production packets fail closed unless the
structured store validates.  Explicit Markdown paths remain available only
for small migration-era fixtures.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from discovery_store import url_identity

import build_coverage_index as coverage_index

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
# Doubled 13 Aug 2026: 15 hydrated community bodies plus the coverage rows
# crossed 48 KiB (49,374 observed) and the community lane refused to start
# its agent at all. The ceiling that made 48 KiB load-bearing was the
# kernel's per-argument limit, and that is gone: the agent reads its prompt
# from a file since the same day. The bound now only keeps the evidence
# sized for one attentive pass, and 96 KiB stays well inside that.
DEFAULT_MAX_MARKDOWN_BYTES = 96 * 1024
REGISTRY_TABLES = ("source", "x_post", "nostr_post")
COMMUNITY_LANES = frozenset({"stackernews", "reddit", "bitcointalk", "nostr"})

URL_IN_LINE = re.compile(r"\((https?://[^)]+)\)")
HYDRATED_HEADING = re.compile(r"^### Candidate ([1-9][0-9]*)$")
OPEN_FENCE = re.compile(r"^<<<UNTRUSTED-([a-zA-Z0-9-]+)$")
HEAD_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTITY_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,127}:[a-z0-9][a-z0-9._-]{0,127}$")
INTAKE_FIELDS = frozenset({
    "schema", "candidate_id", "candidate_head", "url", "queue_line",
})
HYDRATION_FIELDS = frozenset({
    *INTAKE_FIELDS, "platform", "hydration_status", "hydration_detail", "body",
})


class PacketError(ValueError):
    """Input cannot be represented without guessing."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _jsonable(value: Any) -> Any:
    """Normalize TOML values and storage order for a semantic ledger hash."""
    if isinstance(value, dict):
        out = {str(key): _jsonable(item) for key, item in value.items()}
        for table in ("source", "x_post", "nostr_post", "x_watch"):
            rows = out.get(table)
            if isinstance(rows, list):
                identity = "handle" if table == "x_watch" else "id"
                out[table] = sorted(
                    rows, key=lambda row: str(row.get(identity, ""))
                    if isinstance(row, dict) else repr(row))
        return out
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return value


def url_from_queue_line(line: str) -> str:
    match = URL_IN_LINE.search(line)
    if not match:
        raise PacketError(f"candidate line has no Markdown URL: {line[:100]}")
    return match.group(1)


def canonical_external_key(url: str) -> tuple[str, str]:
    """Return ``(platform, external-key)`` for every intake platform.

    Keys intentionally discard handles, slugs, query order and URL aliases.
    They identify the publisher's native object, which is the exact-duplicate
    question the driver can answer without model judgement.
    """
    try:
        platform, native_id = url_identity(url, strict=True)
    except (TypeError, ValueError) as exc:
        raise PacketError(f"unrecognized intake permalink: {url}") from exc
    kinds = {
        "x": "status",
        "reddit": "submission",
        "stackernews": "item",
        "bitcointalk": "topic",
        "nostr": "note",
    }
    kind = kinds.get(platform)
    if kind is None:
        raise PacketError(f"unrecognized intake platform: {platform}")
    return platform, f"{platform}:{kind}:{native_id}"


def stable_candidate_id(external_key: str) -> str:
    """Match discovery_store's stable ``platform:native-id`` identity."""
    parts = external_key.split(":")
    if len(parts) != 3 or not parts[0] or not parts[2]:
        raise PacketError(f"invalid canonical external key: {external_key}")
    return f"{parts[0]}:{parts[2]}"


def _legacy_registry(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_registry(root: Path, explicit: Path | None = None) -> dict[str, Any]:
    """Load through the new store when available, with a legacy fallback."""
    if explicit is not None:
        return _legacy_registry(explicit)
    try:
        import registry_store  # type: ignore
    except ImportError:
        registry_store = None
    if registry_store is not None and hasattr(registry_store, "load"):
        return registry_store.load(root)
    return _legacy_registry(root / "sources.toml")


def registry_entries(registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in REGISTRY_TABLES:
        for raw in registry.get(table, []):
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            rows.append({
                "id": str(raw["id"]),
                "table": table,
                "org": str(raw.get("org") or "?"),
                "title": str(raw.get("title") or ""),
                "url": str(raw.get("url") or ""),
            })
    rows.sort(key=lambda row: (row["table"], row["id"]))
    return rows


def exact_registry_index(rows: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    found: defaultdict[str, list[str]] = defaultdict(list)
    for row in rows:
        if not row["url"]:
            continue
        try:
            _platform, key = canonical_external_key(row["url"])
        except PacketError:
            continue
        found[key].append(row["id"])
    return {key: sorted(ids) for key, ids in sorted(found.items())}


def _section_lines(text: str, heading: str) -> list[str]:
    active = False
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            active = line == heading
            continue
        if active and line.startswith("- "):
            out.append(line)
    return out


def legacy_assessed_lines(discovery: Path, rotated_dir: Path) -> list[str]:
    """Read the live section and the actual header-only rotated format."""
    lines: list[str] = []
    if discovery.exists():
        lines.extend(_section_lines(discovery.read_text(encoding="utf-8"),
                                    "## Assessed"))
    for path in sorted(rotated_dir.glob("assessed-*.md")):
        text = path.read_text(encoding="utf-8")
        if "## Assessed" in text:
            # Tolerate the early/test format as well as the production one.
            selected = _section_lines(text, "## Assessed")
        else:
            selected = [line for line in text.splitlines()
                        if line.startswith("- ")]
        lines.extend(selected)
    return lines


def load_assessed_lines(root: Path, discovery: Path | None = None,
                        rotated_dir: Path | None = None) -> list[str]:
    """Read explicitly supplied legacy fixture paths.

    There is deliberately no implicit production fallback: a missing or bad
    structured store must not look like an empty verdict history.
    """
    del root  # Kept in the signature for callers from the migration period.
    if discovery is None or rotated_dir is None:
        raise PacketError("legacy discovery reads require both explicit "
                          "DISCOVERY.md and rotated-directory paths")
    return legacy_assessed_lines(discovery, rotated_dir)


def load_structured_store(root: Path, *, lock_held: bool):
    try:
        import discovery_store
    except ImportError as exc:  # pragma: no cover - deployment packaging guard
        raise PacketError("structured discovery marker exists but "
                          "discovery_store is unavailable") from exc
    store = discovery_store.DiscoveryStore(root)
    if not store.marker.is_file():
        raise PacketError("structured discovery migration marker is missing")
    try:
        discovery_store.validate_store(root, lock_held=lock_held)
    except (OSError, TypeError, ValueError) as exc:
        raise PacketError(str(exc)) from exc
    return store


def coverage_summary(rows: list[dict[str, Any]],
                     counts: Counter[str]) -> dict[str, Any]:
    nonzero = []
    for row in rows:
        absorbed = counts.get(row["id"], 0)
        if absorbed:
            nonzero.append({
                "id": row["id"],
                "table": row["table"],
                "org": row["org"],
                "title": row["title"],
                "absorbed": absorbed,
            })
    nonzero.sort(key=lambda row: (-row["absorbed"], row["id"]))
    return {
        "total_registry_entries": len(rows),
        "included_nonzero_entries": len(nonzero),
        "omitted_zero_entries": len(rows) - len(nonzero),
        "absorbed_candidate_total": sum(row["absorbed"] for row in nonzero),
        "rows": nonzero,
    }


def _hydrated_sections(text: str) -> list[list[str]]:
    """Split only on candidate headings outside an untrusted body fence."""
    sections: list[list[str]] = []
    current: list[str] | None = None
    fence_nonce: str | None = None
    for line in text.splitlines():
        if fence_nonce is not None:
            current.append(line)  # type: ignore[union-attr]
            if line == f"UNTRUSTED-{fence_nonce}>>>":
                fence_nonce = None
            continue
        heading = HYDRATED_HEADING.fullmatch(line)
        if heading:
            if current is not None:
                sections.append(current)
            current = [line]
            continue
        if current is None:
            # The hydrator's only non-section output goes to stderr.  Treat
            # unexpected stdout as an error rather than silently dropping it.
            if line.strip():
                raise PacketError(f"text before first hydrated candidate: {line}")
            continue
        current.append(line)
        opened = OPEN_FENCE.fullmatch(line)
        if opened:
            fence_nonce = opened.group(1)
    if fence_nonce is not None:
        raise PacketError("hydrated body fence is not closed")
    if current is not None:
        sections.append(current)
    return sections


def parse_hydrated(text: str, queue_lines: list[str]) -> list[dict[str, Any]]:
    sections = _hydrated_sections(text)
    if len(sections) != len(queue_lines):
        raise PacketError(
            f"hydrated evidence has {len(sections)} candidate section(s), "
            f"queue has {len(queue_lines)}")

    parsed: list[dict[str, Any]] = []
    for expected, (section, queue_line) in enumerate(
            zip(sections, queue_lines), 1):
        heading = HYDRATED_HEADING.fullmatch(section[0])
        if heading is None or int(heading.group(1)) != expected:
            raise PacketError(f"hydrated candidate numbering breaks at {expected}")
        reported = next((line[len("Queue line: "):]
                         for line in section if line.startswith("Queue line: ")),
                        None)
        if reported is None:
            raise PacketError(f"hydrated candidate {expected} has no queue line")
        try:
            _raw_platform, raw_key = canonical_external_key(
                url_from_queue_line(queue_line))
            _reported_platform, reported_key = canonical_external_key(
                url_from_queue_line(reported))
        except PacketError as exc:
            raise PacketError(f"hydrated candidate {expected} has an invalid "
                              f"queue identity: {exc}") from exc
        if reported_key != raw_key:
            raise PacketError(f"hydrated candidate {expected} does not match "
                              "the candidate batch")
        body_line = next((i for i, line in enumerate(section)
                          if line.startswith("Body: ")), None)
        if body_line is None:
            raise PacketError(f"hydrated candidate {expected} has no body status")
        status_line = section[body_line]
        body: str | None = None
        detail: str | None = None
        if status_line == "Body: hydrated, complete":
            status = "complete"
        elif status_line == "Body: hydrated, truncated":
            status = "truncated"
        elif status_line.startswith("Body: fetch failed (") and status_line.endswith(")"):
            status = "failed"
            detail = status_line[len("Body: fetch failed ("):-1]
        elif status_line.startswith("Body: not hydrated (") and status_line.endswith(")"):
            status = "not-hydrated"
            detail = status_line[len("Body: not hydrated ("):-1]
        else:
            raise PacketError(f"unknown hydration status: {status_line}")

        if status in {"complete", "truncated"}:
            try:
                open_index = next(i for i in range(body_line + 1, len(section))
                                  if OPEN_FENCE.fullmatch(section[i]))
            except StopIteration as exc:
                raise PacketError(f"hydrated candidate {expected} has no fence") from exc
            nonce = OPEN_FENCE.fullmatch(section[open_index]).group(1)  # type: ignore[union-attr]
            close = f"UNTRUSTED-{nonce}>>>"
            try:
                close_index = section.index(close, open_index + 1)
            except ValueError as exc:
                raise PacketError(f"hydrated candidate {expected} has no closing fence") from exc
            body = "\n".join(section[open_index + 1:close_index]).rstrip("\n")

        parsed.append({
            "hydration_status": status,
            "hydration_detail": detail,
            "body_sha256": sha256_text(body) if body is not None else None,
            "body": body,
        })
    return parsed


def read_candidate_lines(path: Path) -> list[str]:
    lines = [line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if not lines:
        raise PacketError("candidate batch is empty")
    return lines


def _validate_intake_record(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != INTAKE_FIELDS \
            or value.get("schema") != 1:
        raise PacketError(f"{context} has unsupported fields")
    candidate_id = value.get("candidate_id")
    candidate_head = value.get("candidate_head")
    url = value.get("url")
    queue_line = value.get("queue_line")
    if not isinstance(candidate_id, str) \
            or not IDENTITY_RE.fullmatch(candidate_id):
        raise PacketError(f"{context} has an invalid candidate id")
    if not isinstance(candidate_head, str) \
            or not HEAD_RE.fullmatch(candidate_head):
        raise PacketError(f"{context} has an invalid candidate head")
    if not isinstance(url, str) or not 1 <= len(url) <= 8192 or "\x00" in url:
        raise PacketError(f"{context} has an invalid URL")
    if not isinstance(queue_line, str) or len(queue_line) > 16384 \
            or "\x00" in queue_line:
        raise PacketError(f"{context} has an invalid display line")
    platform, external_key = canonical_external_key(url)
    if stable_candidate_id(external_key) != candidate_id:
        raise PacketError(f"{context} id disagrees with its canonical URL")
    return {**value, "platform": platform, "external_key": external_key}


def read_candidate_batch(path: Path, *, structured: bool) -> list[dict[str, Any]]:
    """Read the machine batch; Markdown is accepted only by legacy fixtures."""
    raw_lines = [line for line in path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    if not raw_lines:
        raise PacketError("candidate batch is empty")
    if not structured:
        rows = []
        for number, line in enumerate(raw_lines, 1):
            url = url_from_queue_line(line)
            platform, external_key = canonical_external_key(url)
            rows.append({
                "schema": 1,
                "candidate_id": stable_candidate_id(external_key),
                "candidate_head": None,
                "url": url,
                "queue_line": line,
                "platform": platform,
                "external_key": external_key,
            })
        return rows

    rows = []
    seen: set[str] = set()
    for number, line in enumerate(raw_lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PacketError(
                f"candidate JSONL line {number} is invalid: {exc}") from exc
        row = _validate_intake_record(
            value, context=f"candidate JSONL line {number}")
        if row["candidate_id"] in seen:
            raise PacketError(
                f"candidate JSONL repeats id: {row['candidate_id']}")
        seen.add(row["candidate_id"])
        rows.append(row)
    return rows


def parse_hydrated_jsonl(text: str,
                         candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verify hydration without parsing an id or URL out of display prose."""
    raw_lines = [line for line in text.splitlines() if line.strip()]
    if len(raw_lines) != len(candidates):
        raise PacketError(
            f"hydrated evidence has {len(raw_lines)} JSON object(s), "
            f"batch has {len(candidates)}")
    rows: list[dict[str, Any]] = []
    for number, (raw, candidate) in enumerate(zip(raw_lines, candidates), 1):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PacketError(
                f"hydrated JSONL line {number} is invalid: {exc}") from exc
        if not isinstance(value, dict) or set(value) != HYDRATION_FIELDS \
                or value.get("schema") != 1:
            raise PacketError(
                f"hydrated JSONL line {number} has unsupported fields")
        common = {key: value[key] for key in INTAKE_FIELDS}
        checked = _validate_intake_record(
            common, context=f"hydrated JSONL line {number}")
        for key in ("candidate_id", "candidate_head", "url", "queue_line"):
            if checked[key] != candidate[key]:
                raise PacketError(
                    f"hydrated JSONL line {number} changed candidate {key}")
        if value.get("platform") != candidate["platform"]:
            raise PacketError(
                f"hydrated JSONL line {number} changed candidate platform")
        status = value.get("hydration_status")
        detail, body = value.get("hydration_detail"), value.get("body")
        if status in {"complete", "truncated"}:
            if not isinstance(body, str) or detail is not None:
                raise PacketError(
                    f"hydrated JSONL line {number} has invalid body data")
        elif status in {"failed", "not-hydrated"}:
            if body is not None or not isinstance(detail, str) or not detail:
                raise PacketError(
                    f"hydrated JSONL line {number} has invalid failure data")
        else:
            raise PacketError(
                f"hydrated JSONL line {number} has an unknown status")
        rows.append({
            "hydration_status": status,
            "hydration_detail": detail,
            "body_sha256": sha256_text(body) if body is not None else None,
            "body": body,
        })
    return rows


def render_markdown(packet: dict[str, Any]) -> str:
    coverage = packet["coverage"]
    exact = sum(1 for candidate in packet["candidates"]
                if candidate["registry_exact_match"]["matched"])
    out = [
        "# Intake evidence packet",
        "",
        f"Lane: {packet['lane']}",
        f"Candidates: {len(packet['candidates'])}; exact registry matches: {exact}",
        "",
        "## Saturated coverage themes",
        "",
        (f"{coverage['included_nonzero_entries']} of "
         f"{coverage['total_registry_entries']} registered entries have absorbed "
         f"a prior duplicate; {coverage['omitted_zero_entries']} zero-history "
         "entries are omitted from this packet."),
        "",
    ]
    if coverage["rows"]:
        for row in coverage["rows"]:
            title = row["title"].replace("\n", " ").strip()
            if len(title) > 100:
                title = title[:99].rstrip() + "…"
            out.append(
                f"- `{row['id']}` (absorbed {row['absorbed']}) "
                f"[{row['org']}] {title}")
    else:
        out.append("No registered entry has absorbed a prior duplicate.")

    out.extend(["", "## Candidates", ""])
    for index, candidate in enumerate(packet["candidates"], 1):
        match = candidate["registry_exact_match"]
        exact_text = (", ".join(f"`{ident}`" for ident in match["source_ids"])
                      if match["matched"] else "none")
        out.extend([
            f"### Candidate {index}: `{candidate['candidate_id']}`",
            "",
            f"External key: `{candidate['external_key']}`",
            f"Queue line: {candidate['queue_line']}",
            f"Queue-line SHA-256: `{candidate['queue_line_sha256']}`",
            f"Registry exact match: {exact_text}",
            (f"Hydration: {candidate['hydration_status']}; body SHA-256: "
             f"`{candidate['body_sha256']}`"
             if candidate["body_sha256"] else
             f"Hydration: {candidate['hydration_status']}"
             + (f" ({candidate['hydration_detail']})"
                if candidate["hydration_detail"] else "")),
            "",
        ])
        if candidate["body"] is not None:
            out.extend([candidate["body"], ""])
    return "\n".join(out).rstrip() + "\n"


def build_packet(*, root: Path, lane: str, candidates_path: Path,
                 hydrated_path: Path, registry_path: Path | None = None,
                 discovery_path: Path | None = None,
                 rotated_dir: Path | None = None,
                 lock_held: bool = False,
                 max_markdown_bytes: int = DEFAULT_MAX_MARKDOWN_BYTES,
                 hydrated_text: str | None = None) -> tuple[dict[str, Any], str]:
    if lane not in {"community", "x"}:
        raise PacketError(f"unknown lane: {lane}")
    legacy_fixture = discovery_path is not None or rotated_dir is not None
    batch = read_candidate_batch(candidates_path, structured=not legacy_fixture)
    hydrated_raw = (hydrated_text if hydrated_text is not None else
                    hydrated_path.read_text(encoding="utf-8"))
    hydration = (parse_hydrated(
        hydrated_raw, [candidate["queue_line"] for candidate in batch])
        if legacy_fixture else parse_hydrated_jsonl(hydrated_raw, batch))

    registry = load_registry(root, registry_path)
    rows = registry_entries(registry)
    exact_index = exact_registry_index(rows)
    known_ids = {row["id"] for row in rows}

    if legacy_fixture:
        verdict_evidence: list[Any] = load_assessed_lines(
            root, discovery_path, rotated_dir)
        counts = coverage_index.absorbed_counts(verdict_evidence, known_ids)
        pending_by_id: dict[str, dict] | None = None
        discovery_mode = "legacy-fixture"
    else:
        store = load_structured_store(root, lock_held=lock_held)
        all_candidates = store.list_candidates(lock_held=lock_held)
        assessed = [candidate for candidate in all_candidates
                    if candidate["state"] == "assessed"]
        verdict_evidence = coverage_index.verdict_facts(assessed)
        counts = coverage_index.absorbed_counts_from_facts(
            verdict_evidence, known_ids)
        pending = [candidate for candidate in all_candidates
                   if candidate["state"] == "pending" and
                   ((lane == "x" and candidate["platform"] == "x") or
                    (lane == "community" and candidate["platform"] != "x"))]
        pending_by_id = {candidate["identity"]: candidate
                         for candidate in pending}
        if len(pending_by_id) != len(pending):
            raise PacketError("structured discovery returned duplicate identities")
        discovery_mode = "structured"

    candidates = []
    seen_keys: set[str] = set()
    for batched, hydrated in zip(batch, hydration):
        queue_line = batched["queue_line"]
        url = batched["url"]
        platform = batched["platform"]
        external_key = batched["external_key"]
        if lane == "x" and platform != "x":
            raise PacketError(f"community candidate in X lane: {url}")
        if lane == "community" and platform not in COMMUNITY_LANES:
            raise PacketError(f"X candidate in community lane: {url}")
        if external_key in seen_keys:
            if pending_by_id is not None:
                raise PacketError(
                    f"candidate batch repeats structured identity: {external_key}")
            # A repost and its original share a status id, so a batch can
            # name the same post twice under different queue lines. Defer the
            # later one to a later batch rather than refusing the whole
            # packet: once the first line is assessed and leaves the queue,
            # the deferred one is assessable on its own (observed 13 Aug
            # 2026: three repost lines of one wiz post stopped the drain).
            print(f"build-intake-packet: duplicate external key deferred to "
                  f"a later batch: {external_key}", file=sys.stderr)
            continue
        seen_keys.add(external_key)
        candidate_id = batched["candidate_id"]
        candidate_head: str | None = batched["candidate_head"]
        if pending_by_id is not None:
            projection = pending_by_id.get(candidate_id)
            if projection is None:
                raise PacketError(
                    f"candidate is not pending in the {lane} lane: {candidate_id}")
            projected_head = projection.get("head")
            if not isinstance(projected_head, str) \
                    or not HEAD_RE.fullmatch(projected_head):
                raise PacketError(
                    f"pending candidate has no valid event head: {candidate_id}")
            if candidate_head != projected_head:
                raise PacketError(
                    f"candidate batch head is stale: {candidate_id}")
        matches = exact_index.get(external_key, [])
        record = {
            "candidate_id": candidate_id,
            "platform": platform,
            "native_id": external_key.rsplit(":", 1)[1],
            "external_key": external_key,
            "url": url,
            "queue_line": queue_line,
            "queue_line_sha256": sha256_text(queue_line),
            **hydrated,
            "registry_exact_match": {
                "matched": bool(matches),
                "source_ids": matches,
            },
        }
        if candidate_head is not None:
            record["candidate_head"] = candidate_head
        candidates.append(record)

    semantic_registry = _jsonable(registry)
    semantic_verdicts = sorted(
        verdict_evidence,
        key=lambda value: (value.get("candidate_id", "")
                           if isinstance(value, dict) else str(value)))
    semantic_candidates = [
        {key: candidate.get(key) for key in (
            "candidate_id", "candidate_head", "external_key",
            "queue_line_sha256", "hydration_status", "body_sha256")}
        for candidate in candidates
    ]
    coverage = coverage_summary(rows, counts)
    packet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "lane": lane,
        "discovery": {"mode": discovery_mode},
        "source_ledger_hashes": {
            "registry_semantic_sha256": sha256_bytes(
                canonical_json_bytes(semantic_registry)),
            "verdicts_semantic_sha256": sha256_bytes(
                canonical_json_bytes(semantic_verdicts)),
            "candidate_batch_semantic_sha256": sha256_bytes(
                canonical_json_bytes(semantic_candidates)),
            "saturation_semantic_sha256": sha256_bytes(
                canonical_json_bytes(coverage)),
        },
        "coverage": coverage,
        "candidates": candidates,
    }
    markdown = render_markdown(packet)
    coverage_only = render_markdown({**packet, "candidates": []})
    markdown_bytes = len(markdown.encode("utf-8"))
    packet["size_report"] = {
        "markdown_bytes": markdown_bytes,
        "max_markdown_bytes": max_markdown_bytes,
        "within_limit": markdown_bytes <= max_markdown_bytes,
        "coverage_markdown_bytes": len(coverage_only.encode("utf-8")),
        "hydrated_body_bytes": sum(
            len((candidate["body"] or "").encode("utf-8"))
            for candidate in candidates),
    }
    return packet, markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lane", required=True, choices=("community", "x"))
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--hydrated", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--registry", type=Path,
                        help="legacy sources.toml override")
    parser.add_argument("--discovery", type=Path,
                        help="legacy DISCOVERY.md override")
    parser.add_argument("--rotated-dir", type=Path,
                        help="legacy discovery/ verdict directory override")
    parser.add_argument("--lock-held", action="store_true",
                        help="caller already holds the discovery writer lock")
    parser.add_argument("--max-markdown-bytes", type=int,
                        default=DEFAULT_MAX_MARKDOWN_BYTES)
    args = parser.parse_args(argv)
    if args.max_markdown_bytes < 1:
        parser.error("--max-markdown-bytes must be positive")
    try:
        packet, markdown = build_packet(
            root=args.root.resolve(), lane=args.lane,
            candidates_path=args.candidates,
            hydrated_path=args.hydrated,
            registry_path=args.registry,
            discovery_path=args.discovery,
            rotated_dir=args.rotated_dir,
            lock_held=args.lock_held,
            max_markdown_bytes=args.max_markdown_bytes)
    # Optional storage adapters expose their validation failures as
    # ValueError subclasses. Keep those failures at the same clean CLI
    # boundary as malformed legacy input rather than leaking a traceback.
    except (OSError, ValueError) as exc:
        print(f"build-intake-packet: {exc}", file=sys.stderr)
        return 1

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_bytes(
        json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2)
        .encode("utf-8") + b"\n")
    args.markdown_out.write_text(markdown, encoding="utf-8")
    size = packet["size_report"]
    print(f"intake packet: {len(packet['candidates'])} candidate(s), "
          f"{packet['coverage']['included_nonzero_entries']} saturated "
          f"coverage row(s), {size['markdown_bytes']} Markdown bytes "
          f"(limit {size['max_markdown_bytes']})")
    if not size["within_limit"]:
        print("build-intake-packet: Markdown packet exceeds the configured "
              "size limit; outputs were kept for inspection", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
