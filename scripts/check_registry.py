#!/usr/bin/env python3
"""Refuse a source registry that could turn the poll into a beacon.

The 30-minute poll fetches whatever `sources.toml` names, and for 60 Stacker
News entries it POSTs a body from the same file. That is the longest-lived
capability in this repository: a source registered once is fetched every 30
minutes until somebody removes it. The intake agent appends to that file
unattended, from candidate threads that strangers wrote, so the registry is
where an injection would try to leave something behind.

So the registry is checked rather than trusted, in two modes:

  audit   the whole file, run by `just audit`, so a block cannot survive to a
          build however it arrived
  delta   one agent run's before and after, run by agent_guard.py, which also
          enforces that an existing source was not quietly rewritten

The rules are deliberately narrow, because everything they permit is the
shape the registry already has:

- every URL is https and its host is in scripts/registry_hosts.toml
- a community id prefix binds its host: a `reddit-*` source may only name
  reddit.com, so a candidate cannot register itself as a Reddit thread that
  fetches from somewhere else
- `fetch_post` is the one pinned GraphQL query with only the item id changed,
  so the POST body is not an attacker's to choose
- an agent may retier a community source, move its `watch_until`, correct its
  floor or rewrite its note. It may not change what gets fetched, or how

Exits non-zero and names every offender.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
HOSTS_FILE = Path(__file__).resolve().parent / "registry_hosts.toml"

# The Stacker News item query, fixed except for the item id. Anything else in
# a POST body is an arbitrary request this project did not write.
FETCH_POST = re.compile(
    r'^\{"query": "\{ item\(id: (?P<id>\d+)\) \{ title text createdAt '
    r'user \{ name \} comments \{ comments \{ text createdAt '
    r'user \{ name \} \} \} \} \}"\}$'
)

# An id prefix that names a platform must name that platform's host too.
PREFIX_HOSTS = {
    "stackernews": {"stacker.news"},
    "reddit": {"www.reddit.com"},
    "bitcointalk": {"bitcointalk.org"},
}

BLOCK_HOSTS = {
    "x_post": {"x.com"},
    "nostr_post": {"njump.me"},
}

CAPTURE_MODES = {"http", "browser", "reddit-json"}

# What an intake or sweep agent may correct on a source that already exists.
# Everything absent from this set describes what gets fetched or how it is
# compared, and changing it is a capture decision, not an assessment.
MUTABLE_FIELDS = {"tier", "min_chars", "watch_until", "note", "why", "title"}

URL_FIELDS = ("url", "fetch_url")
NPUB = re.compile(r"^npub1[023456789acdefghjklmnpqrstuvwxyz]{58}$")


def allowed_hosts(path: Path = HOSTS_FILE) -> set[str]:
    groups = tomllib.loads(path.read_text()).get("hosts", {})
    return {host for group in groups.values() for host in group}


def load(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def blocks(registry: dict) -> dict[str, dict[str, dict]]:
    """Every registry block, keyed by table then id."""
    out: dict[str, dict[str, dict]] = {}
    for table in ("source", "x_post", "nostr_post"):
        out[table] = {
            block["id"]: block
            for block in registry.get(table, [])
            if isinstance(block, dict) and "id" in block
        }
    return out


def check_block(table: str, block: dict, hosts: set[str]) -> list[str]:
    """Validate one registry block in isolation."""
    problems = []
    ident = block.get("id", "(no id)")
    prefix = str(ident).split("-")[0]

    for field in URL_FIELDS:
        raw = block.get(field)
        if raw is None:
            continue
        if not isinstance(raw, str):
            problems.append(f"{ident}: {field} is not a string")
            continue
        parsed = urlparse(raw)
        host = parsed.netloc.lower()
        if parsed.scheme != "https":
            problems.append(f"{ident}: {field} is not https ({parsed.scheme or 'no scheme'})")
        if "@" in host or ":" in host:
            # userinfo and a port both let a URL read as one host and resolve
            # as another. Neither appears anywhere in the registry today.
            problems.append(f"{ident}: {field} host carries userinfo or a port ({host})")
            continue
        expected = PREFIX_HOSTS.get(prefix) or BLOCK_HOSTS.get(table)
        if expected is not None and host not in expected:
            problems.append(
                f"{ident}: {field} host {host or '(none)'} does not match the "
                f"{prefix if prefix in PREFIX_HOSTS else table} host "
                f"{', '.join(sorted(expected))}")
        elif host not in hosts:
            problems.append(
                f"{ident}: {field} host {host or '(none)'} is not in "
                f"scripts/registry_hosts.toml. Adding a host is a human edit")

    post = block.get("fetch_post")
    if post is not None:
        if not isinstance(post, str) or not FETCH_POST.match(post):
            problems.append(
                f"{ident}: fetch_post is not the pinned item query with only "
                f"the item id changed")

    capture = block.get("capture")
    if capture is not None and capture not in CAPTURE_MODES:
        problems.append(f"{ident}: unknown capture mode {capture!r}")

    if table == "nostr_post":
        author = block.get("author")
        if not isinstance(author, str) or not NPUB.match(author):
            problems.append(f"{ident}: author is not an npub")

    return problems


def check_registry(registry: dict, hosts: set[str]) -> list[str]:
    problems = []
    for table, by_id in blocks(registry).items():
        for block in by_id.values():
            problems += check_block(table, block, hosts)
    return problems


def check_delta(before: dict, after: dict, hosts: set[str]) -> list[str]:
    """What one agent run did to the registry."""
    problems = []
    old, new = blocks(before), blocks(after)
    for table in new:
        removed = set(old[table]) - set(new[table])
        for ident in sorted(removed):
            problems.append(f"{ident}: removed from [[{table}]]. The registry "
                            f"is append-only to an agent")
        for ident in sorted(set(new[table]) - set(old[table])):
            problems += check_block(table, new[table][ident], hosts)
        for ident in sorted(set(new[table]) & set(old[table])):
            was, now = old[table][ident], new[table][ident]
            if was == now:
                continue
            changed = {
                key for key in set(was) | set(now)
                if was.get(key) != now.get(key)
            }
            frozen = sorted(changed - MUTABLE_FIELDS)
            if frozen:
                problems.append(
                    f"{ident}: existing entry had {', '.join(frozen)} changed. "
                    f"An agent may correct only "
                    f"{', '.join(sorted(MUTABLE_FIELDS))}")
            # A permitted field still has to leave a valid block behind.
            problems += check_block(table, now, hosts)
    return problems


def report(problems: list[str], subject: str) -> int:
    if not problems:
        return 0
    print(f"registry check failed ({len(problems)} problem(s)) in {subject}:",
          file=sys.stderr)
    for problem in problems[:20]:
        print(f"  - {problem}", file=sys.stderr)
    if len(problems) > 20:
        print(f"  ... and {len(problems) - 20} more", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ROOT / "sources.toml")
    parser.add_argument("--hosts", type=Path, default=HOSTS_FILE)
    parser.add_argument("--before", type=Path,
                        help="registry as it stood before an agent run; "
                             "enables the delta rules as well")
    args = parser.parse_args()

    hosts = allowed_hosts(args.hosts)
    try:
        after = load(args.registry)
    except tomllib.TOMLDecodeError as exc:
        print(f"registry check failed: {args.registry} does not parse: {exc}",
              file=sys.stderr)
        return 1

    if args.before is None:
        problems = check_registry(after, hosts)
        if report(problems, str(args.registry)):
            return 1
        counts = {table: len(by_id) for table, by_id in blocks(after).items()}
        print("registry check ok: " + ", ".join(
            f"{n} {table}" for table, n in counts.items() if n))
        return 0

    problems = check_delta(load(args.before), after, hosts)
    if report(problems, "this run's registry changes"):
        return 1
    print("registry check ok: this run's registry changes are in shape")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
