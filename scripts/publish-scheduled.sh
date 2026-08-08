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
publish_rc=0
just publish || publish_rc=$?
if [[ "$publish_rc" -ne 0 ]]; then
  # A failed publish is retried on the next tick (the stamp is only written
  # on success), but a retry loop nobody reads is a silent outage. Alerting
  # is allowed to fail; it never changes this script's exit code.
  .venv/bin/python scripts/alert.py emit \
    --kind publish-failure --severity warning \
    --key "publish-failure-$(date -u +%Y-%m-%d)" \
    --summary "just publish failed (exit $publish_rc) in publish-scheduled; see the unit journal" || true
  exit "$publish_rc"
fi

# Stamped only on success, so a failed publish is retried on the next tick.
echo "$current" > "$STAMP"

# A deploy is not finished until the commit it was built from is pushed.
# /version.json stamps that commit and /cite/ tells readers the state can be
# reconstructed from it on GitHub; an unpushed deploy publishes a citation
# that resolves nowhere. Since 8 Aug 2026 record_commit.py is what makes the
# commit, so this push is how the stamped hash becomes real. A failed push
# does not fail the stamp: the deploy already happened, and the next
# successful publish pushes everything unpushed, this commit included.
branch_now="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
if [[ "$branch_now" == "main" ]]; then
  git -C "$ROOT" push || echo "publish-scheduled: git push failed; the deploy is stamped and the next publish retries the push" >&2
else
  echo "publish-scheduled: HEAD moved to ${branch_now} during the build; not pushing" >&2
fi

# The build regenerates tracked files (site/src/data/x-thread-media.json is
# the known one), and matches_commit in /version.json reads false while they
# sit uncommitted. record_commit.py stages the generated indexes before any
# publish build, so a dirty tracked tree here means that pre-staging broke —
# say so loudly rather than publishing a stamp that does not reproduce.
tracked_dirt="$(git -C "$ROOT" status --porcelain --untracked-files=no)"
if [[ -n "$tracked_dirt" ]]; then
  echo "publish-scheduled: ERROR the build left tracked files modified; matches_commit is false for this deploy. record_commit.py should have staged these first:" >&2
  echo "$tracked_dirt" >&2
  exit 1
fi
echo "publish-scheduled: published"
