#!/usr/bin/env bash
# Invoke the sweep agent over unverified claim markers.
#
# The site's epistemic model (see AGENTS.md) grades every material claim as
# verified, reported, derived or unverified. Absence and outstanding-action
# claims go stale as the world publishes; this script periodically asks the
# agent to recheck them, promote what can now be evidenced, and refresh
# recheck dates on the rest. It owns no archive writes itself, and neither
# does the agent: a source the sweep registers is first-captured here, after
# the guard has accepted the registry change.
#
# The sweep reads only repository state and captures the driver already holds.
# New evidence acquisition belongs to the driver-side discovery and capture
# lanes; an agent never fetches the evidence it uses to alter a claim.
#
# Exit codes:
#   0  sweep completed
#   1  agent run failed, or the run wrote outside the sweep remit (marker not
#      advanced; next tick retries)
#
# State lives in .work/claim-sweep/: last-run marker, rendered prompts and
# per-run reports. .work/ is ignored and never committed.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/agent-run-common.sh"
agent_load_env

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

agent_begin sweep

agent_render "$PROMPT_TEMPLATE" "$PROMPT_RENDERED" \
  --value "SINCE=$SINCE" \
  --value "REPORT_PATH=$REPORT_PATH"

rc=0
agent_invoke "$AGENT_BIN" "$PROMPT_RENDERED" || rc=$?

grc=0
agent_finish sweep || grc=$?

if [[ $grc -ne 0 ]]; then
  echo "claim-sweep: the run was rejected by the guard; marker NOT advanced" >&2
  exit 1
fi
if [[ $rc -ne 0 ]]; then
  echo "claim-sweep: agent run failed; marker NOT advanced" >&2
  exit 1
fi

if [[ ! -f "$ROOT/$REPORT_PATH" ]]; then
  echo "claim-sweep: WARNING - agent succeeded but wrote no report to $REPORT_PATH" >&2
fi
touch "$MARKER"
chmod 600 "$MARKER"
echo "claim-sweep: run complete; marker advanced to $(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit 0
