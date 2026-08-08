#!/usr/bin/env python3
"""Deterministically apply, or refuse, agent-drafted correction proposals.

The corrections agent (scripts/agent-corrections.sh) drafts proposals; this
is the other half of the pipeline and the only part that writes. A proposal
is one markdown file in .work/correction-proposals/<TS>-<page-slug>.md with
a status header, a prose rationale, a ```toml fence holding the complete
[[correction]] block to append to corrections.toml, and a ```diff fence
holding the page edit as a unified diff against the current page text. The
corrections convention (AGENTS.md, corrections.toml) is "fixed on the page
AND appended here; both, or neither counts", so a proposal carries both
halves and is applied all-or-nothing or not at all.

Every proposal is validated against the live tree before anything is
written:

  a. `said` is a verbatim substring of the page the diff patches, as that
     page stands now. The convention quotes the wrong text as published,
     not a paraphrase that softens it.
  b. every route in the block's `pages` resolves to a real file under
     site/src/pages/ (`/` -> index.astro, `/record/funds/` ->
     record/funds.astro)
  c. the TOML block parses and satisfies the same rules the build enforces
     in site/src/lib/corrections.ts, mirrored here: date is YYYY-MM-DD,
     kind is correction|clarification|withdrawal, summary non-empty, says
     present and non-empty unless withdrawal, at least one route
  d. the page diff applies with zero fuzz and zero offset (GNU patch -F0
     --dry-run, and the dry run's output is checked for offsets)
  e. the corrections.toml edit is a pure append: the new file's text has
     the old file's text as a verbatim prefix
  f. the proposal is not marked advice-only
  g. no existing [[correction]] entry already carries the same `said`:
     a rerun of the agent over the same flag must not apply twice

Any failure moves the proposal, unchanged apart from an appended reason,
into .work/correction-proposals/rejected/; nothing is applied from it.
Advice-only proposals are left where they are: they surface in
`just status` and the operator UI for a human decision, and are never
applied by a machine.

On a successful apply the page is patched, the block is appended, the
proposal moves to .work/correction-proposals/applied/, and one
correction-applied alert is emitted (failure-tolerant: alerting never
breaks the applier).

Modes: the default is a dry run that lists a verdict per proposal and
writes nothing. --yes applies. Exit status is 0 always, including for
rejections, except for usage errors (exit 2): a rejected proposal is a
routine outcome, and this runs chained into corrections-watch.service
where a non-zero exit would read as a unit failure.

    apply_corrections.py          dry run: verdicts only
    apply_corrections.py --yes    apply every proposal that passes
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROPOSALS = ".work/correction-proposals"
CORRECTIONS_TOML = "corrections.toml"

KINDS = ("correction", "clarification", "withdrawal")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUS_RE = re.compile(r"^\s*-?\s*status:\s*([\w-]+)", re.IGNORECASE | re.MULTILINE)
FENCE_RE = re.compile(r"```(\w+)\n(.*?)```", re.DOTALL)


class ProposalError(Exception):
    """The file is not a parseable proposal at all."""


@dataclass
class Proposal:
    path: Path
    advice_only: bool
    toml_text: str           # the block exactly as it will be appended
    entry: dict              # the single parsed [[correction]] row
    diff_text: str
    diff_targets: list[str] = field(default_factory=list)


def route_to_page(root: Path, route: str) -> Path | None:
    """Resolve a site route to its file under site/src/pages/."""
    rel = route.strip("/")
    pages = root / "site" / "src" / "pages"
    for candidate in ((pages / f"{rel}.astro") if rel else None,
                      pages / rel / "index.astro"):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def parse_proposal(path: Path) -> Proposal:
    """Read one proposal file into its parts, or raise ProposalError."""
    text = path.read_text(errors="replace")
    match = STATUS_RE.search(text[:2000])
    if not match:
        raise ProposalError("no `- status:` header line")
    status = match.group(1).lower()
    if status == "advice-only":
        return Proposal(path=path, advice_only=True, toml_text="",
                        entry={}, diff_text="")
    if status != "proposal":
        raise ProposalError(f"unknown status {status!r} "
                            f"(want `proposal` or `advice-only`)")

    fences = FENCE_RE.findall(text)
    toml_blocks = [body for lang, body in fences if lang == "toml"]
    diff_blocks = [body for lang, body in fences if lang == "diff"]
    if len(toml_blocks) != 1:
        raise ProposalError(f"want exactly one ```toml fence, found "
                            f"{len(toml_blocks)}")
    if len(diff_blocks) != 1:
        raise ProposalError(f"want exactly one ```diff fence, found "
                            f"{len(diff_blocks)}")

    toml_text = toml_blocks[0].strip("\n")
    try:
        parsed = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ProposalError(f"the ```toml block does not parse: {exc}")
    rows = parsed.get("correction")
    if not isinstance(rows, list) or len(rows) != 1 or \
            not isinstance(rows[0], dict):
        raise ProposalError("the ```toml block must hold exactly one "
                            "[[correction]] entry")

    diff_text = diff_blocks[0]
    targets = re.findall(r"^\+\+\+ b/(\S+)", diff_text, re.MULTILINE)
    if not targets:
        raise ProposalError("the ```diff block names no `+++ b/...` target")
    return Proposal(path=path, advice_only=False, toml_text=toml_text,
                    entry=rows[0], diff_text=diff_text,
                    diff_targets=targets)


def validate_entry(entry: dict) -> list[str]:
    """The corrections.ts rules, mirrored: a malformed entry must fail here
    rather than at the next site build."""
    problems = []
    if not DATE_RE.match(str(entry.get("date", ""))):
        problems.append("date must be YYYY-MM-DD")
    kind = entry.get("kind")
    if kind not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)}")
    if not str(entry.get("summary", "")).strip():
        problems.append("summary is required")
    pages = entry.get("pages")
    if not isinstance(pages, list) or not pages:
        problems.append("pages must list at least one route")
    if kind != "withdrawal" and not str(entry.get("says", "")).strip():
        problems.append("says is required unless the claim was withdrawn")
    return problems


def append_only_ok(before: str, after: str) -> bool:
    """The corrections.toml edit is a pure append: before is a verbatim
    prefix of after, so nothing already logged is rewritten or moved."""
    return after.startswith(before)


def build_corrections_append(before: str, block: str) -> str:
    """The new corrections.toml: the old text, then the block, nothing else."""
    return before.rstrip("\n") + "\n\n" + block.strip("\n") + "\n"


def run_patch(root: Path, diff_text: str, apply: bool) -> tuple[bool, str]:
    """Check (or perform) the page diff with GNU patch, zero fuzz allowed.

    Returns (ok, detail). With apply=False this is `patch --dry-run`; the
    output is also rejected when it reports an offset, because an offset
    means the hunk landed somewhere other than where the agent placed it,
    and a correction must land where the claim was. With apply=True the
    patch runs against a scratch copy of the tree and the caller reads the
    result from there, so a failed apply never half-writes the real tree.
    """
    patch = shutil.which("patch")
    if patch is None:
        return False, "GNU patch is not on PATH"
    argv = [patch, "--no-backup-if-mismatch", "-F0", "-p1", "-d", str(root)] \
        + ([] if apply else ["--dry-run"])
    done = subprocess.run(argv, input=diff_text, capture_output=True,
                          text=True)
    out = (done.stdout + done.stderr).strip()
    if done.returncode != 0:
        return False, f"patch rejects the diff: {out or 'no match'}"
    if re.search(r"offset|fuzz", out, re.IGNORECASE):
        return False, f"patch applies only with drift: {out}"
    return True, out


def validate(root: Path, proposal: Proposal) -> list[str]:
    """Every check a proposal must pass before --yes touches the tree."""
    problems: list[str] = []
    entry = proposal.entry

    # (c) the entry itself, against the build's own rules.
    problems += validate_entry(entry)
    if problems:
        return problems  # route and page checks need a sound entry

    pages = [str(p) for p in entry["pages"]]

    # (b) every named route exists.
    for route in pages:
        if route_to_page(root, route) is None:
            problems.append(f"route {route} resolves to no file under "
                            f"site/src/pages/")
    if problems:
        return problems

    # (d-1) the diff touches only site pages that exist.
    for target in proposal.diff_targets:
        if not target.startswith("site/src/pages/"):
            problems.append(f"diff target {target} is not under "
                            f"site/src/pages/")
        elif not (root / target).is_file():
            problems.append(f"diff target {target} does not exist")
    if problems:
        return problems

    # (a) `said` quotes the page verbatim. It is required for every kind
    # here, stricter than corrections.ts: a correction that cannot quote
    # what was wrong cannot be checked, and an uncheckable correction is
    # advice, not automation.
    said = str(entry.get("said", "")).strip()
    if not said:
        problems.append("said is required: it quotes the wrong text as "
                        "published, and the applier verifies it verbatim")
    else:
        page_texts = []
        for target in proposal.diff_targets:
            page_texts.append((root / target).read_text(errors="replace"))
        if not any(said in text for text in page_texts):
            problems.append("said is not a verbatim substring of the page "
                            "the diff patches (a paraphrase is a rejection, "
                            "not a correction)")
        # (g) not already applied.
        corrections = (root / CORRECTIONS_TOML).read_text(errors="replace")
        if said in corrections:
            problems.append("a correction carrying this `said` text is "
                            "already in corrections.toml; applying would "
                            "log it twice")
    if problems:
        return problems

    # (d) the diff applies, exactly where the agent placed it.
    ok, detail = run_patch(root, proposal.diff_text, apply=False)
    if not ok:
        problems.append(detail)
        return problems

    # (e) the corrections.toml edit is a pure append, and the result parses.
    before = (root / CORRECTIONS_TOML).read_text(errors="replace")
    after = build_corrections_append(before, proposal.toml_text)
    if not append_only_ok(before, after):
        problems.append("the corrections.toml edit is not a pure append")
        return problems
    try:
        tomllib.loads(after)
    except tomllib.TOMLDecodeError as exc:
        problems.append(f"corrections.toml would not parse after the "
                        f"append: {exc}")
    return problems


def apply_proposal(root: Path, proposal: Proposal) -> tuple[bool, str]:
    """Apply a validated proposal. Returns (True, alert summary) or (False,
    reason). Validation has already passed; what can still fail here is the
    two writes, and the page patch runs against a scratch copy first so a
    failure there leaves the real tree untouched."""
    # Patch into a scratch copy of the target pages.
    with tempfile.TemporaryDirectory(prefix="corrections-") as scratch:
        scratch_root = Path(scratch)
        for target in proposal.diff_targets:
            src = root / target
            dst = scratch_root / target
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        ok, detail = run_patch(scratch_root, proposal.diff_text, apply=True)
        if not ok:
            return False, ("the diff passed validation but failed the apply "
                           f"pass against a scratch copy: {detail}")
        patched = {target: (scratch_root / target).read_text(errors="replace")
                   for target in proposal.diff_targets}

    before = (root / CORRECTIONS_TOML).read_text(errors="replace")
    after = build_corrections_append(before, proposal.toml_text)

    # Validation is complete; only the writes remain. Pages first, then the
    # log that indexes them.
    for target, text in patched.items():
        (root / target).write_text(text)
    (root / CORRECTIONS_TOML).write_text(after)

    summary = str(proposal.entry.get("summary", "")).strip()
    summary = re.sub(r"\s+", " ", summary)
    pages = ", ".join(str(p) for p in proposal.entry.get("pages", []))
    return True, (f"{proposal.entry.get('kind')} on {pages}: "
                  f"{summary[:160]}")


def emit_alert(key: str, summary: str) -> None:
    """One correction-applied alert per applied proposal. Allowed to fail:
    the alert stream is the operator's view, not a gate on the write."""
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).with_name("alert.py")),
             "emit", "--kind", "correction-applied", "--severity", "warning",
             "--key", key, "--summary", summary],
            cwd=ROOT, capture_output=True, text=True, check=False)
    except OSError:
        pass


def reject(path: Path, reason: str, stamp: str) -> None:
    rejected = path.parent / "rejected"
    rejected.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(f"\n\n## rejected {stamp}\n{reason}\n")
    path.rename(rejected / path.name)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true",
                        help="apply; the default is a dry run")
    parser.add_argument("--root", type=Path, default=ROOT,
                        help=argparse.SUPPRESS)  # test fixture root
    args = parser.parse_args(argv)
    root = args.root

    if shutil.which("patch") is None:
        print("apply-corrections: GNU patch is not on PATH; nothing can be "
              "validated, and nothing was touched", file=sys.stderr)
        return 2

    queue = root / PROPOSALS
    proposals = sorted(queue.glob("*.md")) if queue.is_dir() else []
    if not proposals:
        print("apply-corrections: no proposals in "
              f"{PROPOSALS}/; nothing to do")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = "APPLY" if args.yes else "dry run"
    print(f"apply-corrections ({mode}): {len(proposals)} proposal(s)")

    for path in proposals:
        name = path.name
        try:
            proposal = parse_proposal(path)
        except ProposalError as exc:
            print(f"  {name}: REJECTED - not a parseable proposal: {exc}")
            if args.yes:
                reject(path, f"not a parseable proposal: {exc}", stamp)
            continue

        if proposal.advice_only:
            print(f"  {name}: advice-only - never applied by a machine; "
                  f"it waits here for a human (see `just status`)")
            continue

        problems = validate(root, proposal)
        if problems:
            reason = "; ".join(problems)
            print(f"  {name}: REJECTED - {reason}")
            if args.yes:
                reject(path, reason, stamp)
            continue

        if not args.yes:
            pages = ", ".join(str(p) for p in proposal.entry["pages"])
            print(f"  {name}: would apply "
                  f"({proposal.entry['kind']} on {pages})")
            continue

        result_ok, message = apply_proposal(root, proposal)
        if not result_ok:
            print(f"  {name}: REJECTED at apply - {message}")
            reject(path, message, stamp)
            continue
        applied = path.parent / "applied"
        applied.mkdir(parents=True, exist_ok=True)
        path.rename(applied / path.name)
        key = f"correction-{path.stem}"
        emit_alert(key, message)
        print(f"  {name}: APPLIED - {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
