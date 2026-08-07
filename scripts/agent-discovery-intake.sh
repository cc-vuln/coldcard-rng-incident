#!/usr/bin/env bash
# Invoke the intake agent over pending discovery candidates.
#
# Community discovery and the manual X watcher queue candidates in DISCOVERY.md.
# This script owns the assessment layer: when pending entries exist it asks the
# agent to judge each one and record every verdict. Community intake registers
# accepted threads; explicit X intake uses a separate read-only triage prompt
# and stops at a recommendation for a person.
#
# A backlog (for example the first reddit enumeration) is assessed in
# bounded chunks: --max N caps how many pending entries one agent run sees.
# X entries are excluded unless an operator supplies --include-x. That flag is
# explicit triage approval while watched-account discovery is on manual
# probation; the recurring community service never supplies it. An approved
# run is X-only and requires X_REVIEW_AGENT_BIN, so hydrated post text never
# falls through to the general community review provider.
# Assessed entries leave Pending, so successive runs work through the rest.
#
# Candidate bodies are text strangers wrote, so three things happen around the
# agent rather than inside it (docs/design/agent-sandbox.md):
#
#   this script fetches each body, so the agent needs no network and, for X,
#   never holds the bearer token
#   the agent runs as its own account, with none of .env in its environment
#   whatever it wrote is checked before the run counts as a success, and the
#   first captures it asked for happen here, only for sources this run
#   actually registered and only after the registry passed check_registry.py
#
# Exit codes:
#   0  assessment completed, the selected agent is unset, or no candidates
#   1  agent run failed, or the run wrote outside its remit (entries stay
#      pending; next tick retries)
#
# An unset selected agent is supported: candidates wait in DISCOVERY.md.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/agent-run-common.sh"
agent_load_env

MAX=15
INCLUDE_X=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max) MAX="$2"; shift 2 ;;
    --include-x) INCLUDE_X=true; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

INTAKE="$ROOT/DISCOVERY.md"
STATE_DIR="$ROOT/.work/agent-discovery-intake"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"
CANDIDATE_LIST="$STATE_DIR/candidates.txt"
HYDRATED="$STATE_DIR/hydrated.md"
COVERAGE="$STATE_DIR/coverage.md"

mkdir -p "$STATE_DIR"

# One agent at a time over DISCOVERY.md and sources.toml: the 12-hourly timer
# and manual drain runs must never overlap. A contender waits a minute, then
# skips quietly; the next tick continues the work.
exec 9>"$STATE_DIR/intake.lock"
if ! flock -w 60 9; then
  echo "agent-discovery-intake: another run holds the lock; skipping"
  exit 0
fi

if [[ ! -f "$INTAKE" ]]; then
  echo "agent-discovery-intake: no DISCOVERY.md; nothing to do"
  exit 0
fi

# Pending entries are list lines between "## Pending" and "## Assessed",
# capped at --max per run so a large backlog is assessed in bounded chunks.
mapfile -t RAW_PENDING < <(awk '/^## Pending/{p=1; next} /^## Assessed/{p=0} p && /^- /' "$INTAKE")
ALL_PENDING=()
if [[ "$INCLUDE_X" == true ]]; then
  for candidate in "${RAW_PENDING[@]}"; do
    if [[ "$candidate" == *"https://x.com/"* ]]; then
      ALL_PENDING+=("$candidate")
    fi
  done
  AGENT_BIN="${X_REVIEW_AGENT_BIN:-}"
  AGENT_ENV_NAME="X_REVIEW_AGENT_BIN"
  PROMPT_TEMPLATE="$ROOT/scripts/agent-x-discovery-triage-prompt.md"
  ROLE=xtriage
else
  for candidate in "${RAW_PENDING[@]}"; do
    if [[ "$candidate" != *"https://x.com/"* ]]; then
      ALL_PENDING+=("$candidate")
    fi
  done
  AGENT_BIN="${REVIEW_AGENT_BIN:-}"
  AGENT_ENV_NAME="REVIEW_AGENT_BIN"
  PROMPT_TEMPLATE="$ROOT/scripts/agent-discovery-intake-prompt.md"
  ROLE=intake
fi
PENDING=("${ALL_PENDING[@]:0:$MAX}")

if [[ ${#ALL_PENDING[@]} -eq 0 ]]; then
  if [[ "$INCLUDE_X" == true ]]; then
    echo "agent-discovery-intake: no pending X candidates; nothing to do"
  elif [[ ${#RAW_PENDING[@]} -gt 0 ]]; then
    echo "agent-discovery-intake: only X candidates are pending; they require --include-x"
  else
    echo "agent-discovery-intake: no pending candidates; nothing to do"
  fi
  exit 0
fi

if [[ -z "$AGENT_BIN" ]]; then
  echo "agent-discovery-intake: ${#ALL_PENDING[@]} pending candidate(s), but $AGENT_ENV_NAME is unset;"
  echo "  candidates wait in DISCOVERY.md for human triage (see .env.example)"
  exit 0
fi

echo "agent-discovery-intake: assessing ${#PENDING[@]} of ${#ALL_PENDING[@]} pending candidate(s)"

agent_begin "$ROLE"

printf -- '%s\n' "${PENDING[@]}" > "$CANDIDATE_LIST"

# Fetch every body here, as the operator account, one request per candidate.
# The X lane needs the bearer token to read a post; exporting it for this step
# keeps it in this process, because run-agent.sh builds the agent's
# environment from nothing rather than inheriting ours.
hydrate_args=()
if [[ "$INCLUDE_X" == true ]]; then
  hydrate_args+=(--include-x)
  export X_API_BEARER_TOKEN
fi
echo "agent-discovery-intake: hydrating ${#PENDING[@]} candidate body(ies)"
.venv/bin/python scripts/hydrate_candidates.py --nonce "$AGENT_NONCE" \
  "${hydrate_args[@]}" < "$CANDIDATE_LIST" > "$HYDRATED"

# What the record already holds, so "already represented by <id>" is a lookup
# rather than recall over sources.toml. Built here, as the operator account,
# from the registry and the assessed verdicts; the agent is handed the text.
#
# A run without it is worse than no run: an agent that cannot see what is
# already covered registers duplicates of it, and duplicates in the registry
# are far more work to undo than a skipped tick.
if ! .venv/bin/python scripts/build_coverage_index.py --out "$COVERAGE"; then
  echo "agent-discovery-intake: could not build the coverage index; not" \
       "starting the agent. Entries stay pending." >&2
  exit 1
fi

agent_render "$PROMPT_TEMPLATE" "$PROMPT_RENDERED" \
  --untrusted "CANDIDATES=$CANDIDATE_LIST" \
  --untrusted "COVERAGE=$COVERAGE" \
  --file "HYDRATED=$HYDRATED" \
  --file "REGISTRY_HOSTS=scripts/registry_hosts.toml" \
  --value "CAPTURE_REQUESTS=.work/capture-requests.txt"

rc=0
agent_invoke "$AGENT_BIN" "$PROMPT_RENDERED" || rc=$?

grc=0
agent_finish "$ROLE" || grc=$?

if [[ $grc -ne 0 ]]; then
  echo "agent-discovery-intake: the run was rejected by the guard; no first" \
       "capture was made and the entries stay as the agent left them" >&2
  exit 1
fi
if [[ $rc -ne 0 ]]; then
  echo "agent-discovery-intake: agent run failed; entries stay pending" >&2
  exit 1
fi

agent_run_captures
echo "agent-discovery-intake: run complete"
exit 0
