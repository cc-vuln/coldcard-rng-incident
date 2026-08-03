#!/usr/bin/env bash
# Invoke the sweep agent over unverified claim markers.
#
# The site's epistemic model (see AGENTS.md) grades every material claim as
# verified, reported, derived or unverified. Absence and outstanding-action
# claims go stale as the world publishes; this script periodically asks the
# agent to recheck them, promote what can now be evidenced, and refresh
# recheck dates on the rest. It owns no archive writes itself: new sources
# enter archive/ only through `just capture-one`, which the agent runs.
#
# Exit codes:
#   0  sweep completed
#   1  agent run failed (marker not advanced; next tick retries)
#
# State lives in .work/claim-sweep/: last-run marker, rendered prompts and
# per-run reports. .work/ is ignored and never committed.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

# The sweep agent is any non-interactive CLI that takes a rendered prompt as
# `<bin> -p "<prompt>"`, reads and edits the working tree, and exits non-zero
# on failure. CLAIM_SWEEP_AGENT_BIN overrides REVIEW_AGENT_BIN; either may be
# set in .env (see .env.example).
AGENT_BIN="${CLAIM_SWEEP_AGENT_BIN:-${REVIEW_AGENT_BIN:-}}"
if [[ -z "$AGENT_BIN" ]]; then
  echo "claim-sweep: neither CLAIM_SWEEP_AGENT_BIN nor REVIEW_AGENT_BIN is set; see .env.example" >&2
  exit 1
fi

STATE_DIR="$ROOT/.work/claim-sweep"
MARKER="$STATE_DIR/last-run"
PROMPT_TEMPLATE="$ROOT/scripts/claim-sweep-prompt.md"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_PATH=".work/claim-sweep/${TS}-report.md"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"

mkdir -p "$STATE_DIR"

if [[ -f "$MARKER" ]]; then
  SINCE="$(date -u -r "$MARKER" +%Y-%m-%dT%H:%M:%SZ)"
else
  SINCE="the beginning of the site (first sweep; every unverified claim is in scope)"
fi

echo "claim-sweep: rechecking unverified claims; last successful sweep: $SINCE"

export SINCE REPORT_PATH
awk '{
  gsub(/{SINCE}/, ENVIRON["SINCE"])
  gsub(/{REPORT_PATH}/, ENVIRON["REPORT_PATH"])
  print
}' "$PROMPT_TEMPLATE" > "$PROMPT_RENDERED"

if "$AGENT_BIN" -p "$(cat "$PROMPT_RENDERED")"; then
  if [[ ! -f "$ROOT/$REPORT_PATH" ]]; then
    echo "claim-sweep: WARNING - agent succeeded but wrote no report to $REPORT_PATH" >&2
  fi
  touch "$MARKER"
  chmod 600 "$MARKER"
  echo "claim-sweep: run complete; marker advanced to $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  exit 0
else
  echo "claim-sweep: agent run failed; marker NOT advanced" >&2
  exit 1
fi
