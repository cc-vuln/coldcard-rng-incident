# Content and archive backlog

Only current, in-scope work belongs here. Completion history belongs in
`CHANGELOG.md` or the relevant design record, and standing conventions belong
in `AGENTS.md` or `docs/`.

The belonging test applies to backlog work too: it must preserve incident
material, organise that material, explain the incident from preserved material,
or keep those functions reliable. General bitcoin-security guidance, personal
wallet classification and material that was never public are outside scope.

A registered source does not need to be quoted on an editorial page to count as
part of the record. Its source page is already a public, citable record. Weave it
into editorial prose only when it helps organise the evidence or supports a
material claim.

Status key: **FIX** the archive or published presentation is materially
incomplete or violates its own contract · **OPEN** ready to work · **GAP** the
needed public evidence has not been identified and may not exist · **MONITOR**
the current absence is already documented and should change only when new
public material appears · **BLOCKED** needs an operator choice, external access
or evidence from a third party.

Last reviewed: 8 Aug 2026, recording the operator's three pipeline decisions
of that date: X discovery moved to the capture browser with automated
promotion, failure delivery resolved onto the operator-UI alert stream, and
the guard-passed pipeline moved to unattended commit, publish and push. All
three are built and installed the same day; the entries below record what
landed and what stays open.
Previous review: 7 Aug 2026, after the site-wide prose pass against the 6 and
7 August record: 143 new registrations read into the editorial pages, three
corrections published, and the placeholder registry notes on the new X posts
replaced.

Ordered by value, not effort.

---

## 0. Correctness fixes

- **DONE 6 Aug 2026: expand long X posts before capture.** `ingest-x.py` now
  calls the tested `expand_truncated()` from `scripts/x_thread.py` before
  reading and again before the screenshot pass, records the expansion in the
  sidecar, and refuses the capture outright if any text is still behind a
  show-more control. `--skip-unchanged` was added so a repair pass writes only
  where text actually recovers.

  The audit found no held capture to repair. All 73 candidates (held bodies in
  [220, 320], the only band a truncated render can produce) were re-read: zero
  recovered, zero failed, no archive write. Detection was proved before the
  result was trusted, against the known-truncated `clay_garrett` continuation
  post: `truncated: true` at 275 characters, `truncated: false` at 397 after
  expansion. The earlier "22 likely affected" figure was unsound, because the
  240-to-320-character bulge in held bodies is X's ordinary 280-character
  limit rather than a truncation signature.

  Why nothing was affected: X does not truncate the focal post of its own
  permalink page, only posts rendered in list context, and `ingest-x.py` has
  only ever captured focal posts on permalinks. The fix is therefore a
  precondition for X-thread capture, where every post is read in list context,
  rather than a repair of held material. Detail in
  `docs/design/x-thread-capture.md`.

- **DONE 7 Aug 2026: isolate dry-run browser activity from live capture.**
  Dry runs and community discovery no longer share a webbridge browser
  session with scheduled polls. The daemon keys one current tab per session
  name, and `navigate` and `close_tab` act on that session's tab, so the fix
  is namespacing rather than locking: live polls keep `coldcard-archive`,
  X-thread polls derive `coldcard-archive-thread` from the active session, a
  dry run uses `coldcard-archive-dry-<pid>` (which also separates two dry
  runs from each other), and Reddit discovery drives
  `coldcard-archive-discover`. `ingest-x.py` already had its own. The
  session name is recorded in no artefact, so nothing a capture records
  changes. Verified live: a full dry run and a `capture-one` of a browser
  source ran overlapping and both completed clean. Six regression tests in
  `scripts/test_capture.py` (`WebBridgeSessionTests`); the session names are
  written down in `docs/capture.md` so the next browser client picks its
  own.

  Residual, not fixed: a failed `navigate` or `list_tabs` still relaunches
  the whole daemon browser, tearing down every session's tabs. That is a
  failure path, not concurrency, and it predates this change.

---

## 1. Evidence acquisition and verification

### Missing primary artefacts

- **GAP: the primary Nunchuk platform-key statement.** The record holds a
  public screenshot crop and a secondary account of the statement headed “How
  this affects Nunchuk platform keys”, but not the original notice page or
  public post. Do not solicit a private customer email. Register a primary only
  if Nunchuk or another party has made it public.

- **GAP: the video linked from `stackernews-prior-warning-video`.** The held
  thread points to `youtu.be/oj_W3xOlt6U`, but the archive has neither the video
  nor a publisher-provided transcript. First establish whether the video adds
  checkable evidence beyond the thread. Do not add a general video dependency
  merely to mirror it; the stdlib-only archive rule and the narrow gallery-dl
  exception still apply.

### Claims that cannot yet be rechecked

- **GAP: James O'Beirne's May 2025 audit artefact.** His 4 August account says
  he reported a randomness and libngu concern to Coinkite in May 2025. No
  contemporary report or correspondence is attached. Coinkite's captured
  disclosure chronology contains no May 2025 entry and no pre-incident
  randomness report, but that is an absence in a vendor-selected chronology,
  not evidence that no private report was made. A public report, ticket or
  correspondence would change the status.

- **GAP: Chainalysis's geographic method.** Its 4 August post attributes 25
  percent of geographically attributable losses to Canada and names Australia,
  the United States and Thailand as prominent. It publishes no victim set,
  address set, method or denominator, so the estimate cannot be reproduced from
  the held post.

- **GAP: instagibbs' reproduction scripts.** The two reported reproductions
  are captured as statements, but the scripts, inputs and output checks are not.
  The bounded 5 August search covered public repositories and gists without
  locating them. Keep the result attributed rather than inferring a method from
  the short posts.

- **GAP: a real-device STM32 UID low-word distribution.** No population sample
  has been identified. It would constrain one term in published candidate-count
  models without settling SysTick, RTC, call-history or derivation-cost
  assumptions. The privacy-preserving measurement plan is in
  `docs/research/uid-distribution-measurement.md`.

  Two sources registered on 6 and 7 August bear on the term without filling it,
  and the difference matters. The honeypot operator's methodology reports one
  instrumented sacrificial Mk3 whose UID word held packed die coordinates rather
  than a uniform value, with SysTick in a narrow early-boot range and the RTC
  registers reading zero; that is one device, and it describes the field's shape,
  not its distribution. A 7 August community derivation bounds the word at
  roughly 23 bits from an assumed wafer yield, which is an assumption rather
  than a measurement. A population sample is still the thing that is missing.

- **GAP: incident population denominators.** The number of distinct human
  victims and the number of seeds generated under vulnerable firmware remain
  unknown. Address counts overstate owners, and no captured unit-sales or seed
  count supplies the denominator. These are published limitations, not numbers
  for this project to estimate.

- **DONE 7 Aug 2026: register the code artefacts the record was describing at
  second hand.** The libngu #62/#63/#64 stack and COLDCARD firmware PR #707
  were registered and captured during the site pass, and the later claim sweep
  added libngu #56 and #68 and firmware #697 and #713, so every proposal the
  pages name is now held with its own patch capture rather than read off a
  title rendered on another repository's page. The last of those matters: #707
  was open at 15:45 UTC on 7 August and closed by 19:22 UTC the same day, its
  author opening #713 to replace it. Registering the successors is what stopped
  `/response/developers/` shipping a page that pointed at the withdrawn
  version.

- **GAP: the Bitcoin Security Consortium.** One held X post is the only source
  in the record that mentions it at all, and it carries a US$15M pledge figure
  and a claim that only two named firms visibly responded. Nothing primary is
  held: no announcement, membership list or pledge document. Register a primary
  if one is public, or leave the single post standing as the only evidence and
  say so wherever it is used.

### Current-state monitoring

- **MONITOR: official technical follow-up.** Checked 8 August 2026.
  Coinkite's promised technical postmortem still has no publication date or
  detailed scope, no captured source announces a post-incident independent audit
  of the fixed firmware, and NVD/MITRE remained unreachable from this host so
  the CVE question could not be re-queried directly. The firmware repository
  did move: PR #707 was closed unmerged and replaced by PR #713, which remains
  open as of this recheck, and the libngu changes it referenced (#68 and #56)
  were merged on 7 and 6 August 2026. No new release tag or shipped firmware
  has appeared. Those developments are registered and requested for capture;
  they describe a proposal, not a shipped release, audit or postmortem.

- **MONITOR: filings, regulator statements and compensation decisions.** Checked
  8 August 2026. Coinkite's 7 August customer-data retention post states that
  records are being preserved for "ongoing and anticipated legal proceedings,"
  which is the first public vendor acknowledgement of legal activity, but it is
  not a filing, regulator statement, insurer decision or compensation offer.
  The legal page's absence claim still holds for those document types.

---

## 2. Archive tooling and operations

- **DONE 6 Aug 2026: an egress allowlist for the agent runs.** The last gap
  in the agent containment is closed. `scripts/agent_proxy.py` refuses a
  CONNECT to any host not in `scripts/registry_hosts.toml` or
  `scripts/agent_egress_hosts.toml`, and `scripts/agent-egress.nft.example`
  drops everything else from the agent account, keyed on uid rather than on a
  systemd unit because `discover-community` runs the driver's own hydration
  and the agent in one cgroup.

  The policy question this was blocked on dissolved rather than being
  answered. Once candidate hydration moved to the driver, only `claim-sweep`
  reads the open web at all, and what it may read is already
  `registry_hosts.toml`: anything it finds has to be registerable to be worth
  reading. So the rule is that an agent may reach its model provider and may
  read what the registry may name, and no new list needs maintaining.

  Verified on the host: direct HTTPS, the capture browser and the cloud
  metadata endpoint all time out from the agent account; an allowed host
  returns 200 through the proxy and a denied one is refused; all three
  providers complete a full run. Provider telemetry stays refused on purpose.
  Detail in `docs/design/agent-sandbox.md`.

- **DONE 6 Aug 2026: prove the agent sandbox on the host.** The account,
  sudoers rule, file modes, browser token, unit drop-ins, egress proxy and
  firewall rule are applied and exercised: `just audit-sandbox` is clean and
  intake, review and all three providers have completed real runs under them.

  Four things had been asserted rather than measured, and applying it
  corrected all four. `IPAddressAllow=any` with `IPAddressDeny=localhost`
  blocks nothing, because an allow entry beats a deny entry whatever its
  prefix. `NoNewPrivileges` must be set false explicitly or the privilege drop
  fails. The repository root needs the sticky bit as well as group write, so a
  provider can create its scratch directory without being able to unlink a
  tracked file. And the queue check read `DISCOVERY.md`'s third section
  asymmetrically, rejecting a clean intake with eleven false positives. Each
  is now covered by a test or by `just audit-sandbox`.

- **BLOCKED: one source is unreachable from this collector's VPN exit.**
  It needs an operator choice, not a registry edit. `theblock-galaxy-total`
  gets a Cloudflare managed block on the whole theblock.co domain: 403 with
  `cf-mitigated: challenge` to a scripted fetch, and an interstitial that never
  clears in the capture browser after 60 seconds; a 7 Aug poll still records
  `origin-challenge`. The exit uses a shared hosting ASN, which is also why the
  Internet Archive answers this host with 429, so the Wayback fallback below
  cannot rescue it from here. Options are to move the VPN exit or accept the
  gap. Do not use `watch = "frozen"`: the material is neither exhausted nor
  withdrawn, and there is no registry state for "live but unreachable from
  here". Eight captures of the Block article are held to 3 Aug 2026, so this
  is a monitoring gap rather than lost material.

  **Resolved 7 Aug 2026: `coldcard-watch` resolves from this host again.** The
  `dns-unresolved` streak that began 4 Aug 09:06Z ended at 7 Aug 03:32Z with a
  `changed` capture, and every poll from 08:19Z through the afternoon returned
  200. Nothing here was changed to achieve that, so the cause was upstream of
  this project and the same failure can recur; the 105 error events stay in the
  poll record because they accurately record what this collector experienced.
  What the recovered capture carries is not small: the tracker renamed itself
  from Coldcard Sweep Watch to Coldcard Watch, split its view filter into
  Verified, Attested and Suspected, and moved its verified total from
  1,366.5774 to 1,405.0671 BTC across 4,580 to 4,925 addresses. The site pages
  that described it as unreachable here were updated on 7 Aug; the published
  6 Aug correction about it stands unedited, because it was accurate when
  published.

- **RESOLVED 8 Aug 2026: the manual builds take the build lock.** Since
  6 Aug 2026 the four builds share one body (`_astro`), so the lock had a
  single place to go: `_astro` now queues on `flock /tmp/cc-build.lock`,
  the same lock `publish-scheduled.sh` already held, and a manual build
  started while the publish timer is mid-run waits rather than corrupting
  the Astro cache. Waiting is the contention behaviour throughout; the
  timer's own skip-on-lock logic is unchanged.

- **OPEN: flatten Hacker News API captures into readable text.** Both HN
  sources moved to the Algolia item API on 6 Aug 2026 because HN 429s this
  exit. `json_pretty` holds the thread completely and deterministically, but
  the stored text is JSON carrying HN's HTML entities and paragraph tags, so
  site excerpts from these two sources read worse than the rendered page did.
  `json_text_fields` cannot help: it requires `json_html_field` and walks a
  flat path, not a comment tree. The fix is a recursive flattener mirroring
  `flatten_reddit_thread`, which would also make the diffs legible.

- **OPEN: integrate curated X-thread capture.** The accepted design is in
  `docs/design/x-thread-capture.md`. Extraction (`scripts/x_thread.py`) and the
  `capture.py` `x-thread` method, registry validation and audit support landed
  6 Aug 2026, with `clay-attribution` live at tier 3 as the pilot. The lane is
  stable: the last poll pair was +55 -0, six new replies and nothing leaving
  the capture.

  `ingest-x.py --thread --tier N` and `just capture-thread <id>` landed 7 Aug
  2026: the manual first capture registers the block and then hands the
  conversation to `capture.py capture --id <slug>`, so there is one snapshot
  write path rather than two. Enabling a thread on an already-registered post
  stays a human edit of `sources.toml`, because appending a second block would
  give one conversation two registry entries.

  `trustwallet-wasm-update` and `bitcoindevs-explainer-thread` joined the
  pilot on 7 Aug 2026, both at tier 3, which takes the curated tier to five
  sources. Both first captures converged with no gaps declared and nothing
  capped: TrustWallet 51 posts (the whole 1/10 to 10/10 chain the entry's own
  note said was missing, plus 41 replies), Bitcoin_Devs 30 posts with 8 that
  arrived truncated and had to be expanded. Neither produced a diff, because a
  first capture has nothing to diff against.

  Remaining: per-status `withhold_posts`; staging and source-page presentation
  including the reply muting rules.

  Closed 8 Aug 2026: the X-thread absence case now classifies without a human
  reading every one. `auto_classify_noise.py` gained a deterministic X-thread
  churn lane, validated against the held thread diffs, so a stalled or capped
  capture no longer waits on the review agent prompt.

  Settled 7 Aug 2026: `part_of = "<head-id>"` on a member's `[[x_post]]`
  block. Several conversations are already held as N separate entries, one per
  post — Galaxy's updated accounting is eight consecutive status ids, its
  third-wave revision five, Dhruv Bansal's response three — and threading
  those heads would otherwise put the same post on two pages with nothing
  connecting them. The member entries stay: their `title` and `why` are
  curation the capture does not reproduce, and retiring them would break
  citable URLs. Both ends of the relation now say so, `just audit` reports an
  undeclared one, and a head may no longer withhold a status that is
  separately registered, because that withholds nothing. Design record section
  4. The first live case, `opensats-2085363706255573313` inside
  `afilini-2085269060028170742`, was found by the new gate rather than by
  reading.

  Still to do before extending the tier to those groups: split LLFOURN's
  fourteen entries into the conversations they actually belong to. The status
  ids span 31 July to 6 August, so that is not one thread.

  Watch the pilot's diff shape for several days. Additions-only is the healthy
  state. Any removal means either a real deletion or under-collection, and the
  two are not distinguishable from the text alone, so read the depth record in
  the capture's `<TS>.json` before classifying.

- **The nostr lane is the noisiest discovery source by a wide margin, and
  deferral does not reach it.** Of 38 nostr candidates assessed, 36 were
  dismissed; the two that were not were both first dismissed on their title
  and only recovered on a body-read re-check, and one of them was a first-hand
  victim letter. The cause is structural: `discover_nostr.py` sieves the note
  body, and a note is tweet-length, so "mentions coldcard" is close to a null
  filter. The comment-count bar the community lanes now use has no nostr
  equivalent, so those candidates are never deferred. Options worth measuring
  before picking one: a substance proxy (reply or repost count) as the second
  signal; requiring tier-1 vocabulary plus a claim-shaped signal rather than a
  bare mention; or accepting that the lane's value is replies to already
  registered notes rather than standalone discovery. Do not tighten it on the
  title alone: on this lane that is exactly the judgement that was wrong twice,
  and it erred toward discarding primary material. Fold this into the nostr
  probation quality review below rather than doing it separately.

- **RESOLVED 8 Aug 2026: a quarantined registration is surfaced, not
  silent.** `quarantine_registry.py` keeps the tree working after a rejected
  registration (7 Aug 2026), but until today the block sat in
  `quarantine/registry-YYYY-MM.toml` and no gate, page or report mentioned it
  again. Two surfaces now carry it: `just status` lists quarantined
  registrations alongside host proposals, failure streaks, capture failures
  and the unreviewed count; and the alert stream (`scripts/alert.py` to the
  operator UI's `/alerts`) raises every automated host admission by
  `vet_host.py` and reminds daily about a host proposal that has waited
  more than 48 hours, so both halves of the new registration automation
  stay auditable without blocking anything. A proposal that fails
  vetting waits for a human, and restoring a quarantined registration
  remains a human edit.

- **Measure whether the coverage index actually moves the "already
  represented" dismissals.** The index shipped on 7 Aug 2026
  (`scripts/build_coverage_index.py`, `docs/DISCOVERY.md`), addressing the
  largest class of dismissals: 88 of 248 read "already represented by `<id>`",
  and the agent was re-deriving them, once naming a thread it had itself
  dismissed weeks earlier as the precedent. It is unmeasured in use. What to
  read after a few scheduled runs: whether dismissals now name their referent
  more often (an unnamed "repetitive" verdict never becomes an `absorbed`
  count, so it teaches nothing), whether the agent registers fewer duplicates
  of saturated themes, and whether the 57KB index crowds the candidate bodies
  in a 72KB prompt. If it does crowd them, the cut to try first is dropping
  the 465 social posts to those with an absorbed count, since only 6 of 72
  named referents were social posts; say so in the file rather than truncating
  silently.

  **Do not replace it with lexical near-duplicate matching, which was measured
  and does not work.** IDF-weighted cosine over titles, leave-one-out against
  the 425-entry assessed corpus, put the verdict's own named referent top-1 in
  4 of 76 cases (top-3 in 7) while flagging 93 of 174 registered entries as
  near-duplicates: more false alarms than hits. The evaluation was winnable,
  so this is a real negative result rather than a missing corpus: 78% of the
  referents were present as registered entries. The failures are semantic, not
  lexical ("Every influencoor and podcaster who took Coinkite's money" ->
  `basedlayer-influencers-vs-engineers`; "Why does CC still have my email
  address?" -> `coldcard-pii-policy`), so no threshold tunes into working.

- **RESOLVED 8 Aug 2026: X intake consumes the coverage index.** The
  registering `xintake` role that replaced read-only X triage on 8 Aug 2026
  reads the coverage index exactly as the community intake does, which removes
  the duplicate-recall problem over the registered X posts this item
  described. The xtriage prompt and its separate agent binary are retired, so
  there is no second prompt left to extend.

- **SUPERSEDED 8 Aug 2026: X discovery moved to the capture browser.** The
  API-lane probation this item gated on is moot: the operator reversed the
  official-API-only policy on 8 Aug 2026, no App was created, and
  `scripts/discover_x.py` is deprecated. The browser lane
  (`scripts/discover_x_browser.py`, kill switch `X_BROWSER_DISCOVERY_ENABLED`)
  takes its place on its own `discover-x.timer`, with promotion automated
  through the registering `xintake` guard role and driver-side `ingest-x.py`
  capture. Its failure surface is session health — login wall, challenge,
  rate limit, distinct and fail-closed with a cooldown — and the operator
  accepted X's automation-rule suspension risk for the signed-in account in
  writing. The direct-post availability re-check landed the same day:
  `scripts/check_x_availability.py` (12-hourly `x-availability.timer`, kill
  switch `X_BROWSER_AVAILABILITY_ENABLED`) classifies deletion, suspension,
  protection, restriction, login wall, challenge and rate limit separately,
  and only two consecutive absence observations escalate, because absence
  is not deletion.

- **BLOCKED: run nostr discovery probation before any scheduling.** The nostr
  lane is live as of 6 Aug 2026: the identity is published (npub on `/cite/`,
  NIP-05 `_@cc-vuln.org`), and manual posting, NIP-50 discovery and ingest are
  built. Discovery is manual-only under `NOSTR_DISCOVERY_ENABLED`, the same
  model as `discover-x`. The gate is an intake quality review of probation
  candidates, then a separate timer decision that preserves the per-run caps,
  fixed spacing and the kill switch; do not add it to
  `discover-community.service`. The search relay set was expanded the same
  day from the NIP-66 monitor events: `search.nos.today`,
  `nostrja-kari-nip50.heguro.com`, `antiprimal.net`, `relay.ditto.pub` and
  `nostr.wine` all answer live queries from the capture host, so the lane no
  longer depends on a single index; `relay.nostr.band` stays
  TCP-unreachable and six other advertised NIP-50 relays return nothing
  (listed in `docs/operations.md`). The site display path for
  `[[nostr_post]]` registrations is built but unexercised until the first
  registration lands.

- **OPEN: hold the evidence behind a diagnosis, not just the verdict.** Failure
  events now carry `diagnosis` and `http_status` (6 Aug 2026), which is enough
  to group and triage. It is not enough to re-check a judgement later: the
  interstitial body that produced an `origin-challenge`, and the response
  headers that would prove it, are read and dropped. They cannot go in the
  archive, because `cf-ray` names the edge that answered and the header
  allowlist exists to keep the collector's location out of the record. So the
  home for them is private operational state with a retention period, which is
  the same unanswered question as the private-state item below: decide that
  first, then keep a bounded, geo-scrubbed excerpt per failure. Until then a
  diagnosis is checkable only by re-probing, which is what today's work had to
  do.

- **RESOLVED 8 Aug 2026: alert on a failure streak, not on a failure.**
  `just diagnose` already computed the consecutive-failure count that
  separates weather from decay; nothing read it on a schedule, which is how
  `mara-slipstream-portal` stayed broken for four days. The 30-minute
  `alert-sweep.timer` now reads it: `scripts/alert.py`'s sweep turns each
  source's streak into a `failure-streak` alert on the operator-UI stream,
  with thresholds that differ by diagnosis family — `content-below-floor`
  and `content-marker-missing` point at this repository and raise quickly,
  `origin-*` at the publisher and raise slower. A failing unit also keys on
  its status value, so a reminder repeats only when the state changes.

- **RESOLVED 8 Aug 2026: failure delivery is the operator-UI alert stream.**
  The route decision is made and built: `scripts/alert.py` appends
  operator-visible alerts to `~/.local/state/coldcard-archive/alerts.jsonl`,
  and the operator UI (`~/coldcard-operator-ui`, a separate read-only repo,
  port 4322) renders them at `/alerts`. The Signal relay stays opt-in and
  untouched. The components that were being built under the 8 Aug program —
  `record_commit.py`, `vet_host.py`, `discover_x_browser.py`, the page-sync
  role and the installed `publish-scheduled.timer` — are now built and
  installed; their entries below are closed. What this item blocked remains
  open: add the nightly archive audit, production build and link check on
  top of the alert stream, and keep deployment outside that job.

- **BLOCKED: add an off-machine backup with a restore test.** Choose the
  destination and retention policy first. The acceptance test is a documented,
  successful restore of the append-only archive and the registries, not merely
  a successful copy command.

- **BLOCKED: define private-state retention before pruning.** Set periods for
  finalized scheduler ticks, per-job results and service logs. Pending outboxes
  must never be pruned. This is private operational state, not archive history.

- **BUILT 8 Aug 2026: `scripts/record_commit.py`.** Commits guard-passed
  pipeline output on an hourly timer, from a fixed staging allowlist rather
  than `git add -A`. A guard rejection still stops the line — the
  unresolved-guard-run check blocks every commit until a person resolves
  the rejected run — and `.no-publish`, a non-main `HEAD`, a red
  `just test` or `just audit`, and a held writer lock each block the tick.
  `SuccessExitStatus=0 1` keeps a blocked tick from reading as a unit
  failure; only a persistent block alerts.

- **BUILT 8 Aug 2026: `scripts/alert.py` and the operator-UI alert
  stream.** The single alert writer: one JSON line per alert, appended
  idempotently to `~/.local/state/coldcard-archive/alerts.jsonl`, which the
  operator UI (`~/coldcard-operator-ui`, a separate read-only repo, port
  4322) renders at `/alerts`. The 30-minute `alert-sweep.timer` turns the
  repo's existing state files into alerts. This is the failure-delivery
  route resolved above.

- **BUILT 8 Aug 2026: `scripts/vet_host.py` automated host admission.** An
  intake agent files a proposal in `.work/host-proposals.txt`; the
  driver-side vetter applies it after deterministic checks (https only,
  independent DNS, robots.txt, redirect shape), admits the sound ones to
  `registry_hosts.toml` and `agent_egress_hosts.toml`, raises a
  `host-admission` alert per admission and leaves a failed vet for a
  human. The quarantine path is unchanged.

- **BUILT 8 Aug 2026: `scripts/discover_x_browser.py`.** The
  capture-browser X lane in the superseded probation item above:
  home-timeline and watched-profile reads, driver-side only, on its own
  12-hourly `discover-x.timer` under `X_BROWSER_DISCOVERY_ENABLED`, with
  promotion automated through the registering `xintake` guard role and
  driver-side `just ingest-x` first captures.

- **BUILT 8 Aug 2026: the page-sync agent role.** The `sync` role
  (`agent-site-sync.sh`, 12-hourly at 06:20 and 18:20 UTC) edits the site's
  editorial prose from the deterministic staleness packet
  (`scripts/report_site_staleness.py` to `.work/site-staleness.md`), under
  the same guard containment as the other roles. Its output is gated
  post-run on `just check-claims` plus a full gated build, and a gate
  failure rejects the run with an urgent alert. Beside it, the propose-only
  `corrections` role (`agent-corrections.sh`, weekly Sunday 06:40 UTC)
  drafts corrections from the claim sweep's state-changed flags and the
  deterministic `scripts/apply_corrections.py` applies them
  all-or-nothing, dry run unless `--yes`.

- **BUILT 8 Aug 2026: `publish-scheduled.timer` installed.** The timer
  publishes guard-passed, committed work every three hours, and a git push
  follows each successful deploy so `/version.json` and `/cite/` never
  resolve nowhere. The skip conditions are unchanged: `.no-publish`, a
  non-main `HEAD`, a dirty tree outside `archive/`, unreviewed diffs and
  the build lock all still stop the line, and only a genuine publish
  failure fails the unit.

- **OPEN: evaluate Wayback as a per-source fallback.** For a registered source
  that repeatedly refuses direct capture while the Internet Archive can reach
  it, recover only the newest state and mark it `provenance: wayback`. Define
  the consecutive-failure threshold and ensure an inherited capture is never
  presented as one this project took.

---

## 3. Publication and corpus usability

- **OPEN: two figures on `/record/funds/` are typed literals that should be
  read from the archive.** The page now carries the tracker's MOVED total and
  its still-held percentage as prose dated to two named captures, because
  `site/src/lib/trackers.ts` reads headline totals only. That is the failure
  mode the module exists to end, one level down. Add readers for the sub-figures
  rather than leaving a hand-typed number to go stale on the next poll.

- **OPEN: `/how-it-broke/entropy/` does not import `entropy-models.ts`.** Every
  candidate-space figure on that page is hand-typed and every one currently
  agrees with the module, which `/record/firmware/`, `/how-it-broke/conditions/`
  and `ModelExplorer` all import. It is the page most exposed to silent drift.
  A 7 August community model putting the Mk3 seeding word nearer 2^23 than 2^32
  is on the page as a table row and is not in the module either.

- **OPEN: publish a corpus-methods page.** Turn the relevant parts of
  `docs/capture.md` into a reader-facing account of source registration,
  sampling frame, normalizers, capture cadences and known coverage bias. Keep it
  distinct from `/about/#known-limits`, which describes limits of incident
  claims rather than limits of the corpus.

- **OPEN: publish coverage statistics as data.** Add stable breakdowns by
  source organisation and kind, date range, poll outcome and capture count.
  Define denominators and machine-readable fields before designing charts.
  `/version.json` already supplies top-level totals.

- **DONE 7 Aug 2026: explain the cited dice entropy arithmetic.** A
  derived-basis block now sits after the roll-count table on
  `/how-it-broke/conditions/`, before the running-digest callout, so the `#dice`
  anchor and the section order are unchanged. It derives log2(6) per fair roll,
  why independent rolls add, both table totals, that 50 is the smallest count
  reaching 128 bits and that 99 falls about 0.09 bits short of 256, and states
  that fairness and secrecy are physical assumptions the firmware cannot check,
  with the non-uniform case given as Shannon entropy strictly below log2(6). It
  carries no procedure and no seed-generation guidance. Beside it, the held
  material that publishes the same per-roll figure and the argument over what
  bias costs: the honeypot methodology's 2.585 bits per roll, the reddit
  explainer's worked bad-die case, a second-hand relay of a claimed 1971 study
  of 219 dice, and a first-hand casino account that retail casino dice are not
  guaranteed balanced. This archive has tested no die and picks between none of
  them.

- **OPEN: submit published project pages to the Wayback Machine on publish.**
  Archive the site's own public state after a successful deploy and record the
  submission outcome. This gives citations an independent copy. It does not
  replace the commit stamp or change the provenance of source captures.

- **PARTLY DONE: the archival-release policy.** A DOI, release tags, Software
  Heritage ingestion and OpenTimestamps solve different problems and should not
  be added independently.

  Settled 6 Aug 2026, with tooling: a deposit carries project-created material
  only. `just deposit` stages every git-tracked file minus `archive/snapshots/`,
  `archive/x/`, `archive/nostr/` and `archive/runs/`, and `just manifest`
  describes every withheld capture with its hashes and none of its content, so
  the deposit cannot understate the corpus it excludes. Roughly 15 MB staged
  against 485 MB withheld. Reasoning, including why the diffs go in and the
  captured bodies do not, is in `docs/deposit.md`. **Nothing has been
  deposited:** the tooling has no upload path.

  Still open, all operator decisions:

  - **Destination and access level.** Zenodo restricted records give a public
    DOI and metadata with files behind request-access, which is the natural
    home if the captures themselves are ever deposited, since it separates
    preservation from redistribution. Software Heritage carries the same
    question as an open Zenodo deposit, because it ingests the whole
    repository rather than a curated subset.
  - **Whether tags have a real cadence.** Staging falls back to a short commit
    because nothing is tagged.
  - **Whether a timestamped manifest is worth the extra dependency.** The
    manifest now exists and is deterministic, so OpenTimestamps over it is a
    smaller step than it was.

  Until a deposit exists, `/cite/` correctly tells readers to cite the URL and
  commit, and `CITATION.cff` correctly carries no DOI.
