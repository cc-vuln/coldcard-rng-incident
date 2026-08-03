#!/usr/bin/env bash
# Capture registered X posts (text, media, metadata) into archive/x/.
#
# X blocks unauthenticated reads, so gallery-dl borrows a logged-in session from
# Chrome. Nothing is posted, followed or liked: this is a read-only pull of URLs
# already listed in sources.toml.
#
#   ./scripts/capture-x.sh            capture every [[x_post]] in sources.toml
#   ./scripts/capture-x.sh <url>      capture one ad-hoc URL
#
# Cookies come from X_COOKIES_BROWSER (default: chrome). When borrowing a
# desktop Chrome on macOS the browser must be fully closed or its keychain
# prompt blocks; the dedicated capture-browser profile has no such
# constraint. If extraction fails, see the --cookies fallback in
# docs/capture.md.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GDL="${GALLERY_DL:-$ROOT/.venv/bin/gallery-dl}"
OUT="${CAPTURE_X_OUT:-$ROOT/archive/x}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$OUT/_capture-$TS.log"

# Direct invocation and `just capture-x` use the same lock as HTTP capture,
# Wayback backfill and browser ingest.  The helper re-enters this script with
# COLDCARD_ARCHIVE_LOCK_HELD set while its parent retains the fcntl lock.
if [[ "${COLDCARD_ARCHIVE_LOCK_HELD:-}" != "1" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/archive_lock.py" \
    --label capture-x -- "$0" "$@"
fi

[[ -x "$GDL" ]] || { echo "gallery-dl missing. run: just setup" >&2; exit 1; }
mkdir -p "$OUT"

# gallery-dl writes <author>/<tweet-id>* and a .json sidecar per post;
# capture_url flattens that into archive/x/<post-id>/<TS>/ afterwards.
common_args=(
  --cookies-from-browser "${X_COOKIES_BROWSER:-chrome}"
  --write-metadata
  --write-info-json
  --retries 3
  --sleep 2-4
)

capture_url() {
  local url="$1" label="${2:-adhoc}"
  local tweet_id safe_label stage artifact_count matched path destination
  tweet_id="${url##*/}"
  tweet_id="${tweet_id%%\?*}"
  safe_label="$(printf '%s' "$label" | tr -cs 'A-Za-z0-9._-' '_')"
  stage="$(mktemp -d "${TMPDIR:-/tmp}/coldcard-x.XXXXXX")"
  echo "--- $label  $url"
  if ! "$GDL" "${common_args[@]}" --dest "$stage" "$url" 2>&1 | tee -a "$LOG"; then
    echo "    FAILED (see $LOG)" >&2
    rm -rf "$stage"
    return 1
  fi

  artifact_count="$(find "$stage" -type f ! -name '.DS_Store' | wc -l | tr -d ' ')"
  if [[ "$artifact_count" -eq 0 ]]; then
    echo "    FAILED: gallery-dl returned success but produced no result" >&2
    rm -rf "$stage"
    return 1
  fi

  # A successful direct status fetch should name the requested numeric post id
  # in an artefact filename or JSON sidecar. This rejects login walls and
  # unrelated timeline output, while still accepting text-only posts whose only
  # held artefact may be gallery-dl's generic info.json.
  if [[ "$tweet_id" =~ ^[0-9]+$ ]]; then
    matched=0
    while IFS= read -r path; do
      case "$(basename "$path")" in
        *"$tweet_id"*) matched=1 ;;
      esac
      if [[ "$path" == *.json ]] && /usr/bin/grep -q "$tweet_id" "$path"; then
        matched=1
      fi
    done < <(find "$stage" -type f ! -name '.DS_Store')
    if [[ "$matched" -ne 1 ]]; then
      echo "    FAILED: no artefact matched requested post id $tweet_id" >&2
      rm -rf "$stage"
      return 1
    fi
  fi

  # One layout for every capture, whatever fetched it: archive/x/<id>/<TS>/,
  # where a file's name states what it is. gallery-dl nests by service and
  # author, which is its business and not the archive's, so flatten it.
  #
  # Media attached to a post is never the post itself. Naming it
  # attachment-N keeps that distinction in the filename, where the site's
  # publication rules can rely on it instead of guessing.
  destination="$OUT/$safe_label/$TS"
  mkdir -p "$destination"
  n=0
  while IFS= read -r path; do
    base="$(basename "$path")"
    case "$base" in
      *.json)
        # Sidecars keep their own names so a reader can tell which artefact
        # each describes; gallery-dl's generic one becomes meta.json.
        if [[ "$base" == "info.json" ]]; then
          mv "$path" "$destination/meta.json"
        else
          mv "$path" "$destination/$base"
        fi
        ;;
      *)
        n=$((n + 1))
        ext="${base##*.}"
        mv "$path" "$destination/attachment-$n.$ext"
        ;;
    esac
  done < <(find "$stage" -type f ! -name '.DS_Store' | sort)
  rm -rf "$stage"
  echo "    ok: $artifact_count artefact(s) -> ${destination#$ROOT/}"
}

if [[ $# -gt 0 ]]; then
  # File under the id this post is registered as, when it is one. Otherwise a
  # capture of a tracked post lands where nothing reads it.
  adhoc_label="$("$ROOT/.venv/bin/python" - "$1" "$ROOT/sources.toml" <<'PY' 2>/dev/null || true
import re, sys, tomllib, pathlib
url = sys.argv[1]
m = re.search(r"/status/(\d+)", url)
cfg = tomllib.loads(pathlib.Path(sys.argv[2]).read_text())
if m:
    for post in cfg.get("x_post", []):
        if m.group(1) in post.get("url", ""):
            print(post["id"])
            break
PY
)"
  capture_url "$1" "${adhoc_label:-adhoc}"
  exit $?
fi

# Pull the registered posts out of sources.toml with the repo's own Python.
# Bash 3.2, which ships with macOS, has no `mapfile`, so use a temporary TSV.
entries_file="$(mktemp "${TMPDIR:-/tmp}/coldcard-x-entries.XXXXXX")"
trap 'rm -f "$entries_file"' EXIT
"$ROOT/.venv/bin/python" - "$ROOT/sources.toml" > "$entries_file" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent / "scripts"))
from capture import load_sources
cfg = load_sources()
for p in cfg.get("x_post", []):
    print(f"{p['id']}\t{p['url']}")
PY

entry_count="$(wc -l < "$entries_file" | tr -d ' ')"
echo "capturing $entry_count registered X post(s) -> $OUT"
failed=0
while IFS=$'\t' read -r id url; do
  [[ -n "$id" && -n "$url" ]] || continue
  capture_url "$url" "$id" || failed=$((failed+1))
done < "$entries_file"

echo
if (( failed )); then
  echo "$failed capture(s) failed. Most common cause: Chrome open, or no X session."
  exit 1
fi
echo "all captured. log: $LOG"
