# Agent instructions

The site is the public record of the July 2026 COLDCARD predictable-RNG
incident: it preserves what each party published and how it changed, organises
the material, and explains it without adjudicating between the people involved.

Read `BACKLOG.md` before starting work. It is the current state of what is
missing, wrong, or blocked.

## Layout

```
sources.toml          the single source registry. Adding a source is one block.
revision-reviews.toml additive classification of detected differences
corrections.toml      this project's own corrections, published at /corrections/
CITATION.cff          how to cite the repository; /cite/ is the fuller guidance
scripts/
  archive_lock.py     one advisory lock shared by every archive writer
  capture.py          poll, extract text, hash, diff, log       (stdlib only)
  wayback.py          recover pre-capture history from the Internet Archive
  capture-x.sh        manual X capture via gallery-dl + browser cookies
  ingest-x.py         one-post capture through the capture browser
  notify.sh           capture, alert on change or incomplete poll
  scheduled_runner.py due-state runner for recurring known-URL capture
  check_publishable.py, check_reviews.py   audit gates run by `just audit`
  discovery_common.py   keyword sieve, seen state and the DISCOVERY.md queue,
                        shared by every discovery lane below
  rotate_discovery.py   age old verdicts out of DISCOVERY.md into
                        discovery/assessed-YYYY-MM.md, verbatim and locked
  discover_stackernews.py  find new incident threads on Stacker News (gentle)
  discover_reddit.py    find new incident threads on Reddit (via capture browser)
  discover_bitcointalk.py  find new incident threads on BitcoinTalk (direct)
  discover_x.py         find new posts from watched X accounts (manual probation)
  ingest_nostr.py       capture one nostr note and its replies via nak
  discover_nostr.py     find new incident notes via NIP-50 search (manual probation)
  nostr_post.py         manual kind-1 post from the project key (--yes)
  nostr_publish_profile.py  publish the kind-0 profile and kind-10002 relay list
  agent-discovery-intake.sh  community intake and explicit X triage agent
  agent-x-discovery-triage-prompt.md  read-only X recommendation prompt
  derive_funds_evidence.py  reproduce the pinned funds-accounting inputs
  verify_mk3_vector.py      check the fixed synthetic Mk3 test vector
  build_manifest.py   describe every held capture without reproducing any
  make_deposit.py     stage the archival deposit; stages and reports, never uploads
  agent-review.sh     classify new diffs via REVIEW_AGENT_BIN (optional)
  agent-maintenance.sh  run agent work with the capture timers paused
  agent-run-common.sh   the containment every agent driver shares
  run-agent.sh        run one agent deprivileged, with a built environment
  agent-prompt-rules.md  standing rules injected into all four prompts
  agent_guard.py      check what an agent run did; refuse if it overreached
  check_registry.py   registry host, fetch_post and no-mutation rules
  registry_hosts.toml hosts sources.toml may name; a human edit
  quarantine_registry.py  move a rejected registration out of the live
                        registry, so an invalid one never stops the tree
  hydrate_candidates.py  fetch candidate bodies so the agent needs no network
  build_coverage_index.py  what the record already covers, with a saturation
                        count per entry read out of past verdicts
  render_agent_prompt.py  join a trusted template to fenced untrusted evidence
  agent-permissions.sh   apply and re-check the agent sandbox's file modes
  *.{service,timer}.example  systemd units for recurring capture and review
archive/
  snapshots/<id>/<TS>.{html,txt,meta.json}
  diffs/<id>/<TS>.diff
  index.jsonl         append-only: every poll, changed or not
  runs/<TS>-p<PID>.json  structured result for every non-dry capture
  nostr/<id>/<TS>/      one note capture: event.json/txt, replies.json, meta.json
docs/
  README.md           document index and placement rules
  design/             future-facing technical designs
  research/           open research work packages
discovery/
  assessed-YYYY-MM.md  intake verdicts rotated out of DISCOVERY.md, verbatim
quarantine/
  registry-YYYY-MM.toml  registrations moved out of sources.toml verbatim,
                      with the reason and the run. Never polled, never read
                      back; restoring one is a human edit
site/                 Astro front end, reads archive/ at build time
  /llms.txt           generated machine orientation and citation guidance
  /record/*.json      generated source register and change feed
  /cite/              how to cite the record, and what a citation asserts
  /corrections/       this project's own corrections, from corrections.toml
  /version.json       the commit a build was made from, and the record's size
```

Keep only durable project knowledge in `docs/`. Put temporary audits,
screenshots and generated review evidence under `.work/`, which is ignored.
Never commit assessment output.

## The two halves have different lifespans

`scripts/` and `archive/` must still work in ten years: stdlib-only Python, no
dependency tree to rot, with `gallery-dl` and `nak` the two exceptions, both
confined to social capture and posting. The site is a presentation layer and is
rebuildable, so Astro and npm live entirely under `site/`. Do not let site
dependencies leak outward.

## Epistemic model

The belonging test for site content is strict: a page belongs if it preserves
incident material, organises that material, or explains the incident using
preserved material. General bitcoin-security guidance fails this test even when
it is sound.

Material claims and claim groups carry one evidence basis:

- **verified** checked against source code, a repo file, or a captured snapshot
- **reported** someone said it. Attributed, dated, linked. Inclusion is not endorsement
- **derived** calculated from stated inputs, with the method shown
- **unverified** could not be confirmed. Say so rather than omitting it

Dispute state is separate: add **contested** alongside the basis when relevant
sources disagree. Every marker must state exactly what it applies to.
`just check-claims` enforces the marker contract.

**Report and attribute. Do not adjudicate.** Where parties give different
numbers, show all of them and explain what each assumes. Do not pick a winner.

## The record leads; precision belongs deeper

The landing page orients a non-technical reader to the incident and shows the
record itself. It does not classify the reader's wallet or direct a course of
action. Use plain words, little jargon and rounded figures. Everything precise
is one click away.

## Conventions

- `sources.toml` is the single source registry. Adding a source is one block.
- `archive/` is append-only: every poll, changed or not, appends to
  `index.jsonl`. A re-capture is a new timestamped directory; nothing is ever
  overwritten. This is a rule from 6 Aug 2026, not a description of the whole
  archive's history: the 4 Aug reddit-json migration deleted 134 pre-migration
  captures, their diffs and their `index.jsonl` lines, and nothing recorded it
  at the time. That is logged at `/corrections/`, and `/cite/` states the date
  the rule starts. **A migration is not a licence to delete history.** If a
  capture method changes, the old captures stay and the new ones are added
  beside them; if that is genuinely not wanted, it is an operator decision
  written down before it happens, not after somebody notices.
- `revision-reviews.toml` is additive classification of detected differences.
  Deleting a review does not delete the diff; it just removes the
  classification.
- `corrections.toml` is this project's own corrections, published at
  `/corrections/`. A published material claim that turns out to be wrong is
  fixed on the page where the claim was **and** appended here. Both, or neither
  counts: a log nobody reading the claim would see, and a quiet edit with no
  index, are each half a corrections policy. Rewording, restructuring and
  tooling work are not corrections and belong in `CHANGELOG.md`; a source
  editing its own page is not our error and belongs in `revision-reviews.toml`.
- A DNS failure on the capture host does not establish that a source is gone.
  Multiple clients on that host are not independent when they share its
  resolver. Before setting `gone = true` for a name-resolution failure,
  corroborate through public DNS or a genuinely independent network path;
  otherwise keep polling and describe it as unreachable from this collector.
  This rule exists because the project got it wrong once and had to correct it.
- `DISCOVERY.md` is the tracked intake queue for community-thread discovery.
  Verdicts older than a few weeks rotate to `discovery/assessed-YYYY-MM.md`
  via `just rotate-discovery`; the rotated files are project records, not
  captures, so they live beside the queue rather than under `archive/`.

## Working on the site

```bash
just check-claims    # validate evidence basis, scope, provenance and coverage
just build-site      # SITE_URL and PUBLIC_CONTACT come from .env
```

**The Astro dev server does not work.** Vite fails with "Cannot split a chunk
that has already been edited (import.meta)". The production build is unaffected,
so local preview builds and serves `dist/`. Rebuild and restart the preview to
see changes.

**The community trackers' totals are read out of the archive, never typed.**
`site/src/lib/trackers.ts` pulls each chain monitor's headline figure from the
newest held snapshot whose reader still parses. Do not put a tracker total back
into a page as a literal.

**Hand-built figures live in `site/src/components/figures/`.** Every diagram
on the site is an inline SVG component. Mermaid was removed entirely; do not
reintroduce it.

**`.astro` template pitfalls.** Conditionals returning markup (`{x && <span/>}`)
and string escapes in templates break the expression parser, and the error is
reported at a bogus location inside the `<style>` block. Precompute display data
in the frontmatter and drive visibility with the `hidden` attribute.

**MDX is not usable for ported content.** It parses raw HTML as strict JSX and
the explainer markup is not JSX-safe. Ported pages inject HTML via `set:html`.

**Published builds show excerpts, not mirrors.** `PUBLIC_FULL_TEXT` defaults to
false: source pages show diffs, a 40-line excerpt and a link. Full captures stay
local. Do not flip this on for a public deploy.

## Building and deploying

- **Serialise on `flock /tmp/cc-build.lock`.** More than one build at a time
corrupts the Astro cache. Recovery is `rm -rf site/dist .astro node_modules/.astro node_modules/.vite` and rebuild.
- `just publish` runs every content gate but no pre-publish capture.
- `just publish-fresh` captures first and refuses to deploy an incomplete poll.

## The unattended agents are contained, not trusted

Four agents run here without anyone watching, and all four read text that
strangers wrote. Assume the prompt injection in a captured thread works.

- **An agent never fetches its own evidence.** The driver hydrates first, as
the operator account, and the agent receives text.
- **An agent never writes `archive/`.** It appends a source id to
`.work/capture-requests.txt` and the driver captures afterwards.
- **An agent never holds a secret.** `run-agent.sh` builds its environment
from an allowlist and it runs as `cc-agent`, which cannot read `.env`,
`AGENTS.local.md` or `.capture-browser/`.
- **An agent never reaches the capture browser.** Its `evaluate` and `cdp`
actions are arbitrary JavaScript and raw DevTools inside a signed-in session.
- **Every run is checked afterwards.** `agent_guard.py` enforces the role's
path allowlist and scans for secret values.
- **A new host is a human edit, twice over.** `registry_hosts.toml` says what
`sources.toml` may name, and it plus `agent_egress_hosts.toml` say what an
agent may connect to at all.

## Exit codes and locks

`capture.py` exits **21** on writer-lock contention, and `just audit` will hit
it when the 30 minute timer is mid-run. Retry rather than diagnose.

For any work that edits `scripts/capture.py` or otherwise changes what a capture
would record, run it through `just agent-maintenance <command>`: the wrapper
pauses `archive-poll.timer` and `archive-review.timer`, waits for any in-flight
poll to finish, runs the command, and always restarts both timers afterwards.

`just audit` also runs the review gate (`scripts/check_reviews.py`), which
fails while any detected difference lacks a classification in
`revision-reviews.toml`. Between a poll and the two-hourly review timer a window
of unreviewed diffs is normal; classify by hand when the agent is wrong.

## Do not

- Do not enable `NOTIFY=relay` without following the steps in
  `docs/operations.md`
- Do not make `capture-x.sh` do anything other than read. No posting, following
  or liking from a borrowed session
- Do not name the blockchain-services provider Block traced the operator to.
  It is unnamed at its own request
- Material its author published is publishable here, including first-hand
  accounts and the address sets chain monitors enumerate. It stays in the
  record: this project does not undertake to withdraw published material on its
  author's request. What still comes down is material that was never public,
  personal data, and anything this project got wrong, which is a correction.
  A source that must be held back sets `withhold_text = true` in `sources.toml`
- Do not add a source that forbids it in robots.txt without checking first

## Testing a change

`just test-capture` exercises registry validation, normalizers, exit precedence
and capture shell syntax. `just test-scheduler` exercises due-state, source
ownership, failure handling, aggregation and scheduler locking. `just dry-run`
polls without writing. `just capture-one <id>` exercises one source end to end.
After changing text extraction, repeat a dry run and confirm existing sources
report `same` rather than a spurious change: a false positive there silently
corrupts the change record, which is the one thing this repo exists to get
right.

---

For capture methods and social-capture procedures, see `docs/capture.md`.  
For community discovery and intake procedures, see `docs/DISCOVERY.md`.  
For agent sandbox design, see `docs/design/agent-sandbox.md`.  
For operations (host setup, services, nostr), see `docs/operations.md`.
