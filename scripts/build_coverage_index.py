#!/usr/bin/env python3
"""Describe what the record already covers, for the intake agent to consult.

The largest single class of intake dismissals is not noise that a sieve can
reach. Of 248 dismissals in the assessed corpus on 7 Aug 2026, 88 read
"already represented by <id>": the candidate is genuinely about the incident,
and the record already holds that theme. The agent reaches those verdicts by
recalling `sources.toml` across a long prompt, and it demonstrably
re-derives them, naming a thread it had itself dismissed weeks earlier as the
precedent for dismissing another.

The obvious mechanical fix does not work, and was measured before this was
written. IDF-weighted cosine over candidate titles, evaluated leave-one-out
against the 425-entry assessed corpus, put a verdict's own named referent
top-1 in 4 of 76 cases while flagging 93 of 174 registered entries as
near-duplicates. The failures are semantic rather than lexical ("Every
influencoor and podcaster who took Coinkite's money" is a duplicate of
basedlayer-influencers-vs-engineers, and shares no word with it), so token
overlap cannot reach them and a threshold cannot be tuned into working.

So this does not try to decide anything. It turns recall into a lookup: one
line per registered source, in a list short enough to read, in front of the
agent at the moment it needs it. What the agent does with it is the agent's
judgement, which is the part that was working.

The `absorbed` count is the saturation signal, and it is the reason this is
worth generating rather than hand-writing. It counts candidates already
dismissed as duplicates of that entry, read out of the assessed verdicts. A
theme that has absorbed nine candidates will absorb a tenth; a theme that has
absorbed none is one where a new thread may still add something. The corpus
is self-labelling, so nobody has to maintain this.

Output is data only: no instructions, no framing. The prose that tells the
agent how to read it belongs in the trusted template, because this file does
not qualify. Source titles are the titles of threads strangers wrote, so the
index is untrusted material and the driver passes it through the untrusted
channel with everything else somebody else's keyboard reached.

Zero dependencies: stdlib only, Python 3.11+ for tomllib.

Usage:
    build_coverage_index.py [--out PATH] [--max-title N]
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

import registry_store

ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT
SOURCES = ROOT / "sources.toml"

# The community lanes' own id prefixes. A candidate most often duplicates
# another thread, so those are listed first, but not exclusively: a link post
# relaying a chain monitor or a vendor statement duplicates that primary
# source, and the corpus contains exactly those verdicts.
COMMUNITY_PREFIXES = ("reddit-", "stackernews-", "bitcointalk-")

# "already represented by <id>", and the two other phrasings the agent has
# used for the same verdict. The pattern is loose on purpose and the registry
# does the filtering: an earlier version demanded three hyphen-separated
# segments to keep prose out, which kept out `optech-416` and every
# `<author>-<status-id>` social post as well. Anything id-shaped is lifted
# here and discarded below unless it is really registered.
REFERENT_RE = re.compile(
    r"(?:represented by|duplicate of|reprise of)\s+"
    r"([a-z][a-z0-9]*(?:-[a-z0-9]+)+)"
)
# A verdict may name two: "already represented by X and Y".
ALSO_RE = re.compile(r"\band\s+([a-z][a-z0-9]*(?:-[a-z0-9]+)+)")


def assessed_lines(intake: Path, rotated: Path) -> list[str]:
    """Read an explicitly named legacy fixture.

    Production consumers switch to the structured discovery store once its
    migration marker exists.  Keeping the Markdown reader parameterised makes
    it useful to small historical fixtures without leaving an implicit live
    fallback that could silently shadow a damaged store.
    """
    lines = []
    paths = [intake] + sorted(rotated.glob("assessed-*.md"))
    for path in paths:
        if not path.exists():
            continue
        body = path.read_text(encoding="utf-8").split("## Assessed", 1)
        if len(body) != 2:
            continue
        lines += [l for l in body[1].splitlines() if l.startswith("- ")]
    return lines


def verdict_facts(candidates: list[dict]) -> list[dict]:
    """Extract the coverage-relevant facts from candidate projections."""
    facts: list[dict] = []
    for candidate in candidates:
        verdict = candidate.get("verdict")
        if not isinstance(verdict, dict):
            raise ValueError(
                f"assessed discovery candidate lacks verdict: "
                f"{candidate.get('identity', '?')}")
        kind, reason, at = (verdict.get("kind"), verdict.get("reason"),
                            verdict.get("at"))
        if kind not in {"registered", "dismissed", "already-registered"} \
                or not isinstance(reason, str) or not reason.strip() \
                or not isinstance(at, str) or not at:
            raise ValueError(
                f"invalid structured discovery verdict: "
                f"{candidate.get('identity', '?')}")
        facts.append({
            "candidate_id": candidate["identity"],
            "kind": kind,
            "reason": reason,
            "at": at,
            **({"source_id": verdict["source_id"]}
               if isinstance(verdict.get("source_id"), str) else {}),
        })
    return facts


def structured_verdict_facts(root: Path, *,
                             lock_held: bool = False) -> list[dict]:
    """Return assessed verdict facts from a validated structured store.

    The marker is an activation boundary, not a hint.  Once present, a broken
    store must stop coverage generation rather than reset saturation counts by
    falling back to rendered Markdown.
    """
    try:
        import discovery_store
    except ImportError as exc:  # pragma: no cover - deployment packaging guard
        raise ValueError("structured discovery marker exists but "
                         "discovery_store is unavailable") from exc

    store = discovery_store.DiscoveryStore(root)
    if not store.marker.is_file():
        raise ValueError("structured discovery migration marker is missing")
    try:
        discovery_store.validate_store(root, lock_held=lock_held)
    except TypeError as exc:
        raise ValueError(
            "discovery store does not support lock-held validation") from exc
    return verdict_facts(store.list_candidates(
        state="assessed", lock_held=lock_held))


def absorbed_counts(lines: list[str], known: set[str]) -> Counter:
    """How many candidates each entry has already absorbed as a duplicate.

    Checked against the registry rather than trusted from the text: a verdict
    is prose, and the regex will occasionally lift a phrase that looks like an
    id. Only names that are actually registered are counted.
    """
    counts: Counter = Counter()
    for line in lines:
        if "dismissed" not in line:
            continue
        found = REFERENT_RE.findall(line)
        if not found:
            continue
        found += ALSO_RE.findall(line)
        counts.update(name for name in found if name in known)
    return counts


def absorbed_counts_from_facts(facts: list[dict], known: set[str]) -> Counter:
    """Count duplicate referents from structured dismissal verdicts.

    The reason remains prose because that is what the intake verdict records;
    the structured fact tells us authoritatively which rows are dismissals, so
    rendered line shape and legacy suffixes no longer participate.
    """
    counts: Counter = Counter()
    for fact in facts:
        if fact.get("kind") != "dismissed":
            continue
        reason = fact.get("reason")
        if not isinstance(reason, str):
            raise ValueError("structured dismissal verdict has no reason")
        found = REFERENT_RE.findall(reason)
        if found:
            found += ALSO_RE.findall(reason)
            counts.update(name for name in found if name in known)
    return counts


def structured_absorbed_counts(root: Path, known: set[str], *,
                               lock_held: bool = False) -> Counter:
    return absorbed_counts_from_facts(
        structured_verdict_facts(root, lock_held=lock_held), known)


def entries() -> list[dict]:
    """Every registered source and social post, as index rows.

    All four tables, because a candidate can duplicate any of them: the corpus
    holds community threads dismissed as relays of a registered X post and of
    a chain monitor's own page, not only of other threads.
    """
    # The live repository reads the discoverable sharded projection when its
    # manifest is current. Tests and one-off callers that replace SOURCES with
    # an explicit fixture path still read that file directly.
    if ROOT == PROJECT_ROOT and SOURCES == ROOT / "sources.toml":
        data = registry_store.load(ROOT)
    else:
        data = tomllib.loads(SOURCES.read_text(encoding="utf-8"))
    rows = []
    for table in ("source", "x_post", "nostr_post"):
        for entry in data.get(table, []):
            if not entry.get("id"):
                continue
            rows.append({
                "id": entry["id"],
                "table": table,
                "org": entry.get("org") or "?",
                "title": entry.get("title") or "",
            })
    return rows


def render(rows: list[dict], counts: Counter, max_title: int) -> str:
    def line(row: dict) -> str:
        title = row["title"].replace("\n", " ").strip()
        if len(title) > max_title:
            title = title[:max_title - 1].rstrip() + "…"
        n = counts.get(row["id"], 0)
        tail = f"  (absorbed {n})" if n else ""
        return f"{row['id']}  [{row['org']}]  {title}{tail}"

    def block(selected: list[dict]) -> list[str]:
        # Saturated themes first: they are the ones a new candidate is most
        # likely to be a duplicate of, and the ones worth reading if the whole
        # list is not read.
        selected.sort(key=lambda r: (-counts.get(r["id"], 0), r["id"]))
        return [line(r) for r in selected]

    community = [r for r in rows if r["table"] == "source"
                 and r["id"].startswith(COMMUNITY_PREFIXES)]
    other = [r for r in rows if r["table"] == "source"
             and not r["id"].startswith(COMMUNITY_PREFIXES)]
    social = [r for r in rows if r["table"] in ("x_post", "nostr_post")]

    out: list[str] = []
    for heading, selected in (
        ("Community threads", community),
        ("Other registered sources", other),
        ("Registered social posts", social),
    ):
        if out:
            out.append("")
        out += [f"## {heading} ({len(selected)})", ""]
        out += block(selected)
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path,
                    help="write here instead of standard output")
    ap.add_argument("--max-title", type=int, default=100,
                    help="truncate titles to this many characters")
    args = ap.parse_args()

    rows = entries()
    known = {r["id"] for r in rows}
    try:
        counts = structured_absorbed_counts(ROOT, known)
    except (OSError, TypeError, ValueError) as exc:
        print(f"build-coverage-index: {exc}", file=sys.stderr)
        return 1
    text = render(rows, counts, args.max_title)

    if args.out:
        args.out.write_text(text, encoding="utf-8")
        saturated = sum(1 for v in counts.values() if v >= 2)
        print(f"coverage index: {len(rows)} entries, "
              f"{len(counts)} with an absorbed candidate "
              f"({saturated} with two or more) -> {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
