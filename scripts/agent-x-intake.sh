#!/usr/bin/env bash
# Invoke the X intake agent over pending X discovery candidates.
#
# X discovery queues permalinks in DISCOVERY.md, mixed in with the community
# candidates. This script owns their assessment layer, separately from the
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

# Keep every agent prompt and guard decision independent. The outer process
# holds no intake lock; each child acquires and releases it normally. Stop if
# a successful child made no queue progress (no configured agent, hydration
# could not supply evidence, or nothing remains) rather than spinning through
# the requested batch count.
if (( BATCHES > 1 )); then
  pending_x_count() {
    awk '/^## Pending/{p=1; next} /^## Assessed/{p=0} p && /^- / && /https:\/\/x\.com\//{n++} END{print n+0}' \
      "$ROOT/DISCOVERY.md" 2>/dev/null || printf '0\n'
  }

  for ((batch = 1; batch <= BATCHES; batch++)); do
    before="$(pending_x_count)"
    if (( before == 0 )); then
      echo "agent-x-intake: backlog drained after $((batch - 1)) batch(es)"
      exit 0
    fi
    echo "agent-x-intake: batch ${batch}/${BATCHES}; ${before} pending"
    "$0" --max "$MAX" --batches 1
    after="$(pending_x_count)"
    if (( after >= before )); then
      echo "agent-x-intake: no queue progress; stopping bounded drain"
      exit 0
    fi
  done
  exit 0
fi

INTAKE="$ROOT/DISCOVERY.md"
STATE_DIR="$ROOT/.work/agent-x-intake"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"
CANDIDATE_LIST="$STATE_DIR/candidates.txt"
HYDRATED="$STATE_DIR/hydrated.md"
COVERAGE="$STATE_DIR/coverage.md"
PROMPT_TEMPLATE="$ROOT/scripts/agent-x-intake-prompt.md"
ROLE=xintake

mkdir -p "$STATE_DIR"

# One agent at a time over DISCOVERY.md and sources.toml, shared with the
# community lane: the two locks are the same file, so an X run and a
# community run never hold the queue at once. A contender waits a minute,
# then skips quietly; the next tick continues the work.
mkdir -p "$ROOT/.work/agent-discovery-intake"

# Operator-dropped candidate URLs join the queue before anything counts it.
# Runs before this driver takes the lock: the script takes it itself.
.venv/bin/python scripts/queue_candidates.py || true

exec 9>"$ROOT/.work/agent-discovery-intake/intake.lock"
if ! flock -w 60 9; then
  echo "agent-x-intake: another run holds the lock; skipping"
  exit 0
fi

if [[ ! -f "$INTAKE" ]]; then
  echo "agent-x-intake: no DISCOVERY.md; nothing to do"
  exit 0
fi

# Pending entries are list lines between "## Pending" and "## Assessed",
# capped at --max per run so a large backlog is assessed in bounded chunks.
# This lane takes the X permalinks; everything else is the community lane's.
mapfile -t RAW_PENDING < <(awk '/^## Pending/{p=1; next} /^## Assessed/{p=0} p && /^- /' "$INTAKE")
ALL_PENDING=()
for candidate in "${RAW_PENDING[@]}"; do
  if [[ "$candidate" == *"https://x.com/"* ]]; then
    ALL_PENDING+=("$candidate")
  fi
done
PENDING=("${ALL_PENDING[@]:0:$MAX}")

if [[ ${#ALL_PENDING[@]} -eq 0 ]]; then
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
  echo "agent-x-intake: X browser cooldown active; ${#ALL_PENDING[@]} candidate(s) wait in DISCOVERY.md"
  exit 0
fi

AGENT_BIN="${REVIEW_AGENT_BIN:-}"
if [[ -z "$AGENT_BIN" ]]; then
  echo "agent-x-intake: ${#ALL_PENDING[@]} pending X candidate(s), but REVIEW_AGENT_BIN is unset;"
  echo "  candidates wait in DISCOVERY.md for human triage (see .env.example)"
  exit 0
fi

echo "agent-x-intake: assessing ${#PENDING[@]} of ${#ALL_PENDING[@]} pending X candidate(s)"

agent_begin "$ROLE"

printf -- '%s\n' "${PENDING[@]}" > "$CANDIDATE_LIST"

# Read every post here, as the operator account, one navigation per
# candidate. run-agent.sh builds the agent's environment from nothing rather
# than inheriting ours, so the capture browser session never reaches it.
echo "agent-x-intake: hydrating ${#PENDING[@]} candidate body(ies)"
.venv/bin/python scripts/hydrate_candidates.py --nonce "$AGENT_NONCE" \
  --include-x < "$CANDIDATE_LIST" > "$HYDRATED"

# What the record already holds, so "already represented by <id>" is a lookup
# rather than recall over sources.toml. Built here, as the operator account,
# from the registry and the assessed verdicts; the agent is handed the text.
#
# A run without it is worse than no run: an agent that cannot see what is
# already covered registers duplicates of it, and duplicates in the registry
# are far more work to undo than a skipped tick.
if ! .venv/bin/python scripts/build_coverage_index.py --out "$COVERAGE"; then
  echo "agent-x-intake: could not build the coverage index; not" \
       "starting the agent. Entries stay pending." >&2
  exit 1
fi

agent_render "$PROMPT_TEMPLATE" "$PROMPT_RENDERED" \
  --untrusted "CANDIDATES=$CANDIDATE_LIST" \
  --untrusted "COVERAGE=$COVERAGE" \
  --file "HYDRATED=$HYDRATED" \
  --value "CAPTURE_REQUESTS=.work/capture-requests.txt"

rc=0
agent_invoke "$AGENT_BIN" "$PROMPT_RENDERED" || rc=$?

grc=0
agent_finish "$ROLE" || grc=$?

if [[ $grc -ne 0 ]]; then
  echo "agent-x-intake: the run was rejected by the guard; no first" \
       "capture was made and the entries stay as the agent left them" >&2
  exit 1
fi
if [[ $rc -ne 0 ]]; then
  echo "agent-x-intake: agent run failed; entries stay pending" >&2
  exit 1
fi

agent_run_captures

# Host proposals the agent queued in .work/host-proposals.txt are vetted and
# admitted driver-side. vet_host.py exits 0 on every outcome short of usage,
# and a vetting failure must never fail the intake run.
.venv/bin/python scripts/vet_host.py --yes || true

echo "agent-x-intake: run complete"
exit 0
