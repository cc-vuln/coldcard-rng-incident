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

# Work in progress, including archive churn not yet committed by the hourly
# record committer. An indexable build must match one reconstructible commit;
# publishing an uncommitted capture would make /version.json say otherwise.
dirty="$(git -C "$ROOT" status --porcelain | head -5)"
[[ -z "$dirty" ]] || skip "uncommitted work:
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
state_digest() {
  {
    git -C "$ROOT" rev-parse HEAD
    find "${ROOT}/archive/snapshots" "${ROOT}/archive/x" -name '*.txt' -printf '%P\n' 2>/dev/null | sort
    sha256sum "${ROOT}/revision-reviews.toml" "${ROOT}/sources.toml"
  } | sha256sum | cut -d' ' -f1
}
current="$(state_digest)"
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

# The build regenerates tracked indexes (site/src/data/x-thread-media.json)
# from whatever captures exist at build time, so media landing between the
# hourly commit and this build would otherwise dirty the tree mid-build and
# flip matches_commit false on the deployed stamp (8 and 9 Aug 2026).
# Regenerate and commit them here, before the build, so the tripwire below
# only ever fires on a genuinely unexpected dirtying. Staging must run under
# the site's dotenv environment (PUBLIC_X_MEDIA), exactly as the build's own
# staging step does, or this commit records empty manifests and the build
# immediately dirties them again (observed 12 Aug 2026).
just stage-x-media >/dev/null 2>&1 || true
if ! git -C "$ROOT" diff --quiet -- site/src/data; then
  git -C "$ROOT" add site/src/data
  git -C "$ROOT" commit -q -m "site: the media index the publish build regenerates"
  # HEAD is part of the stamp. Record the state actually published rather than
  # forcing one redundant deploy on the next tick after this pre-build commit.
  current="$(state_digest)"
fi

# The version stamp must describe one stable tree. The build and its gates
# read the archive while polls keep writing it; a poll landing mid-build
# dirties a tracked file after version.json is generated and fails
# check-version-exact (observed 12 Aug 2026, twice: the pre-build index fix
# alone could not close this, because the racing writer is the poll, not the
# staging). Hold the read-side shared lock across the build so a poll defers
# with its routine exit 21 instead of racing the stamp. Lock order matches
# record_commit.py — build lock (fd 9, held above) first, then the archive
# lock — and this acquisition is non-blocking, so a writer mid-poll simply
# defers this tick.
publish_rc=0
.venv/bin/python scripts/archive_lock.py --shared --label publish -- just publish || publish_rc=$?
if [[ "$publish_rc" -eq 21 ]]; then
  skip "an archive writer holds the lock; the next tick retries"
fi
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
# sit uncommitted. This script stages the generated indexes before its publish
# build, so a dirty tracked tree here means that pre-staging broke —
# say so loudly rather than publishing a stamp that does not reproduce.
tracked_dirt="$(git -C "$ROOT" status --porcelain --untracked-files=no)"
if [[ -n "$tracked_dirt" ]]; then
  echo "publish-scheduled: ERROR the build left tracked files modified; the pre-build generated-index commit should have staged these first:" >&2
  echo "$tracked_dirt" >&2
  exit 1
fi
echo "publish-scheduled: published"
