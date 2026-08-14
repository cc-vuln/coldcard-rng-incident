#!/usr/bin/env bash
# Invoke the X intake agent over pending X discovery candidates.
#
# X discovery queues permalinks in the structured discovery store. This
# script owns their assessment layer, separately from the
# community lane so the two stay independently auditable: when pending X
# entries exist it asks the registering xintake agent to judge each one,
# register the ones that belong as [[x_post]] blocks, and record every
# verdict. The community driver (agent-discovery-intake.sh) excludes X
# candidates; this one takes only them.
#
# A backlog is assessed in bounded chunks: --max N caps how many pending
# entries one agent run sees, and --batches N lets a scheduled invocation run
# several separately rendered, invoked and guarded chunks. Assessed entries
# leave Pending, so each batch works through the next part of the queue.
#
# Candidate bodies are text strangers wrote, so three things happen around the
# agent rather than inside it (docs/design/agent-sandbox.md):
#
#   this script reads each post through the capture browser, as the operator
#   account, so the agent never reaches a signed-in session and needs no
#   network at all
#   the agent runs as its own account, with none of .env in its environment
#   whatever it wrote is checked before the run counts as a success, and the
#   first captures it asked for happen here, only for posts this run actually
#   registered and only after the registry passed check_registry.py
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
  echo "agent-x-intake: structured discovery migration is not active" >&2
  exit 1
fi

# Keep every agent prompt and guard decision independent. The outer process
# holds no discovery lock; each child acquires and releases it normally. Stop if
# a successful child made no queue progress (no configured agent, hydration
# could not supply evidence, or nothing remains) rather than spinning through
# the requested batch count.
if (( BATCHES > 1 )); then
  pending_x_count() {
    .venv/bin/python scripts/discovery_store.py --root "$ROOT" count \
      --state pending --lane x
  }

  for ((batch = 1; batch <= BATCHES; batch++)); do
    if ! before="$(pending_x_count)"; then
      echo "agent-x-intake: cannot count the structured queue" >&2
      exit 1
    fi
    if (( before == 0 )); then
      echo "agent-x-intake: backlog drained after $((batch - 1)) batch(es)"
      exit 0
    fi
    echo "agent-x-intake: batch ${batch}/${BATCHES}; ${before} pending"
    "$0" --max "$MAX" --batches 1
    if ! after="$(pending_x_count)"; then
      echo "agent-x-intake: cannot recount the structured queue" >&2
      exit 1
    fi
    if (( after >= before )); then
      echo "agent-x-intake: no queue progress; stopping bounded drain"
      exit 0
    fi
  done
  exit 0
fi

STATE_DIR="$ROOT/.work/agent-x-intake"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"
CANDIDATE_LIST="$STATE_DIR/candidates.jsonl"
HYDRATED="$STATE_DIR/hydrated.jsonl"
PACKET_JSON="$STATE_DIR/intake-packet.json"
PACKET_MARKDOWN="$STATE_DIR/intake-packet.md"
PROMPT_TEMPLATE="$ROOT/scripts/agent-x-intake-prompt.md"
ROLE=xintake

mkdir -p "$STATE_DIR"

# Each structured-store command takes the short discovery lock itself. The
# batch carries candidate heads, so a concurrent update makes the guarded
# apply fail atomically instead of holding this lock across browser hydration
# and an agent run. agent_begin below separately serializes all agent roles.
# Operator-dropped candidate URLs join the queue before anything counts it.
# Runs before this driver takes the lock: the script takes it itself.
.venv/bin/python scripts/queue_candidates.py || true

if ! ALL_PENDING_COUNT="$(.venv/bin/python scripts/discovery_store.py \
    --root "$ROOT" count --state pending --lane x)"; then
  echo "agent-x-intake: cannot count the structured queue" >&2
  exit 1
fi
if (( ALL_PENDING_COUNT == 0 )); then
  echo "agent-x-intake: no pending X candidates; nothing to do"
  exit 0
fi

# An active X browser cooldown means no candidate body can hydrate, so an
# agent run here would spend itself deciding nothing (observed 12 Aug 2026:
# a full run over 15 unhydratable candidates). The discovery lane owns the
# cooldown and the candidates wait; when it clears, the next tick assesses.
if .venv/bin/python -c "
import sys
sys.path.insert(0, 'scripts')
import x_browser
sys.exit(0 if x_browser.read_cooldown() is not None else 1)
"; then
  echo "agent-x-intake: X browser cooldown active; ${ALL_PENDING_COUNT} candidate(s) remain pending"
  exit 0
fi

AGENT_BIN="${REVIEW_AGENT_BIN:-}"
if [[ -z "$AGENT_BIN" ]]; then
  echo "agent-x-intake: ${ALL_PENDING_COUNT} pending X candidate(s), but REVIEW_AGENT_BIN is unset;"
  echo "  candidates remain pending for human triage (see .env.example)"
  exit 0
fi

if ! .venv/bin/python scripts/discovery_store.py --root "$ROOT" list \
    --state pending --lane x --format intake-json --limit "$MAX" \
    > "$CANDIDATE_LIST"; then
  echo "agent-x-intake: cannot list the structured queue" >&2
  exit 1
fi
mapfile -t PENDING < "$CANDIDATE_LIST"
if [[ ${#PENDING[@]} -eq 0 ]]; then
  echo "agent-x-intake: structured queue count/list disagreed" >&2
  exit 1
fi

echo "agent-x-intake: assessing ${#PENDING[@]} of ${ALL_PENDING_COUNT} pending X candidate(s)"

agent_begin "$ROLE"

# Read every post here, as the operator account, one navigation per
# candidate. run-agent.sh builds the agent's environment from nothing rather
# than inheriting ours, so the capture browser session never reaches it.
echo "agent-x-intake: hydrating ${#PENDING[@]} candidate body(ies)"
.venv/bin/python scripts/hydrate_candidates.py --nonce "$AGENT_NONCE" \
  --include-x < "$CANDIDATE_LIST" > "$HYDRATED"

# Build one bounded evidence packet. It joins each structured candidate id,
# head and URL to its hydrated body, resolves exact registry duplicates, and includes every
# non-zero saturation row while counting the zero-history rows it omits.
if ! .venv/bin/python scripts/build_intake_packet.py \
  --root "$ROOT" --lane x \
  --candidates "$CANDIDATE_LIST" --hydrated "$HYDRATED" \
  --json-out "$PACKET_JSON" --markdown-out "$PACKET_MARKDOWN"; then
  echo "agent-x-intake: could not build a bounded intake packet; not" \
       "starting the agent. Entries stay pending." >&2
  exit 1
fi
cp "$PACKET_JSON" "$AGENT_RUN_DIR/intake-packet.json"

agent_render "$PROMPT_TEMPLATE" "$PROMPT_RENDERED" \
  --untrusted "INTAKE_PACKET=$PACKET_MARKDOWN" \
  --value "CAPTURE_REQUESTS=.work/capture-requests.txt" \
  --value "INTAKE_VERDICTS=.work/intake-verdicts.jsonl"

rc=0
agent_invoke "$AGENT_BIN" "$PROMPT_RENDERED" || rc=$?

grc=0
agent_finish "$ROLE" || grc=$?

if [[ $grc -ne 0 ]]; then
  echo "agent-x-intake: the run was rejected by the guard; no first" \
       "capture was made and the entries stay as the agent left them" >&2
  exit 1
fi
# The guard has approved the legacy registry edit. Refresh the canonical
# shards before captures or any later reader can observe the new registration.
if ! cmp -s "$AGENT_RUN_DIR/before/sources.toml" "$ROOT/sources.toml"; then
  if ! .venv/bin/python scripts/migrate_registry.py --refresh; then
    echo "agent-x-intake: registry edit passed the guard but the canonical" \
         "registry refresh failed; no first capture was made" >&2
    exit 1
  fi
fi

# A provider can return non-zero after producing guard-approved edits. Keep
# registry projections coherent, but do not apply verdicts or make captures.
if [[ $rc -ne 0 ]]; then
  echo "agent-x-intake: agent run failed; entries stay pending" >&2
  exit 1
fi

# The applier reacquires discovery.lock, checks every packet event head and
# commits the guarded verdict rows as one immutable transaction.
if ! .venv/bin/python scripts/apply_intake_verdicts.py \
  --root "$ROOT" --run-dir "$AGENT_RUN_DIR" \
  --operation-id "$AGENT_RUN_ID"; then
  echo "agent-x-intake: guarded verdicts could not be applied; no first" \
       "capture was made" >&2
  exit 1
fi

agent_run_captures

# Host proposals the agent queued in .work/host-proposals.txt are vetted and
# admitted driver-side. vet_host.py exits 0 on every outcome short of usage,
# and a vetting failure must never fail the intake run.
.venv/bin/python scripts/vet_host.py --yes || true

agent_mark_workflow_complete

echo "agent-x-intake: run complete"
exit 0
