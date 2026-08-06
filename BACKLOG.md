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

Last reviewed: 6 Aug 2026, after the record-first refit, the 5 August intake,
the citation and corrections work, and the first X-thread extractor.

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

- **FIX: isolate dry-run browser activity from live capture.** Dry runs and
  community discovery share the webbridge browser session with scheduled polls.
  A concurrent run can navigate or close a tab that the writer is reading. The
  maintenance wrapper prevents the overlap it owns, but an independently
  started dry run can still interfere. Add session isolation or serialization
  without starting an ad hoc replacement for `webbridge.service`.

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

- **GAP: incident population denominators.** The number of distinct human
  victims and the number of seeds generated under vulnerable firmware remain
  unknown. Address counts overstate owners, and no captured unit-sales or seed
  count supplies the denominator. These are published limitations, not numbers
  for this project to estimate.

### Current-state monitoring

- **MONITOR: official technical follow-up.** As of the bounded 6 August check,
  Coinkite's promised technical postmortem had no publication date or detailed
  scope, no captured source announced a post-incident independent audit of the
  fixed firmware, and NVD keyword searches identified no incident-specific CVE.
  The March 2022 paid pre-release review in Coinkite's chronology is historical
  context and does not answer the post-incident audit question. Recheck these
  together through the claim-verification sweep.

- **MONITOR: filings, regulator statements and compensation decisions.** The
  legal page records organising activity, not a commenced proceeding, as of 6
  August. A statement of claim, arbitration notice, regulator or insurer
  statement, or a vendor refund or replacement decision would change that
  section. Keep private legal activity outside the claim until a document is
  public.

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

- **BLOCKED: two sources are unreachable from this collector's VPN exit.**
  Both need an operator choice, not a registry edit. `theblock-galaxy-total`
  gets a Cloudflare managed block on the whole theblock.co domain: 403 with
  `cf-mitigated: challenge` to a scripted fetch, and an interstitial that never
  clears in the capture browser after 60 seconds. `coldcard-watch` remains live
  but is subject to the DNS block already written up in `AGENTS.local.md`:
  public DNS and a direct HTTPS check through its public address both succeeded
  on 6 August, while an ordinary request from this host still cannot resolve
  the name. The exit uses a shared hosting ASN, which is also why the Internet
  Archive answers this host with 429, so
  the Wayback fallback below cannot rescue either one from here. Options are to
  move the VPN exit, allowlist at the resolver for the DNS case, or accept the
  gap. Do not use `watch = "frozen"`: the material is neither exhausted nor
  withdrawn, and there is no registry state for "live but unreachable from
  here". Eight captures of the Block article are held to 3 Aug 2026, so this
  is a monitoring gap rather than lost material.

- **OPEN: make the manual builds take the build lock.** `AGENTS.md` says every
  build serialises on `flock /tmp/cc-build.lock`, and the documented recovery
  from not doing so (a corrupted Astro cache reported as a missing
  `renderers.mjs`) is specific enough that someone has hit it. Only
  `publish-scheduled.sh` actually takes the lock; `just build-site`,
  `build-preview`, `build-site-full` and `build-site-indexable` do not, so a
  manual build started while the publish timer is mid-run still collides. Since
  6 Aug 2026 the four builds share one body (`_astro`), so the lock now has a
  single place to go. Decide the contention behaviour first: `flock -w`
  and wait, which is right for a person at a terminal, or `flock -n` and refuse,
  which is right for anything on a timer. They are not the same choice and the
  recipes are used both ways.

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

  Remaining: `ingest-x.py --thread` and a `just capture-thread` recipe;
  `trustwallet-wasm-update` and `bitcoindevs-explainer-thread` added to the
  pilot; per-status `withhold_posts`; staging and source-page presentation
  including the reply muting rules; and the X-thread absence case added to the
  review agent prompt, so a stalled or capped capture classifies without a
  human reading every one.

  Watch the pilot's diff shape for several days. Additions-only is the healthy
  state. Any removal means either a real deletion or under-collection, and the
  two are not distinguishable from the text alone, so read the depth record in
  the capture's `<TS>.json` before classifying.

- **BLOCKED: finish X discovery probation before scheduling it.** Manual
  watched-account discovery and read-only triage exist. Unattended use still
  needs approval of the API use case, retention obligations and spend, plus
  observed authentication and intake outcomes. Direct-post availability checks
  also need deletion, suspension, protection, access restriction and
  authentication failure to remain distinguishable. Do not add X discovery to
  a timer before the gate in `docs/design/discovery-and-x-watch.md` is met.

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

- **OPEN: alert on a failure streak, not on a failure.** `just diagnose`
  computes the consecutive-failure count that separates weather from decay, but
  nothing reads it on a schedule. A source failing 105 polls and a source
  failing twice look identical in the poll's own output, which is how
  `mara-slipstream-portal` stayed broken for four days. The threshold should
  differ by diagnosis: `content-below-floor` and `content-marker-missing` point
  at this repository and should raise quickly, `origin-*` at the publisher.
  Depends on the delivery route below.

- **BLOCKED: choose failure delivery, then add the nightly integrity job.** A
  nightly archive audit, production build and link check are valuable only if a
  failure reaches the operator. The optional Signal relay is implemented but
  enabling it changes and restarts an external notification service. Once a
  delivery route is approved, add the audit job and keep deployment outside it.

- **BLOCKED: add an off-machine backup with a restore test.** Choose the
  destination and retention policy first. The acceptance test is a documented,
  successful restore of the append-only archive and the registries, not merely
  a successful copy command.

- **BLOCKED: define private-state retention before pruning.** Set periods for
  finalized scheduler ticks, per-job results and service logs. Pending outboxes
  must never be pruned. This is private operational state, not archive history.

- **OPEN: evaluate Wayback as a per-source fallback.** For a registered source
  that repeatedly refuses direct capture while the Internet Archive can reach
  it, recover only the newest state and mark it `provenance: wayback`. Define
  the consecutive-failure threshold and ensure an inherited capture is never
  presented as one this project took.

---

## 3. Publication and corpus usability

- **OPEN: publish a corpus-methods page.** Turn the relevant parts of
  `docs/capture.md` into a reader-facing account of source registration,
  sampling frame, normalizers, capture cadences and known coverage bias. Keep it
  distinct from `/about/#known-limits`, which describes limits of incident
  claims rather than limits of the corpus.

- **OPEN: publish coverage statistics as data.** Add stable breakdowns by
  source organisation and kind, date range, poll outcome and capture count.
  Define denominators and machine-readable fields before designing charts.
  `/version.json` already supplies top-level totals.

- **OPEN: explain the cited dice entropy arithmetic.** Add a short
  derived-basis explanation near `/how-it-broke/conditions/#dice`: why a fair
  six-sided roll contributes `log2(6)` bits, how independent rolls accumulate,
  and why fairness and secrecy are assumptions. Keep it incident-specific and
  do not turn it into a seed generator or general wallet procedure.

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
