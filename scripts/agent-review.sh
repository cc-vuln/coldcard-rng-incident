#!/usr/bin/env bash
# Invoke the review agent over newly captured diffs.
#
# The capture runner (archive-poll.timer) owns archive writes. This script
# owns only the additive interpretation layer: it finds diff files newer than
# the last successful review and asks the agent to classify them in
# revision-reviews.toml.
#
# Exit codes:
#   0  review completed, or there was nothing new to review
#   1  agent run failed (marker not advanced; next tick retries the backlog)

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
: "${REVIEW_AGENT_BIN:?REVIEW_AGENT_BIN not set. Point it at a review agent CLI in .env -- see .env.example.}"
STATE_DIR="$ROOT/.work/agent-review"
MARKER="$STATE_DIR/last-run"
# Stamped when this run starts. The marker only advances to it on
# success, so a capture that lands mid-run is still newer than the
# marker next time and does not slip past the review.
RUN_STAMP="$STATE_DIR/.run-started"
PROMPT_TEMPLATE="$ROOT/scripts/agent-review-prompt.md"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"

mkdir -p "$STATE_DIR" "$ROOT/.work/normalizer-proposals"
rm -f "$RUN_STAMP"
touch "$RUN_STAMP"

# Diff files newer than the last successful review. On the first run the
# marker does not exist and every diff is a candidate.
if [[ -f "$MARKER" ]]; then
  mapfile -t CANDIDATES < <(find archive/diffs -name '*.diff' -newer "$MARKER" | sort)
else
  mapfile -t CANDIDATES < <(find archive/diffs -name '*.diff' | sort)
fi

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  echo "agent-review: no new diffs since last run; nothing to do"
  exit 0
fi

echo "agent-review: ${#CANDIDATES[@]} diff(s) to review"

if [[ -f "$MARKER" ]]; then
  SINCE="$(date -u -r "$MARKER" +%Y%m%dT%H%M%SZ)"
else
  SINCE="the beginning of the archive (first run)"
fi

# Render the standing prompt with this run's scope.
export SINCE
awk -v candidates="$(printf -- '- %s\n' "${CANDIDATES[@]}")" '
  {
    gsub(/{SINCE}/, ENVIRON["SINCE"])
    if ($0 ~ /{CANDIDATES}/) { printf "%s", candidates } else { print }
  }
' "$PROMPT_TEMPLATE" > "$PROMPT_RENDERED"

if "$REVIEW_AGENT_BIN" -p "$(cat "$PROMPT_RENDERED")"; then
  mv -f "$RUN_STAMP" "$MARKER"
  chmod 600 "$MARKER"
  echo "agent-review: run complete; marker advanced to $(date -u +%Y%m%dT%H%M%SZ)"
  exit 0
else
  echo "agent-review: agent run failed; marker NOT advanced" >&2
  exit 1
fi
