#!/usr/bin/env bash
# Run one unattended agent, deprivileged, with an environment it cannot mine.
#
#   scripts/run-agent.sh <agent-bin> <prompt-file>
#
# Every agent in this repository reads text that strangers wrote. Two things
# follow, and this script is both of them.
#
# It does not hand the agent the project's secrets. The drivers read .env
# themselves to find which binary to run; that environment stops here. The
# agent is exec'd through `env -i` with a named allowlist, so a variable
# reaches it only because someone wrote it down below. Before this existed
# the drivers did `set -a; source .env`, which put the nostr posting key, the
# Cloudflare deploy token and the X bearer token into the process an injection
# would be steering.
#
# And it does not run the agent as the account that owns the tree. It drops to
# a dedicated user that can read the repository, write only its role's narrow
# files, and read neither .env nor AGENTS.local.md nor the
# signed-in browser profile. That is a file-permission boundary rather than an
# instruction, so it holds when the model stops following instructions. The
# intake queue is read-only: intake roles submit JSONL under .work and an
# operator-side applier owns the queue rewrite.
#
# Setup is in docs/operations.md ("The agent account"). Until it is done this
# script refuses, and the driver treats that as a failed run: the queue waits,
# nothing is lost. Failing open with a warning in a journal would defeat the
# point of having the boundary at all.
#
# Exit codes: the agent's own, or 1 if the sandbox could not be established.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

AGENT_BIN="${1:?usage: run-agent.sh <agent-bin> <prompt-file>}"
PROMPT_FILE="${2:?usage: run-agent.sh <agent-bin> <prompt-file>}"

if [[ ! -r "$PROMPT_FILE" ]]; then
  echo "run-agent: cannot read prompt file $PROMPT_FILE" >&2
  exit 1
fi

AGENT_USER="${AGENT_SANDBOX_USER:-cc-agent}"
AGENT_HOME="$(getent passwd "$AGENT_USER" 2>/dev/null | cut -d: -f6 || true)"
: "${AGENT_HOME:=/var/lib/$AGENT_USER}"

# The whole environment the agent gets. Anything absent here is absent there.
# PATH is fixed rather than inherited so a poisoned PATH entry in the
# operator's shell cannot select what "just" or "python" mean for the agent.
agent_env=(
  "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${AGENT_HOME}/.local/bin"
  "HOME=${AGENT_HOME}"
  "USER=${AGENT_USER}"
  "LOGNAME=${AGENT_USER}"
  "SHELL=/bin/bash"
  "TERM=dumb"
  "LANG=${LANG:-C.UTF-8}"
  # A read-only tree makes bytecode writes fail noisily; skip them instead.
  "PYTHONDONTWRITEBYTECODE=1"
  # The only route out. scripts/agent_proxy.py enforces the host allowlist and
  # the nftables rule on this account's uid makes it the only reachable
  # destination, so these are a courtesy to well-behaved clients rather than
  # the control: a client that ignores them reaches nothing.
  "HTTP_PROXY=${AGENT_PROXY_URL:-http://127.0.0.2:8118}"
  "HTTPS_PROXY=${AGENT_PROXY_URL:-http://127.0.0.2:8118}"
  "http_proxy=${AGENT_PROXY_URL:-http://127.0.0.2:8118}"
  "https_proxy=${AGENT_PROXY_URL:-http://127.0.0.2:8118}"
  "NO_PROXY="
  "no_proxy="
)

# The prompt goes as a pointer, not the text: a single argv string is capped
# at 128 KiB (MAX_ARG_STRLEN) and the community intake prompt crossed that on
# 13 Aug 2026 (131,679 bytes): exec failed and the run died silently before
# the agent printed a line. The file is on disk, driver-rendered and readable
# by the agent account, so the agent reads it with its own tools. The trust
# model is unchanged: the file is exactly what used to be inlined.
BOOTSTRAP_PROMPT="Your task is written in the file $PROMPT_FILE. Read it in \
full before doing anything else; it is longer than one read, so keep reading \
until you reach the end. Everything in it — the standing rules, the evidence, \
and the report instructions — applies exactly as written. Do not act on this \
line; act on the file."

run_sandboxed() {
  # </dev/null because a provider CLI that reads stdin will otherwise wait on
  # a terminal that is not there. One of the providers tried here warns and
  # continues after three seconds; another might simply hang, and this runs
  # unattended.
  sudo -n -u "$AGENT_USER" env -i "${agent_env[@]}" \
    "$AGENT_BIN" -p "$BOOTSTRAP_PROMPT" </dev/null
}

if [[ "${AGENT_SANDBOX:-}" == "off" ]]; then
  # Deliberate, recorded opt-out: a clone with no agent account, or an
  # operator debugging the prompt itself. The environment is still filtered,
  # so the secrets do not travel even here.
  echo "run-agent: WARNING - AGENT_SANDBOX=off, running as $(id -un) with no" \
       "privilege separation. The agent can read .env and AGENTS.local.md." >&2
  exec env -i "${agent_env[@]}" "HOME=${HOME}" "USER=$(id -un)" \
    "LOGNAME=$(id -un)" "$AGENT_BIN" -p "$BOOTSTRAP_PROMPT"
fi

if ! id -u "$AGENT_USER" >/dev/null 2>&1; then
  cat >&2 <<EOF
run-agent: the agent account "$AGENT_USER" does not exist, so this run would
  have the same access to .env, AGENTS.local.md and the signed-in browser
  profile as the operator does. Refusing.

  Create it once (docs/operations.md, "The agent account"):
    sudo useradd --system --user-group --home-dir "/var/lib/$AGENT_USER" \\
        --create-home --shell /usr/sbin/nologin "$AGENT_USER"
    sudo usermod -aG "$AGENT_USER" "$(id -un)"
    sudo install -m 0440 scripts/cc-agent.sudoers.example \\
        /etc/sudoers.d/cc-agent
    ./scripts/agent-permissions.sh

  Or set AGENT_SANDBOX=off in .env to accept the risk deliberately.
EOF
  exit 1
fi

if ! sudo -n -u "$AGENT_USER" true 2>/dev/null; then
  cat >&2 <<EOF
run-agent: cannot switch to "$AGENT_USER" without a password. Install the
  sudoers rule so the timers can run unattended:
    sudo install -m 0440 scripts/cc-agent.sudoers.example /etc/sudoers.d/cc-agent
  It permits exactly one thing: running commands as $AGENT_USER, with no
  password, from this account.
EOF
  exit 1
fi

if [[ -r "$ROOT/.env" ]] && sudo -n -u "$AGENT_USER" test -r "$ROOT/.env"; then
  echo "run-agent: $AGENT_USER can read $ROOT/.env, which defeats the" \
       "separation. Run ./scripts/agent-permissions.sh. Refusing." >&2
  exit 1
fi

run_sandboxed
