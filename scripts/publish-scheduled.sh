#!/usr/bin/env bash
#
# Publish on a schedule, but only from a tree nobody is working in.
#
# The funds page now reads the community trackers' totals out of the archive at
# build time, so a published figure is only as fresh as the last deploy. That
# argues for deploying on a timer. It also means a timer can pick up whatever
# happens to be sitting in the working tree, which is the part that has to be
# made safe: this repository has one canonical working tree, and that tree is
# where editing happens.
#
# So every guard below answers one question: is what is on disk right now
# something the operator meant to publish? If the answer is no, this exits 0
# and says why. A skip is not a failure; the next run picks it up.
#
#   0   published, or deliberately skipped
#   1   a guard could not be evaluated, or the publish itself failed
#
# Install it as publish-scheduled.timer (see the .example units beside this
# file). Run it by hand first: `just publish-scheduled --dry-run`.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

STAMP="${ROOT}/.work/publish-scheduled.stamp"
BUILD_LOCK=/tmp/cc-build.lock

skip() { echo "publish-scheduled: skipped, $1"; exit 0; }

# The operator's kill switch, and the first thing to reach for before starting
# a long edit. Untracked, so it is never published or committed by accident.
[[ -e "${ROOT}/.no-publish" ]] && skip "$(cat "${ROOT}/.no-publish" 2>/dev/null || echo '.no-publish is present')"

# Publishing a feature branch to the live site would be an accident every time.
branch="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
[[ "$branch" == "main" ]] || skip "HEAD is on ${branch}, not main"

# Work in progress. archive/ is excluded deliberately: capture dirties it
# continuously and the whole point of this timer is to publish that churn.
# Everything else, tracked or not, means somebody is mid-change.
dirty="$(git -C "$ROOT" status --porcelain -- ':(exclude)archive' | head -5)"
[[ -z "$dirty" ]] || skip "uncommitted work outside archive/:
${dirty}"

# An unreviewed difference is normal between a poll and the review pass, and
# `just audit` refuses to build while one exists. Check it here so a routine
# window logs as a skip rather than a failed unit every time it is hit.
if ! .venv/bin/python scripts/check_reviews.py >/dev/null 2>&1; then
  skip "unreviewed differences are waiting for classification"
fi

# Has anything actually changed since the last scheduled publish? Snapshot
# filenames are the archive's own record of content changing: a poll that finds
# no change writes no file. Pair them with HEAD and the review classifications,
# which are what the site renders around them.
current="$(
  {
    git -C "$ROOT" rev-parse HEAD
    find "${ROOT}/archive/snapshots" "${ROOT}/archive/x" -name '*.txt' -printf '%P\n' 2>/dev/null | sort
    sha256sum "${ROOT}/revision-reviews.toml" "${ROOT}/sources.toml"
  } | sha256sum | cut -d' ' -f1
)"
if [[ -f "$STAMP" && "$(cat "$STAMP")" == "$current" ]]; then
  skip "nothing has changed since the last publish"
fi

if [[ "$DRY_RUN" == true ]]; then
  echo "publish-scheduled: would publish now (stamp ${current:0:12})"
  exit 0
fi

# One build at a time. A concurrent build corrupts the Astro cache, and the
# symptom points at the wrong thing entirely, so a run that cannot take the
# lock waits for the next tick rather than queueing behind an operator's build.
exec 9>"$BUILD_LOCK"
flock -n 9 || skip "another build holds ${BUILD_LOCK}"

mkdir -p "${ROOT}/.work"
echo "publish-scheduled: publishing (stamp ${current:0:12})"
just publish

# Stamped only on success, so a failed publish is retried on the next tick.
echo "$current" > "$STAMP"
echo "publish-scheduled: published"
