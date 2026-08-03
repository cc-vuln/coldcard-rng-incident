#!/usr/bin/env bash
# Capture, and alert when something changed or the poll was incomplete.
#
# capture.py exits 10 when one or more tracked sources moved. That is the whole
# signal: an advisory being quietly edited is the event worth waking up for.
# Errors, blocked responses and skipped browser sources are also operational
# signals because silence must not be mistaken for a clean poll.
#
# Two delivery paths:
#   local   desktop notification + a line in archive/CHANGES.md (default, safe)
#   relay  Signal via an internal notification relay reached over SSH
#           (opt-in, see docs/operations.md; configure NOTIFY_SSH_HOST,
#           NOTIFY_REMOTE_BIN and NOTIFY_REMOTE_CONFIG in .env)
#
#   ./scripts/notify.sh                    capture and alert locally
#   ./scripts/notify.sh --tier 1           capture a selected tier
#   NOTIFY=relay ./scripts/notify.sh

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

NOTIFY="${NOTIFY:-local}"
STATE="$HOME/.local/state/coldcard-archive"
mkdir -p "$STATE"
LOCK="$STATE/capture.lock"

# Never let a slow run overlap the next timer tick. flock on Linux; BSD
# shlock on macOS, which does not ship flock. Fail loudly when neither
# exists: a missing lock tool must not be mistaken for lock contention.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    echo "another scheduled capture is running" >&2
    exit 21
  fi
elif [[ -x /usr/bin/shlock ]]; then
  if ! /usr/bin/shlock -f "$LOCK" -p $$; then
    echo "another scheduled capture is running" >&2
    exit 21
  fi
  trap 'rm -f "$LOCK"' EXIT
else
  echo "neither flock nor shlock is available; cannot guarantee one writer" >&2
  exit 21
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$STATE/runs"
RESULT="$RUN_DIR/$TS-$$.json"
mkdir -p "$RUN_DIR"
OUT="$(.venv/bin/python scripts/capture.py capture "$@" --result-file "$RESULT" 2>&1)" && rc=0 || rc=$?
echo "$OUT"

# Lock contention can stop capture.py before it creates the requested result.
# Keep a structured operational record anyway, outside the locked archive.
if [[ ! -s "$RESULT" ]]; then
  .venv/bin/python - "$RESULT" "$TS" "$rc" <<'PY'
import json, pathlib, sys
path, ts, rc = pathlib.Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
payload = json.dumps({
    "schema": 1, "command": "capture", "started_at": ts,
    "finished_at": ts, "outcome": "launcher-error", "exit_code": rc,
    "counts": {"launcher_error": 1},
    "events": [{"event": "launcher-error",
                "error": f"capture exited {rc} before writing a result"}],
}, indent=2, sort_keys=True) + "\n"
tmp = path.with_name(f".{path.name}.tmp")
tmp.write_text(payload)
tmp.replace(path)
PY
fi

extract_events() {
  .venv/bin/python - "$RESULT" "$1" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
mode = sys.argv[2]
for event in payload.get("events", []):
    kind = event.get("event")
    if mode == "changed" and kind in ("changed", "first"):
        if kind == "changed":
            print(f"{event['id']}  +{event.get('diff_added', 0)} -{event.get('diff_removed', 0)}")
        else:
            print(f"{event['id']}  (first capture)")
    elif mode == "failures" and kind in (
        "error", "blocked", "skipped", "config-error", "launcher-error"
    ):
        detail = event.get("error")
        if not detail and kind == "blocked":
            detail = f"{event.get('chars', '?')} chars < {event.get('min_chars', '?')} floor"
        print(f"{event.get('id', 'capture')}  {kind.upper()}  {detail or ''}")
PY
}

CHANGED="$(extract_events changed)"
FAILURES="$(extract_events failures)"
COUNT="$(echo "$CHANGED" | grep -c . || true)"
FAILURE_COUNT="$(echo "$FAILURES" | grep -c . || true)"
SUMMARY="$(printf '%s' "$CHANGED" | tr '\n' '; ' | sed 's/; $//')"
FAILURE_SUMMARY="$(printf '%s' "$FAILURES" | tr '\n' '; ' | sed 's/; $//')"

if [[ "$COUNT" -gt 0 ]]; then
  .venv/bin/python scripts/capture.py record-run "$RESULT"
fi

if [[ "$COUNT" -eq 0 && "$FAILURE_COUNT" -eq 0 && "$rc" -eq 0 ]]; then
  exit 0
fi

case "$NOTIFY" in
  local)
    if [[ "$FAILURE_COUNT" -gt 0 ]]; then
      title="COLDCARD archive: capture incomplete"
      message="$FAILURE_COUNT failure(s); $COUNT source(s) changed. $FAILURE_SUMMARY"
    else
      title="COLDCARD archive: $COUNT source(s) changed"
      message="$SUMMARY"
    fi
    if [[ "$(uname)" == "Darwin" ]]; then
      osascript - "$title" "$message" <<'APPLESCRIPT' 2>/dev/null || true
on run argv
  display notification (item 2 of argv) with title (item 1 of argv)
end run
APPLESCRIPT
    elif command -v notify-send >/dev/null 2>&1 \
        && [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
      notify-send "$title" "$message" || true
    fi
    # Headless hosts have no notification surface; the CHANGES.md line and
    # the structured result file are the durable record either way.
    ;;
  relay)
    # Requires the notification id 'coldcard-archive-change' to exist in the
    # relay's route config on NOTIFY_SSH_HOST, and a router restart after
    # adding it. See docs/operations.md "Signal alerting" before enabling.
    # Deliberately not wired by default: it edits a production notification
    # stack.
    : "${NOTIFY_SSH_HOST:?NOTIFY_SSH_HOST not set. Set it (and NOTIFY_REMOTE_BIN, NOTIFY_REMOTE_CONFIG) in .env -- see .env.example. Required for NOTIFY=relay.}"
    : "${NOTIFY_REMOTE_BIN:?NOTIFY_REMOTE_BIN not set. Set it in .env -- see .env.example. Required for NOTIFY=relay.}"
    : "${NOTIFY_REMOTE_CONFIG:?NOTIFY_REMOTE_CONFIG not set. Set it in .env -- see .env.example. Required for NOTIFY=relay.}"
    command -v jq >/dev/null 2>&1 \
      || { echo "jq is required for NOTIFY=relay" >&2; exit 2; }
    PAYLOAD="$STATE/payload-$TS.json"
    jq -n \
      --arg ts "$TS" --arg count "$COUNT" --arg detail "$CHANGED" \
      --arg failure_count "$FAILURE_COUNT" --arg failures "$FAILURES" \
      '{ts:$ts, changed_count:($count|tonumber), detail:$detail,
        failure_count:($failure_count|tonumber), failures:$failures,
        incident:"coldcard-entropy-2026"}' > "$PAYLOAD"
    ssh -t "$NOTIFY_SSH_HOST" \
      "$NOTIFY_REMOTE_BIN \
       --config $NOTIFY_REMOTE_CONFIG \
       notify-route coldcard-archive-change \
       --payload-file - --idempotency-key coldcard-$TS" < "$PAYLOAD"
    ;;
  *)
    echo "unknown NOTIFY=$NOTIFY" >&2; exit 2 ;;
esac

echo "alerted: $COUNT source(s) changed; $FAILURE_COUNT failure(s); result: $RESULT"
if [[ "$rc" -eq 0 || "$rc" -eq 10 ]]; then
  exit 0
fi
exit "$rc"
