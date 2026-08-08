# COLDCARD RNG incident archive
#
# Capture primary sources, detect when they change, keep the evidence.

set dotenv-load := true

py := ".venv/bin/python"

default:
    @just --list

# One-time setup: venv and gallery-dl
setup:
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet gallery-dl
    @echo "ready. gallery-dl $(.venv/bin/gallery-dl --version)"

# Poll every tracked source, store a snapshot only where the text changed
capture:
    @{{py}} scripts/capture.py capture

# Poll without writing anything
dry-run:
    @{{py}} scripts/capture.py capture --dry-run

# Publication-friendly capture: changes are success, incomplete polls are not.
capture-gate:
    @rc=0; {{py}} scripts/capture.py capture || rc=$?; \
        if [ "$rc" -eq 10 ]; then exit 0; fi; exit "$rc"

# Poll one source by id
capture-one id:
    @{{py}} scripts/capture.py capture --id {{id}}

# Only tier 1: the fastest-moving sources, the same slice the 30-minute
# poll's tier1 job runs
capture-urgent:
    @{{py}} scripts/capture.py capture --tier 1

# Discover new Stacker News incident threads (2 gentle requests; see script docstring)
discover-stackernews *ARGS:
    @{{py}} scripts/discover_stackernews.py {{ARGS}}

# Discover new Reddit incident threads (reads via the capture browser session)
discover-reddit *ARGS:
    @{{py}} scripts/discover_reddit.py {{ARGS}}

# Discover new BitcoinTalk incident threads (2 board pages, direct)
discover-bitcointalk *ARGS:
    @{{py}} scripts/discover_bitcointalk.py {{ARGS}}

# Discover new posts from watched X accounts through the official read-only
# API. Manual and opt-in during probation; requires X_DISCOVERY_ENABLED=true.
discover-x *ARGS:
    @{{py}} scripts/discover_x.py {{ARGS}}

# Assess community candidates. X candidates are the separate X lane's.
discovery-intake *ARGS:
    @./scripts/agent-discovery-intake.sh {{ARGS}}

# Assess queued X candidates: the registering xintake role, then driver-side
# ingest of each approved post
x-intake *ARGS:
    @./scripts/agent-x-intake.sh {{ARGS}}

# Rotate old DISCOVERY.md verdicts into discovery/assessed-YYYY-MM.md
rotate-discovery *ARGS:
    @{{py}} scripts/rotate_discovery.py {{ARGS}}

# What the record already covers, as the intake agent is shown it. The driver
# builds this itself before every run; this is for reading it by hand.
coverage-index *ARGS:
    @{{py}} scripts/build_coverage_index.py {{ARGS}}

# Move a rejected registration out of sources.toml into quarantine/, so an
# invalid registry does not stop the tree. agent_finish runs this by itself
# after a guard rejection; by hand it needs --before to know what a run added.
quarantine-registry *ARGS:
    @{{py}} scripts/quarantine_registry.py {{ARGS}}

# ---- nostr -----------------------------------------------------------------
#
# The project has its own nostr identity for announcements of record updates.
# Posting is manual and operator-run only; discovery and ingest are read-only.
# Keys and relays come from .env (NOSTR_SECRET_KEY, PUBLIC_NOSTR_PUBKEY_HEX,
# PUBLIC_NOSTR_NPUB, NOSTR_WRITE_RELAYS). The one external binary is nak.

# Generate a fresh nostr keypair and print it as npub/nsec.
# Prints only, never writes .env; store the values by hand and back up offline.
nostr-keygen:
    @command -v nak >/dev/null 2>&1 || { echo "nak not found on PATH (expected ~/.local/bin/nak)" >&2; exit 1; }
    @hex="$(nak key generate)"; pub="$(nak key public "$hex")"; \
        echo "secret key (hex): $hex"; \
        echo "nsec:             $(nak encode nsec "$hex")"; \
        echo "public key (hex): $pub"; \
        echo "npub:             $(nak encode npub "$pub")"; \
        echo; \
        echo "Store these in .env by hand (this recipe never writes it):"; \
        echo "  NOSTR_SECRET_KEY=<the nsec above>"; \
        echo "  PUBLIC_NOSTR_PUBKEY_HEX=<the public hex above>"; \
        echo "  PUBLIC_NOSTR_NPUB=<the npub above>"; \
        echo "Keep an offline backup of the nsec; it cannot be recovered."

# Post a kind-1 note from the project identity: announcements of record
# updates only, manual use, --yes or interactive confirmation required.
nostr-post *ARGS:
    @{{py}} scripts/nostr_post.py {{ARGS}}

# Publish the project kind-0 profile and kind-10002 relay list (NIP-65).
# Replaceable events, so re-running after a profile or relay change is fine.
nostr-publish-profile *ARGS:
    @{{py}} scripts/nostr_publish_profile.py {{ARGS}}

# Discover new incident-relevant nostr posts (read-only)
discover-nostr *ARGS:
    @{{py}} scripts/discover_nostr.py {{ARGS}}

# Ingest one nostr post into the archive
ingest-nostr *ARGS:
    @{{py}} scripts/ingest_nostr.py {{ARGS}}

# What is tracked, when each source last moved, and what is waiting on a
# person (quarantine, host proposals, failure streaks, unreviewed diffs)
status:
    @{{py}} scripts/capture.py status
    @{{py}} scripts/report_status.py

# Sources failing their most recent poll, grouped by cause and streak.
# `just diagnose --json` is the same view for an automated triage pass.
diagnose *ARGS:
    @{{py}} scripts/capture.py diagnose {{ARGS}}

# Corroborate dns-unresolved streaks against public DoH resolvers before any
# source is recorded gone. Dry-run unless --yes is passed; exit 0 always.
corroborate-gone *ARGS:
    @{{py}} scripts/corroborate_gone.py {{ARGS}}

# Vet the hosts intake agents proposed in .work/host-proposals.txt (DNS,
# robots.txt, redirect shape) and admit the sound ones to
# registry_hosts.toml and agent_egress_hosts.toml. Dry-run unless --yes.
vet-hosts *ARGS:
    @{{py}} scripts/vet_host.py {{ARGS}}

# Verify every held capture against the unified record contract
audit:
    @{{py}} scripts/capture.py audit
    @{{py}} scripts/check_publishable.py
    # Before the review gate: an unreviewed diff between a poll and the review
    # timer is routine and would otherwise mask a registry problem, which is
    # not.
    @{{py}} scripts/check_registry.py
    @{{py}} scripts/agent_proxy.py --check
    @{{py}} scripts/check_reviews.py

# Is the agent sandbox still in place? Reports only; run the script without
# --check to apply. Separate from `audit` because it is a property of the
# machine rather than of the record, so a clone should not fail on it.
audit-sandbox:
    @./scripts/agent-permissions.sh --check

# This carried three hand-maintained lists: every test to run, every module to
# byte-compile, every shell script to parse. Two files had already fallen off
# them by 6 Aug 2026, and one of them was publish-scheduled.sh, which the
# publish timer runs unattended. Globs cannot forget, so a new script is
# checked from the moment it exists.
#
# Each test file still runs in its own interpreter rather than through
# `unittest discover`, because several of them monkeypatch module state and
# sharing one process would couple them. test_scheduler.py is the one
# exclusion: `test` runs it separately through test-scheduler.
#
# The shell check loops one file per bash rather than passing them all at
# once. `bash -n a.sh b.sh` parses only a.sh and makes the rest positional
# parameters, so the list form this replaced had been checking capture-x.sh
# and nothing else since it was written, agent drivers included.

# Focused capture regression tests
test-capture:
    @set -e; for t in scripts/test_*.py; do \
        case "$t" in scripts/test_scheduler.py) continue ;; esac; \
        PYTHONPATH=scripts {{py}} -m unittest "$t"; \
    done
    @{{py}} scripts/discover_x.py --list >/dev/null
    @{{py}} scripts/discover_nostr.py --check >/dev/null
    @{{py}} -m py_compile scripts/*.py
    @set -e; for s in scripts/*.sh; do /bin/bash -n "$s"; done

# Rank sources by how much capture noise reaches the review layer.
review-signal *ARGS:
    @{{py}} scripts/report_review_signal.py {{ARGS}}

# Rank active Tier 3 sources for a human freeze decision.
watch-candidates *ARGS:
    @{{py}} scripts/recommend_watch_state.py {{ARGS}}

# Due-state, source-ownership, aggregation and scheduler-lock regressions
test-scheduler:
    @{{py}} -m unittest scripts/test_scheduler.py

# Fixed synthetic RNG-to-xpub regression vector
test-vectors:
    @{{py}} scripts/verify_mk3_vector.py

test: test-capture test-scheduler test-vectors

# Chronological log of every detected change
log limit="40":
    @{{py}} scripts/capture.py log --limit {{limit}}

# Snapshot history for one source
show id:
    @{{py}} scripts/capture.py show {{id}}

# Capture registered X posts (cookie source: X_COOKIES_BROWSER, see .env.example)
capture-x:
    @./scripts/capture-x.sh

# Trailing ARGS reach the script, which is how --thread --tier N is passed:
#   just ingest-x <url> <slug> "" "" --thread --tier 3

# Ingest one X post: element-only screenshot + sidecar + register (browser bridge)
ingest-x url slug="" tag="" why="" *ARGS="":
    @{{py}} scripts/ingest-x.py "{{url}}" {{ if slug != "" { "--id " + slug } else { "" } }} {{ if tag != "" { "--tag " + tag } else { "" } }} {{ if why != "" { "--why \"" + why + "\"" } else { "" } }} {{ARGS}}

# --kind is a guard, not decoration: a plain [[x_post]] id or a web source id
# matches nothing here, so a typo cannot silently poll a different source. To
# enable a thread, set thread = true and a tier on the post's block in
# sources.toml; ingest-x.py --thread does that for a post being registered now.

# Capture one thread-enabled X post's conversation now, not on its tier's timer
capture-thread id:
    @{{py}} scripts/capture.py capture --id {{id}} --kind social-thread

# Install the capture browser: its own venv and Chromium, under
# .capture-browser/. Needed only for sources that render client-side.
install-capture-browser:
    python3 -m venv .capture-browser/venv
    .capture-browser/venv/bin/pip install --quiet --upgrade pip
    .capture-browser/venv/bin/pip install --quiet -r capture-browser/requirements.txt
    .capture-browser/venv/bin/playwright install chromium
    @echo "installed. run: just capture-browser"

# Run the capture browser in the foreground. See capture-browser/README.md.
capture-browser:
    @.capture-browser/venv/bin/python capture-browser/webbridge.py

# Sign in once, so captures see what a logged-in reader sees. Opens a real
# browser; you type the credentials, this project stores none.
capture-login url="https://x.com/login":
    @./capture-browser/login.sh "{{url}}"

# Is the capture browser reachable?
capture-browser-status:
    @curl -fsS -m 3 -X POST http://127.0.0.1:10086/command \
        -H 'Content-Type: application/json' \
        -d '{"action":"list_tabs","args":{},"session":"status"}' \
        && echo "" || echo "capture browser not reachable"

# Capture, and alert on changes or an incomplete poll
watch:
    @./scripts/notify.sh

# Backfill an ad-hoc capture directory as the first snapshot
import dir:
    @{{py}} scripts/capture.py import-dir {{dir}}

# What pre-capture history does the Wayback Machine hold for a source?
wayback-list id:
    @{{py}} scripts/wayback.py list {{id}} --from 20260728

# Recover pre-capture history from the Wayback Machine
wayback-backfill id:
    @{{py}} scripts/wayback.py backfill {{id}} --from 20260728

wayback-all:
    @{{py}} scripts/wayback.py backfill-all --from 20260728
    @{{py}} scripts/wayback.py rebuild-diffs

# Regenerate diffs so recovered snapshots slot into chronological order
rebuild-diffs:
    @{{py}} scripts/wayback.py rebuild-diffs

# Run one due-state tick in the foreground
schedule-tick:
    @{{py}} scripts/scheduled_runner.py

# ---- site ----------------------------------------------------------------

# Enforce the epistemic claim-marker contract before a public build.
check-claims:
    @node site/tools/check-claims.mjs

# Reject operational details in generated public files.
check-public-output:
    @node site/tools/check-public-output.mjs

# The funds page's tracker figures must still be read from a held capture, not
# frozen at a pinned value because a tracker rebuilt its page.
check-trackers:
    @node site/tools/check-trackers.mjs

# Every internal link and anchor in the built site must resolve, including the
# retired routes served by public/_redirects. Pages get merged and routes get
# retired; without this, a stale nav entry or citation becomes a silent 404.
check-links:
    @node site/tools/check-links.mjs

# Known broken: vite fails on chunk splitting, so preview a production build
# from dist/ instead.

# Local preview with hot reload.
dev:
    @cd site && npx astro dev

# ---- building -------------------------------------------------------------
#
# Four named builds, one body. They differ only in the environment Astro is
# given and in whether the output gates run, so the body lives in _astro and
# _gates and each build is the pair of them plus its own environment. The
# reason to keep it that way: when these were four copies, the gate list had
# to be added to each of them by hand, and the copy that matters most is the
# one that gets published.
#
# SITE_URL is resolved by the shell at recipe time, not by just, because it
# comes from .env. The three expressions below are the whole difference
# between a local build, a review build and a publication build.

site_url_local := '${SITE_URL:-https://example.invalid}'
site_url_preview := 'https://${CF_PAGES_PROJECT:?set CF_PAGES_PROJECT}.pages.dev'
site_url_public := '${SITE_URL:?set SITE_URL}'

# Stage the publishable X media, then build. `env` is any extra environment
# the build needs, as a shell assignment prefix.
#
# The build itself serialises on flock /tmp/cc-build.lock, the same lock
# publish-scheduled.sh takes with flock -n before it runs `just publish`.
# The two modes are deliberate: the timer skips rather than queue behind an
# operator's build (it retries on the next tick), and an operator's build
# queues with flock -w rather than skip, because a skipped manual build looks
# exactly like a successful one in the scrollback.
#
# The probe before the wait is what makes the two modes nest. When
# publish-scheduled.sh calls `just publish`, the recipe shells inherit its
# fd 9, already holding the lock; flock(2) on an open file description that
# already holds the lock is a no-op success, so the build proceeds. Without
# the probe the recipe would open the file anew, and a fresh open file
# description blocks behind the lock its own ancestor holds: deadlock.
# Verified 8 Aug 2026 on this host: nested flock on the inherited fd succeeds
# at once, a fresh open waits until the holder exits.
_astro site_url env="":
    @node site/tools/stage-x-media.mjs
    @if flock -n 9 2>/dev/null; then \
        : "build lock already held by an ancestor on inherited fd 9"; \
    else \
        exec 9>/tmp/cc-build.lock; \
        flock -w 900 9 || { echo "astro build: could not take /tmp/cc-build.lock within 900s" >&2; exit 1; }; \
    fi; \
    cd site && {{env}} SITE_URL="{{site_url}}" npx astro build

# What every publishable build must survive: nothing operational in the
# output, every tracker total still read from a capture rather than the pin,
# no broken internal link. Takes site_url because check-public-output decides
# from it whether a pages.dev reference is expected or is a leak.
_gates site_url:
    @SITE_URL="{{site_url}}" node site/tools/check-public-output.mjs
    @node site/tools/check-trackers.mjs
    @node site/tools/check-links.mjs

# Build the public site (diffs and excerpts, full captures stay local)
build-site: test audit check-claims (_astro site_url_local) (_gates site_url_local)

# Deliberately does not run _gates: this build embeds captured text that the
# public-output gate exists to reject, so it must never be a deploy input.

# Build with complete snapshot bodies embedded. Local or gated use only.
build-site-full: test audit check-claims (_astro site_url_local "PUBLIC_FULL_TEXT=true")

# ---- deploying ------------------------------------------------------------
#
# Two paths, and the difference is what the built pages say about themselves
# rather than where they go. Both upload to the same Cloudflare Pages project
# and both land on <project>.pages.dev. Neither attaches a custom domain:
# that is a separate, deliberate action in the Cloudflare dashboard.
#
#   just preview   noindex, canonical URLs point at pages.dev
#   just publish   indexable, canonical URLs point at SITE_URL
#
# Indexing is opt-in in the layout, so a build that forgets the flag is a build
# search engines are told to ignore. That is the safe way round.

# Not indexable, and its sitemap advertises the pages.dev host rather than a
# domain that is not serving the site yet.

# Review build for pages.dev, for assessing changes before any deploy.
build-preview: test audit check-claims (_astro site_url_preview) (_gates site_url_preview)

# Capture, audit, build and push a review copy to pages.dev.
preview: capture-gate audit build-preview deploy
    @echo "review copy live at https://${CF_PAGES_PROJECT}.pages.dev (noindex)"

# The 30-minute poll keeps the record fresh on its own, so there is no
# pre-publish capture here: every content gate (test, audit, check-claims,
# public-output, links) still runs via build-site-indexable, and a source that
# intermittently refuses this host cannot block a deploy. Only run this once
# the content is settled: unlike preview, the output invites indexing.

# Audit, rebuild and publish for real.
publish: audit build-site-indexable deploy

# Publish only if the tree is clean and the record has actually moved. This is
# what the publish-scheduled timer runs; --dry-run reports the decision only.
publish-scheduled *ARGS:
    @./scripts/publish-scheduled.sh {{ARGS}}

# The strict path: capture first and refuse to deploy an incomplete poll.
# Exit 10 (healthy changes) does not block; a source erroring does.
publish-fresh: capture-gate audit build-site-indexable deploy

# Separated from build-site so that nothing indexable is ever produced
# without asking for it by name.

# The public build, marked indexable.
build-site-indexable: test audit check-claims (_astro site_url_public "PUBLIC_INDEXABLE=true") (_gates site_url_public)

# Push the built site to Cloudflare Pages by direct upload, so no source repo
# is ever exposed. Needs CF_PAGES_PROJECT set, and `npx wrangler login` once.
deploy:
    @cd site && npx wrangler pages deploy dist \
        --project-name "${CF_PAGES_PROJECT:?set CF_PAGES_PROJECT}" \
        --commit-dirty=true

# Describe every held capture without reproducing any of it.
manifest *ARGS:
    @{{py}} scripts/build_manifest.py --summary {{ARGS}}

# Stage the archival deposit under .work/. Stages and reports only: there is
# no upload path here, and the captured bodies are deliberately not included.
deposit *ARGS:
    @{{py}} scripts/make_deposit.py {{ARGS}}

# Total archive size and snapshot count
stats:
    @echo "snapshots: $(find archive/snapshots -name '*.txt' | wc -l | tr -d ' ')"
    @echo "diffs:     $(find archive/diffs -name '*.diff' 2>/dev/null | wc -l | tr -d ' ')"
    @echo "x posts:   $(find archive/x -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
    @echo "size:      $(du -sh archive | cut -f1)"

# Remove generated site output only. Never touches archive/.
clean:
    @rm -rf site/dist
    @echo "cleaned site/dist (archive/ untouched)"

# Run a command with the capture and review timers paused, for agent work
# that edits scripts/capture.py. Timers always restart afterwards, even on
# failure: just agent-maintenance <agent-command>
#
# Pass ONE executable and its arguments. just word-splits *ARGS, so a quoted
# compound command (`just agent-maintenance bash -c 'a && b'`) is split at
# every space: bash then runs only the first word and exits 0, and the window
# closes having done nothing while reporting success. Observed 6 Aug 2026.
# For anything with a pipe, && or a loop, put it in a script and pass the path.
agent-maintenance *ARGS:
    @./scripts/agent-maintenance.sh {{ARGS}}

# Recheck unverified claim markers once by hand (scripts/claim-sweep.sh).
# The 12-hour timer (scripts/claim-sweep.timer.example) runs the same script.
claim-sweep:
    @./scripts/claim-sweep.sh

# Draft correction proposals from the newest claim-sweep report's
# state-changed flags (propose-only agent role; scripts/agent-corrections.sh)
corrections-draft:
    @./scripts/agent-corrections.sh

# Validate and apply correction proposals in .work/correction-proposals/.
# Default is a dry run listing verdicts; --yes applies. Exit 0 either way:
# a rejected proposal is routine, and the corrections-watch unit relies on it.
apply-corrections *ARGS:
    @{{py}} scripts/apply_corrections.py {{ARGS}}
