#!/usr/bin/env bash
# Sign in to a site once, so that automated capture can read what a logged-in
# reader sees. Opens a real browser window using the SAME profile the capture
# daemon uses, so the session persists after you close it.
#
#   just capture-login                 sign in to X
#   just capture-login url=https://…   sign in somewhere else
#
# On a desktop this opens a window directly. On a headless machine it starts a
# virtual display and a VNC server on 127.0.0.1:5901, which you reach with an
# SSH tunnel:
#
#   ssh -N -L 5901:localhost:5901 <host>     then open vnc://localhost:5901
#
# Nothing is typed for you and no credential is stored by this project: you
# sign in yourself, and only the browser profile keeps the session.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${CAPTURE_BROWSER_PROFILE:-$ROOT/.capture-browser/profile}"
URL="${1:-https://x.com/login}"

CHROME="${CAPTURE_BROWSER_BIN:-}"
if [[ -z "$CHROME" ]]; then
  # Playwright's own Chromium, wherever this platform put it.
  CHROME="$(find "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}" \
    -maxdepth 3 -type f \( -name chrome -o -name 'Chromium' \) 2>/dev/null | head -1 || true)"
fi
[[ -n "$CHROME" && -x "$CHROME" ]] || {
  echo "no Chromium found. run: just install-capture-browser" >&2; exit 1; }

mkdir -p "$PROFILE"

# The daemon holds an exclusive lock on the profile while it runs.
if curl -fsS -m 2 -X POST http://127.0.0.1:10086/command \
     -H 'Content-Type: application/json' \
     -d '{"action":"list_tabs","args":{},"session":"login"}' >/dev/null 2>&1; then
  echo "the capture daemon is running and holds the profile." >&2
  echo "stop it first, then run this again:" >&2
  echo "  sudo systemctl stop webbridge     # or kill the webbridge.py process" >&2
  exit 1
fi

# --no-sandbox matches what Playwright does by default, and is required where
# unprivileged user namespaces are restricted (Ubuntu 23.10+ and similar).
ARGS=(--no-sandbox --user-data-dir="$PROFILE" --no-first-run
      --no-default-browser-check --window-size=1440,880 "$URL")

if [[ -n "${DISPLAY:-}" ]]; then
  echo "opening a browser window. sign in, then close it."
  exec "$CHROME" "${ARGS[@]}"
fi

command -v Xvfb >/dev/null || {
  echo "headless machine and no Xvfb. install xvfb x11vnc fluxbox" >&2; exit 1; }

echo "headless: starting a virtual display and VNC on 127.0.0.1:5901"
export DISPLAY=:99
pkill -f 'Xvfb :99' 2>/dev/null || true
Xvfb :99 -screen 0 1440x900x24 >/dev/null 2>&1 &
sleep 1
command -v fluxbox >/dev/null && { fluxbox >/dev/null 2>&1 & sleep 1; }
"$CHROME" "${ARGS[@]}" >/dev/null 2>&1 &
sleep 2

# A password is required because some clients, including macOS Screen Sharing,
# refuse an open VNC server. It is per-session and thrown away afterwards.
PW="$(head -c 6 /dev/urandom | base64 | tr -d '/+=' | cut -c1-8)"
mkdir -p "$ROOT/.capture-browser"
x11vnc -storepasswd "$PW" "$ROOT/.capture-browser/vncpass" >/dev/null 2>&1
echo
echo "  tunnel:   ssh -N -L 5901:localhost:5901 <this-host>"
echo "  open:     vnc://localhost:5901"
echo "  password: $PW"
echo
echo "sign in, then press Ctrl-C here. Start the capture daemon again after."
exec x11vnc -display :99 -localhost -rfbport 5901 \
  -rfbauth "$ROOT/.capture-browser/vncpass" -forever -shared -quiet
