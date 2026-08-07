# Community Discovery and Intake

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
  `DEFER_MAX_COMMENTS` (2) comments or fewer waits under `## Deferred`
- of the 24 such candidates in the assessed corpus, **0** were registered.
  On the two Reddit lanes separately: 20 in r/coldcard, 0 registered; 21 in
  r/Bitcoin, 2 registered
- either signal on its own is not enough. A quiet thread that names the
  incident goes to the agent, and so does a busy thread that does not

Deferral is reversible and nothing leaves the record. The lane that found a
deferred candidate re-reports it for as long as it is in the listing window,
so the comment count on the line is the last one observed, and the lane
promotes the entry to Pending by itself once the thread grows past the bar.
A candidate with no comment count to read (X, nostr) is never deferred: the
rule abstains rather than guessing. To assess one now, move its line to
Pending by hand.

Only the lanes write to `## Deferred`. `agent_guard.py` treats it as a queue
rather than a verdict on both sides of a run, so an agent that moved a pending
line into it would be rejected for disposing of a candidate without recording
why, while a lane appending there mid-run is not mistaken for an invented
verdict.

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

`just coverage-index` (`scripts/build_coverage_index.py`) turns that recall
into a lookup. One line per registered entry across all four tables, since a
candidate can duplicate an X post or a chain monitor's page and not only
another thread:

```
reddit-hardware-wallet-comparison  [reddit]  r/Bitcoin: comparison of major
hardware wallets after the entropy bug  (absorbed 9)
```

`absorbed N` counts candidates already dismissed as duplicates of that entry,
read out of past verdicts and validated against the registry, so hyphenated
prose in a dismissal reason ("repetitive self-custody sentiment") scores
nothing. It is the saturation signal: a theme that has absorbed nine
candidates will absorb a tenth. Each block is sorted most-absorbed first. The
corpus is self-labelling, so nobody maintains this by hand.

The index does not decide anything, and the obvious mechanical alternative was
measured before this was built. IDF-weighted cosine over candidate titles,
leave-one-out against the 425-entry corpus, put a verdict's own named referent
top-1 in 4 of 76 cases while flagging 93 of 174 registered entries as
near-duplicates. The failures are semantic rather than lexical, so no
threshold tunes into working. Judgement stays with the agent; only recall was
replaced.

Two consequences worth knowing:

- the driver builds the index as the operator account before it drops
  privilege, and **refuses to start an agent without one**. An agent that
  cannot see what is covered registers duplicates of it, and duplicates in the
  registry cost far more to undo than a skipped tick
- the index is other people's thread titles, so it is untrusted material and
  goes through the fenced channel with the candidate bodies. The prose telling
  the agent how to read it lives in the trusted template; the generated file
  is data only, and a test enforces that every line is an entry

The verdict format is what keeps this working. A dismissal that names its
referent becomes next run's `absorbed` count; an unnamed "repetitive"
dismissal teaches nothing, and the same theme arrives unmarked next time. The
intake prompt says so.

X triage is deliberately outside this. It is read-only, under probation, and
runs a separate prompt and agent binary; extending the index to it is a
separate decision.

## Rotation

Once assessed, a verdict ages out of the queue rather than accumulating in
it: `just rotate-discovery` moves entries whose verdict stamp is older than
`--keep-days` (31 by default) into `discovery/assessed-YYYY-MM.md`, verbatim
and under the same intake lock the lanes and the agent take. The
discover-community unit runs it after each intake pass, so the queue stays
bounded without anyone remembering. A hand-entered
verdict without a UTC stamp has no assessment date to file under and never
rotates on its own. The destination is the top-level `discovery/` directory,
not `archive/`: rotated verdicts are project records, and every `archive/`
tree is reserved for captured material.
