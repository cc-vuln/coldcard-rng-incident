#!/usr/bin/env bash
# Invoke the intake agent over pending discovery candidates.
#
# Community discovery and the X watcher queue candidates in DISCOVERY.md.
# This script owns the community assessment layer: when pending entries exist
# it asks the agent to judge each one and record every verdict, registering
# accepted threads. X candidates are assessed by their own lane,
# scripts/agent-x-intake.sh, and are excluded here so the two lanes stay
# separately auditable. The read-only X triage prompt and the --include-x
# admission flag were retired on 8 Aug 2026, when X promotion was automated
# under the registering xintake role.
#
# A backlog (for example the first reddit enumeration) is assessed in
# bounded chunks: --max N caps how many pending entries one agent run sees,
# and --batches N lets one scheduled invocation run several separately
# rendered and guarded chunks. Assessed entries leave Pending, so successive
# batches work through the rest.
#
# Candidate bodies are text strangers wrote, so three things happen around the
# agent rather than inside it (docs/design/agent-sandbox.md):
#
#   this script fetches each body, so the agent needs no network
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
BATCHES=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max) MAX="$2"; shift 2 ;;
    --batches) BATCHES="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! [[ "$MAX" =~ ^[1-9][0-9]*$ && "$BATCHES" =~ ^[1-9][0-9]*$ ]]; then
  echo "--max and --batches must be positive integers" >&2
  exit 2
fi

if (( BATCHES > 1 )); then
  pending_community_count() {
    awk '/^## Pending/{p=1; next} /^## Assessed/{p=0} p && /^- / && !/https:\/\/x\.com\//{n++} END{print n+0}' \
      "$ROOT/DISCOVERY.md" 2>/dev/null || printf '0\n'
  }

  for ((batch = 1; batch <= BATCHES; batch++)); do
    before="$(pending_community_count)"
    if (( before == 0 )); then
      echo "agent-discovery-intake: backlog drained after $((batch - 1)) batch(es)"
      exit 0
    fi
    echo "agent-discovery-intake: batch ${batch}/${BATCHES}; ${before} pending"
    "$0" --max "$MAX" --batches 1
    after="$(pending_community_count)"
    if (( after >= before )); then
      echo "agent-discovery-intake: no queue progress; stopping bounded drain"
      exit 0
    fi
  done
  exit 0
fi

INTAKE="$ROOT/DISCOVERY.md"
STATE_DIR="$ROOT/.work/agent-discovery-intake"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"
CANDIDATE_LIST="$STATE_DIR/candidates.txt"
HYDRATED="$STATE_DIR/hydrated.md"
COVERAGE="$STATE_DIR/coverage.md"

mkdir -p "$STATE_DIR"

# Operator-dropped candidate URLs join the queue before anything counts it.
# Runs before this driver takes the lock: the script takes it itself.
.venv/bin/python scripts/queue_candidates.py || true

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
# X candidates are the X lane's (scripts/agent-x-intake.sh) and are excluded
# here, whatever else is pending.
mapfile -t RAW_PENDING < <(awk '/^## Pending/{p=1; next} /^## Assessed/{p=0} p && /^- /' "$INTAKE")
ALL_PENDING=()
for candidate in "${RAW_PENDING[@]}"; do
  if [[ "$candidate" != *"https://x.com/"* ]]; then
    ALL_PENDING+=("$candidate")
  fi
done
AGENT_BIN="${REVIEW_AGENT_BIN:-}"
AGENT_ENV_NAME="REVIEW_AGENT_BIN"
PROMPT_TEMPLATE="$ROOT/scripts/agent-discovery-intake-prompt.md"
ROLE=intake
PENDING=("${ALL_PENDING[@]:0:$MAX}")

if [[ ${#ALL_PENDING[@]} -eq 0 ]]; then
  if [[ ${#RAW_PENDING[@]} -gt 0 ]]; then
    echo "agent-discovery-intake: only X candidates are pending; they are the X lane's (just x-intake)"
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
echo "agent-discovery-intake: hydrating ${#PENDING[@]} candidate body(ies)"
.venv/bin/python scripts/hydrate_candidates.py --nonce "$AGENT_NONCE" \
  < "$CANDIDATE_LIST" > "$HYDRATED"

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

# Host proposals the agent queued in .work/host-proposals.txt are vetted and
# admitted driver-side. vet_host.py exits 0 on every outcome short of usage,
# and a vetting failure must never fail the intake run.
.venv/bin/python scripts/vet_host.py --yes || true

echo "agent-discovery-intake: run complete"
exit 0
