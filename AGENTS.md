# Agent instructions

The site is the public record of the July 2026 COLDCARD predictable-RNG
incident: it preserves what each party published and how it changed, organises
the material, and explains it without adjudicating between the people involved.

The scale already documented makes the incident likely to rank among the most
consequential in Bitcoin's history. Treat that as a provisional editorial
assessment, not a settled ranking: attribution totals differ and the number of
distinct people affected is unknown. A core historical purpose of the project
is to preserve the contemporaneous public record for posterity, including
material later edited or removed.

Organise explanations, opinions and speculation so a reader can understand how
public interpretation changed over time, including rapid shifts during the
first hours and days. Conspiracy theories alleging an inside job or that
law-enforcement or intelligence agencies caused or directed the incident belong
only as dated, attributed public reaction. Preserve their later retractions,
corrections and changes of view, and never present their inclusion as evidence
that a theory was true.

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
  hydrate_candidates.py  fetch candidate bodies so the agent needs no network
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
it is sound. The site does not classify a reader's wallet or publish its own
incident-response procedure. Published guidance remains in scope when it is
attributed and presented as part of the record.

Material claims and claim groups carry one evidence basis. This is the spine of
the site's impartiality, not decoration:

- **verified** checked against source code, a repo file, or a captured snapshot,
  with the artefact cited so a reader can recheck it
- **reported** someone said it. Attributed, dated, linked. Inclusion is not
  endorsement
- **derived** calculated from stated inputs, with the method shown so the
  arithmetic can be disputed on its merits
- **unverified** could not be confirmed. Say so rather than omitting it

Dispute state is separate from evidence basis. Add **contested** alongside the
basis when relevant sources disagree, so a claim can be both reported and
contested or verified and contested. Every marker must state exactly what it
applies to. Verified and reported markers must link to re-checkable evidence.
`just check-claims` enforces the marker contract and requires at least one scoped
marker on every editorial page.

**Report and attribute. Do not adjudicate.** Where Coinkite, Block and
independent researchers give different numbers, show all of them and explain what
each assumes. Do not pick a winner, and do not let a verdict column creep back
in.

Distinguish primary research from reporting on it. LLFOURN's cost model,
instagibbs' reproduction, Rob Hamilton's forensics and Galaxy's fee analysis are
primary work. An article about them is not.

Disclose conflicts plainly where they bear on advice. The Slipstream
recommendation traces to AnchorWatch people and to the tool's own author; that is
stated on the page without implying bad faith.

## The record leads; precision belongs deeper

The landing page orients a non-technical reader to the incident and shows the
record itself. It does not classify the reader's wallet or direct a course of
action. The landing hero and every page standfirst use plain words, little
jargon and rounded figures. Everything precise is one click away and should
stay there.

This has drifted twice, in both cases toward writing that is more accurate and
less useful:

- `594.47722484 BTC` is right on `/record/funds/`, where the arithmetic is
  the subject. On the hero it is noise. Use `sweptBtcApprox` and friends: the
  rounded forms live in `figures.ts` beside the exact ones for this reason
- "the STM32 hardware random-number peripheral", "a compile-time guard",
  "Mk4-class devices mixed in secure-element randomness but reduced 40 bytes to
  one 32-bit value" are all true and all belong in `/how-it-broke/`
- Listing the four evidence bases by name in the hero explains a system the
  reader has not met yet. Say claims are graded and linked; the badges teach
  themselves in context

The test: read it as somebody who does not write firmware. If a sentence sends
them to a search engine before orienting them to the subject, it belongs deeper
on the site rather than being wrong.

Depth is the point of the rest of the site. `/how-it-broke/` should be as exact
as the source allows, and the accounting pages should carry every decimal they
can defend.

## Conventions

- UTC everywhere, `YYYYMMDDTHHMMSSZ`, in filenames and JSON alike
- A snapshot is written only when the source's canonical comparison text
  changes. Source-specific normalizers suppress known presentation churn, but
  held extracted text and old snapshots are never rewritten
- Historical collection noise stays in the append-only archive. Classify it in
  `revision-reviews.toml`; never delete or rewrite the underlying capture
- `source-content` means relevant text served by the publisher changed. It does
  not verify the new claim. New differences remain `unreviewed` until classified
- `capture.py` exits **10** only for a healthy run with changes, **20** when any
  source errored, was blocked, or was skipped, and **21** on writer-lock
  contention. Do not repurpose exit 10
- `archive/` is append-only. Never rewrite or delete a snapshot: a wrong
  capture is part of the record. Correct by adding a later capture or a
  `revision-reviews.toml` entry. The one standing exception is redacting this
  project's own leaked personal data; the sidecar hashes still record what
  was held.

  This is a rule from 6 Aug 2026, not a description of the whole archive's
  history, and the difference is why it is worth stating carefully. The
  4 Aug 2026 reddit-json migration deleted 134 pre-migration captures of five
  Reddit sources, their diffs and their lines in `archive/index.jsonl`, and
  nothing recorded it at the time. That is logged at `/corrections/` and the
  material was deliberately not restored: it was duplicate rendered-page
  capture of threads still held in better form. **A migration is not a licence
  to delete history.** If a capture method changes, the old captures stay and
  the new ones are added beside them; if that is genuinely not wanted, it is
  an operator decision that gets written down before it happens, not after
  somebody notices
- Response headers are stored through an allowlist (`scripts/response_headers.py`).
  Most of a response's headers describe the path from the origin to this
  collector, and several name the CDN edge that answered, which is a
  place. Add a header to `KEEP` only if it describes the document itself;
  `just audit` refuses anything else, in the archive as well as in the
  built site
- Wayback-recovered captures carry `provenance: wayback`. Never present an
  inherited capture as one this project took
- A DNS failure on the capture host does not establish that a source is gone.
  Multiple clients on that host are not independent when they share its
  resolver. Before setting `gone = true` for a name-resolution failure,
  corroborate it through public DNS or a genuinely independent network path;
  otherwise keep polling and describe it as unreachable from this collector
- A published material claim that turns out to be wrong is fixed on the page
  where the claim was **and** appended to `corrections.toml`, which is
  published at `/corrections/`. Both, or neither counts: a log nobody reading
  the claim would see, and a quiet edit with no index, are each half a
  corrections policy. Rewording, restructuring and tooling work are not
  corrections and belong in `CHANGELOG.md`. A source editing its own page is
  not our error and belongs in `revision-reviews.toml`
- Every build stamps the commit it was made from, in the footer and at
  `/version.json`, because `/cite/` tells readers to quote it. Nothing about
  the version is hand-maintained; if you find yourself typing a version
  string, that is the bug
- Python runs through `.venv`. Never invoke system python here
- No em-dashes in prose. Commas, colons, parentheses or full stops

## Working on the site

```bash
just check-claims    # validate evidence basis, scope, provenance and coverage
just build-site      # SITE_URL and PUBLIC_CONTACT come from .env
```

`just build-site` runs the capture and scheduler regression tests, archive
contract audit and claim-marker gate before Astro, then checks generated public
files for operational details. It also generates `/llms.txt`, the source
register and the change feed from the same evidence model as the human pages.
The recurring schedule invokes one due-state runner every 30 minutes: a
systemd timer on the capture host (`scripts/archive-poll.service.example`).
Tier 1 and chain-monitor sources run every 30 minutes; tiers 2 and 3 run
every six hours. X, discovery, backup and deployment are not part of that
service.

**The Astro dev server does not work.** Vite fails with "Cannot split a chunk
that has already been edited (import.meta)". The production build is unaffected,
so local preview builds and serves `dist/`. Rebuild and restart the preview to
see changes. Fixing this properly is in the backlog.

**The community trackers' totals are read out of the archive, never typed.**
`site/src/lib/trackers.ts` pulls each chain monitor's headline figure from the
newest held snapshot whose reader still parses, and `/record/funds/` prints
which capture it came from, when the figure last moved and whether the source
is still answering. Do not put a tracker total back into a page as a literal:
it is right the day it is typed and silently wrong afterwards. When a tracker
rebuilds its page the reader stops matching and the reading degrades visibly,
first to an older capture (`lagging`), then to the pinned figure (`pinned`);
`site/tools/check-trackers.mjs` fails a build on `pinned`, so the fix is the
reader's anchor rather than the pin. A tracker that has stopped answering is
not a build failure: the page states it beside the figure, from this archive's
own poll record via `pollHealth()` in `lib/archive.ts`.

**Hand-built figures live in `site/src/components/figures/`.** Every diagram
on the site is an inline SVG component (a shared `Fig.astro` frame plus one
component per figure): they theme from the design tokens in both schemes,
carry `<title>`/`<desc>` text alternatives, and keep the visible `.cap`
caption inside the figure. A caption that carries a Claim marker goes in the
frame's `caption` slot. Any computed geometry belongs in the component
frontmatter, not in conditional template markup. Mermaid was removed
entirely; do not reintroduce it for a new diagram, draw the SVG.

**`.astro` template pitfalls, learned the hard way.** Conditionals returning
markup (`{x && <span/>}`, `{x ? <a/> : null}`) and string escapes in templates
break the expression parser, and the error is reported at a bogus location inside
the `<style>` block, which sends you hunting in the wrong file. Precompute
display data in the frontmatter and drive visibility with the `hidden` attribute.

**MDX is not usable for ported content.** It parses raw HTML as strict JSX and
the explainer markup is not JSX-safe. Ported pages inject HTML via `set:html`
and diagrams render client-side.

**Published builds show excerpts, not mirrors.** `PUBLIC_FULL_TEXT` defaults to
false: source pages show diffs, a 40-line excerpt and a link. Full captures stay
local, where they still back every claim. Snapshot hashes remain internal
capture and audit data; the public site does not present them as independent
proof of provenance or tamper resistance. Do not flip this on for a public
deploy.

## The capture host

There is one canonical working tree, on one machine, and capture runs there on
a schedule. Everything about where that machine is and how to reach it is
operational and stays out of this tracked file: see `AGENTS.local.md`
(gitignored) beside this file on a machine that has access.

Two long-running services are owned by the init system there. Never start
ad-hoc replacements for either; restart the unit instead.

- `webbridge.service`: the capture browser on 127.0.0.1:10086, which is
  `capture-browser/webbridge.py` in this repository, running headless Chromium
  from its own virtual environment. capture.py and ingest-x.py use it
  unmodified. It relaunches a crashed browser itself, systemd restarts it on
  failure and recycles it daily. Sessions and challenge cookies persist in
  `.capture-browser/profile`, which is gitignored. See
  `capture-browser/README.md` for the protocol and the setup a clone needs
- the site preview service: serves the built site from `site/dist`, for assessing changes before any deploy. To see changes: rebuild
  (`npx astro build` in `site/` with `.env` exported), the service picks up
  the new `dist` without a restart. The Astro dev server stays broken on the
  VM too; do not try it there
- `archive-poll.timer` -> `archive-poll.service`: the due-state capture
  runner, every 30 minutes, exit codes 10/20/21 treated as recorded outcomes
  rather than unit failures
- `discover-community.timer` -> `discover-community.service`: community-thread
  discovery and intake (Stacker News, Reddit and BitcoinTalk), every 12 hours.
  Two feed requests, two subreddit listing reads and two board indexes queue
  candidates in `DISCOVERY.md`,
  then the intake agent assesses them, registers relevant threads in
  `sources.toml` (and may correct existing `stackernews-*`/`reddit-*`
  entries), and first-captures each registration with `just capture-one`,
  deferring to the next poll on writer-lock contention
- `claim-sweep.timer` -> `claim-sweep.service`: the claim-verification sweep,
  every 12 hours. Runs `scripts/claim-sweep.sh` (CLAIM_SWEEP_AGENT_BIN, the
  same agent pattern as agent-review.sh), which rechecks unverified and
  date-bounded current-state claims, promotes what can now be evidenced
  (registering and first-capturing new sources), and refreshes recheck dates
  on the rest. Reports land in `.work/claim-sweep/`; a failed run does not
  advance the last-run marker

There is exactly ONE archive writer on the capture host: capture.py, driven
by the scheduled runner. Manual `just capture-one <id>` runs on the same host
(including the intake agent first-capturing its own registrations) use the
same writer and lock. Never run non-dry captures anywhere else. Which machine
that is, and its current service state, live in `AGENTS.local.md`.

## Capturing social posts

Two tools, and they are not interchangeable:

- `capture-x.sh` drives gallery-dl, which downloads **media only**. A post with
  no image or video reports "No results" and produces nothing. That is not a
  failure to fix; it is what the tool does
- `ingest-x.py` takes the **element-only screenshot** of the post itself plus a
  text sidecar. This is what a text-only post needs, and what the site displays

`capture-x.sh` reads its cookie source from `X_COOKIES_BROWSER`. `just
capture-x` loads `.env` and gets it; running the script directly does not, and
it will silently fall back to a browser profile that does not exist. Export it
or use the recipe.

Both write `archive/x/<post-id>/<TS>/` directly: `post.png` and `post.txt`
are the post, `attachment-N.<ext>` is media it carried, `meta.json` is the
fetching tool's own sidecar. The post id is looked up in the registry by
status id, so a capture always lands where the site reads it, and a
re-capture is a new directory beside the old one. That makes the append-only
rule a property of the layout rather than something a writer has to remember.

`just discover-x` is a separate pre-capture lane for the small `[[x_watch]]`
registry. It performs bounded official X API reads, queues new permalinks
in `DISCOVERY.md`, and writes no archive capture. Live reads require
`X_DISCOVERY_ENABLED=true`; first contact baselines instead of backfilling,
and account-health signals stop the whole run with a persistent cooldown. It
is manual during probation and must not be added to a timer until the gate in
`docs/design/discovery-and-x-watch.md` is satisfied.

## Capturing nostr posts

`ingest_nostr.py` captures one note plus its replies through `nak` (the second
sanctioned external binary beside gallery-dl, likewise confined to social
capture and posting) into `archive/nostr/<id>/<TS>/`: `event.json` is the
signed event, `event.txt` its flattened text, `replies.json` the fetched
replies where any exist, `meta.json` the sidecar. A re-capture is a new
timestamped directory, so the append-only rule is a property of the layout
here too. Signed events are self-authenticating, so none of the screenshot
provenance gating that X captures need applies.

Discovery is a separate manual-probation lane: `just discover-nostr` runs
bounded NIP-50 keyword searches against the configured search relays,
requires `NOSTR_DISCOVERY_ENABLED=true`, and queues njump permalinks in
`DISCOVERY.md`. Candidates are assessed by the standard community intake
agent: nostr reads are anonymous public-relay queries, so no separate triage
agent is needed. First capture of a registered note is
`just ingest-nostr <note1>`, never `just capture-one`: `[[nostr_post]]`
blocks are validated by capture.py but never polled. The lane must not go on
a timer until the gate in `docs/design/discovery-and-x-watch.md` is met.

The project posts from its own key: npub `npub1pfuvza2kkeqjqnp6l2tlqr2ewgx5ue0kc7rwztxvjr8p5wcec3zsrvp9w2`,
NIP-05 `_@cc-vuln.org` served at `/.well-known/nostr.json` from the site
build, the npub shown on `/cite/`. The nsec lives in `.env` as
`NOSTR_SECRET_KEY`, and posting is manual only via `just nostr-post`.
Operational detail is in `docs/operations.md`.

## Capturing Stacker News threads

Rendered stacker.news pages crash the capture tab, so every `stackernews-*`
source polls the public GraphQL API instead: `capture = "http"` with
`fetch_url`/`fetch_post` holding a fixed item query (title, text, two levels
of comments, author and absolute timestamp on each). A new thread is added by
copying that block shape and changing the item id; see the 4 Aug 2026 sweep
batch in `sources.toml`. Reddit threads are `capture = "reddit-json"`:
the thread JSON is read through the capture browser's signed-in session
(anonymous JSON from this host gets a 403 challenge) and flattened to a
deterministic canonical text, so no normalizer binding is needed.

## Community-thread discovery and intake

Discovery is separate from capture: Stacker News keyword search does not
index recent items, and Reddit has no usable anonymous search from this host.
Three scheduled community scripts and the separate manual X watcher feed one
intake queue:

- `just discover-stackernews` reads the ~bitcoin and ~security recent feeds
  (two requests per run, 1.5s apart) and queues title-matched candidates
- `just discover-reddit` reads the r/coldcard and r/Bitcoin /new listings
  through the capture browser session (two reads per run) and queues
  keyword-matched threads; r/coldcard is low-volume and on-topic since the
  incident, so every new post there is queued
- `just discover-bitcointalk` reads the Bitcoin Discussion and Wallet
  software board indexes directly (SMF answers this host; two pages per run)
  and queues keyword-matched topics. Registered threads capture the print
  view (`action=printpage`), the whole thread as stable text; the `;all`
  view is Cloudflare-challenged from this host and board pages carry live
  user counters
- `just discover-x` reads shallow public user timelines for the curated
  `[[x_watch]]` registry through the official read-only X API. It is opt-in
  and manual during probation, baselines first contact and fails closed on
  API-health signals. Do not put it in `discover-community.service` yet

Candidates land in `DISCOVERY.md`, the tracked intake file at the repo root.
On the capture host `discover-community.timer` runs the three community
discoveries every 12 hours, chained with the intake agent
(`agent-discovery-intake.sh`, the same
REVIEW_AGENT_BIN pattern as agent-review.sh), which assesses each pending
candidate (bounded chunks of 15 per run while a backlog exists), appends
registrations to `sources.toml` in the established shapes, may correct
existing `stackernews-*`/`reddit-*` entries with the reason in its report,
first-captures each community registration via `just capture-one` (exit 10 is
healthy; exit 21 defers to the next poll), and records every verdict in
`DISCOVERY.md`. With REVIEW_AGENT_BIN unset, candidates wait in
`DISCOVERY.md` for human triage. stacker.news serves no robots.txt, so there
is no published crawl policy; keep discovery at this volume unless the
operators have been asked. The recurring community service neither runs
`discover-x` nor sends queued X links to its general intake agent. During X
probation an operator may run `just discovery-intake --include-x` to authorize
a bounded X-only assessment through the separately configured
`X_REVIEW_AGENT_BIN`. That prompt may only add recommendation or dismissal
verdicts to `DISCOVERY.md`; it cannot capture or register a post. Direct manual
`just ingest-x` capture is unchanged and does not pass through this queue.
Human X promotion remains separate. Full polls remain the scheduled runner's
alone.


## Publishing screenshots: provenance, never inspection

A screenshot taken in a signed-in browser carries that session. On a
whole-window capture the account name sits in the site's own navigation. On an
**element-only** capture the account's avatar still appears in the reply row
under the post, and no image measurement (width, aspect ratio) detects it
reliably: a profile picture is a few hundred pixels and moves with the
layout.

So the rule is when a capture was taken, not what it looks like.
`stage-x-media.mjs` publishes only captures from the dedicated capture
profile, gated on `OWN_HOST_FROM`, and reports how many posts it withheld.
Two things follow:

- Do not add an image-inspection heuristic and call a capture cleared
- A capture directory that is not a timestamp (`undated`) must be rejected
  explicitly. String comparison will not do it: `"undated" < "20260802..."` is
  false, because letters sort after digits

Anything that could reproduce captured text asks `withholdsCapturedText()` in
`lib/archive.ts`, including diffs and excerpts. Two added lines of a withheld
thread are still two lines of it. Keep that rule in one function: copies of a
policy drift, and the copy that withholds nothing is the one that leaks.

## Building and deploying

- **Serialise on `flock /tmp/cc-build.lock`.** More than one build at a time
  corrupts the Astro cache and the symptom is misleading: `Cannot find module
  renderers.mjs`, a `dist/` with server chunks and no HTML, and a public-output
  gate that fails on `/home/` paths inside them. Recovery is
  `rm -rf site/dist .astro node_modules/.astro node_modules/.vite` and rebuild
- The deploy token is scoped to Cloudflare Pages only. It **cannot purge the
  cache**, so unpublishing a file needs a dashboard purge or a wait for
  `max-age`
- Past Pages deployments stay reachable at their own subdomains after a new
  one goes live. Removing something from the site means deleting those
  deployments too, and `wrangler pages deployment delete` needs `--force`;
  without it, it prints usage and exits as though nothing were wrong
- `just publish` runs every content gate but no pre-publish capture: the
  30-minute poll keeps the record fresh, and a source that intermittently
  refuses this host must not block a deploy. `just publish-fresh` is the
  strict path: it captures first and refuses to deploy an incomplete poll

## The unattended agents are contained, not trusted

Four agents run here without anyone watching, and all four read text that
strangers wrote. Assume the prompt injection in a captured thread works. The
full reasoning and the residual gaps are in
`docs/design/agent-sandbox.md`; the rules that bind new work are short.

- **An agent never fetches its own evidence.** The driver hydrates first, as
  the operator account, and the agent receives text. `render_review_packets.py`
  and `hydrate_candidates.py` are the two ends of the same pattern. Adding a
  fetch instruction to a prompt undoes it
- **An agent never writes `archive/`.** It appends a source id to
  `.work/capture-requests.txt` and the driver captures afterwards, only for
  ids the guard confirmed this run registered. `capture.py` stays the one
  writer, and a poisoned source is refused before anything fetches it
- **An agent never holds a secret.** `run-agent.sh` builds its environment
  from an allowlist and it runs as `cc-agent`, which cannot read `.env`,
  `AGENTS.local.md` or `.capture-browser/`. Do not restore `set -a; source
  .env` in a driver, and do not pass a credential through as an argument
- **An agent never reaches the capture browser.** Its `evaluate` and `cdp`
  actions are arbitrary JavaScript and raw DevTools inside a signed-in
  session. Two units deny loopback; the third cannot, so the browser's token
  in `.capture-browser/` separates them there
- **Every run is checked afterwards, and a rejected run is left alone.**
  `agent_guard.py` enforces the role's path allowlist, scans for secret
  values, and hands `check_registry.py` the registry delta. Nothing is
  reverted: what an injected run tried to do is the evidence, and a dirty
  tree outside `archive/` already stops the scheduled publish
- **A new host is a human edit, twice over.** `registry_hosts.toml` says what
  `sources.toml` may name, and it plus `agent_egress_hosts.toml` say what an
  agent may connect to at all: `scripts/agent_proxy.py` refuses every other
  CONNECT and an nftables rule on the agent's uid drops every route that
  bypasses it. Both files live in `scripts/`, which is read-only to the
  agent, so a run cannot widen its own reach. Add a host in the same commit
  as the source needing it, say why, and restart `agent-proxy`
- **An agent may reach its model provider, and may read what the registry may
  name.** That is the whole egress policy. It works because hydration moved
  to the driver, so only the sweep reads the open web, and because anything
  it reads has to be registerable to be worth reading. Do not add a second
  list

Prompt wording is the weakest layer and is kept anyway, in one copy at
`scripts/agent-prompt-rules.md`, injected into all four prompts. Untrusted
material is fenced with a per-run nonce; do not wrap it in a markdown fence
instead, because content closes those.

## Exit codes and locks

`capture.py` exits **21** on writer-lock contention, and `just audit` will hit
it when the 30 minute timer is mid-run. Retry rather than diagnose. For a long
capture run, stop the timer first (`sudo systemctl stop archive-poll.timer`)
and start it again afterwards.

For any work that edits `scripts/capture.py` or otherwise changes what a
capture would record, run it through `just agent-maintenance <command>`: the
wrapper pauses `archive-poll.timer` and `archive-review.timer`, waits for any
in-flight poll to finish, runs the command, and always restarts both timers
afterwards, even on failure. A capture taken while `capture.py` is mid-edit
can record a change hash no shipped code reproduces, which leaves
`just audit` permanently red; the quiet window exists so that cannot happen.

`just audit` also runs the review gate (`scripts/check_reviews.py`), which
fails while any detected difference lacks a classification in
`revision-reviews.toml`, because the public changes page renders unreviewed
differences as placeholders and those must never ship. Between a poll and
the two-hourly review timer a window of unreviewed diffs is normal; if the
gate blocks a build or deploy, run `sudo systemctl start
archive-review.service`, wait for it to finish, and retry. Classify by hand
when the agent is wrong; never delete the underlying diff.

## Do not

- Do not enable `NOTIFY=relay` without following the steps in
  `docs/operations.md`. It edits
  the route config of an internal notification relay (host set via
  `NOTIFY_SSH_HOST` in `.env`) and needs a restart
- Do not make `capture-x.sh` do anything other than read. No posting, following
  or liking from a borrowed session
- Do not name the blockchain-services provider Block traced the operator to. It
  is unnamed at its own request and Block found no evidence it participated
- Material its author published is publishable here, including first-hand
  accounts and the address sets chain monitors enumerate (3 Aug 2026
  decision). It stays in the record: this project does not undertake to
  withdraw published material on its author's request (6 Aug 2026 decision,
  superseding the removal undertaking). What still comes down is material
  that was never public, personal data, and anything this project got wrong,
  which is a correction. A complaint with a legal basis is not an author
  preference: assess it and act where it is good, and never cite this bullet
  as a reason to ignore one. A source that must be held back sets
  `withhold_text = true` in `sources.toml`, which keeps its bodies off the
  site and out of any commit; none does today
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
