#!/usr/bin/env bash
# Invoke the corrections agent over the newest claim-sweep report.
#
# The claim sweep (scripts/claim-sweep.sh) rechecks dated current-state
# claims and, when it finds a state that changed underneath a page, leaves
# the text alone and flags it in its report: a changed state is an editorial
# event, not a date refresh. Some of those flags are this project's own
# errors rather than the record working, and those are corrections: fixed on
# the page AND appended to corrections.toml, or not at all (AGENTS.md).
#
# This script owns the drafting half of that pipeline. It gathers the
# evidence as the operator account (the state-changed flags, the affected
# pages' current text, the newest held capture excerpts of the sources they
# name), hands it to the agent fenced, and checks what the agent wrote. The
# agent proposes only: one file per suspected correction under
# .work/correction-proposals/. Applying is deterministic and separate, in
# scripts/apply_corrections.py, which the corrections-watch unit runs right
# after this script. The agent never edits corrections.toml or a page, and
# the guard's corrections role remit makes that a permission fact rather
# than an instruction.
#
# Exit codes:
#   0  drafting completed, no state-changed flags, or the agent is unset
#   1  agent run failed, or the run wrote outside the corrections remit
#
# State lives in .work/corrections/: evidence packs and rendered prompts.
# .work/ is ignored and never committed.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/agent-run-common.sh"
agent_load_env

# The drafting agent is any non-interactive CLI that takes a rendered prompt
# as `<bin> -p "<prompt>"`. CORRECTIONS_AGENT_BIN overrides REVIEW_AGENT_BIN;
# either may be set in .env (see .env.example). Unset is supported: the flags
# wait in the sweep report for human triage.
AGENT_BIN="${CORRECTIONS_AGENT_BIN:-${REVIEW_AGENT_BIN:-}}"

SWEEP_DIR="$ROOT/.work/claim-sweep"
STATE_DIR="$ROOT/.work/corrections"
PROPOSALS_DIR="$ROOT/.work/correction-proposals"
PROMPT_TEMPLATE="$ROOT/scripts/agent-corrections-prompt.md"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="$STATE_DIR/$TS"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"

# The newest sweep report is the trigger. No report, nothing to draft from.
REPORT="$(ls "$SWEEP_DIR"/*-report.md 2>/dev/null | sort | tail -1 || true)"
if [[ -z "$REPORT" ]]; then
  echo "agent-corrections: no claim-sweep report; nothing to do"
  exit 0
fi

# The only flags this lane acts on are state-changed ones: a claim whose
# underlying state moved and was deliberately not rewritten. Refreshed
# dates, promotions and inherent claims are the sweep's own business.
if ! grep -qi "state-changed" "$REPORT"; then
  echo "agent-corrections: $(basename "$REPORT") flags no changed state; nothing to do"
  exit 0
fi

if [[ -z "$AGENT_BIN" ]]; then
  echo "agent-corrections: $(basename "$REPORT") flags a changed state, but neither CORRECTIONS_AGENT_BIN nor REVIEW_AGENT_BIN is set;"
  echo "  the flags wait in the report for human triage (see .env.example)"
  exit 0
fi

mkdir -p "$EVIDENCE_DIR" "$PROPOSALS_DIR"

# Hydrate the evidence pack here, as the operator account, so the agent
# needs no network and no archive walk:
#
#   flags.md     the state-changed lines and the changed-state section of
#                the report (agent-written text: rendered untrusted)
#   pages.md     the full current text of every page the flags name
#   captures.md  an excerpt of the newest held capture of each registered
#                source the flags name (sources' text: rendered untrusted)
echo "agent-corrections: hydrating evidence from $(basename "$REPORT")"
"$ROOT/.venv/bin/python" - "$REPORT" "$EVIDENCE_DIR" <<'PY'
import re
import sys
from pathlib import Path

report_path, evidence_dir = Path(sys.argv[1]), Path(sys.argv[2])
root = report_path.resolve().parent.parent.parent
text = report_path.read_text(errors="replace")
lines = text.splitlines()

# State-changed rows anywhere in the report, plus the whole section the
# sweep prompt reserves for changed states ("...requiring editorial review"
# through the next heading), whichever a given report carries.
flags = [l for l in lines if "state-changed" in l.lower()]
for i, line in enumerate(lines):
    if line.startswith("## ") and "changed state" in line.lower():
        j = i + 1
        while j < len(lines) and not lines[j].startswith("## "):
            j += 1
        flags.extend(lines[i:j])
        break
(evidence_dir / "flags.md").write_text("\n".join(flags) + "\n")

# Backticked tokens in the flags are the sweep naming pages (path.astro,
# optionally with :line) and source ids.
tokens = set()
for line in flags:
    tokens.update(re.findall(r"`([^`]+)`", line))

pages_out = []
seen_pages = set()
for token in sorted(tokens):
    rel = token.split(":")[0].strip()
    if not rel.endswith(".astro") or rel in seen_pages:
        continue
    page = root / "site" / "src" / "pages" / rel
    if page.is_file():
        seen_pages.add(rel)
        pages_out.append(f"===== site/src/pages/{rel} =====\n"
                         + page.read_text(errors="replace"))
(evidence_dir / "pages.md").write_text("\n\n".join(pages_out) + "\n")

captures_out = []
snapshots = root / "archive" / "snapshots"
for token in sorted(tokens):
    ident = token.strip()
    source_dir = snapshots / ident
    if not source_dir.is_dir():
        continue
    txts = sorted(p for p in source_dir.iterdir() if p.suffix == ".txt")
    if not txts:
        continue
    newest = txts[-1]
    captures_out.append(
        f"===== archive/snapshots/{ident}/{newest.name} (excerpt) =====\n"
        + newest.read_text(errors="replace")[:20000])
(evidence_dir / "captures.md").write_text("\n\n".join(captures_out) + "\n")

print(f"agent-corrections: {len(flags)} flag line(s), "
      f"{len(pages_out)} page(s), {len(captures_out)} capture excerpt(s)")
PY

echo "agent-corrections: drafting proposals from $(basename "$REPORT")"

agent_begin corrections

agent_render "$PROMPT_TEMPLATE" "$PROMPT_RENDERED" \
  --untrusted "FLAGS=$EVIDENCE_DIR/flags.md" \
  --file "PAGES=$EVIDENCE_DIR/pages.md" \
  --untrusted "CAPTURES=$EVIDENCE_DIR/captures.md" \
  --value "REPORT=${REPORT#"$ROOT"/}" \
  --value "PROPOSALS_DIR=.work/correction-proposals" \
  --value "DATE=$(date -u +%Y-%m-%d)"

rc=0
agent_invoke "$AGENT_BIN" "$PROMPT_RENDERED" || rc=$?

grc=0
agent_finish corrections || grc=$?

if [[ $grc -ne 0 ]]; then
  echo "agent-corrections: the run was rejected by the guard; proposals stay as the agent left them" >&2
  exit 1
fi
if [[ $rc -ne 0 ]]; then
  echo "agent-corrections: agent run failed" >&2
  exit 1
fi

echo "agent-corrections: run complete; apply with: just apply-corrections --yes"
exit 0
