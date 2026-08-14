#!/usr/bin/env bash
# Invoke the intake agent over pending discovery candidates.
#
# Community discovery and the X watcher queue candidates in the structured
# discovery store.
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
# An unset selected agent is supported: candidates remain pending.

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

if ! [[ "$MAX" =~ ^[1-9][0-9]*$ && "$BATCHES" =~ ^[1-9][0-9]*$ ]] || \
   (( MAX > 15 )); then
  echo "--max must be 1-15 and --batches must be a positive integer" >&2
  exit 2
fi

if [[ ! -f "$ROOT/discovery/migration-v1/manifest.json" ]]; then
  echo "agent-discovery-intake: structured discovery migration is not active" >&2
  exit 1
fi

if (( BATCHES > 1 )); then
  pending_community_count() {
    .venv/bin/python scripts/discovery_store.py --root "$ROOT" count \
      --state pending --lane community
  }

  for ((batch = 1; batch <= BATCHES; batch++)); do
    if ! before="$(pending_community_count)"; then
      echo "agent-discovery-intake: cannot count the structured queue" >&2
      exit 1
    fi
    if (( before == 0 )); then
      echo "agent-discovery-intake: backlog drained after $((batch - 1)) batch(es)"
      exit 0
    fi
    echo "agent-discovery-intake: batch ${batch}/${BATCHES}; ${before} pending"
    "$0" --max "$MAX" --batches 1
    if ! after="$(pending_community_count)"; then
      echo "agent-discovery-intake: cannot recount the structured queue" >&2
      exit 1
    fi
    if (( after >= before )); then
      echo "agent-discovery-intake: no queue progress; stopping bounded drain"
      exit 0
    fi
  done
  exit 0
fi

STATE_DIR="$ROOT/.work/agent-discovery-intake"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"
CANDIDATE_LIST="$STATE_DIR/candidates.jsonl"
HYDRATED="$STATE_DIR/hydrated.jsonl"
PACKET_JSON="$STATE_DIR/intake-packet.json"
PACKET_MARKDOWN="$STATE_DIR/intake-packet.md"

mkdir -p "$STATE_DIR"

# Operator-dropped candidate URLs join the queue before anything counts it.
# Runs before this driver takes the lock: the script takes it itself.
.venv/bin/python scripts/queue_candidates.py || true

# Each structured-store command takes the short discovery lock itself. The
# batch carries candidate heads, so a concurrent update makes the guarded
# apply fail atomically instead of holding this lock across network hydration
# and an agent run. agent_begin below separately serializes all agent roles.

AGENT_BIN="${REVIEW_AGENT_BIN:-}"
AGENT_ENV_NAME="REVIEW_AGENT_BIN"
PROMPT_TEMPLATE="$ROOT/scripts/agent-discovery-intake-prompt.md"
ROLE=intake

if ! ALL_PENDING_COUNT="$(.venv/bin/python scripts/discovery_store.py \
    --root "$ROOT" count --state pending --lane community)"; then
  echo "agent-discovery-intake: cannot count the structured queue" >&2
  exit 1
fi
if (( ALL_PENDING_COUNT == 0 )); then
  if ! X_PENDING_COUNT="$(.venv/bin/python scripts/discovery_store.py \
      --root "$ROOT" count --state pending --lane x)"; then
    echo "agent-discovery-intake: cannot count the X queue" >&2
    exit 1
  fi
  if (( X_PENDING_COUNT > 0 )); then
    echo "agent-discovery-intake: only X candidates are pending; they are the X lane's (just x-intake)"
  else
    echo "agent-discovery-intake: no pending candidates; nothing to do"
  fi
  exit 0
fi

if [[ -z "$AGENT_BIN" ]]; then
  echo "agent-discovery-intake: ${ALL_PENDING_COUNT} pending candidate(s), but $AGENT_ENV_NAME is unset;"
  echo "  candidates remain pending for human triage (see .env.example)"
  exit 0
fi

if ! .venv/bin/python scripts/discovery_store.py --root "$ROOT" list \
    --state pending --lane community --format intake-json --limit "$MAX" \
    > "$CANDIDATE_LIST"; then
  echo "agent-discovery-intake: cannot list the structured queue" >&2
  exit 1
fi
mapfile -t PENDING < "$CANDIDATE_LIST"
if [[ ${#PENDING[@]} -eq 0 ]]; then
  echo "agent-discovery-intake: structured queue count/list disagreed" >&2
  exit 1
fi

echo "agent-discovery-intake: assessing ${#PENDING[@]} of ${ALL_PENDING_COUNT} pending candidate(s)"

agent_begin "$ROLE"

# Fetch every body here, as the operator account, one request per candidate.
echo "agent-discovery-intake: hydrating ${#PENDING[@]} candidate body(ies)"
.venv/bin/python scripts/hydrate_candidates.py --nonce "$AGENT_NONCE" \
  < "$CANDIDATE_LIST" > "$HYDRATED"

# Build one bounded evidence packet. It joins each structured candidate id,
# head and URL to its hydrated body, resolves exact registry duplicates, and includes every
# non-zero saturation row while counting the zero-history rows it omits.
if ! .venv/bin/python scripts/build_intake_packet.py \
  --root "$ROOT" --lane community \
  --candidates "$CANDIDATE_LIST" --hydrated "$HYDRATED" \
  --json-out "$PACKET_JSON" --markdown-out "$PACKET_MARKDOWN"; then
  echo "agent-discovery-intake: could not build a bounded intake packet; not" \
       "starting the agent. Entries stay pending." >&2
  exit 1
fi
cp "$PACKET_JSON" "$AGENT_RUN_DIR/intake-packet.json"

agent_render "$PROMPT_TEMPLATE" "$PROMPT_RENDERED" \
  --untrusted "INTAKE_PACKET=$PACKET_MARKDOWN" \
  --file "REGISTRY_HOSTS=scripts/registry_hosts.toml" \
  --value "CAPTURE_REQUESTS=.work/capture-requests.txt" \
  --value "INTAKE_VERDICTS=.work/intake-verdicts.jsonl"

rc=0
agent_invoke "$AGENT_BIN" "$PROMPT_RENDERED" || rc=$?

grc=0
agent_finish "$ROLE" || grc=$?

if [[ $grc -ne 0 ]]; then
  echo "agent-discovery-intake: the run was rejected by the guard; no first" \
       "capture was made and the entries stay as the agent left them" >&2
  exit 1
fi
# The guard has approved the legacy registry edit. Refresh the canonical
# shards before captures or any later reader can observe the new registration.
if ! cmp -s "$AGENT_RUN_DIR/before/sources.toml" "$ROOT/sources.toml"; then
  if ! .venv/bin/python scripts/migrate_registry.py --refresh; then
    echo "agent-discovery-intake: registry edit passed the guard but the" \
         "canonical registry refresh failed; no first capture was made" >&2
    exit 1
  fi
fi

# A provider can return non-zero after producing guard-approved edits. Keep
# registry projections coherent, but do not apply verdicts or make captures.
if [[ $rc -ne 0 ]]; then
  echo "agent-discovery-intake: agent run failed; entries stay pending" >&2
  exit 1
fi

# The applier reacquires discovery.lock, checks every packet event head and
# commits the guarded verdict rows as one immutable transaction.
if ! .venv/bin/python scripts/apply_intake_verdicts.py \
  --root "$ROOT" --run-dir "$AGENT_RUN_DIR" \
  --operation-id "$AGENT_RUN_ID"; then
  echo "agent-discovery-intake: guarded verdicts could not be applied; no" \
       "first capture was made" >&2
  exit 1
fi

agent_run_captures

# Host proposals the agent queued in .work/host-proposals.txt are vetted and
# admitted driver-side. vet_host.py exits 0 on every outcome short of usage,
# and a vetting failure must never fail the intake run.
.venv/bin/python scripts/vet_host.py --yes || true

agent_mark_workflow_complete

echo "agent-discovery-intake: run complete"
exit 0
