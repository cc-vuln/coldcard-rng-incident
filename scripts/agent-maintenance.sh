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
# Pass ONE executable and its arguments. A compound command must go in a
# script; see the guards below for why that is not a style preference.
#
# Exits 0 if the command did, 2 on a caller mistake caught before the window
# opens, 1 on wrapper failure (including a poll that would not finish in
# time), otherwise the command's exit code.

set -Eeuo pipefail

TIMERS=(archive-poll.timer archive-review.timer)
POLL_SERVICE=archive-poll.service
WAIT_BUDGET_S=180

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <command...>" >&2
  exit 1
fi

# Everything below runs BEFORE the timers are touched. A caller mistake must
# not cost a quiet window, and must never leave the timers stopped.
#
# The failure this catches, observed 6 Aug 2026: `just` word-splits its *ARGS,
# so `just agent-maintenance bash -c 'drop.py && test.py'` arrives here as six
# separate arguments. bash -c then takes only "drop.py" as the command string
# and binds the rest to $0 and $@, so it runs one word, exits 0, and the
# wrapper reports success and closes the window having changed nothing. A
# safety wrapper whose most likely misuse is a silent no-op is worse than no
# wrapper, because the operator believes the work happened.

die_usage() {
  echo "agent-maintenance: $1" >&2
  echo "agent-maintenance: put the compound command in a script and pass its path:" >&2
  echo "    just agent-maintenance ./scripts/my-maintenance.sh [args...]" >&2
  echo "agent-maintenance: timers untouched." >&2
  exit 2
}

# A shell operator that arrived as its own argument was meant to be parsed by a
# shell that never saw it.
for arg in "$@"; do
  case "$arg" in
    '&&'|'||'|';'|'|'|'>'|'>>'|'<')
      die_usage "argument $arg reached this script literally, so the command was split before it got here"
      ;;
  esac
done

# `<shell> -c` with more than one argument after the -c string: the extras
# become $0 and positional parameters rather than part of the command, which
# is legal, almost never intended, and silent.
case "${1##*/}" in
  bash|sh|zsh|dash|ksh)
    for i in "${@:2}"; do
      if [[ "$i" == "-c" ]]; then
        # Arguments after the -c string itself.
        if (( $# > 3 )); then
          die_usage "$1 -c was given $(($# - 3)) argument(s) after the command string; they become \$0 and \$@, not part of the command"
        fi
        break
      fi
    done
    ;;
esac

if ! command -v "$1" >/dev/null 2>&1; then
  die_usage "$1 is not an executable on PATH"
fi

restart_timers() {
  sudo -n systemctl start "${TIMERS[@]}" \
    && echo "agent-maintenance: timers restarted" \
    || echo "agent-maintenance: WARNING - timer restart failed; run: sudo systemctl start ${TIMERS[*]}" >&2
}
trap restart_timers EXIT

echo "agent-maintenance: pausing ${TIMERS[*]}"
sudo -n systemctl stop "${TIMERS[@]}"

# is-active cannot guard this service: archive-poll.service is Type=oneshot,
# so it sits in ActiveState "activating" for its whole run, and is-active
# treats "activating" as not active (exit 3). The window opened over a live
# poll once because of this (4 Aug 2026). Ask for the state instead.
poll_in_flight() {
  local state
  state=$(systemctl show -p ActiveState --value "$POLL_SERVICE" 2>/dev/null) || return 1
  [[ "$state" == "active" || "$state" == "activating" || "$state" == "reloading" ]]
}

deadline=$((SECONDS + WAIT_BUDGET_S))
if poll_in_flight; then
  echo "agent-maintenance: poll in flight, waiting up to ${WAIT_BUDGET_S}s for it to finish"
  while poll_in_flight; do
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
