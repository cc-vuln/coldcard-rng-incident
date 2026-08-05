#!/usr/bin/env bash
# Invoke the review agent over newly captured diffs.
#
# The capture runner (archive-poll.timer) owns archive writes. This script
# owns only the additive interpretation layer: it finds differences that lack
# a completed classification and asks the agent to classify a bounded batch in
# revision-reviews.toml.
#
# Exit codes:
#   0  review completed, or there was nothing new to review
#   1  agent run failed (the unclassified batch is retried next tick)

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

# The review agent is any non-interactive CLI that takes a rendered prompt as
# `<bin> -p "<prompt>"`, reads and edits the working tree, and exits non-zero
# on failure. Which agent that is stays out of the repository: set
# REVIEW_AGENT_BIN in .env (see .env.example).
STATE_DIR="$ROOT/.work/agent-review"
PROMPT_TEMPLATE="$ROOT/scripts/agent-review-prompt.md"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"
PACKETS_RENDERED="$STATE_DIR/evidence-packets.md"
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

.venv/bin/python scripts/render_review_packets.py "${CANDIDATES[@]}" \
  > "$PACKETS_RENDERED"

# Render without passing diff text through shell or awk escaping.
.venv/bin/python scripts/render_agent_review_prompt.py \
  --template "$PROMPT_TEMPLATE" --packets "$PACKETS_RENDERED" \
  "${CANDIDATES[@]}" > "$PROMPT_RENDERED"

if "$REVIEW_AGENT_BIN" -p "$(cat "$PROMPT_RENDERED")"; then
  echo "agent-review: batch complete; any remaining backlog will run next tick"
  exit 0
else
  echo "agent-review: agent run failed; unclassified diffs remain queued" >&2
  exit 1
fi
