# Changelog

Sections are dated by the day the change shipped, UTC. There are no release
numbers: the citable identifier is the commit each build was made from,
published in the footer and at `/version.json`.

## 2026-08-12

### Tooling

- Fixed the scheduled publish's pre-build media-index staging, which ran
  with a bare systemd environment and never saw `PUBLIC_X_MEDIA` from `.env`:
  it committed empty manifests, the build regenerated the full ones, and
  `check-version-exact` refused the deploy. Staging now runs through
  `just stage-x-media`, the same dotenv path the build uses.
- New `guard-run-silent` alert: an agent run that dies between its edits and
  its guard verdict blocks record-commit by design, but nothing said so;
  the alert sweep now names such a run (pass verdict and rejection alert both
  absent, driver pid gone, quiet for over three hours).
- A registered X or nostr post with no capture on disk is now warned about
  by the registry audit and listed in `just status`; two such posts (their
  registering run died mid-finish on 9 Aug) were captured by hand.
- X browser discovery fails closed when a full run parses zero posts across
  the home timeline and every watched profile: pages can render empty while
  the structural session probe reads "ok", which hid the forming login wall
  for a day and a half before the 11 Aug cooldown.
- The X intake skips its agent run while a browser cooldown is active
  instead of assessing candidates whose bodies cannot hydrate.
- record-commit pushes after every commit; pushing no longer waits for a
  successful deploy, so publish skips cannot strand the off-machine copy.
- archive-poll.timer is pinned to fixed clock times (:23/:53) instead of a
  boot-relative interval that reshuffled the pipeline cadence on every
  reboot; x-media's weekly pull gained a same-day retry slot.
- Operator-supplied candidate URLs (one per line in
  `.work/operator-candidates.txt`) are queued into DISCOVERY.md's Pending by
  both intake drivers via the new `scripts/queue_candidates.py`.

## 2026-08-09

### Site

- The historical funds-movement comparison now reads its MOVED totals and
  still-held percentages from the exact two archive snapshots named in the
  prose, failing the build if either labelled field cannot be read. The attack-
  cost register now imports every repeated candidate-space label and published
  input from `entropy-models.ts`, including the 7 August community bound,
  instead of maintaining a second hand-typed set.
- The public-record boundary was tightened after an editorial review. The
  landing page and machine orientation no longer rank the incident against
  other Bitcoin events. The technical explainer no longer publishes this
  project's post-merge libngu derivation, and the legal page no longer selects
  or applies Ontario consumer statutes; it remains a register of published
  terms, statements, claimant organising, legal opinions and record-retention
  notices. Interpretive language that appeared to decide between competing
  disclosure accounts was made descriptive. A new `/methods/` page states the
  collection surfaces, intake criteria, archival process, prominence model,
  reproducibility and known coverage limits for journalists and researchers.
  These removals are scope changes, not corrections. The durable boundary is recorded in
  `docs/design/public-record-editorial-boundary.md`.
- The repository's proposed STM32 device-identifier population study was
  removed with its backlog item. It asked this project to gather new physical-
  device evidence and publish a new finding, which is outside the public-record
  mission. The record retains the published models and their attributed limits.
- The human change log now shows the 240 newest reviewed differences rather
  than rendering the complete unbounded history into one page. The complete
  machine-readable change feed remains available alongside it.
- The conditions explainer now reflects the held 8 August CKTRIPWIRE state:
  two low-dice honeypots had been swept, while no passphrase-hardened case had
  been swept. The record presents those observations without generalising from
  the small, self-selected set.

### Archive and automation

- Hacker News API captures gained a deterministic readable-thread extractor,
  preserving story and comment identity, authorship, timestamps, parentage and
  text while ignoring volatile score and option fields. The two registered HN
  threads were recaptured once under the new representation; both subsequent
  dry runs were unchanged.
- Nineteen high-signal gaps were registered and first-captured: Ledger's
  incident article, open-source position and complete FAQ; three independent
  reporting/chronology pages; CoinDesk's third-wave report; six early Reddit
  discussions; and six primary X responses including the Bitcoin Security
  Consortium, AnchorWatch and Blockchain Unmasked. Twenty-one distinct
  Internet Archive states recover the pre-registration history of the
  CoinDesk, Ledger, BleepingComputer and Web3 Is Going Great pages.
- Browser capture can now select a single component, cross declared shadow-
  root boundaries and extract CSS-hidden text. Ledger's Salesforce FAQ uses
  this mode because its six answers exist only inside a collapsed nested
  component; completeness markers reject the visible question-only shell. A
  repeat dry run matched the 6,237-character first capture.
- Article-boundary normalizers now keep the editorial text of the recovered
  CoinDesk, BleepingComputer and The Block reports while excluding their live
  tickers, localized share controls, rotating news rails and ads. Across the
  held history, nine CoinDesk states and eleven BleepingComputer states each
  reduce to one unchanged article; The Block's ten states reduce to its article
  plus the preserved Cloudflare challenge, so access failures remain visible.
- Internet Archive replays now pass through the same collector-geolocation
  body scrub as direct captures before extraction, hashing or storage, and
  record the scrub count in their sidecars. Eight newly recovered CoinDesk
  replay files were sanitized before publication; their publisher text and
  citable text hashes did not change.
- X watched-profile discovery now supports bounded multi-pass history reads,
  can deliberately reconsider legacy seen state while still excluding
  registered posts, and never marks overflowed posts seen. A controlled
  recovery pass covered all 35 watched profiles and queued 896 candidates
  skipped by the old first-contact baseline; the two profiles that first hit
  the history cap were completed in a 40-pass follow-up. Scheduled X intake
  can drain up to eight separately
  rendered and guarded 15-item batches per tick, while stopping immediately
  when no queue progress is possible.
- Site prose sync now consumes the additive review ledger through an explicit
  offset. Packet generation cannot advance it, bounded batches advance only
  through the last review they consumed, and failed or unconfigured runs leave
  the cursor unchanged for retry. The uncited-source inventory remains useful
  for research but no longer creates editorial work by itself.
- Discovery persistence now writes the public queue before its raw log and
  checkpoints seen state atomically only after both durable records succeed.
  The Reddit, Stacker News and BitcoinTalk lanes can deliberately replay their
  currently visible windows without bypassing registered/assessed deduplication.
  Like X intake, scheduled community intake can drain eight separately
  rendered and guarded 15-item batches per tick and stops on no progress.
- Agent egress is now provider-only. The claim sweep no longer fetches live
  evidence, direct agent DNS is blocked, and both the proxy and host-vetting
  path reject non-global DNS answers and pin connections to validated public
  addresses. The tracked source-host admission mirror is no longer live proxy
  authority.
- Revision reviews now implement their documented additive contract: a later
  human entry may supersede a machine classification for the same diff, and
  readers consistently use the latest entry. Machine classifier names are
  validated.
- The build-time archive data layer now parses the append-only poll log once
  and caches immutable snapshot, revision and social-capture projections for
  the duration of the serialized build. The record register and JSON source
  feed also group revisions once instead of rescanning the complete review set
  for every source, removing quadratic work as the archive grows. On the same
  host, a 993-page build fell from roughly five minutes to under 20 seconds.
- Publication now fails before upload unless the built `/version.json`,
  current `HEAD` and tracked tree identify the same clean commit. The hourly
  committer shares the build lock, scheduled publication refuses uncommitted
  archive churn as well as editorial dirt, and its pre-build generated-index
  commit is included in the state stamp. This closes both commit/build races
  observed by the first unattended deploy.
- X conversation records are now explicit in the machine-readable source
  register: each X item declares whether it is a single post, conversation
  head or member, and captured conversations expose copy, post, reply and gap
  counts. `/llms.txt` reports the conversation total, completing the existing
  capture, per-status withholding and bounded thread-reader path.
- The source-register JSON now publishes corpus coverage denominators and
  stable breakdowns by kind, organisation, current poll state, capture-count
  band and publication/capture date range, giving research users a citable
  inventory without deriving its denominator from presentation cards.

## 2026-08-08

### Site

- The site's focus tightened to capture, archive and present the public
  discourse, and the pages were re-grounded to match: this project's own
  original research — independent firmware verification, unpublished
  findings, derivations beyond arithmetic on stated inputs — is out of
  scope. Fourteen pages changed, about 1,400 lines removed. The blast-radius
  register's own-verified call-site review, the developer page's commit-history
  grading, the reference page's fork comparison and seed-checker watch, and
  the conditions page's self-derived arithmetic left the record; claims they
  carried are now reported from the captured publications that state them
  (Block's disclosure, Coinkite's backgrounder, Wizardsardine's post-mortem,
  Dettmer's walkthrough), with two attributed answers shown where the parties
  disagree, such as the v4.0.0/v4.0.1 boundary. Two findings no captured
  source states (the MuSig2 and microSD-2FA-nonce omissions) leave the record
  unless another party publishes them. The one exception is the screen-hazard
  callout on `/how-it-broke/conditions/`, kept verified because it is
  safety-relevant. What was retracted was out of scope, not wrong, so this is
  a changelog entry, not a correction. The basis mix moved accordingly:
  369 claim markers now read 67 verified, 265 reported, 6 derived, 31
  unverified.

### Process

- The operator directed full automation of the pipeline: guard-passed agent
  output is to be committed, published and pushed by scheduled scripts, with
  human review retroactive rather than a gate. Policy amendments recording
  the decision live in AGENTS.md ("The pipeline runs unattended (8 Aug
  2026)"), `docs/design/discovery-and-x-watch.md` (the 5 Aug official-API
  policy for X is reversed; discovery moves to the capture browser and
  `discover_x.py` is deprecated), `docs/operations.md`, `docs/DISCOVERY.md`
  and `docs/capture.md`. Failure delivery, long blocked, is resolved by an
  alert stream the operator UI renders. New-host admission moves from a human
  edit to driver-side vetting with an alert per admission.
- Prerequisite correctness and guard work landed with it: first-capture
  dispatch routes `[[nostr_post]]` ids to `just ingest-nostr` instead of the
  unresolvable `just capture-one`; the guard now enforces append-only files
  as a verbatim-prefix check rather than a prompt instruction;
  `check_reviews.py` validates the shape of every `[[revision]]` entry;
  `check_registry.py` refuses placeholder `why`/`note` prose on new or
  rewritten blocks (11 legacy short `why` fields are warned, not failed);
  `auto_classify_noise.py` gained a deterministic X-thread churn lane
  validated against the held thread diffs; `just status` surfaces
  quarantined registrations, host proposals, failure streaks, capture
  failures and the unreviewed count; manual Astro builds now queue on
  `flock /tmp/cc-build.lock`.
- The commit-publish-push-alert loop now runs unattended.
  `scripts/record_commit.py` (hourly timer) commits guard-passed pipeline
  output from a fixed staging allowlist, refusing on a non-main `HEAD`, a
  `.no-publish` file, a red `just test` or `just audit`, a held writer lock,
  or an unresolved agent-guard run — a run directory without
  `approved-captures.txt` was rejected, is in flight, or died mid-run, and
  all three block the commit, because committing a rejected run's edits
  would launder an injection into the record. `publish-scheduled.timer`
  (three-hourly) is installed and pushes after each successful deploy, so
  the commit stamped into `/version.json` always resolves on GitHub.
  `scripts/alert.py` is the single alert writer: one JSON line per alert,
  appended idempotently to
  `~/.local/state/coldcard-archive/alerts.jsonl`, which the operator UI
  (`~/coldcard-operator-ui`, a separate read-only repo) renders at
  `/alerts`; `alert-sweep.timer` (30 minutes) turns the repo's existing
  state files — failing units, stale host proposals, quarantined
  registrations, failure streaks — into alerts, and failure delivery is no
  longer blocked. `urgent` is reserved for guard rejections, gate failures
  and the X session-health login wall.
- The X lane runs through the capture browser, driver-side only.
  `scripts/discover_x_browser.py` (12-hourly `discover-x.timer`, kill switch
  `X_BROWSER_DISCOVERY_ENABLED`) reads the home timeline and watched
  profiles and queues permalinks in `DISCOVERY.md`; the API lane
  (`discover_x.py`) is deprecated and no bearer credential is used.
  Promotion is automated: the registering `xintake` guard role
  (`agent-x-intake.sh`) assesses queued candidates under the same
  containment as community intake, and the driver first-captures each
  approved post with `just ingest-x` afterwards — the agent never reaches
  the browser. `check_x_availability.py` (12-hourly
  `x-availability.timer`, kill switch `X_BROWSER_AVAILABILITY_ENABLED`)
  re-checks that registered posts are still observable: a single absence is
  recorded and alerted at info, and only two consecutive observations from
  separate runs escalate, because absence is not deletion. A weekly
  `x-media.timer` pulls registered posts' media with
  `capture-x.sh --skip-unchanged`, which is what makes the gallery-dl pull
  schedulable. The session-health classes — login wall, challenge, rate
  limit — fail every lane closed and share a 24-hour cooldown. The operator
  accepted X's automation-rule suspension risk for the signed-in account in
  writing; the lane remains read-only: no posting, following or liking.
- The editorial lanes keep the published prose in step with the record.
  `scripts/report_site_staleness.py` builds a deterministic packet —
  unreferenced sources, source-content revisions versus the pages citing
  them, aging dated assertions, tracker degradation — into
  `.work/site-staleness.md`. The `sync` role (`agent-site-sync.sh`,
  12-hourly at 06:20 and 18:20 UTC) edits pages from that packet, and its
  output is gated post-run on `just check-claims` plus a full gated build;
  a gate failure rejects the run and raises an urgent alert. Corrections
  are drafted, never applied, by the propose-only `corrections` role
  (`agent-corrections.sh`, weekly Sunday 06:40 UTC) from the claim sweep's
  state-changed flags; `scripts/apply_corrections.py` is the deterministic
  applier — it validates each proposal (verbatim `said`, real routes, the
  corrections.ts entry rules, zero-fuzz patch, pure append) and applies
  all-or-nothing, dry run unless `--yes`, with an alert per applied
  correction.
- New-host admission is automated. An intake agent files a proposal in
  `.work/host-proposals.txt`; the driver-side `scripts/vet_host.py` applies
  it after deterministic checks (https only, independent DNS, robots.txt,
  redirect shape) and admits the sound ones to `registry_hosts.toml` and
  `agent_egress_hosts.toml`. Every admission raises an alert and stays
  auditable; a proposal that fails vetting waits for a human, and the
  quarantine path is unchanged.
- Gone-corroboration is automated. `scripts/corroborate_gone.py`
  (six-hourly timer) re-resolves `dns-unresolved` failure streaks through
  public DNS-over-HTTPS resolvers and may set `gone = true` only when the
  streak and the independent resolvers agree; anything short of agreement
  keeps polling. The corroboration transcript is recorded in `gone_note`
  and an alert is raised. The rule it automates is the one written after
  this project called `coldcard-watch` gone on its own resolver's word and
  had to correct it.
- One operational lesson, recorded: the guard REJECTED the first `xintake`
  run because concurrent development work dirtied the tree mid-run. The
  containment worked as designed — the run's legitimate registrations were
  re-captured driver-side afterwards and nothing was lost — and the
  standing rule is now that agent runs and tree edits serialise.
  `record_commit.py`'s unresolved-guard-run check is the enforcement: a
  rejected run blocks the auto-commit until a person resolves it.

## 2026-08-07

### Record

- Say when a post is held twice. A post can be both its own registered record,
  with this project's note on why it matters, and a post inside a conversation
  captured around it. Until now the record held both copies with nothing
  connecting them, and the reader met the same material twice. `part_of =
  "<head-id>"` on the member's `[[x_post]]` block states the relation, and both
  ends read it: the member's page names the conversation it also sits in, and
  the thread reader links the post to its own record and foregrounds it rather
  than risking collapsing as applause a post that stands as evidence
  elsewhere. Three rules are enforced in `validate_sources`: `part_of` must
  name a registered post that actually holds a conversation; `part_of` and
  `thread` are exclusive, because a head is not a member of itself; and a head
  may not withhold a status that is separately registered, since withholding
  it from the conversation withholds nothing while it publishes one link away.
  The registry cannot see this relation on its own — whether a post is inside a
  conversation depends on what a capture collected, and that changes as a
  thread grows — so `just audit` now reads the newest structured record of
  every thread source and reports a registered post held inside one without
  `part_of` to declare it. The first live case,
  `opensats-2085363706255573313` inside `afilini-2085269060028170742`, was
  found by the gate rather than by reading, and is now declared. Display reads
  the declared key and not the capture, because a reply can drop out of a
  capture on X's ranking alone and the relation should not blink with it.
  Design record `docs/design/x-thread-capture.md` section 4, which also records
  why retiring the duplicate entries was rejected: their titles and notes are
  curation the capture does not reproduce, and their URLs are citable.
- Add `trustwallet-wasm-update` and `bitcoindevs-explainer-thread` to the
  curated X-thread tier, which now holds five conversations at tier 3. Both
  entries said in their own registry notes that only the first post of a
  thread was held, which is the gap the thread lane exists to close. Trust
  Wallet's 2023 browser-extension advisory now holds the whole 1/10 to 10/10
  chain and 41 replies; the Bitcoin_Devs explainer holds 30 posts, 8 of which
  arrived truncated and had to be expanded before they could be read. Both
  first captures converged with nothing capped and no gaps declared, and
  neither produced a diff, a first capture having nothing to diff against.
  Their `why` notes are updated to say what is held now and from when, rather
  than continuing to describe the single-post capture they replaced.

### The site

- Bring every editorial page back into agreement with the record. The prose was
  written on 5 and 6 August; since then 143 registrations were added and 214
  detected differences were classified, and the pages had drifted behind both.
  Each page was read against the new material rather than topped up: what
  changed, what a source revised, and above all what the pages assert is
  *absent*, because a stated absence that has since been filled is the failure
  mode this site is least able to notice on its own. Three of them had been
  filled. The largest is on `/response/statements/`, which said no held source
  reconciled the 2 August exchange over customer-data retention; Coinkite has
  since published its own account, so the sentence is gone and the vendor's
  120-day practice, its suspension and the opt-out are recorded, along with what
  the document does not settle. Every re-dated bound on the site was rechecked
  rather than incremented; several were left at their old date because the
  recheck needed a network search this pass could not repeat, and those say so.

- Publish three entries in the corrections log, the first since it opened.
  `/record/funds/` said neither Galaxy nor the tracker itemised a reconciliation
  between the two published wave-4 figures; the tracker had itemised one, in a
  capture already held when the claim was published, and the page now carries
  the tracker's derivation as the tracker's reading of Galaxy rather than as
  Galaxy's own. `/response/scams/` counted three post-disclosure scam reports
  when four more were already held on the day it was published; the fix is not a
  larger number but no number, because a count of our own holdings goes stale on
  the next capture and invites exactly that error. Two clarifications: `/` and
  `/how-it-broke/entropy/` said no confirmed drain of an Mk4-class wallet had
  been captured without saying they meant this incident's July waves, while the
  archive held a catalogue of an earlier confirmed Mk4 theft whose cause is
  unproven; and `/how-it-broke/` dated two claims about libngu #58 to 6 August
  while the newest capture behind them was from 4 August, and the request was
  merged on the 6th. That second one is worth stating plainly: the error was not
  the reading but the bound. A claim belongs to the capture that supports it,
  not to the day it was typed.

- Write the dice arithmetic out, closing a standing backlog item. A derived
  block after the roll-count table on `/how-it-broke/conditions/` shows why a
  fair six-sided roll contributes log2(6) bits, why independent rolls add, that
  50 rolls is the smallest count reaching 128 bits and that 99 falls about 0.09
  bits short of 256 — and that fairness and secrecy are physical assumptions the
  firmware cannot check. Beside it, the held argument over what bias costs: a
  worked bad-die case, a second-hand relay of a claimed 1971 study of 219 dice,
  and a first-hand casino account that retail casino dice are not guaranteed
  balanced. No procedure, no seed-generation guidance, and no die tested here.

### The registry

- Replace the placeholder notes on the newly registered X posts. A bulk intake
  had left 119 entries whose `why` was the post's own first line with
  "(TODO: expand)" appended, and no `title`. Both are published: `why` renders
  as this project's note on why a post is in the record, and a missing title
  leaves the source page headed with a machine-mangled slug. Each note is now
  written from the capture, says what the post claims and why it is held, states
  a commercial or competitive interest where one exists, and attributes rather
  than adopts. Where a post's text does not stand on its own, the rendered
  screenshot was read for the quoted card, which is the only way several of them
  are legible at all. Where a post alleges something about a named individual
  that no held source supports, the note records that an allegation was made and
  against whom, without restating it.

- Register the code the pages had been describing at second hand. The libngu
  #62, #63 and #64 stack and COLDCARD firmware PR #707 were being written about
  from titles rendered on other repositories' pages; all four are now registered
  and captured with their patches. #707 matters most: it is a vendor-repository
  proposal, unmerged, that would mix secure-element entropy into seed
  generation, reseed with the full digest, require user-supplied entropy for
  every new wallet and warn before dice-only generation.

- Also registered: Coinkite's own customer-data-retention post, which the record
  had been holding only as a row in the vendor's blog index and as a relay
  through the vendor's X account. Three source kinds were corrected so entries
  land in the right register, and `org-statement` was added for a named
  organisation speaking for itself without being a vendor.

### Tooling

- Finish the manual half of X thread capture: `ingest-x.py --thread --tier N`
  and `just capture-thread <id>`. Registering a conversation and taking its
  first capture was the last step of the capture path that still had to be
  done by hand in two parts. `--thread` does the focal-post ingest as before,
  writes `thread = true` and the tier into the `[[x_post]]` block, and then
  hands the conversation to `capture.py capture --id <slug>`, which is the
  same poll the tier's timer runs from then on. Deliberately one write path
  and not two: change detection, the diff, the `index.jsonl` event and the run
  record all come from the first capture *being* a poll rather than
  resembling one. It is a separate process because the archive writer lock is
  not reentrant. `just capture-thread` is that poll on its own, for
  re-capturing a registered conversation without waiting for its tier.
  Enabling a thread on a post that is already registered stays a human edit of
  `sources.toml`, refused with the exact keys to add: this script appends
  blocks and does not rewrite them, and a second block for the same post would
  give one conversation two registry entries. Fixed on the way through: the
  lookup that resolves a status to its registry entry tested `tweet_id in
  url`, a substring match, so a shorter X id could match inside a longer one
  and file a capture under another post's id. It now compares the id parsed
  out of the registered URL. 28 offline tests in
  `scripts/test_ingest_x.py`, wired into `just test-capture`. Design record
  `docs/design/x-thread-capture.md`, step 3 of section 9.
- Let `ingest-x.py` capture an image-only post. Its readiness gate required a
  text body, so a post whose whole content is an attached image — no
  `tweetText` node, X's own page title showing only the media `t.co` link —
  could never be ingested and was misreported as "tweet article not found
  (deleted? wrong URL?)". The gate now accepts a found article with media and
  no text, still refuses one with neither, and the sidecar says plainly "no
  text body; the attached image is the whole post" above an empty verbatim
  section rather than inventing a body. First used for
  `matteopelleg-2085300048120668211`, the Satoshi announcement-email
  screenshot. Extraction itself is unchanged: posts with text follow the same
  path byte for byte.
- Stop a rejected registration from stopping the tree. `agent_guard.py`
  deliberately reverts nothing when it refuses a run, which is right for
  judging the run and wrong for an invalid `sources.toml`: the registry is
  checked by `just audit`, `just test` and the publish gate, so one unlistable
  host fails all three until a person edits the file by hand. On 7 Aug 2026
  that was a single OpenSats article and it held the tree for most of a day.
  `agent_finish` now runs `scripts/quarantine_registry.py` after a rejection,
  which moves any block **that run added** and the rules refuse into
  `quarantine/registry-YYYY-MM.toml`, verbatim, with its reason and run id.
  The rejection still stands and no capture is approved; the evidence is kept
  and greppable, just not in the file that decides what is fetched every 30
  minutes. Two properties make it safe unattended, both tested: it only ever
  removes, never adds a host or edits a surviving block; and only what the run
  added is eligible, so a pre-existing source cannot be evicted by an agent
  breaking a rule on purpose, and with no `--before` baseline it refuses to
  move anything at all. It is not an approval mechanism: restoring a block is
  still a human edit, twice over. What changed is that nothing waits on it.
  The intake prompt is now given `registry_hosts.toml` as well, so the
  ordinary case is that the agent reports an unlistable host in its run report
  instead of registering it and having the run thrown away.
- Add `opensats.org` to `scripts/registry_hosts.toml` for `opensats-code-red`,
  the registration that had been holding the tree. OpenSats is a bitcoin
  funding nonprofit already in this project's incident vocabulary, and the
  post is its own statement about supporting incident responders.
- Put a coverage index in front of the intake agent, so "already represented
  by `<id>`" is a lookup rather than recall over `sources.toml`. That verdict
  is the largest single class of dismissals (88 of 248 in the assessed
  corpus), the agent was reaching it from memory of a long prompt, and it
  re-derived them: one dismissal names a thread the agent had itself dismissed
  weeks earlier as the precedent. `just coverage-index`
  (`scripts/build_coverage_index.py`) emits one line per registered entry
  across all four tables, because a candidate can duplicate an X post or a
  chain monitor's page and not only another thread. Each carries an
  `absorbed N` count of candidates already dismissed as duplicates of it, read
  out of past verdicts and validated against the registry so that hyphenated
  prose in a dismissal reason scores nothing; blocks are sorted most-absorbed
  first. The corpus is self-labelling, so nothing here is hand-maintained.
  The index decides nothing: the lexical alternative was measured first and
  put a verdict's own named referent top-1 in 4 of 76 cases while flagging 93
  of 174 registered entries as near-duplicates, so judgement stays with the
  agent and only recall was replaced. The driver builds it as the operator
  account before dropping privilege and now refuses to start an agent without
  one, because an agent that cannot see what is covered registers duplicates
  of it. Thread titles are text other people wrote, so the index goes through
  the untrusted channel with the candidate bodies and the framing prose stays
  in the trusted template. X triage is unchanged: it is read-only and under
  probation.
- Defer the lowest-yield discovery candidates instead of putting them in front
  of the intake agent. The lanes' incident vocabulary is now two tiers: tier 1
  (`coldcard`, `coinkite`, `nvk`, `rng`, `slipstream`, `btcrecover`, the BTC
  figures) names this incident, tier 2 is the bitcoin-security vocabulary it
  borrows. The tiers do not decide what is queued. Measured against the 425
  assessed candidates, refusing tier-2-only titles would have removed 58
  dismissals and lost 33 registered sources, which is the wrong trade for a
  preservation record, so the sieve is unchanged and every `--all` still
  reports everything new. What the tier decides is where a candidate waits,
  and only together with a second weak signal: a title that never names the
  incident *and* two comments or fewer sends it to a new `## Deferred`
  section. Of the 24 such candidates in the assessed corpus, none were ever
  registered (r/coldcard 20, none registered; r/Bitcoin 21, two registered).
  Deferral is reversible and nothing leaves the record: the lane re-reports a
  deferred candidate while it is in the listing window, keeps its comment
  count current, and promotes it to Pending by itself once the thread grows.
  A candidate with no comment count to read (X, nostr) is never deferred.
  `agent_guard.py` treats Deferred as a queue and not a verdict on both sides
  of a run, so an agent that moved a pending line into it is rejected for
  disposing of a candidate without recording why, while a lane appending
  there mid-run is not mistaken for an invented verdict. On the 12 candidates
  the Reddit lane found on 7 Aug, one deferred.
- Add verdict rotation for the discovery intake queue: `just rotate-discovery`
  (`scripts/rotate_discovery.py`) moves `DISCOVERY.md` verdicts older than
  `--keep-days` (31 by default) into `discovery/assessed-YYYY-MM.md`, verbatim
  and append-only, under the same intake lock the lanes and the intake agent
  take. A hand-entered verdict without a UTC stamp never rotates, and the
  queue's third section ("Link review, held for a human decision") is
  preserved untouched. The discover-community unit runs it after each intake
  pass, so the queue stays bounded without anyone remembering. The
  destination is the top-level `discovery/`
  directory, not `archive/`: the deposit gate refuses unclassified archive
  trees and the manifest describes every archive tree as captures, while
  rotated verdicts are project records, so they ride the deposit's ordinary
  inclusion path. The queue stops growing without bound; nothing rotates on
  the day it ships because every held verdict is days old.
- Preserve the file mode across atomic queue writes in
  `discovery_common.atomic_text`: `mkstemp` makes the replacement 0600, so
  every lane rewrite stripped the group access the intake agent needs, and a
  6 Aug lane run left `DISCOVERY.md` unreadable to the agent account. The
  failure was observed on 7 Aug, when an intake run assessed 15 candidates,
  registered 6, and could not record a single verdict. The registrations
  survived the rejected run (the registry check had passed them), so the
  verdicts were recorded by hand from the agent's report and the six first
  captures were run directly.
- Move the kimi agent binary out of the credential directory: the wrapper
  `/usr/local/bin/cc-agent-kimi` exec'd `~/.kimi-code/bin/kimi`, which is
  unreachable the moment the sandbox permissions keep the credential dir at
  700 as intended. The binary and its bundled `fd`/`rg` now live at
  `/usr/local/libexec/cc-agent-kimi/` (re-sync after a kimi update; the
  wrapper comment says how), and `~/.kimi-code` stays 700 with
  `just audit-sandbox` clean.
- Fix two deposit-gate false positives that kept `just deposit` red: the
  hardening example's literal `/home/OPERATOR/` paths (now an
  `OPERATOR_HOME` substitution token) and a guard test fixture simulating a
  home-path leak (now split so the scanner's own needle is not in source).
- Merge `docs/CAPTURE.md` away: the capture-host material moves to
  `operations.md`, the social-capture procedures to `capture.md`, and the
  screenshot-provenance gate to `publication.md`. Two files differing only
  in case collide on case-insensitive checkouts, and the split overlapped
  both established documents.
- Set `X_REVIEW_AGENT_BIN` to the kimi wrapper, so operator-approved X
  triage no longer falls back to the community review provider.
- Isolate dry-run and discovery browser activity from live capture by
  session namespace rather than by lock. The webbridge daemon keys one
  current tab per session name and `navigate`/`close_tab` act on that
  session's tab, but every `capture.py` browser read — live poll, dry run —
  and every Reddit discovery read used the one name `coldcard-archive`, so
  an independently started dry run could close the tab a scheduled poll was
  mid-read on. Live polls keep the name; a dry run now drives
  `coldcard-archive-dry-<pid>` (which also separates two dry runs), the
  X-thread session is derived from the active one, and Reddit discovery
  drives `coldcard-archive-discover`. No artefact records the session name,
  so nothing a capture records changes; the maintenance wrapper's pause is
  still what protects a mid-edit capture. Verified with a full dry run
  overlapping a live `capture-one` of a browser source, both clean. Six
  `WebBridgeSessionTests` in `scripts/test_capture.py`; session names
  documented in `docs/capture.md`. One residual: a failed `navigate` still
  relaunches the whole daemon browser, which tears down every session —
  pre-existing failure path, not concurrency.

## 2026-08-06

### The record

- Record, and stop claiming otherwise, that the archive has not always been
  append-only. The 4 Aug 2026 reddit-json migration deleted the pre-migration
  captures of five Reddit sources (134 captures, 402 files, 1 to 4 Aug), their
  diffs and their lines in `archive/index.jsonl`, so those sources begin on
  4 Aug. Nothing recorded it at the time; it was found on 6 Aug while auditing
  for superseded material before a deposit. The out-of-repository backup was
  deleted deliberately rather than restored, as duplicate rendered-page capture
  of threads still held in better form from before the capture process settled.
  First entry in `/corrections/`; the `/cite/` commitment is now dated and
  scoped to what is true, `README.md` states the gap in the poll log, and
  `AGENTS.md` and the migration's design record carry the rule that came out of
  it: a migration is not a licence to delete history.
- Correct the claim that `coldcard-watch` was gone. The 53 failed polls
  established only that this capture host's filtering resolver blocks
  `coldcardwatch.com`. Public DNS resolves it, and a direct HTTPS check with
  that public answer returned the live tracker with status 200. The source is
  polling again, its last held reading remains visibly stale until the host's
  resolver is fixed, and the capture card now says "unreachable here" rather
  than "offline". The error is recorded at `/corrections/` and marked on every
  page where it appeared.
- Recover the original three-post @intangiblecoins wave-4 thread and its two
  linked Pastebins, and present them beside the two later corrections.
- Recover both files in profedustream's Smash transfer, and register the
  announcement, transaction graph and method note separately.
- Add the nostr lane: project identity and NIP-05, manual posting, NIP-50
  discovery in probation, and `ingest_nostr.py` capture into
  `archive/nostr/<id>/<TS>/`. `nak` joins gallery-dl as a sanctioned binary.

### The site

- Name and link the sources that have disappeared, rather than stating a count
  a reader cannot follow. The register gains a Status facet, present only while
  something is gone.
- Show the archive capture time beside every published screenshot, separately
  from the source's publication time.
- State the archive's historical purpose: preserve the contemporaneous record
  for posterity, and keep changing explanations legible. Conspiracy theories
  stay dated and attributed as reaction, not evidence.
- Publish citation apparatus: `/cite/`, `CITATION.cff`, and `/version.json`
  plus a footer stamp linking the commit each build was made from.
- Add a corrections log: `corrections.toml`, rendered at `/corrections/`,
  empty at the outset and saying so.

### Policy

- Withdraw the undertaking to honour author removal requests (superseding the
  3 Aug 2026 position). Material its author published publicly stays; what
  still comes down is material that was never public, personal data, and
  anything this project got wrong. The licensing rationale is unchanged.
- Distinguish an author's preference from a complaint with a legal basis.
  Declining to withdraw correctly reported material is not a refusal to answer
  a copyright complaint or a court order.

### Capture and tooling

- Fix a shell syntax check that had never checked anything. `just test-capture`
  ran `bash -n` over a list of nine scripts, and `bash -n a.sh b.sh` parses
  only `a.sh`, making the rest positional parameters. Every shell script but
  `capture-x.sh` had therefore gone unparsed since the list was written,
  including all four agent drivers and `agent-run-common.sh`, which carries the
  containment they share. The check now loops one file per interpreter, and
  `publish-scheduled.sh`, which the publish timer runs unattended and which had
  fallen off the list entirely, is covered for the first time.
- Give the discovery lanes a shared module instead of a shared lane.
  `discover_stackernews.py` held the keyword sieve, the seen-state helpers and
  the whole DISCOVERY.md write path, so Reddit, BitcoinTalk, nostr and X all
  imported a peer lane: the Stacker News module owned the intake header
  describing nostr and a line formatter branching on X candidates. That code is
  now `scripts/discovery_common.py`, imported as a peer by all five lanes, and
  it has direct tests for the first time (`scripts/test_discovery_common.py`):
  assessed verdicts survive a run verbatim, a dismissed thread is not
  re-queued, and a registered thread leaves Pending. Behaviour is unchanged,
  checked by rebuilding each lane's registered-URL set against the live
  registry and comparing it to the old implementation's.
- Drive the test, byte-compile and shell-parse gates from globs rather than
  three hand-maintained lists. Two files had already fallen off them. A new
  script is now checked from the moment it exists.
- Collapse the four site builds to one body. `build-site`, `build-site-full`,
  `build-preview` and `build-site-indexable` repeated the same steps and
  differed only in Astro's environment and, for the full-text build, in
  skipping the output gates it is designed to fail. They are now `_astro` and
  `_gates` plus one line each, so the gate list cannot drift between the local
  build and the one that gets published.
- Contain the four unattended agents, on the assumption that a prompt
  injection in a captured thread works. They ran as the account that owns the
  tree, with the whole of `.env` exported into their environment, so a
  successful injection reached the nostr posting key, the Cloudflare deploy
  token, the X bearer token and `AGENTS.local.md`. Five layers now answer it,
  none of which depends on the model: the agent runs as `cc-agent` with an
  environment built from an allowlist (`scripts/run-agent.sh`); two of the
  three units deny loopback, and the capture browser, whose `evaluate` and
  `cdp` actions are arbitrary JavaScript and raw DevTools inside signed-in
  sessions, now requires a token the agent account cannot read; candidate
  bodies are fetched by the driver before the agent starts
  (`scripts/hydrate_candidates.py`), so no agent makes network requests and
  the X lane never holds the bearer token; first captures moved to the driver,
  so no agent writes `archive/` or fetches an address it just chose; and
  `scripts/agent_guard.py` checks everything a run produced against its role's
  remit, scanning for secret values and handing the registry delta to
  `scripts/check_registry.py`, which pins hosts, the Stacker News query and
  what may change on an existing source. A rejected run is reported and left
  exactly as the agent left it, because what an injection tried to do is worth
  keeping. The reasoning, and the two gaps it does not close, are in
  `docs/design/agent-sandbox.md`; the setup is in `docs/operations.md`, and
  the drivers refuse to run until it is done rather than running unprotected.
  Applied and proved on the capture host the same day, which corrected three
  things that had been asserted rather than measured: `IPAddressAllow=any`
  with `IPAddressDeny=localhost` blocks nothing, because an allow entry beats
  a deny entry whatever its prefix; `NoNewPrivileges` has to be set false
  explicitly or the privilege drop fails; and the repository root needs the
  sticky bit as well as group write, so a provider can create its scratch
  directory without being able to unlink a tracked file. The first live run
  also found a bug in the queue check itself, which read `DISCOVERY.md`'s
  third section asymmetrically and rejected a clean intake with eleven false
  positives. All four are now covered by tests or by `just audit-sandbox`.
- Close the last gap in that containment: an agent can no longer reach a host
  of its choosing. `scripts/agent_proxy.py` refuses a CONNECT to anything not
  in `registry_hosts.toml` or `agent_egress_hosts.toml`, and an nftables rule
  drops everything else from the agent account. The rule is keyed on uid
  rather than on a systemd unit, because `discover-community` runs the
  driver's own Reddit hydration and the agent in one cgroup and `IPAddress*`
  cannot tell a parent from a child; a uid also covers manual runs, which no
  unit setting reaches. The policy question this was blocked on dissolved
  once hydration moved to the driver: only the sweep reads the open web, and
  what it may read is already the registry's own host list, because anything
  it finds has to be registerable to be worth reading. Verified on the host:
  from the agent account, direct HTTPS, the capture browser and the cloud
  metadata endpoint all time out, while an allowed host answers through the
  proxy and a denied one is refused. Provider telemetry stays refused on
  purpose: an agent here reads victim accounts, and a crash reporter is a
  route for fragments of that to leave the host.
- Make the unattended agent provider swappable. Three providers each get a
  wrapper in `/usr/local/bin` speaking the same `-p` contract and a credential
  under the agent account, so `REVIEW_AGENT_BIN`, `X_REVIEW_AGENT_BIN` and
  `CLAIM_SWEEP_AGENT_BIN` can name different models and be compared on the
  same queue. Which providers they are stays out of the repository, with
  `.env` and `AGENTS.local.md`: an archive whose agents read
  attacker-adjacent material should not publish which models read it. Fixed
  in passing: one provider's config file, which holds an API key, was
  world-readable.
- Add curated X-thread capture. `scripts/x_thread.py` reads a thread through the
  capture browser and `capture.py` gains an `x-thread` method, with registry
  validation and audit support, so a `thread = true` `[[x_post]]` writes
  canonical text, a flattened transcript and a record of the depth reached.
  `clay-attribution` is the tier 3 pilot; its last poll pair added 55 lines and
  removed none, which is the healthy shape, because a removal is either a real
  deletion or under-collection and the text alone does not tell them apart.
  Design in `docs/design/x-thread-capture.md`, remaining work in `BACKLOG.md`.
- Expand truncated X posts before capture. `ingest-x.py` calls
  `expand_truncated()` before reading and again before the screenshot pass,
  records the expansion in the sidecar, and refuses a capture whose text is
  still behind a show-more control. `--skip-unchanged` lets a repair pass write
  only where text actually recovers. No held capture needed repairing: all 73
  candidates were re-read and none recovered, because X truncates posts
  rendered in list context and this script has only ever captured focal posts
  on their own permalinks. The fix is therefore a precondition for thread
  capture, where every post is read in list context, rather than a repair.
- Move both Hacker News sources to the Algolia item API
  (`hn-maxwell-mechanism`, `hn-dettmer-writeup-thread`). HN answers this exit
  with a persistent 429, still 429 on single requests 20 seconds apart, so it
  is the address rather than the cadence. The API dates every comment
  absolutely, which retires `relative-time` for these two, and `hn-api-points`
  suppresses score churn. `url` stays the human permalink the site links. The
  stored text is JSON, so site excerpts from these two read worse than the
  rendered page did; the flattener is open in `BACKLOG.md`.
- Record why a poll failed, not just that it did. Failure events carry a
  `diagnosis` slug and, where the origin gave one, `http_status`, alongside the
  unchanged `failure` word. Only the derived slug is stored, never the headers
  behind it. `just diagnose` groups current failures by cause.
- Prepare the archival deposit without making one: `make_deposit.py` stages
  every tracked file minus the captured bodies and has no upload path;
  `build_manifest.py` describes all 1,768 held captures with their hashes and
  none of their content. Staging refuses on a dirty tree, on an unclassified
  `archive/` path, or on a machine path in the staged output. Reasoning in
  `docs/deposit.md`.
- Mirror capture.py's thread-enabled X-post projection in the site's source
  lookup, so those snapshots and reviews resolve during a build.
- Describe X-thread captures correctly in the deposit manifest: a
  `thread = true` `[[x_post]]` also writes into `archive/snapshots/`.
- Remove the unbound `reddit-engagement` normalizer, its table entry and its
  test. It appears in no held sidecar, so nothing replays it;
  `reddit-more-stub-counts` is likewise unbound but appears in 153 and stays.
- Warn in the justfile that `just agent-maintenance` word-splits its arguments:
  a quoted compound command runs only its first word and exits 0.
- Allow "just published" in a held Reddit thread through the public-output
  gate. The token exists to catch this project's recipe name, not the verb.

### Recorded exception

Nine pre-convention capture files from the first days of collection were
retired rather than carried into a permanent deposit. No snapshot was deleted
and no unique record was lost: the two X directories held attachments
byte-identical to those in three dated captures of each post, and the Reddit
thread is held in three canonical `reddit-json` snapshots carrying its full
text, the poster's stated address and the image URLs. What went was the
downloaded image itself, which the display policy never publishes.

```
196855  9c09ed7f43b2ec26  archive/reddit/reddit-drained-timeline/undated/1vb6teq Wallet Drained Timeline.jpg
 12000  0ce758a8f79ae0d1  archive/reddit/reddit-drained-timeline/undated/1vb6teq Wallet Drained Timeline.json
 11929  189fe41e999f89be  archive/reddit/reddit-drained-timeline/undated/info.json
 33506  13db23547ec4e481  archive/x/llfourn-model/undated/attachment-1.png
  2731  4819859f00c1372d  archive/x/llfourn-model/undated/2082990000896147942_1.png.json
  5109  fe6efdb081f32a41  archive/x/nvk-apology/undated/attachment-1.jpg
  2292  6152503e8f9a8081  archive/x/nvk-apology/undated/2083216713693151552_1.jpg.json
  2575  fdcf86722f257705  archive/x/twitter/LLFOURN/info.json
  2120  b3a1af5ee7f54bdc  archive/x/twitter/nvk/info.json
```

This is a recorded operator exception, and the standing shape for any future
one.

## 2026-08-05

- Refocus the public site on the record itself: retire the affectedness,
  risk-overview, personal-estimator, seed-handling and moving-funds pages,
  redirect every retired route, and remove `INCIDENT_PHASE` (recorded in
  `docs/design/record-first-focus.md`).
- Capture the 5 August intake: 62 X posts, five web resources, CKTRIPWIRE
  monitoring, migration reports, scam fallout, prior-warning accounts and
  updated attacker estimates.
- Register and first-capture the five coldcard.rip routes the operator's
  rebuild moved the evidence onto, and separate a guard miss from a publisher
  challenge in `pollHealth`, so a page the archive cannot parse is never
  reported as a source that is blocking us. The guard had recorded 44 blocked
  polls while the site served 200s.
- Read the community trackers' headline totals out of the held captures instead
  of carrying them as literals, with the capture, the last movement and whether
  the source still answers. `check-trackers.mjs` fails a build that falls back
  to the pinned figure.
- Tag each tracker card with its capture liveness, and show provenance as
  fields rather than a paragraph.
- Add `scripts/publish-scheduled.sh` and its example units: an opt-in timer
  that publishes only from a clean tree, and skips rather than fails.

## 2026-08-03

- First commit.
