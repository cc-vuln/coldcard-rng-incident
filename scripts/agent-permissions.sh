#!/usr/bin/env bash
# Apply, or check, the file permissions the agent sandbox rests on.
#
#   scripts/agent-permissions.sh          apply
#   scripts/agent-permissions.sh --check  report only, exit 1 if wrong
#
# The unattended agents run as their own account (scripts/run-agent.sh). That
# account is only as contained as the permissions make it, and permissions
# drift: a new secret lands in .env, someone untars a backup, a tool recreates
# a directory group-writable. So the layout is written down here, applied by
# one command, and re-checkable at any time.
#
# Three rules, and each one answers a question about the agent account:
#
#   what it must not read    .env, AGENTS.local.md, the operator's keys, the
#                            signed-in browser profile
#   what it must not write   everything, by default, including scripts/ and
#                            the archive
#   what it may write        the four registry and queue files, the editorial
#                            pages, and .work/
#
# Run it after creating the account, and again after anything that adds a
# secret or a writable path. `just audit` calls the check.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

AGENT_USER="${AGENT_SANDBOX_USER:-cc-agent}"
AGENT_GROUP="${AGENT_SANDBOX_GROUP:-$AGENT_USER}"
CHECK=false
[[ "${1:-}" == "--check" ]] && CHECK=true

problems=0
note() { echo "  $*"; }
fail() { echo "  WRONG: $*" >&2; problems=$((problems + 1)); }

# Paths the agent must never read. Mode 600 (or 700 for a directory) and
# owned by whoever runs the timers: the agent account is not in the picture.
SECRET_FILES=(
  ".env"
  "AGENTS.local.md"
  "site/tools/private-tokens.json"
)
# Provider credential and config directories, which name the tooling and so
# are listed outside the tracked tree, one path per line. Same reason as
# .env and AGENTS.local.md.
EXTRA_SECRET_DIRS_FILE="${AGENT_SECRET_DIRS_FILE:-$ROOT/scripts/agent-secret-dirs.local}"
EXTRA_SECRET_DIRS=()
if [[ -r "$EXTRA_SECRET_DIRS_FILE" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    EXTRA_SECRET_DIRS+=("${line/#\~/$HOME}")
  done < "$EXTRA_SECRET_DIRS_FILE"
fi

SECRET_DIRS=(
  ".capture-browser"
  "$HOME/.ssh"
  "$HOME/.aws"
  "$HOME/.config/wrangler"
  "$HOME/.config/gh"
  # Provider credential directories are listed in AGENTS.local.md and read
  # from EXTRA_SECRET_DIRS below, so this file names no tooling.
  "$HOME/.local/share/wrangler"
  "${EXTRA_SECRET_DIRS[@]}"
)

# Paths a role is allowed to write. These are the same paths ROLES names in
# scripts/agent_guard.py; if they diverge, the guard rejects a run the
# permissions permitted, which is the safe direction but still a bug.
AGENT_FILES=(
  "sources.toml"
  "DISCOVERY.md"
  "revision-reviews.toml"
  "BACKLOG.md"
)
AGENT_DIRS=(
  ".work"
  "site/src/pages"
)

# Directories that have no business being group-writable, and have been.
TIGHTEN_DIRS=(
  "scripts/__pycache__"
  ".venv"
  ".backups"
)

mode_of() { stat -c '%a' "$1" 2>/dev/null || echo "missing"; }
group_of() { stat -c '%G' "$1" 2>/dev/null || echo "missing"; }

if $CHECK; then
  echo "agent-permissions: checking for ${AGENT_USER}"
else
  echo "agent-permissions: applying for ${AGENT_USER}"
fi

if ! id -u "$AGENT_USER" >/dev/null 2>&1; then
  fail "the agent account ${AGENT_USER} does not exist. Create it:
    sudo useradd --system --user-group --home-dir /var/lib/${AGENT_USER} \\
        --create-home --shell /usr/sbin/nologin ${AGENT_USER}
    sudo usermod -aG ${AGENT_GROUP} $(id -un)"
  # Everything below still reports usefully, so carry on rather than exiting.
fi

echo "secrets the agent must not read:"
for path in "${SECRET_FILES[@]}"; do
  [[ -e "$path" ]] || { note "$path (absent)"; continue; }
  if $CHECK; then
    if [[ "$(mode_of "$path")" == "600" ]]; then
      note "$path 600"
    else
      fail "$path is $(mode_of "$path"), want 600"
    fi
  else
    chmod 600 "$path" && note "$path 600"
  fi
done
for path in "${SECRET_DIRS[@]}"; do
  [[ -e "$path" ]] || { note "$path (absent)"; continue; }
  if $CHECK; then
    if [[ "$(mode_of "$path")" == "700" ]]; then
      note "$path 700"
    else
      fail "$path is $(mode_of "$path"), want 700"
    fi
  else
    chmod 700 "$path" && note "$path 700"
  fi
done

# The agent must be able to walk into the working tree without being able to
# list the home directory that contains it. o+x grants traversal only: a name
# has to be known already to be reached, and the secrets above are 600/700
# whether or not someone knows they are there.
if [[ "$ROOT" == "$HOME"/* ]]; then
  current="$(mode_of "$HOME")"
  if $CHECK; then
    if [[ "${current: -1}" =~ [1357] ]]; then
      note "$HOME $current (agent can traverse)"
    else
      fail "$HOME is $current: the agent cannot reach the working tree. Want o+x: chmod o+x $HOME"
    fi
  else
    chmod o+x "$HOME" && note "$HOME $(mode_of "$HOME")"
  fi
fi

# The provider CLIs create a scratch directory inside the workspace, and each
# one picks its own name, and the name changes between releases, so
# pre-creating them is a game of catch-up that breaks on every upgrade.
#
# Instead the repository root is setgid plus STICKY, 3775. Group-writable so a
# provider can make its own scratch directory; sticky so the agent can only
# remove or rename entries it owns itself. Verified 6 Aug 2026: as cc-agent,
# creating one succeeds while `rm AGENTS.md` and `mv justfile` both fail
# with EPERM. Without the sticky bit, write permission on a directory is
# permission to unlink anything in it, tracked files included.
echo "the working tree root:"
if $CHECK; then
  if [[ "$(mode_of .)" == "3775" && "$(group_of .)" == "$AGENT_GROUP" ]]; then
    note ". 3775 ${AGENT_GROUP} (agent may create, not destroy)"
  else
    fail ". is $(mode_of .) $(group_of .), want 3775 ${AGENT_GROUP}"
  fi
else
  sudo chgrp "$AGENT_GROUP" .
  sudo chmod 3775 .
  note ". $(mode_of .) ${AGENT_GROUP}"
fi

echo "paths the agent may write:"
for path in "${AGENT_FILES[@]}"; do
  [[ -e "$path" ]] || { note "$path (absent)"; continue; }
  if $CHECK; then
    if [[ "$(group_of "$path")" == "$AGENT_GROUP" && "$(mode_of "$path")" =~ [67][0-9]$ ]]; then
      note "$path $(mode_of "$path") ${AGENT_GROUP}"
    else
      fail "$path is $(mode_of "$path") $(group_of "$path"), want group-writable by ${AGENT_GROUP}"
    fi
  else
    sudo chgrp "$AGENT_GROUP" "$path"
    # DISCOVERY.md is 600 today and stays off `other`; the rest are public
    # content and stay world-readable.
    if [[ "$path" == "DISCOVERY.md" ]]; then chmod 660 "$path"; else chmod 664 "$path"; fi
    note "$path $(mode_of "$path") ${AGENT_GROUP}"
  fi
done
for path in "${AGENT_DIRS[@]}"; do
  [[ -e "$path" ]] || { note "$path (absent)"; continue; }
  if $CHECK; then
    if [[ "$(group_of "$path")" == "$AGENT_GROUP" && "$(mode_of "$path")" == "2775" ]]; then
      note "$path 2775 ${AGENT_GROUP}"
    else
      fail "$path is $(mode_of "$path") $(group_of "$path"), want 2775 ${AGENT_GROUP}"
    fi
  else
    # setgid so anything the agent creates stays writable by the operator,
    # rather than leaving files only the agent account can edit.
    #
    # Through sudo, because the kernel silently clears the setgid bit when a
    # non-root caller chmods a directory whose group is not in its own
    # supplementary groups. Right after `usermod -aG cc-agent`, the running
    # shell still carries the old credentials, so the plain chmod appears to
    # succeed and leaves 775 behind. Observed 6 Aug 2026, caught by --check.
    sudo chgrp -R "$AGENT_GROUP" "$path"
    sudo find "$path" -type d -exec chmod 2775 {} +
    sudo find "$path" -type f -exec chmod 664 {} +
    note "$path 2775 ${AGENT_GROUP} (recursive)"
  fi
done

# The capture browser answers any local process, and its protocol runs
# arbitrary JavaScript in sessions signed in as a person. A port is not an
# access control, so the daemon takes a shared secret; it lives inside the
# 700 profile directory, where the agent account cannot read it.
echo "capture browser token:"
BRIDGE_TOKEN=".capture-browser/token"
if [[ -d ".capture-browser" ]]; then
  if $CHECK; then
    if [[ -f "$BRIDGE_TOKEN" && "$(mode_of "$BRIDGE_TOKEN")" == "600" ]]; then
      note "$BRIDGE_TOKEN 600"
    else
      fail "$BRIDGE_TOKEN is $(mode_of "$BRIDGE_TOKEN"), want a 600 token file. Without it any local process can drive the signed-in browser"
    fi
  elif [[ -f "$BRIDGE_TOKEN" ]]; then
    chmod 600 "$BRIDGE_TOKEN"
    note "$BRIDGE_TOKEN 600 (kept; rotating it needs a webbridge restart)"
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$BRIDGE_TOKEN"
    chmod 600 "$BRIDGE_TOKEN"
    note "$BRIDGE_TOKEN created. Restart webbridge.service to pick it up:"
    note "  sudo systemctl restart webbridge.service"
  fi
else
  note "$BRIDGE_TOKEN (no capture browser on this machine)"
fi

# The run record is the one thing under .work/ the agent must not be able to
# edit: it is the evidence of what the agent did.
if [[ -e ".work/agent-guard" ]]; then
  if $CHECK; then
    if [[ "$(mode_of .work/agent-guard)" == "700" ]]; then
      note ".work/agent-guard 700 (agent cannot edit its own record)"
    else
      fail ".work/agent-guard is $(mode_of .work/agent-guard), want 700"
    fi
  else
    sudo chgrp -R "$(id -gn)" .work/agent-guard
    chmod -R go-rwx .work/agent-guard
    # Symbolic, because the .work/ pass above sets setgid recursively and this
    # directory must not keep it. Neither `chmod 700` nor `chmod 0700` clears
    # it here: both left 2700 behind, which --check then reported every run.
    # `g-s` does. Observed 6 Aug 2026.
    chmod g-s .work/agent-guard
    chmod 700 .work/agent-guard
    note ".work/agent-guard 700"
  fi
fi

echo "group-writable paths that should not be:"
for path in "${TIGHTEN_DIRS[@]}"; do
  [[ -e "$path" ]] || { note "$path (absent)"; continue; }
  mode="$(mode_of "$path")"
  if $CHECK; then
    if [[ "${mode: -2:1}" =~ [2367] ]]; then
      fail "$path is $mode, want no group write"
    else
      note "$path $mode"
    fi
  else
    chmod -R g-w "$path" && note "$path $(mode_of "$path")"
  fi
done

if [[ $problems -gt 0 ]]; then
  echo "agent-permissions: $problems problem(s). The agent sandbox is not fully in place; run without --check to apply." >&2
  exit 1
fi
echo "agent-permissions: ok"
