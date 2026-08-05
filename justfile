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

# Only the tier-1 mutable vendor advisories
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

# Assess community candidates. X uses explicit, read-only --include-x triage.
discovery-intake *ARGS:
    @./scripts/agent-discovery-intake.sh {{ARGS}}

# What is tracked, and when each source last moved
status:
    @{{py}} scripts/capture.py status

# Verify every held capture against the unified record contract
audit:
    @{{py}} scripts/capture.py audit
    @{{py}} scripts/check_publishable.py
    @{{py}} scripts/check_reviews.py

# Focused capture regression tests
test-capture:
    @{{py}} -m unittest scripts/test_capture.py
    @{{py}} -m unittest scripts/test_discover_x.py
    @{{py}} -m unittest scripts/test_agent_discovery_intake.py
    @{{py}} scripts/discover_x.py --list >/dev/null
    @PYTHONPATH=scripts {{py}} -m unittest scripts/test_list_unreviewed_diffs.py
    @PYTHONPATH=scripts {{py}} -m unittest scripts/test_review_packets.py
    @{{py}} -m py_compile scripts/discover_x.py scripts/render_review_packets.py scripts/render_agent_review_prompt.py scripts/auto_classify_noise.py
    @/bin/bash -n scripts/capture-x.sh scripts/notify.sh

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

# Ingest one X post: element-only screenshot + sidecar + register (browser bridge)
ingest-x url slug="" tag="" why="":
    @{{py}} scripts/ingest-x.py "{{url}}" {{ if slug != "" { "--id " + slug } else { "" } }} {{ if tag != "" { "--tag " + tag } else { "" } }} {{ if why != "" { "--why \"" + why + "\"" } else { "" } }}

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

# Local preview with hot reload
dev:
    @cd site && npx astro dev

# Build the public site (diffs and excerpts, full captures stay local)
build-site: test audit check-claims
    @node site/tools/stage-x-media.mjs
    @cd site && SITE_URL="${SITE_URL:-https://example.invalid}" npx astro build
    @node site/tools/check-public-output.mjs
    @node site/tools/check-trackers.mjs
    @node site/tools/check-links.mjs

# Build with complete snapshot bodies embedded. Local or gated use only.
build-site-full: test audit check-claims
    @node site/tools/stage-x-media.mjs
    @cd site && PUBLIC_FULL_TEXT=true SITE_URL="${SITE_URL:-https://example.invalid}" npx astro build

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

# Review build for pages.dev. Not indexable, and its sitemap advertises the
# pages.dev host rather than a domain that is not serving the site yet.
build-preview: test audit check-claims
    @node site/tools/stage-x-media.mjs
    @cd site && SITE_URL="https://${CF_PAGES_PROJECT:?set CF_PAGES_PROJECT}.pages.dev" npx astro build
    @SITE_URL="https://${CF_PAGES_PROJECT}.pages.dev" node site/tools/check-public-output.mjs
    @node site/tools/check-trackers.mjs
    @node site/tools/check-links.mjs

# Capture, audit, build and push a review copy to pages.dev.
preview: capture-gate audit build-preview deploy
    @echo "review copy live at https://${CF_PAGES_PROJECT}.pages.dev (noindex)"

# Audit, rebuild and publish for real. The 30-minute poll keeps the record
# fresh on its own, so there is no pre-publish capture here: every content
# gate (test, audit, check-claims, public-output, links) still runs via
# build-site-indexable, and a source that intermittently refuses this host
# cannot block a deploy. Only run this once the content is settled: unlike
# preview, the output invites indexing.
publish: audit build-site-indexable deploy

# Publish only if the tree is clean and the record has actually moved. This is
# what the publish-scheduled timer runs; --dry-run reports the decision only.
publish-scheduled *ARGS:
    @./scripts/publish-scheduled.sh {{ARGS}}

# The strict path: capture first and refuse to deploy an incomplete poll.
# Exit 10 (healthy changes) does not block; a source erroring does.
publish-fresh: capture-gate audit build-site-indexable deploy

# The public build, marked indexable. Separated from build-site so that
# nothing indexable is ever produced without asking for it by name.
build-site-indexable: test audit check-claims
    @node site/tools/stage-x-media.mjs
    @cd site && PUBLIC_INDEXABLE=true SITE_URL="${SITE_URL:?set SITE_URL}" npx astro build
    @node site/tools/check-public-output.mjs
    @node site/tools/check-trackers.mjs
    @node site/tools/check-links.mjs

# Push the built site to Cloudflare Pages by direct upload, so no source repo
# is ever exposed. Needs CF_PAGES_PROJECT set, and `npx wrangler login` once.
deploy:
    @cd site && npx wrangler pages deploy dist \
        --project-name "${CF_PAGES_PROJECT:?set CF_PAGES_PROJECT}" \
        --commit-dirty=true

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
agent-maintenance *ARGS:
    @./scripts/agent-maintenance.sh {{ARGS}}

# Recheck unverified claim markers once by hand (scripts/claim-sweep.sh).
# The 12-hour timer (scripts/claim-sweep.timer.example) runs the same script.
claim-sweep:
    @./scripts/claim-sweep.sh
