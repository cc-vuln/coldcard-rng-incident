#!/usr/bin/env bash
# Invoke the intake agent over pending discovery candidates.
#
# discover-community.timer owns finding candidates (discover_stackernews.py
# and discover_reddit.py, queued in DISCOVERY.md). This script owns the
# assessment layer: when DISCOVERY.md has pending entries it asks the agent
# to judge each one, register the relevant threads in sources.toml,
# first-capture them, and record every verdict in DISCOVERY.md. The same
# division of labour as archive-poll / archive-review.
#
# A backlog (for example the first reddit enumeration) is assessed in
# bounded chunks: --max N caps how many pending entries one agent run sees.
# Assessed entries leave Pending, so successive runs work through the rest.
#
# Exit codes:
#   0  assessment completed, REVIEW_AGENT_BIN unset (agent disabled), or no
#      pending candidates
#   1  agent run failed (entries stay pending; next tick retries)
#
# An unset REVIEW_AGENT_BIN is a supported configuration, matching
# agent-review.sh: candidates accumulate in DISCOVERY.md for human triage.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

MAX=15
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max) MAX="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

INTAKE="$ROOT/DISCOVERY.md"
PROMPT_TEMPLATE="$ROOT/scripts/agent-discovery-intake-prompt.md"
STATE_DIR="$ROOT/.work/agent-discovery-intake"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"

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
mapfile -t ALL_PENDING < <(awk '/^## Pending/{p=1; next} /^## Assessed/{p=0} p && /^- /' "$INTAKE")
PENDING=("${ALL_PENDING[@]:0:$MAX}")

if [[ ${#ALL_PENDING[@]} -eq 0 ]]; then
  echo "agent-discovery-intake: no pending candidates; nothing to do"
  exit 0
fi

if [[ -z "${REVIEW_AGENT_BIN:-}" ]]; then
  echo "agent-discovery-intake: ${#ALL_PENDING[@]} pending candidate(s), but REVIEW_AGENT_BIN is unset;"
  echo "  candidates wait in DISCOVERY.md for human triage (see .env.example)"
  exit 0
fi

echo "agent-discovery-intake: assessing ${#PENDING[@]} of ${#ALL_PENDING[@]} pending candidate(s)"

awk -v candidates="$(printf -- '%s\n' "${PENDING[@]}")" '
  $0 ~ /{CANDIDATES}/ { printf "%s", candidates; next } { print }
' "$PROMPT_TEMPLATE" > "$PROMPT_RENDERED"

if "$REVIEW_AGENT_BIN" -p "$(cat "$PROMPT_RENDERED")"; then
  echo "agent-discovery-intake: run complete"
  exit 0
else
  echo "agent-discovery-intake: agent run failed; entries stay pending" >&2
  exit 1
fi
