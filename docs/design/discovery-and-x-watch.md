# Design: periodic discovery and X account watching

**Status:** known-URL capture and tier-aware scheduling built; community
discovery scheduled; bounded X watch and LLM triage built for manual probation;
X scheduling, deletion checks, nightly audit and backup remain proposed
**Date:** 5 Aug 2026

The question this answers: how does the archive notice *new* information,
rather than only noticing edits to pages we already know about?

---

Current implementation, verified against the working tree on 3 Aug 2026:

- `capture.py` polls registered HTTP and browser sources, writes structured run
  results, and distinguishes changes from incomplete runs.
- `capture-x.sh` captures registered X post URLs manually through gallery-dl;
  `ingest-x.py` is the authenticated-browser fallback for individual posts.
- Source-specific normalisers, a shared archive-writer lock and `just audit` are
  built. One 30-minute scheduled tick invokes a due-state runner for
  non-overlapping source groups and emits one aggregate notification for
  changes or failures.
- `[[x_watch]]` and `scripts/discover_x.py` now perform shallow watched-account
  discovery through the official read-only X API, with local credential
  preflight, bounded profile and post counts, first-run baselining, private
  state, hard-stop failure classes and `DISCOVERY.md` intake. The live command
  is opt-in and manual during probation. It is not installed in the recurring
  community timer.
- Stacker News, Reddit and BitcoinTalk discovery feed the same intake file and
  are scheduled every 12 hours. Direct X-post availability classification,
  nightly audit and backup are not built.

## 1. What "check for new information" actually decomposes into

Three different jobs, with different mechanics and different reliability:

1. **Watch known URLs for change.** Built. `capture.py` performs each capture;
   `scheduled_runner.py` assigns each web source to one due-state group. The
   former all-sources timer was replaced after dynamic page chrome created
   false revisions.
2. **Watch known actors for new artefacts.** An X account, a blog index, a
   GitHub repo. New URLs enter the record without anyone pasting them in. This
   is what "a few people on X worth tracking" means mechanically, and it is
   the genuinely new piece of work.
3. **Watch the topic for new sources.** New reporting, new PoC repos, new
   victim threads. The noisiest job, and only partly scriptable without
   authenticated sessions.

The design below covers all three, in that order of maturity.

## 2. Principles, inherited from the repo's existing rules

- **Discovery is not publication.** Discovery fills `DISCOVERY.md`; only
  registered sources appear on the site. The intake agent may assess and
  register bounded community batches under a standing prompt. X uses a
  separate read-only triage prompt and stops at a human-review recommendation.
  Every promoted item still needs a scoped `why`, attribution and a successful
  manual first capture.
- **Discovery records are operational leads.** Machine-discovered X IDs and
  recommendations are never presented as archive captures or published
  evidence.
- **Stdlib-only Python**, with `gallery-dl` the one exception and confined to
  social capture. New discovery code follows the same rule: HN and GitHub both
  offer unauthenticated JSON APIs, so no new dependency is needed.
- **Read-only official API.** No posting, following, liking, browser scripting,
  home feed or search. Timeline reads only. The signed-in account session is
  not part of discovery.
- **Alert on signal, never on activity.** A run that found nothing says
  nothing.

### Open-source and platform-policy review, 5 Aug 2026

`lhl/tweetxvault` was inspected as operational prior art. Its useful patterns
are credential preflight, a fresh head pass on every run, process locking,
atomic state advancement, raw-plus-normalized records and explicit rate-limit
cooldowns. This implementation adopts those patterns in small JSON files. It
does not adopt tweetxvault's internal-GraphQL client, LanceDB, query-id
discovery or embedding dependency tree, which do not fit the ten-year capture
boundary or this external-actor watch case.

gallery-dl, `d60/twikit`, `snscrape` and RSSHub were considered for timeline
discovery. X's current automation rules say to use the official API and warn
that non-API website scripting can lead to permanent account suspension.
Consequently none of those tools is used for unattended or recurring account
watching. gallery-dl remains confined to the repository's existing manual
media-capture command; it is not a fallback for discovery. Twikit also exposes
account-writing operations, snscrape duplicates extraction, and RSSHub adds a
service between provenance collection and intake.

The watcher instead calls the documented user-lookup and user-posts GET
endpoints directly with stdlib `urllib`. App-only bearer authentication reads
public data without borrowing the signed-in capture-browser session. This
removes a dependency and, more importantly, follows the platform's published
automation boundary. It does not guarantee continued API access: plans,
prices, endpoint availability and policy can change. X also requires API uses
to match the App's approved description and restricts data retention and AI
training. The operator must disclose this archive-discovery and transient
relevance-analysis use case. The implementation does not train a model on X
data and does not persist hydrated API post text or metrics. That data-minimum
design is not a legal conclusion about the separately captured evidence
archive. The operator must resolve that retention question in the App approval
and current developer terms before enabling the watcher.

References:

- <https://github.com/mikf/gallery-dl>
- <https://github.com/lhl/tweetxvault>
- <https://github.com/d60/twikit>
- <https://github.com/JustAnotherArchivist/snscrape>
- <https://github.com/DIYgod/RSSHub>
- <https://help.x.com/en/rules-and-policies/x-automation>
- <https://docs.x.com/developer-terms/restricted-use-cases>
- <https://docs.x.com/x-api/users/get-user-by-username>
- <https://docs.x.com/x-api/users/get-posts>

## 3. The pivotal constraint: X authentication vs automation

`capture-x.sh` still reads cookies from the configured browser profile for a
manually selected media capture. `discover_x.py` does not. Discovery requires
an app-only bearer token from an X developer App whose registered use permits
this work, held only in the untracked `.env` as `X_API_BEARER_TOKEN`.

Implemented mechanics:

- Before any request, discovery checks locally that the bearer token is
  present and contains no whitespace. It never prints or stores its value.
- `just discover-x --check-auth` runs that local preflight without touching X.
- Authentication errors, API access denial, exhausted quota, rate limits and
  transient service failures are different failure states. Any of them stops
  the whole run and writes a default 24-hour cooldown.
- Protected, suspended and unavailable watched profiles remain distinct
  per-profile results. An empty successful API timeline is healthy.
- Credential refresh remains manual. `--clear-cooldown` clears only local
  state and performs no request; it does not repair API access.

### Where the capture browser fits

The capture browser (`capture-browser/webbridge.py`) is a headless Chromium
the repository ships, holding the project's own signed-in sessions behind a
daemon on localhost. It is not used for X timeline discovery.

1. **Registered browser sources.** Implemented. `capture.py` drives the
   capture browser over local HTTP for sources marked `capture = "browser"`,
   records the method in metadata, and treats an unavailable browser as an
   incomplete poll. This path holds the JS-hydrated chain tracker and the
   other script-challenged pages; the Reddit threads moved to the
   `reddit-json` method (see `design/reddit-json-capture.md`).
2. **Individual X posts that gallery-dl cannot retrieve.** Implemented through
   `ingest-x.py`, which captures an element-only screenshot and text sidecar,
   then registers the post. It is interactive and is not the timeline watcher.
3. **X discovery.** Implemented with an app-only official API credential.
   Browser cookies, navigation and unattended session renewal are deliberately
   outside that path.

## 4. Watching X accounts (`x-watch`)

### Registry

`sources.toml` gains a third block type alongside `[[source]]` and
`[[x_post]]`:

```toml
[[x_watch]]
handle = "nvk"
org = "Coinkite"
since = "2026-07-30"
why = "CEO. Statements and retractions land here first."
```

No keyword filter. The accounts worth watching during a live incident are
low-volume, and the post that matters is the one that does not say the keyword
("that firmware report..."). Filtering risks losing exactly the signal the
watcher exists for. Storage is trivial. If a high-volume account is ever
added, an optional `keywords` list can be introduced then, not before.

The registry begins with 35 accounts already represented in the incident
record. It covers Coinkite, primary technical and on-chain researchers,
responders, relevant wallet and custody providers, the Bitcoin Red Team and
funders. Each entry states why that actor is likely to carry primary material
or useful leads. It deliberately does not include every captured author or
broad news account. Adding an important actor is one `[[x_watch]]` block, not
a follow from the project account.

### Mechanics

`scripts/discover_x.py` is stdlib-only:

1. Read active `[[x_watch]]` entries and choose the least-recently attempted
   handles, at most six by default and ten by a hard ceiling.
2. Resolve uncached handles in one documented `GET /2/users/by` request, then
   read `GET /2/users/:id/tweets` once per selected actor. The request asks only
   for attribution, relationship and timestamps. The API returns default post
   text, but discovery does not retain or queue it. It does not request public
   metrics or long-post expansion, use home timelines, mentions or search, and
   performs no writes or retries.
3. Keep at most 20 posts per handle by default, with a hard ceiling of 30.
   Reposts retain the watched actor's outer status id while the referenced
   original id is recorded separately. Full repost attribution and long-post
   text are hydrated only during explicitly approved intake.
4. On first successful contact, record the returned status ids as a baseline
   without queuing them. `--queue-initial` is the explicit history-import path.
5. Later calls use `since_id`. If more new posts exist than the bounded page
   holds, treat it as an overflow failure and do not advance the checkpoint.
6. Append ID-only candidate metadata to `.work/x-candidates.jsonl`, add a local
   relation label and permalink to `DISCOVERY.md`, then atomically advance the
   private checkpoint. Hydrated API text and metrics are not persisted. A crash
   before checkpointing safely repeats the head read; intake URL deduplication
   prevents duplicate queue entries.
7. During operator-approved X triage, `discover_x.py --show ID` performs one
   official post lookup and emits hydrated text only to that assessment
   process. X intake requires a separate `X_REVIEW_AGENT_BIN` and never falls
   back to the general review provider, so the operator can select a local or
   otherwise approved processor. The LLM never controls the authenticated
   browser, never follows links found in a post and does not train on the text.
   It may only append a recommendation or dismissal to `DISCOVERY.md`; capture,
   registration and publication remain a separate human decision.

Reposts are kept and marked as reposts: amplification by a watched party is an
attention signal, but the original statement remains attributed to its author.

### The deletion pass (phase 2, and arguably the point)

The archive exists because parties edit and delete. A captured post that later
vanishes from X is the social-media equivalent of the Mk3 advisory revision.
Detection: periodically re-fetch every registered `[[x_post]]` URL and every
watched post we hold, individually. Absence from a timeline proves nothing
because timelines truncate. A direct post that becomes unavailable must first
be recorded as `unavailable`, then retried with a healthy authenticated session.
Deletion, account suspension, protected-account changes, regional restriction,
login failure and rate limiting must not collapse into one event.

## 5. Topic discovery (phase 3)

Only channels that are scriptable without authentication, because anything
requiring a session inherits the staleness problem above:

- **HN Algolia API** (`hn.algolia.com/api/v1/search?query=coldcard`): JSON, no
  auth, polite by design. Catches new reporting and new discussion, each hit
  carrying a URL that is a candidate source.
- **GitHub search API**, within its documented unauthenticated limits: catches PoC repos,
  test-vector publications, and new libngu issues referencing the incident.
  The known libngu pull requests and Optech #416 are already registered as
  ordinary `[[source]]` entries.
- **Reddit**: not reliably searchable through the project's scripted methods as
  of 1 Aug 2026, and search remains unavailable. The held threads capture
  through the `reddit-json` method, and `discover_reddit.py` reads the
  r/coldcard and r/Bitcoin /new listings through the capture browser session on
  the 12-hour community schedule.

Hits land in the same `archive/inbox.jsonl` with a proposed `[[source]]`
block. A `just promote <inbox-id>` helper appends it to `sources.toml` so
promotion is one command, but the human still writes or edits the `why`.

## 6. Scheduling: one timer, due-state per job

Known-URL scheduling is implemented with one 30-minute timer tick (systemd on
the archive host). The runner
stores last-success state outside the repository, runs overdue work once after
sleep, gives every source exactly one owning job, and aggregates notifications.
Failures remain due for the next tick.

| Job | Cadence | Notes |
|---|---|---|
| web tier 1, excluding chain monitors | 30 min | mutable advisories and documentation |
| holding-address chain state | 30 min | BTC movement only; fiat values are normalised |
| web tier 2 | 6 h | moderate mutability |
| web tier 3 | 6 h | slow-moving or append-only sources |

Last-run timestamps live in
`~/.local/state/coldcard-archive/last-run.json`. Per-job results and an aggregate
tick result are retained beside that state. Changed events are passed once to
`capture.py record-run`; clean ticks are silent. Local desktop notification
remains the default where the host provides one. Signal alerting stays opt-in
and untouched.

Direct-post availability checks, X discovery, nightly archive and build audits,
and off-machine backup remain candidate jobs. X discovery is a manual command
during probation and is not part of either recurring timer. Deployment is
never a scheduled job.

## 7. What never reaches the site automatically

The intake queue is operational, not editorial. The public site continues to
render only registered sources and registered posts. Promotion requires a
scoped `why`, attribution, evidence treatment and first capture. During X
probation a person authorizes read-only triage with `--include-x`; the model
then recommends or dismisses from transiently hydrated candidate data.
Promotion and capture remain separate human decisions. The recurring community
service does not run X discovery or send queued X lines to its general agent,
so merely queueing or triaging a watched post cannot publish or register it.
Direct manual ingestion of an operator-supplied X permalink remains a separate,
unchanged path.

Also unchanged: the unnamed blockchain-services provider stays unnamed
regardless of what any watched account posts, and nothing identifying a
private individual gets published. Watching is not publishing.

## 8. Remaining implementation order

1. Confirm that the App's registered API and transient-analysis use case is
   permitted, review the current retention obligations, then configure the App.
   Run manual baselines and health checks over the configured watches. Record
   billed usage, duration and every failure class without weakening the hard
   stops.
2. Review X intake quality with human approval. Confirm repost, quote, reply,
   long-form article and text-only cases before unattended discovery or triage
   is considered. Promotion remains manual.
3. Only after that probation, decide whether to add a separate X timer. It must
   preserve the command's caps, fixed spacing, cooldown and opt-in kill switch,
   and must report incomplete runs without borrowing capture.py's exit 10.
4. Add direct-post availability checks only after deletion, suspension,
   protection, restriction and authentication failure are distinguishable.
5. Add GitHub and HN topic discovery, nightly audits and backup after their own
   alerting and retention decisions.

### nostr inherits the same model (6 Aug 2026)

The nostr lane adopts this probation design unchanged: live reads are manual
and opt-in through `NOSTR_DISCOVERY_ENABLED`, per-run caps with fixed relay
spacing bound each run, failure classes fail closed, and no timer exists
until the gate is met. Two differences follow from the platform. Reads are
anonymous NIP-50 queries against public search relays, with no credential
and no platform automation policy in play, so queued candidates go to the
standard community intake agent rather than a separate triage agent. And a
capture is a signed event fetched through `nak`, self-authenticating rather
than a session screenshot, so none of the screenshot provenance gating
applies. The scheduling gate has the same shape as step 3: after a probation
intake quality review, decide whether to add a separate nostr timer that
preserves the caps, spacing and kill switch.

## 9. Honest limits

- Timeline capture is forward-looking. The API may return some recent posts,
  but X profile coverage in the Internet Archive is incomplete. Posts
  from before the watcher starts may be unrecoverable.
- Rate limiting is controlled by X. No cadence is claimed safe. The probation
  command uses fixed spacing, aborts on the first rate-limit signal and applies
  a persistent cooldown rather than pushing through.
- None of this is a legal evidentiary chain, same caveat as the README. For
  any post that turns out to matter, the belt-and-braces move is a Wayback
  submission recorded with its permalink.
