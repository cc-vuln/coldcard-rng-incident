#!/usr/bin/env bash
# Run one command with the capture and review timers paused.
#
# Agent runs that edit scripts/capture.py contend with the live poll: a
# capture taken while the file is mid-edit can record a change hash no
# shipped code reproduces, which leaves `just audit` permanently red. This
# wrapper gives such work a quiet window:
#
#   1. stop archive-poll.timer and archive-review.timer
#   2. wait for any in-flight poll to finish (never kill a capture mid-write)
#   3. run the command
#   4. restart both timers, ALWAYS, whether the command succeeded, failed,
#      or the shell was signalled
#
# Usage:
#   scripts/agent-maintenance.sh <command...>
#   just agent-maintenance <command...>
#
# Exits 0 if the command did, 1 on wrapper failure (including a poll that
# would not finish in time), otherwise the command's exit code.

set -Eeuo pipefail

TIMERS=(archive-poll.timer archive-review.timer)
POLL_SERVICE=archive-poll.service
WAIT_BUDGET_S=180

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <command...>" >&2
  exit 1
fi

restart_timers() {
  sudo -n systemctl start "${TIMERS[@]}" \
    && echo "agent-maintenance: timers restarted" \
    || echo "agent-maintenance: WARNING - timer restart failed; run: sudo systemctl start ${TIMERS[*]}" >&2
}
trap restart_timers EXIT

echo "agent-maintenance: pausing ${TIMERS[*]}"
sudo -n systemctl stop "${TIMERS[@]}"

deadline=$((SECONDS + WAIT_BUDGET_S))
if systemctl is-active --quiet "$POLL_SERVICE"; then
  echo "agent-maintenance: poll in flight, waiting up to ${WAIT_BUDGET_S}s for it to finish"
  while systemctl is-active --quiet "$POLL_SERVICE"; do
    if (( SECONDS >= deadline )); then
      echo "agent-maintenance: poll still active after ${WAIT_BUDGET_S}s; aborting (timers restarting)" >&2
      exit 1
    fi
    sleep 5
  done
fi

echo "agent-maintenance: quiet window open; running: $*"
rc=0
"$@" || rc=$?
echo "agent-maintenance: command exited $rc; closing window"
exit "$rc"
