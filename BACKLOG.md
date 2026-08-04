# Content backlog

What still needs acquiring, verifying, or writing. Ordered by value, not effort.

Status key: **BLOCKED** needs something only the maintainer can do · **OPEN** ready to
work · **GAP** evidence has not been identified publicly and may not be gettable
· **FIX** something already published that is wrong or unbalanced.

Last reviewed: 3 Aug 2026, after the weekend intake batch (40 sources
registered and integrated), the captures-front-and-centre IA review and
its P1/P2 implementation (decision record at
`docs/design/captures-front-and-centre.md`).

---

## 0. Follow-ups from the 3 Aug intake batch and IA implementation

**OPEN.** The batch registered 38 X posts and two web sources, captured all but
the two below, and wove the material into twelve pages. What remains:

- [x] **stackernews-prior-warning-video first capture.** Done 4 Aug 2026:
      capture.py gained `fetch_post` (a raw JSON body the http backend POSTs),
      and both stacker.news sources now poll the site's GraphQL API with a
      fixed query instead of the crashing browser route; first GraphQL
      captures are held for both. The work surfaced two adjacent bugs, both
      fixed the same day: the agent-maintenance quiet window never actually
      waited for the Type=oneshot poll service (`is-active` reads "activating"
      as not active), and a crashed target tab let the capture browser read
      back a different page, which filed MARA portal text under this source at
      20260804T001640Z (kept, classified in revision-reviews.toml; a
      tab-identity check in fetch_browser now refuses that).
- [ ] **kevinkelbie-tracker-update screenshot.** Registered and text-read, but
      ingest-x refuses the capture because the post's attached media never
      hydrates (six attempts across a webbridge restart). Retry later; the
      figures are already integrated as reported claims.
- [ ] **Alex Thorn's wave-4 thread.** Parent of both registered intangiblecoins
      corrections and of korraflow's Duel reply; the original claim thread and
      its pastebins are unregistered. Primary for the wave-4 story.
- [x] **Zenul_Abidin material** behind btctherapist-prior-drain-report. Done
      4 Aug 2026: the nvk.wtf reactions index (nvkwtf-reactions) linked both
      the circulation post and the underlying report. The X post where the
      screenshot circulated is registered and captured as
      zenulabidin-drain-report-screenshot; the underlying report is
      Economy-Cash6726's r/ledgerwallet comment of 13 Mar 2024 (drain said to
      be 2022), registered and captured as reddit-ledgerwallet-drain-comment.
      The thread's own replies contest the "no dice rolls" claim.
- [ ] **The prior-warning video** (youtu.be/oj_W3xOlt6U) linked from
      stackernews-prior-warning-video. Video capture is outside the pipeline;
      decide whether to add a yt-dlp lane or hold the thread as the pointer.
- [x] **Kelbie's railway tracker** (coldcard-hack.up.railway.app, announced at
      1,367 BTC / 4,620 addresses). Resolved 4 Aug 2026: Kelbie moved the
      tracker to coldcard.rip on 3 Aug (kevinkelbie-tracker-move), and
      coldcard.rip is registered and polled as coldcard-rip-tracker. No second
      polling source needed.
- [ ] **profedustream's JSON export artefact.** The announcement post is
      captured; the linked export file is not.
- [ ] Registered but deliberately not woven anywhere yet: vladcostea's
      disclosure-history thread (no page owns disclosure-culture history),
      robhamilton-bip39-reevaluation, the two Bitkey design posts
      (benowhere-bitkey-entropy-answer, claygarrett-bitkey-entropy-design;
      integrating them risks reading as product endorsement), and
      kevinkelbie-gpu-farm-question.

## 0a. Captures front and centre: remaining pieces

The IA review's P1 and most of P2 are implemented (decision record at
`docs/design/captures-front-and-centre.md`; policy at
`docs/design/capture-display-policy.md`; artefact-first source pages; homepage
record band with freshness, latest events and named journeys; capture wall and
freshness stamp on /record/; feed in the subnav; phase-aware nav;
`withholdsCapturedMedia()` consulted by every media renderer). Still open:

- [x] **R7: thumbnails beside citing entries.** Done 3 Aug 2026: a shared
      `CaptureThumb` component (both display gates applied, nothing rendered
      when either fails) placed 41 thumbnails inside rungs and entry bodies
      on /record/timeline/, /record/analysis/ and /response/, one per dated
      entry, never above Answers.
- [x] **R9: a "with screenshots" facet on the feed.** Done 3 Aug 2026:
      RecordSearch facets accept an optional boolean-attribute form
      (`{label, attr}`) alongside data-group equality; the feed stamps
      `data-has-shot` only on entries whose image actually rendered. Existing
      consumers unchanged; no-JS behaviour unchanged.
- [ ] **R11: per-source og images** from staged screenshots. Policy now
      exists; still deliberately parked until after a public deploy settles.
- [ ] **Presentation duties from the policy, partially outstanding:** capture
      timestamps shown beside media on the feed and cards (the source page
      has them), and the removal-route line next to displayed media (stated
      on /about/ today).

## 1. Open questions not answered in the archive

**GAP.** Worth stating on the site as open, because that is itself information.

- [ ] **STM32 UID low-word distribution across real devices.** No population
      measurement has been identified in this archive as of a recheck on 3 Aug
      2026 (GitHub, web search, HN and Stacker News surfaces). It would
      constrain the UID term in published candidate-count models, but would not
      settle SysTick, RTC, call-history or derivation-cost assumptions. See the
      research plan under `docs/research/`.
- [ ] No publication of instagibbs' two reproduction scripts has been captured
      or linked in the archive. A 4 Aug 2026 recheck covered his public GitHub
      repositories and rendered gist index without finding them; a 3 Aug 2026
      recheck also covered issue and comment surfaces and his personal site;
      unauthenticated global code search was not reachable either time, so a
      script pasted inside an unrelated repository cannot be excluded. The
      bounded method is preserved under `docs/reviews/`.
- [ ] Number of distinct human victims unknown; address counts overstate owners.
- [ ] Total population of seeds generated under vulnerable firmware unknown.
      No unit-sales figure has been identified in the captured Coinkite material.
- [ ] Coinkite's formal technical review: promised, with no publication date or
      scope identified as of a recheck on 4 Aug 2026. The official
      blog index and current firmware repository were checked again; the
      vendor's 2 Aug update still describes the postmortem as forthcoming.
- [ ] No CVE request or assignment has been identified in the checked sources as
      of 4 Aug 2026. NVD keyword queries for `COLDCARD` and `Coinkite` returned
      only the earlier `CVE-2019-14356` OLED side-channel record.
- [ ] No third-party audit announcement has been identified in the checked
      official blog, current firmware repository or incident pull-request set as
      of 4 Aug 2026. These negative results do not address private work.

## 2. Archive and tooling

- [ ] Dry runs share the capture browser session with live polls. A dry run
      during an active poll navigates and closes tabs in the same browser the
      poll is reading from (observed 4 Aug 2026, when a full dry run
      overlapped a tick the maintenance wrapper had failed to wait for). The
      fixed wrapper prevents the overlaps it knows about, but a dry run
      started outside it can still interfere. Consider a separate webbridge
      session for dry runs.
- [ ] Astro dev server is unusable: vite fails on import.meta chunk splitting.
      Production build is fine, so local preview builds and serves dist. Worth
      fixing properly rather than living with the rebuild loop.
- [ ] Signal alerting via an internal notification relay is written but
      deliberately off. Enabling it edits the relay's route config (host set
      via `NOTIFY_SSH_HOST` in `.env`) and needs a restart.
- [ ] Add unattended X watching only after authentication failure, deletion,
      suspension and access restriction can be distinguished reliably.
- [ ] Add a nightly archive/build/link audit after defining failure alerting.
      Keep deployment outside that job.
- [ ] Add a daily off-machine backup after choosing retention and proving a
      restore procedure.
- [ ] Define retention for finalized scheduler ticks, per-job results and
      service logs before adding a pruning job. Pending outboxes must never be
      pruned.

## 3. Standing rule: the disclosure shape is not optional

The August 2026 consolidation found the cause of "far too much content" was not
over-writing: it was that step 8 of the IA design doc, refitting every editorial
page onto the disclosure component, was never done. One page had it; 26 opened
flat at full precision. The fix is durable only if new work keeps the shape.

- [ ] Every new editorial page is built as standfirst / `<Answer>` / detail
      ladder / artefacts from its first commit, not refitted later.
- [ ] A new content tier (the library and the digest, sections 5a and 5b of the
      IA design doc) ships only after the pages it sits beside are already
      rung-shaped.
- [ ] `just check-links` runs in every build gate. It exists because the merges
      left 163 silent 404s that nothing else caught.

## 4. Ideas parked (unscoped)

Raw ideas from the operator, captured so they are not lost. Unscoped and
undesigned until promoted into a numbered section or the IA design doc.

- **Captured-tweet timeline, infinite scroll.** Largely landed 3 Aug 2026:
  /record/feed/ already interleaved captures chronologically with progressive
  reveal, and it is now in the subnav, on the homepage record band, and paired
  with the register's capture wall. The copyright posture is settled by
  `docs/design/capture-display-policy.md`. Residual piece is section 0a's R9
  facet. (2 Aug 2026, updated 3 Aug 2026)

- **Interactive entropy widgets.** Hands-on explainers for "what is entropy
  and how does it map to a secure seed": for example a live demonstration of
  how many guesses N bits costs, a weak-versus-strong RNG output comparison
  the reader can step through, or a dice-roll entropy counter. Would extend
  /how-it-broke/entropy beyond prose and static diagrams. Design questions
  before scoping: client-side only with no dependencies, consistent with the
  ten-year rule for everything outside site/; how interactive claims carry
  evidence-scope notes; and never letting a widget resemble a seed generator
  a reader might actually use, which intersects the scam-wave safety rules.
  (2 Aug 2026)

- **Display the captures, starting with tweet screenshots.** Resolved 3 Aug
  2026: the IA review chose the evidence-wall-plus-feed hybrid (ledger wall
  rejected; /record/changes/ already is one), the decision record is at
  `docs/design/captures-front-and-centre.md`, the policy revision is written
  at `docs/design/capture-display-policy.md` and stated publicly on /about/,
  and the P1/P2 implementation shipped (see section 0a for what remains).
  (2 Aug 2026, resolved 3 Aug 2026)

- **Wayback as a per-source capture fallback.** The Block intermittently
  refuses the archive host: scheduled polls succeeded at 00:17, 00:59 and
  04:43 UTC on 2 August, returned 403 at 08:00 and 08:29, then succeeded
  again at 09:00. Manual curl carrying a browser user agent was refused from
  every egress path tried while capture.py's own identifying agent still got
  through, so this is rate limiting or bot heuristics rather than a standing
  block on the address. The Wayback Machine still
  reaches it, and wayback.py plus the provenance:wayback convention already
  exist. A per-source fallback ("if direct capture is blocked N consecutive
  polls, pull the newest Wayback snapshot, marked wayback") would keep the
  record continuous without weakening egress isolation. (2 Aug 2026)

- **Explain the quoted entropy arithmetic.** Figures like Wizardsardine's
  "each roll of a perfectly fair 6-sided die is worth 2.585 bits" are quoted
  across the site and in sources without the reader being shown where the
  number comes from (log2(6)), why fairness and secrecy of the rolls matter,
  or how rolls accumulate toward 128 bits. Candidates: a short derived-basis
  aside on /risk/mitigations/#dice, a glossary entry, and one of the interactive
  widgets above. Source: wizardsardine.com/blog/coldcard-rng-vulnerability/
  (already tracked as wizardsardine-postmortem). (2 Aug 2026)

- **Mine Stacker News properly.** The site exposes a public GraphQL API at
  stacker.news/api/graphql that returns an item with its text, author and
  comment count, and supports a search query with a sort argument. That is a
  much better capture route than the browser. Two caveats found on 2 Aug
  2026: search did not surface the recent incident threads at all, returning
  items from 2022 to 2024, so discovery needs the territory feed or known item
  ids rather than keyword search; and no robots.txt is served (the path returns
  the application shell), so crawl permission should be confirmed with the
  operators before polling at any volume. Updated 4 Aug 2026: the API is now
  the capture route for both registered threads (stackernews-drains-since-2022
  and stackernews-prior-warning-video, items 1538415 and 1538447) via
  `fetch_post` in capture.py, after the browser route began crashing the
  capture tab sitewide. Largely resolved 4 Aug 2026: the ~bitcoin and
  ~security recent feeds were enumerated back to 30 Jul, 50 incident threads
  were registered and first-captured (the `stackernews-*` batch at the end of
  sources.toml), and `scripts/discover_stackernews.py` (`just
  discover-stackernews`) keeps discovery going at two requests per run, fired
  every 12 hours by `discover-community.timer` on the capture host, chained
  with the intake agent (`agent-discovery-intake.sh`, REVIEW_AGENT_BIN)
  which assesses candidates, registers relevant threads in `sources.toml`
  itself (and may correct existing `stackernews-*`/`reddit-*` entries),
  first-captures
  each registration via `just capture-one`, and records verdicts in the
  tracked `DISCOVERY.md` intake file. Reddit joined the same pipeline on
  4 Aug 2026: `scripts/discover_reddit.py` reads the r/coldcard and r/Bitcoin
  /new listings through the capture browser session (anonymous JSON is 403
  from this host), with every new r/coldcard post queued and r/Bitcoin
  keyword-sieved; the first enumeration's backlog is being assessed by the
  intake agent in bounded chunks of 15 per run. BitcoinTalk followed the same
  day: `scripts/discover_bitcointalk.py` reads the Bitcoin Discussion and
  Wallet software board indexes directly (SMF answers this host; robots.txt
  carries only a sitemap line), and registered threads capture the print view
  (`action=printpage`) because `;all` is Cloudflare-challenged from this
  host. Still
  open: the permission question above before any higher-volume polling, and
  title matching misses oblique thread titles (run with `--all` for a full
  manual sweep when the feed is busy). (2 Aug 2026, updated 4 Aug 2026)

- **Provider-communications intake (client emails).** Custody and platform
  providers sent incident guidance to clients by email before or instead of
  posting publicly: Nunchuk reportedly emailed subscribers before its X
  thread, and the "How this affects Nunchuk platform keys" text is known only
  from a community screenshot (csbastiat-nunchuk-criticism). Those emails are
  not capturable and currently enter the record only as secondhand crops.
  Build an intake lane for forwarded provider notices: solicit .eml forwards
  via the contact email and an issue template; verify provenance with DKIM
  signature checks where headers survive forwarding (fits the
  provenance-first bar and gated-publication scam defence, since fake
  "provider emails" are an active phishing vector this incident); redact
  recipient details; store under a new provider-comms evidence class with
  org, sent date, subject, channel and verification status. Candidate first
  asks: the Nunchuk subscriber notice, any Unchained client email (their X
  thread mentions Support PINs, implying direct client contact), Casa member
  notices. (3 Aug 2026)

- **Custody-provider coverage gaps.** Theya, Onramp, Swan and River had no
  findable public statement as of 3 Aug 2026 despite Theya being a Coldcard
  export target and River reportedly seeing a 3,679 BTC one-day inflow
  (secondary claim, bitcoinworld.co.in; no River primary found). Nunchuk's
  platform-key statement primary is still unlocated (blog is JS-rendered;
  would need webbridge capture if one exists); a community description of it
  (2-of-4 platform key generated on a Mk4 with a custom derivation path) is
  now held as colourorange-nunchuk-platform-key, 3 Aug 2026. Periodically re-sweep provider
  blogs/status pages and X accounts; register statements when they appear.
  Absences are themselves worth recording per the casa-blog-index pattern.
  (3 Aug 2026)
