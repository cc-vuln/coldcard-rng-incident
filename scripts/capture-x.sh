#!/usr/bin/env bash
# Capture registered X posts (text, media, metadata) into archive/x/.
#
# X blocks unauthenticated reads, so gallery-dl borrows a logged-in session from
# Chrome. Nothing is posted, followed or liked: this is a read-only pull of URLs
# already listed in sources.toml.
#
#   ./scripts/capture-x.sh            capture every [[x_post]] in sources.toml
#   ./scripts/capture-x.sh --skip-unchanged
#                                   same, but write nothing for a post whose
#                                   media are all already held (weekly timer)
#   ./scripts/capture-x.sh <url>      capture one ad-hoc URL
#
# --skip-unchanged is the change detection the scheduled run needs: without it
# every weekly tick would add a new timestamped directory per post holding the
# same media. gallery-dl's --download-archive (supported since long before the
# installed 1.32.9) records each successfully downloaded file in a per-post
# sqlite database under .work/ and skips files already in it; when a run
# downloads nothing new and the post already has a held capture, the staging
# directory holds only JSON sidecars and no archive write happens. A post with
# genuinely new media (or a first capture) writes exactly as before. The
# download-archive databases are operational state, not captures, so they live
# in .work/x-media-download-archive/ rather than in the append-only archive.
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
MEDIA_STATE="$ROOT/.work/x-media-download-archive"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$OUT/_capture-$TS.log"

# Direct invocation and `just capture-x` use the same lock as HTTP capture,
# Wayback backfill and browser ingest.  The helper re-enters this script with
# COLDCARD_ARCHIVE_LOCK_HELD set while its parent retains the fcntl lock.
if [[ "${COLDCARD_ARCHIVE_LOCK_HELD:-}" != "1" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/archive_lock.py" \
    --label capture-x -- "$0" "$@"
fi

# Parsed after the lock re-entry: the re-entered child is the one that runs
# the captures, so the flag must survive into it.
SKIP_UNCHANGED=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-unchanged) SKIP_UNCHANGED=1; shift ;;
    --) shift; break ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done

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
  local media_count held dl_archive
  tweet_id="${url##*/}"
  tweet_id="${tweet_id%%\?*}"
  safe_label="$(printf '%s' "$label" | tr -cs 'A-Za-z0-9._-' '_')"
  stage="$(mktemp -d "${TMPDIR:-/tmp}/coldcard-x.XXXXXX")"
  echo "--- $label  $url"
  local -a gdl_args=("${common_args[@]}")
  dl_archive=""
  if [[ "$SKIP_UNCHANGED" == "1" ]]; then
    mkdir -p "$MEDIA_STATE"
    dl_archive="$MEDIA_STATE/$safe_label.sqlite"
    gdl_args+=(--download-archive "$dl_archive")
  fi
  if ! "$GDL" "${gdl_args[@]}" --dest "$stage" "$url" 2>&1 | tee -a "$LOG"; then
    echo "    FAILED (see $LOG)" >&2
    rm -rf "$stage"
    return 1
  fi

  # Same-check, before the empty-stage failure below: with --download-archive
  # active, an unchanged post downloads nothing and the stage holds only JSON
  # sidecars (or nothing). If the post already has a held capture, that is an
  # unchanged read, not a failure, and no new timestamped directory is written.
  if [[ -n "$dl_archive" ]]; then
    media_count="$(find "$stage" -type f ! -name '*.json' ! -name '.DS_Store' | wc -l | tr -d ' ')"
    # A post with no held capture yet has no directory to find; `|| true`
    # keeps that expected miss from tripping set -e/pipefail.
    held="$(find "$OUT/$safe_label" -mindepth 1 -maxdepth 1 -type d \
      -name '[0-9]*' 2>/dev/null | head -1 || true)"
    if [[ "$media_count" -eq 0 && -n "$held" ]]; then
      rm -rf "$stage"
      echo "    unchanged: no new media; nothing written"
      return 0
    fi
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
