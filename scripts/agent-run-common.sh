# Shared plumbing for the unattended agent drivers. Sourced, never executed.
#
# Three drivers run an agent over text strangers wrote: agent-review.sh,
# agent-discovery-intake.sh and claim-sweep.sh. They differ in what they ask
# for and agree on everything about how the run is contained, so the
# containment lives here in one copy. Three copies of a security control drift,
# and the copy that drifts is the one that stops checking.
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
  return $rc
}

agent_run_captures() {
  local approved="$AGENT_RUN_DIR/approved-captures.txt"
  [[ -s "$approved" ]] || return 0
  local id rc
  while read -r id; do
    [[ -n "$id" ]] || continue
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
