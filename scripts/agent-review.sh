#!/usr/bin/env bash
# Invoke the review agent over newly captured diffs.
#
# The capture runner (archive-poll.timer) owns archive writes. This script
# owns only the additive interpretation layer: it finds differences that lack
# a completed classification and asks the agent to classify a bounded batch in
# revision-reviews.toml.
#
# The diffs are text the sources wrote, so the agent is contained rather than
# trusted: it runs as its own account with none of .env in its environment
# (scripts/run-agent.sh), and everything it produced is checked before the run
# is called a success (scripts/agent_guard.py). See docs/design/agent-sandbox.md.
#
# Exit codes:
#   0  review completed, or there was nothing new to review
#   1  agent run failed, or the run wrote outside the review remit (the
#      unclassified batch is retried next tick; the edits are left in place)

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/agent-run-common.sh"
agent_load_env

# The review agent is any non-interactive CLI that takes a rendered prompt as
# `<bin> -p "<prompt>"`, reads and edits the working tree, and exits non-zero
# on failure. Which agent that is stays out of the repository: set
# REVIEW_AGENT_BIN in .env (see .env.example).
STATE_DIR="$ROOT/.work/agent-review"
PROMPT_TEMPLATE="$ROOT/scripts/agent-review-prompt.md"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"
PACKETS_RENDERED="$STATE_DIR/evidence-packets.md"
CANDIDATE_LIST="$STATE_DIR/candidates.md"
: "${REVIEW_BATCH_SIZE:=15}"
: "${REVIEW_BATCH_BYTES:=120000}"
if [[ ! "$REVIEW_BATCH_SIZE" =~ ^[1-9][0-9]*$ ]] || \
   [[ ! "$REVIEW_BATCH_BYTES" =~ ^[1-9][0-9]*$ ]]; then
  echo "agent-review: REVIEW_BATCH_SIZE and REVIEW_BATCH_BYTES must be positive integers" >&2
  exit 1
fi

mkdir -p "$STATE_DIR" "$ROOT/.work/normalizer-proposals"
exec 9>"$STATE_DIR/review.lock"
if ! flock -w 60 9; then
  echo "agent-review: another review run holds the lock; skipping"
  exit 0
fi

# Tested current normalizers can prove some historical diffs contain no
# tracked source-content change. Classify those mechanically before buying
# model context. Unknown and substantive differences remain for the agent.
PYTHONPATH=scripts .venv/bin/python scripts/auto_classify_noise.py --apply

mapfile -t CANDIDATES < <(
  .venv/bin/python scripts/list_unreviewed_diffs.py \
    --limit "$REVIEW_BATCH_SIZE" --max-bytes "$REVIEW_BATCH_BYTES"
)

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  echo "agent-review: no unreviewed diffs; nothing to do"
  exit 0
fi

if [[ -z "${REVIEW_AGENT_BIN:-}" ]]; then
  echo "agent-review: ${#CANDIDATES[@]} unreviewed diff(s) remain, but REVIEW_AGENT_BIN is unset"
  exit 0
fi

echo "agent-review: reviewing bounded batch of ${#CANDIDATES[@]} diff(s)"

agent_begin review

.venv/bin/python scripts/render_review_packets.py "${CANDIDATES[@]}" \
  > "$PACKETS_RENDERED"
printf -- '- %s\n' "${CANDIDATES[@]}" > "$CANDIDATE_LIST"

# The candidate list is our own archive paths; the packets are the sources'
# own text, so only the packets are fenced.
agent_render "$PROMPT_TEMPLATE" "$PROMPT_RENDERED" \
  --file "CANDIDATES=$CANDIDATE_LIST" \
  --untrusted "PACKETS=$PACKETS_RENDERED"

rc=0
agent_invoke "$REVIEW_AGENT_BIN" "$PROMPT_RENDERED" || rc=$?

# The gate runs whether the agent succeeded or not. A run that failed halfway
# has still written whatever it wrote, and that is exactly when it is worth
# looking at what that was.
grc=0
agent_finish review || grc=$?

if [[ $grc -ne 0 ]]; then
  echo "agent-review: the run was rejected by the guard; see above" >&2
  exit 1
fi
if [[ $rc -ne 0 ]]; then
  echo "agent-review: agent run failed; unclassified diffs remain queued" >&2
  exit 1
fi
echo "agent-review: batch complete; any remaining backlog will run next tick"
exit 0
