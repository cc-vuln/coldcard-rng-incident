# Community Discovery and Intake

Discovery is separate from capture: Stacker News keyword search does not
index recent items, and Reddit has no usable anonymous search from this host.
Three scheduled community scripts and the capture-browser X lane on its own
timer feed one structured intake record:

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
- `just discover-x` reads the home timeline and the curated `[[x_watch]]`
  profiles through the capture browser, driver-side only, under the
  `X_BROWSER_DISCOVERY_ENABLED` kill switch. First contact reads back to the
  watch's `since` date or stable exhaustion and queues that history; reaching
  the hard pass cap first leaves the watch uncheckpointed. It fails closed
  with a cooldown on the session-health classes (login wall, challenge, rate
  limit). A full run that parses zero posts across the timeline and every
  watch is also a sick session — pages can render empty while the structural
  probe reads "ok" (observed 10-11 Aug 2026) — and stops the same way. While
  a cooldown is active the X intake skips its agent run: no candidate body
  can hydrate, so there is nothing to assess. Since 8 Aug 2026 it runs on its own
  `discover-x.timer`, separate from `discover-community`. The official-API
  lane it replaced (`scripts/discover_x.py`) is deprecated

Each discovery run reconciles its candidate observations into the immutable,
hash-chained transactions under `discovery/transactions/`. The shared
`.work/locks/discovery.lock` serialises every writer. Per-candidate JSON,
state- and platform-sharded Markdown pages, `discovery/state.json` and the
root `DISCOVERY.md` are generated from those transactions; they are working
views, not parallel records and not files to append or edit by hand.

On the capture host `discover-community.timer` runs the three community
discoveries every 12 hours, chained with the intake agent
(`agent-discovery-intake.sh`, the same
REVIEW_AGENT_BIN pattern as agent-review.sh), which assesses each pending
candidate (bounded chunks of 15 per run while a backlog exists), appends
registrations to `sources.toml` in the established shapes, may correct
existing `stackernews-*`/`reddit-*` entries with the reason in its report,
first-captures each community registration via `just capture-one` (exit 10 is
healthy; exit 21 defers to the next poll), and submits one JSON verdict per
candidate. Candidate selection and verdict application each use a short
discovery lock; no discovery lock is held while evidence is hydrated or an
agent runs. The protected packet binds every candidate id to its current event
head; the displayed queue line is presentation only. The operator-side
applier refuses the whole batch if any candidate is no longer pending or its
head changed, then commits all validated decisions in one immutable intake
transaction. This optimistic handoff lets other discovery lanes keep writing
without admitting a stale decision. Explicit `retry` events leave the
candidate pending. With REVIEW_AGENT_BIN unset, candidates remain pending for
human triage. stacker.news serves no robots.txt, so there
is no published crawl policy; keep discovery at this volume unless the
operators have been asked. The recurring community service still does not run
X discovery: the browser lane has its own `discover-x.timer`, so an X session
failure cannot stall the community lanes and a community backlog cannot hide
X candidates. Since 8 Aug 2026, queued X candidates are assessed by the
registering `xintake` guard role, which consumes the same bounded packet as the
community intake and submits the same verdict outbox. The driver captures each
approved post with `just ingest-x` afterwards; the agent never reaches the
browser. The read-only xtriage prompt and the `--include-x` admission flag are
retired. Direct manual `just ingest-x` capture is unchanged and does not pass
through structured intake. Full polls remain the scheduled runner's alone.

A person with candidate URLs in hand drops them in
`.work/operator-candidates.txt`, one per line; both intake drivers run
`scripts/queue_candidates.py` at the top of a run, which records X status
permalinks as X candidates and community-platform URLs as community
candidates (duplicates and unrecognized URLs are reported, not admitted).
Dropped candidates are hydrated, assessed and given verdicts exactly like
lane-found ones.

The three community listing commands accept `--reconsider-seen` for a bounded
historical audit of the listing surface they can currently read. It ignores the
lane's saved seen set but still deduplicates registered and assessed URLs.
Combine it with `--no-state` for a read-only inspection or omit `--no-state`
to record newly relevant observations. This does not make the listing
historical: Reddit `/new`, the selected Stacker News pages and the selected
BitcoinTalk board pages remain bounded windows, and older material needs a
dated search or direct candidate intake.

## What the agent is not asked to read

The vocabulary the lanes sieve on is in two tiers. Tier 1 (`coldcard`,
`coinkite`, `nvk`, `rng`, `slipstream`, `btcrecover`, the BTC figures) names
this incident and little else. Tier 2 (`entropy`, `dice`, `seed phrase`,
`passphrase`, `hardware wallet`, `self-custody`, `phishing`, `hack`, `theft`,
`stolen`, `drain`, `sweep`, `bitkey`, `opensats`) is the bitcoin-security
vocabulary the incident borrows, and on its own it describes the subject area
rather than the event.

The tiers do not decide what is queued. Measured against the 425 assessed
candidates on 7 Aug 2026, refusing tier-2-only titles would have removed 58
dismissals and lost 33 registered sources, which is the wrong trade for a
record whose purpose is preservation. What the tier decides is where a
candidate waits, and only in company with a second weak signal:

- a candidate whose title never names the incident **and** which has drawn
  `DEFER_MAX_COMMENTS` (2) comments or fewer starts in the `deferred` state
- of the 24 such candidates in the assessed corpus, **0** were registered.
  On the two Reddit lanes separately: 20 in r/coldcard, 0 registered; 21 in
  r/Bitcoin, 2 registered
- either signal on its own is not enough. A quiet thread that names the
  incident goes to the agent, and so does a busy thread that does not

Deferral is reversible and nothing leaves the record. The lane that found a
deferred candidate re-reports it for as long as it is in the listing window,
so its observation history is retained and the generated view shows the
latest comment count. Once the thread grows past the bar, the next observation
promotes its projected state to `pending`; the earlier deferred observation
remains in the transaction history. A candidate with no comment count to read
(X, nostr) is never deferred: the rule abstains rather than guessing. To assess
one now, list the deferred candidates and record an explicit state event:

```bash
.venv/bin/python scripts/discovery_store.py list \
  --state deferred --format json
.venv/bin/python scripts/discovery_store.py set-state reddit:abc123 pending \
  --reason "manual promotion for assessment"
```

Only the lanes assign the initial deferred state automatically. The canonical
store and its generated views are read-only to the agent account; intake
decisions cross the boundary as data, and the operator-side applier can change
only pending packet candidates whose bound event heads still match.

This is a bar on what the agent reads first, not a filter on what is found:
the underlying sieve is unchanged, every lane's `--all` still reports
everything new, and a dry run prints the destination (`deferred`, `topical`,
`body`) beside each candidate.

## What the record already covers

The largest class of intake dismissals is not something a sieve can reach. Of
248 dismissals in the assessed corpus on 7 Aug 2026, 88 read "already
represented by `<id>`": the candidate is genuinely about the incident and the
record already holds that theme. The agent reached those verdicts by recalling
`sources.toml` across a long prompt, and it re-derived them, once naming a
thread it had itself dismissed weeks earlier as the precedent for dismissing
another.

`scripts/build_intake_packet.py` turns that recall into a bounded lookup. Each
candidate occurs exactly once beside its hydrated body, stable native-object
key and mechanical exact-registry match. A candidate can duplicate an X post
or a chain monitor's page and not only another thread, so the packet also
retains every registered entry that has absorbed a previous duplicate:

```
reddit-hardware-wallet-comparison  [reddit]  r/Bitcoin: comparison of major
hardware wallets after the entropy bug  (absorbed 9)
```

`absorbed N` counts candidates already dismissed as duplicates of that entry,
read out of structured verdict facts and validated against the registry, so
hyphenated prose in a dismissal reason ("repetitive self-custody sentiment")
scores nothing. It is the saturation signal: a theme that has absorbed nine
candidates will often absorb a tenth. Rows with no absorbed history are
counted, not copied into every prompt; exact native-id matches are still
reported per candidate. The corpus is self-labelling, so nobody maintains
this by hand. `just coverage-index` remains the complete human-readable view.

The index does not decide anything, and the obvious mechanical alternative was
measured before this was built. IDF-weighted cosine over candidate titles,
leave-one-out against the 425-entry corpus, put a verdict's own named referent
top-1 in 4 of 76 cases while flagging 93 of 174 registered entries as
near-duplicates. The failures are semantic rather than lexical, so no
threshold tunes into working. Judgement stays with the agent; only recall was
replaced.

Two consequences worth knowing:

- the driver builds and retains the JSON packet as the operator account before
  it drops privilege, and **refuses to start an agent without one**. An agent that
  cannot see what is covered registers duplicates of it, and duplicates in the
  registry cost far more to undo than a skipped tick
- the packet contains other people's thread titles and bodies, so it is
  untrusted material and goes through one fenced channel. The prose telling
  the agent how to read it lives in the trusted template; the generated file
  is data only, and a test enforces that every line is an entry

The verdict format is what keeps this working. A dismissal that names its
referent becomes next run's `absorbed` count; an unnamed "repetitive"
dismissal teaches nothing, and the same theme arrives unmarked next time. The
intake prompt says so.

The X lane is no longer outside this (amended 8 Aug 2026): the registering
`xintake` role consumes the same bounded packet as the community intake
does, which removes the duplicate-recall problem over the registered X posts
that the read-only xtriage prompt solved by hand. That prompt and its
separate agent binary are retired.

## Storage, views and recovery

The canonical history is one immutable transaction per logical batch, stored
under `discovery/transactions/YYYY-MM/`. Each file names its sequence and
content hash, points to the previous transaction and carries a stable operation
id, so an interrupted caller can replay the same operation without creating a
second decision. Observations, retries, verdicts and explicit state changes are
events; changing a candidate's current presentation never rewrites an earlier
event.

Everything organised for reading is generated from that chain:

- `discovery/candidates/<platform>/<native-id>.json` is the current projection
  for one candidate, including retained observation, retry and verdict history
- `discovery/views/pending/`, `deferred/` and `human-review/` are split by
  platform and into pages of at most 100 candidates
- `discovery/views/assessed/YYYY-MM/` is split by verdict month, platform and
  the same page size
- `discovery/state.json` carries the transaction head, semantic root and
  inventory; root `DISCOVERY.md` is a small generated index into those views

There is no retention rotation after cutover. An assessed candidate appears in
the appropriate generated month without being moved or removed from canonical
history. The former `rotate_discovery.py` command is a compatibility renderer:
its age options no longer delete or select anything. `just discovery-check`
validates the transaction chain, the one-time migration manifest and exact
legacy-source copies, then compares every generated projection with a clean
rebuild. Rendering can repair presentation files; it cannot alter the
transactions they came from.

The cutover also retains the old root queue and monthly assessed files
byte-for-byte under `discovery/migration-v1/legacy/`, with hashes and exact
line provenance in the migration manifest. That preserves what readers saw
before the reorganisation while making current material smaller and easier to
find. Discovery remains a project record beside `archive/`; only captured
source material belongs under `archive/`.
