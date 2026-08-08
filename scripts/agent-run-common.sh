# Shared plumbing for the unattended agent drivers. Sourced, never executed.
#
# Six drivers run an agent over text strangers wrote: agent-review.sh,
# agent-discovery-intake.sh, agent-x-intake.sh, claim-sweep.sh,
# agent-corrections.sh and agent-site-sync.sh. They
# differ in what they ask for and agree on everything about how the run is
# contained, so the containment lives here in one copy. Three copies of a
# security control drift, and the copy that drifts is the one that stops
# checking.
#
# The shape every driver follows:
#
#   agent_load_env            read .env WITHOUT exporting it
#   agent_begin <role>        stamp a run id and nonce, record the tree
#   ... render the prompt ...
#   agent_invoke <bin> <file> run the agent, deprivileged
#   agent_finish <role>       check what it did; refuse if it overreached
#   agent_run_captures        first-capture only what the gate approved
#
# The ordering is the point. Nothing the agent produced is acted on before
# agent_finish has passed, and agent_finish is the only thing that decides
# what agent_run_captures is allowed to fetch.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Variables run-agent.sh needs to see. Everything else in .env stays in this
# shell: the agent's environment is built from scratch in run-agent.sh, and
# the secrets have no route into it.
AGENT_PASSTHROUGH=(AGENT_SANDBOX AGENT_SANDBOX_USER AGENT_SANDBOX_GROUP)

# Where the agent asks for a first capture. It cannot write the run record
# itself (.work/agent-guard is the operator's), so it writes here and the
# driver moves the request into the record before the gate reads it.
CAPTURE_REQUESTS="$ROOT/.work/capture-requests.txt"

agent_load_env() {
  # Deliberately not `set -a`. Before this, every driver exported the whole of
  # .env into the agent process: the nostr posting key, the Cloudflare deploy
  # token, the X bearer token. The driver still needs those names to decide
  # which binary to run; the agent never does.
  if [[ -r "$ROOT/.env" ]]; then
    # shellcheck disable=SC1091
    source "$ROOT/.env"
  fi
  local name
  for name in "${AGENT_PASSTHROUGH[@]}"; do
    [[ -n "${!name:-}" ]] && export "${name?}"
  done
  return 0
}

agent_begin() {
  local role="$1"
  # One agent run at a time, across all roles. The guard attributes what a
  # run did from a whole-tree manifest, so a second agent writing mid-run
  # reads as out-of-remit contamination and gets the run rejected: on
  # 8 Aug 2026 the review lane appended classifications while the sync lane
  # ran, and the sync run was rejected for revision-reviews.toml changes it
  # never made. Polls write archive/ only and are not the hazard; agents
  # writing the shared mutable files are. Queue behind a run in progress
  # rather than colliding with it; a skipped run retries on its next tick.
  mkdir -p "$ROOT/.work"
  exec 8>"$ROOT/.work/agent-runs.lock"
  if ! flock -w 3600 8; then
    echo "agent-run: another run held the lock for an hour; skipping" >&2
    exit 0
  fi
  AGENT_ROLE="$role"
  AGENT_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  AGENT_RUN_DIR="$ROOT/.work/agent-guard/$AGENT_RUN_ID"
  # A fence marker the prompt names and untrusted text cannot predict.
  AGENT_NONCE="$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  mkdir -p "$ROOT/.work/agent-guard"
  chmod 700 "$ROOT/.work/agent-guard" 2>/dev/null || true
  mkdir -p "$AGENT_RUN_DIR"
  rm -f "$CAPTURE_REQUESTS"
  "$ROOT/.venv/bin/python" "$ROOT/scripts/agent_guard.py" before \
    --role "$role" --run-dir "$AGENT_RUN_DIR"
}

agent_invoke() {
  local bin="$1" prompt_file="$2"
  "$ROOT/scripts/run-agent.sh" "$bin" "$prompt_file"
}

agent_finish() {
  local role="$1"
  # The agent writes its capture requests where it can write. Moving the file
  # in here, as the operator account, is what makes the request evidence
  # rather than an instruction: the gate reads it, and only ids this run
  # actually registered survive.
  if [[ -f "$CAPTURE_REQUESTS" ]]; then
    cp "$CAPTURE_REQUESTS" "$AGENT_RUN_DIR/capture-requests.txt"
    rm -f "$CAPTURE_REQUESTS"
  fi
  local rc=0
  "$ROOT/.venv/bin/python" "$ROOT/scripts/agent_guard.py" after \
    --role "$role" --run-dir "$AGENT_RUN_DIR" || rc=$?

  # A rejected run keeps its edits: what an injected run tried to do is the
  # evidence, and reverting it would throw that away. But an invalid registry
  # is not evidence, it is a stopped tree. `just audit`, `just test` and the
  # scheduled publish all fail while sources.toml names a host that
  # registry_hosts.toml does not, and they stay failing until a person edits
  # the file. So anything this run added that the registry rules refuse is
  # moved out of the way, verbatim and with its reason, into quarantine/.
  #
  # Only what this run added, and only ever out of the registry: the tool
  # cannot add a host or restore a block. The rejection stands either way,
  # and its exit code is the one this function returns.
  if [[ $rc -ne 0 && -f "$AGENT_RUN_DIR/before/sources.toml" ]]; then
    "$ROOT/.venv/bin/python" "$ROOT/scripts/quarantine_registry.py" \
      --before "$AGENT_RUN_DIR/before/sources.toml" \
      --run-id "$AGENT_RUN_ID" || true
  fi
  # A rejection is the one event the unattended pipeline must not keep to
  # itself. Alerting is allowed to fail; it never breaks the driver. Tests
  # exercise the drivers in temp roots with rejected stub runs, and their
  # rejections are fixtures, not operator signal: the driver-test harness
  # sets AGENT_ALERTS=off (in the fixture .env) to keep them out of the
  # real alert stream.
  if [[ $rc -ne 0 && "${AGENT_ALERTS:-on}" != "off" ]]; then
    "$ROOT/.venv/bin/python" "$ROOT/scripts/alert.py" emit \
      --kind guard-rejection --severity urgent \
      --key "guard-rejection-$AGENT_RUN_ID" \
      --summary "guard rejected the $role run $AGENT_RUN_ID; evidence kept in $AGENT_RUN_DIR" || true
  fi
  return $rc
}

agent_run_captures() {
  local approved="$AGENT_RUN_DIR/approved-captures.txt"
  [[ -s "$approved" ]] || return 0
  local id rc note_ref
  while read -r id; do
    [[ -n "$id" ]] || continue
    # An xintake approval is a post permalink, not a source id. The agent
    # registered the [[x_post]] block itself, so ingest-x.py is called with
    # the URL alone: it resolves the registered id from the registry,
    # captures under it and leaves sources.toml untouched. A block the agent
    # thread-enabled then gets its first conversation capture through
    # capture.py's single-source path, the same hand-off ingest-x.py --thread
    # uses, so there is still only one writer of snapshots and diffs.
    if [[ "$id" == https://x.com/* ]]; then
      local xid xthread
      read -r xid xthread < <("$ROOT/.venv/bin/python" - "$id" "$ROOT/sources.toml" <<'PY'
import sys, tomllib
url, registry = sys.argv[1], sys.argv[2]
with open(registry, "rb") as fh:
    data = tomllib.load(fh)
for block in data.get("x_post", []):
    if block.get("url") == url:
        print(block.get("id", ""),
              "thread" if block.get("thread") is True else "single")
        break
PY
)
      if [[ -z "${xid:-}" ]]; then
        echo "agent-run: $id was approved but no [[x_post]] block names it; skipping" >&2
        continue
      fi
      echo "agent-run: first capture of $xid (X ingest)"
      rc=0
      just ingest-x "$id" || rc=$?
      if [[ $rc -ne 0 ]]; then
        # X posts are not polled unless thread-enabled, so nothing re-attempts
        # this by itself; record the failure where the operator surface can
        # see it.
        echo "agent-run: $xid X ingest failed (exit $rc)" >&2
        printf '%s\t%s\n' "$xid" "$AGENT_RUN_ID" >> "$ROOT/.work/capture-failures.txt"
        continue
      fi
      echo "agent-run: $xid captured"
      if [[ "${xthread:-}" == "thread" ]]; then
        rc=0
        just capture-one "$xid" || rc=$?
        case "$rc" in
          0|10) echo "agent-run: $xid conversation captured (exit $rc)" ;;
          21)   echo "agent-run: $xid conversation deferred, the poll holds the writer lock" ;;
          *)    echo "agent-run: $xid conversation first capture failed (exit $rc); the next poll will pick it up" >&2 ;;
        esac
      fi
      continue
    fi
    # A [[nostr_post]] is ingested through nak, never polled: capture.py's
    # pollable_sources() excludes the table, so `just capture-one` could only
    # report the id unresolvable. The note ref comes from the registry block
    # this run added, which the gate has already validated.
    note_ref="$("$ROOT/.venv/bin/python" - "$id" "$ROOT/sources.toml" <<'PY'
import sys, tomllib
target, registry = sys.argv[1], sys.argv[2]
with open(registry, "rb") as fh:
    data = tomllib.load(fh)
for block in data.get("nostr_post", []):
    if block.get("id") == target:
        url = str(block.get("url", ""))
        if "njump.me/" in url:
            print(url.rstrip("/").rsplit("/", 1)[-1])
        break
PY
)"
    if [[ -n "$note_ref" ]]; then
      echo "agent-run: first capture of $id (nostr ingest)"
      rc=0
      just ingest-nostr "$note_ref" || rc=$?
      if [[ $rc -eq 0 ]]; then
        echo "agent-run: $id captured"
      else
        # Nothing re-requests this by itself: nostr posts are not polled, so
        # record the failure where the operator surface can see it.
        echo "agent-run: $id nostr ingest failed (exit $rc)" >&2
        printf '%s\t%s\n' "$id" "$AGENT_RUN_ID" >> "$ROOT/.work/capture-failures.txt"
      fi
      continue
    fi
    echo "agent-run: first capture of $id"
    rc=0
    just capture-one "$id" || rc=$?
    case "$rc" in
      0|10) echo "agent-run: $id captured (exit $rc)" ;;
      21)   echo "agent-run: $id deferred, the poll holds the writer lock" ;;
      *)    echo "agent-run: $id first capture failed (exit $rc); the next poll will pick it up" >&2 ;;
    esac
  done < "$approved"
}

# Render a prompt template, substituting placeholders without letting the
# substituted text be interpreted. The intake driver used `awk -v` for this,
# and awk expands backslash escapes inside a -v value, which is a value a
# candidate line controls.
agent_render() {
  local template="$1" out="$2"
  shift 2
  "$ROOT/.venv/bin/python" "$ROOT/scripts/render_agent_prompt.py" \
    --template "$template" --nonce "$AGENT_NONCE" \
    --file "RULES=$ROOT/scripts/agent-prompt-rules.md" "$@" > "$out"
}
