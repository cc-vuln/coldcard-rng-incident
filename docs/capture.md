# Capture

How the archive collects material and classifies what it collects. The command
reference is in the root [README](../README.md); this document is the detail
behind it. Scheduled, unattended capture is covered in
[operations.md](operations.md).

## Capture methods

One command, `just capture`, polls every tracked source. Each source declares
how it is fetched, and the method is recorded in the snapshot's meta.json:

```
http (default)   plain scripted fetch; raw HTML kept for changed captures
browser          rendered through the capture browser, for pages
                 that answer scripts with a JS challenge or an empty shell.
                 The rendered artefact is a PDF. Read-only by construction:
                 navigate, text extraction and PDF are the whole vocabulary
gallery-dl       X posts, via scripts/capture-x.sh (see "X capture")
```

Browser captures block known ad and tracker hosts at resolution
(`capture-browser/ad-hosts.txt`, mapped to localhost at launch) and run a
consent-cleanup pass that rejects cookie walls and hides their containers.
Neither touches publisher content: the list is committed and reviewable,
each browser snapshot's meta.json records the list name, mechanism and
retrieval date, and first-party promotions (Reddit's Promoted slots) are
unaffected by design. `WEBBRIDGE_BLOCK_MODE=off` disables the blocking.

The daemon keys one current tab per **session name**, and `navigate` and
`close_tab` act on that session's tab, so every caller class drives the
browser under its own name and concurrent callers cannot close each other's
pages mid-read: live polls use `coldcard-archive`, X-thread polls derive
`coldcard-archive-thread` from it, a dry run uses
`coldcard-archive-dry-<pid>` (and `-dry-<pid>-thread`), Reddit discovery
uses `coldcard-archive-discover`, and `ingest-x.py` uses
`coldcard-archive-x`. A new browser client picks a new name; sharing one is
the bug this list exists to prevent.

HTTP and browser sources managed by `capture.py` land as the same artefacts:
`<TS>.txt`, a rendered artefact, `<TS>.meta.json`, a diff on change, and an
`index.jsonl` event. Registered X posts use the separate `capture-x.sh` or
`ingest-x.py` paths under `archive/x/`; they do not enter the snapshot/diff
contract above. If the browser is unavailable, browser sources are skipped with
the skip recorded as an event. Other sources still run, but the overall capture
exits 20 so a missing browser cannot look like a clean poll.

For an official page whose human URL is only a JavaScript shell,
`sources.toml` may keep that page as `url` while declaring an official
machine-readable `fetch_url`. The public evidence link remains the human URL
and snapshot metadata records the endpoint actually fetched. An endpoint that
only answers POST (a GraphQL API, for example) can declare `fetch_post` with
the raw JSON request body; the response is then treated exactly as a GET's.
JSON endpoints
can set `json_html_field` plus selected `json_text_fields` to extract a
readable document, or
`json_pretty = true` to retain deterministic, line-oriented JSON. Positive
`min_chars` and `required_text` guards remain mandatory for these sources so a
changed API shape cannot be archived as valid content.

Rendered stacker.news pages crash the capture tab, so every `stackernews-*`
source polls the public GraphQL API through the `fetch_post` shape above, with
a fixed item query (title, text, two levels of comments, author and absolute
timestamp on each). A new thread is added by copying that block and changing
the item id; see the 4 Aug 2026 sweep batch in `sources.toml`. Reddit threads
are `capture = "reddit-json"`: the thread JSON is read through the capture
browser's signed-in session (anonymous JSON from this host gets a 403
challenge) and flattened to a deterministic canonical text, so no normalizer
binding is needed.

Every non-dry capture writes an atomic JSON result under `archive/runs/`. It
contains the selected sources, per-source events, counts, outcome and exit
code. The notification wrapper writes its equivalent retained result under
`~/.local/state/coldcard-archive/runs/` and formats alerts from that file rather
than scraping console output.

## Change normalisation

`sources.toml` may declare a small named `normalizers` list. Normalisers affect
only the comparison hash and generated diff. The extracted text in each held
snapshot remains what the fetch returned, and old snapshots are never rewritten.
The currently enabled rules suppress known non-editorial churn:

- relative time labels on Coin360 and browser-rendered pages
- market tickers and transient ticker availability in The Block's site chrome
- fiat conversions in the live chain tracker, while retaining BTC balances
- repository navigation counters and reaction totals on tracked pull requests
- TFTC's rotating related-content cards after the article
- un-inlined comment-branch counts in Reddit thread JSON, while retaining post
  and comment text
- the COLDCARD FAQ's page-wide rolling footer date, while retaining page edits
- Slipstream's live block height and fee readings, while retaining portal terms
- current blog chrome around the historical Android and Unciphered disclosures
- live footer state on the community chain trackers: the hack tracker's
  snapshot clock and fallback mirror, and CKTRIPWIRE's advancing honeypot ages
- article bodies on tracked news pages (CoinDesk, ChainCatcher, crypto.news,
  newsbit.com and NewsBTC), compared without rotating site chrome
- Substack engagement counters and comment ages, while retaining post text

An unknown normalizer, a duplicate ID in `[[source]]` or `[[x_post]]`, or an
unsupported capture or JSON-extraction declaration makes the registry invalid
and fails both capture and audit.

## When a poll fails

Failures are classified, and the class decides what happens:

| class | example | what happens |
|---|---|---|
| `transient` | connection reset, timeout, 429, 5xx | retried twice with backoff inside the run |
| `refused` | 403 | taken at face value; after three in a row, Wayback is replayed |
| `absent` | 404, 410 | taken at face value; if permanent, mark the source `gone` |
| `challenged` | consent wall, `min_chars` floor, missing required text | recorded, never stored |

The class decides what the runner does. It is deliberately coarse, and it is
not enough to fix anything: `challenged` covers a genuine bot wall, a thread
shorter than a `min_chars` value copied in at registration, and a
`required_text` marker anchored to a sentence the publisher has since
rewritten. All three were live on 6 August 2026 and the record said the same
word about each.

So every failure event also carries a `diagnosis`, and `http_status` where the
origin answered with one. These are additive: `failure` keeps its meaning and
its effect on the gate, and `diagnosis` says which cause it was.

| diagnosis | what it means | where to look |
|---|---|---|
| `origin-challenge` | an interstitial, detected in the refusal's own body | the collector's address or browser fingerprint |
| `origin-rate-limit` | 429 | cadence, or a shared exit address |
| `origin-refused` | 403 with no challenge markers | the origin's own policy |
| `origin-absent` | 404, 410 | the publisher; candidate for `gone` |
| `origin-server-error` | 5xx | the origin, usually temporary |
| `dns-unresolved` | the name does not resolve | this host's resolver, not the origin |
| `connect-timeout`, `connect-reset`, `connect-refused`, `connect-failed` | no usable response | the network path |
| `content-below-floor` | parsed, valid, shorter than `min_chars` | usually the registry, not the source |
| `content-marker-missing` | `required_text` absent, body not a challenge | usually a stale marker after a publisher rewrite |
| `browser-tab-lost`, `browser-unavailable` | the capture browser | `webbridge.service` |

Only the slug is stored. The headers that identify a challenge are not: a
`cf-ray` ends in the code of the edge that answered, which names a city, and
`response_headers.KEEP` exists to keep that out of the archive. A diagnosis
obeys the same rule as a capture.

`just diagnose` groups the sources currently failing by cause, with a
consecutive-failure count and the timestamp of the last good poll, and leaves
out anything already recorded `gone`. `just diagnose --json` is the same view
for an automated triage pass. Events captured before this field existed report
`unrecorded` rather than a guess.

A failure on its own does not stop a publication. Exit 20 means a source has
missed roughly three of its own polling cycles (90 minutes for tier 1, 18 hours
for tiers 2 and 3), which is decay rather than weather. Every source that did
not capture prints its margin either way, so a passing run still shows how close
it came.

There is no override flag. If the gate blocks, the record is genuinely behind.

## When a source disappears

Origins withdraw material. When one stops resolving, mark it rather than
leaving it to fail forever:

```toml
gone = true
gone_since = "20260803T021200Z"   # when the archive observed it gone
gone_status = "404"               # what the origin returned, if anything
gone_note = """what was observed, checked against more than one user agent
so a block on this collector is not mistaken for a withdrawal"""
```

A `gone` source is no longer polled, so a permanently dead URL cannot mark
every run incomplete and block publication. Its captures are untouched, and
both `/record/` and its own source page say plainly that the original is gone
and that the held capture is the only copy left. That case is the reason the
captures exist, so it is stated rather than filed as an error.

Record what was observed, not why: whether something was deleted, renamed or
made private is usually not establishable from outside.

## Recovering history from before we started

Our capture only sees forward. `scripts/wayback.py` pulls what the Internet
Archive holds for a tracked source and slots it into the timeline:

```bash
just wayback-list coinkite-mk3-advisory     # what exists
just wayback-backfill coinkite-mk3-advisory # pull it in
just rebuild-diffs                          # diff in chronological order
```

Recovered snapshots carry `provenance: wayback`, the original Wayback timestamp,
and the replay URL, so they are never confused with our own captures. Coverage
must be checked per source; an absent Wayback result does not prove that an
earlier version never existed.

## X capture

X blocks unauthenticated reads. Two tools capture posts, and they are not
interchangeable: `capture-x.sh` drives gallery-dl, which downloads **media
only**, so a post with no image or video reports "No results" and produces
nothing (that is what the tool does, not a failure to fix), while
`ingest-x.py` takes the element-only screenshot of the post itself plus a
text sidecar, which is what a text-only post needs and what the site
displays. gallery-dl borrows a logged-in session from Chrome:

```bash
just capture-x        # Chrome must be CLOSED for cookie extraction on macOS
```

`capture-x.sh` reads its cookie source from `X_COOKIES_BROWSER`. `just
capture-x` loads `.env` and gets it; running the script directly does not, and
it will silently fall back to a browser profile that does not exist. Export it
or use the recipe.

This is read-only: it fetches URLs already listed in `sources.toml` and posts,
follows and likes nothing. Since 8 Aug 2026 a scheduled weekly media pull runs
the same script over the registered posts; scheduling changes nothing about
what it may do. If cookie extraction fails, export cookies to a
Netscape-format file and swap `--cookies-from-browser chrome` for
`--cookies /path/to/cookies.txt` in `scripts/capture-x.sh`.

The script is compatible with macOS Bash 3.2. Each URL downloads into a private
staging directory and is accepted only if gallery-dl produced an artefact whose
name contains the requested post ID. A zero-result exit is a failure, not a
successful capture. Accepted captures land in `archive/x/<post-id>/<TS>/`, with media named
`attachment-N`, so an earlier capture is never overwritten and an attachment
is never mistaken for the post itself. `post.png` and `post.txt` are the
post, `attachment-N.<ext>` is media it carried and `meta.json` is the fetching
tool's own sidecar.

For posts that gallery-dl cannot retrieve, the authenticated-browser bridge can
capture the rendered post without browser navigation, account or trend chrome:

```bash
just ingest-x 'https://x.com/example/status/123' example-analysis independent-analysis 'Why this post matters.'
```

This writes an element-only PNG and verbatim text sidecar under `archive/x/`,
then registers the post in `sources.toml`. Since 8 Aug 2026 the driver also
invokes it for X candidates the `xintake` role approved; the agent itself
never reaches the browser. The capture helper selects the exact
status ID, including when the post embeds another post by the same author, and
isolates a rendered clone before taking long screenshots so X cannot reflow the
article out of the clip. Set `CAPTURE_BROWSER_SESSION` to a task-specific value
when recovering from stale daemon tab state.

Registered posts are first-class public evidence records under
`/record/sources/<id>/`. Each record reports whether attributable local
capture material is held. When a screenshot passes the publication gates, the
record displays it with its capture time and a link to the original post.

### Capturing the conversation, not just the post

Where the thread around a post is itself the evidence, the post can be
registered as a polled conversation. `thread = true` and a `tier` on its
`[[x_post]]` block make it a source under the same id, so it gets snapshots,
diffs, review classification and change-feed entries without anything
downstream learning a new concept. Design and rationale:
[design/x-thread-capture.md](design/x-thread-capture.md).

```bash
# register a new post and take the first capture of its conversation
just ingest-x 'https://x.com/example/status/123' example-thread "" "" --thread --tier 3

# re-capture the conversation of an already-registered thread, now
just capture-thread example-thread
```

`--thread` does the focal-post ingest exactly as above, then hands the
conversation to `capture.py capture --id <slug>`, which is the same poll the
tier's timer runs from then on. That is deliberate: `capture.py` owns snapshot
writing, change detection, the diff and the `index.jsonl` event, and a manual
first capture with its own write path would be a second implementation of the
part of this repo that must not be got wrong. It runs as a separate process
because the archive writer lock is not reentrant.

`--tier` states the cadence and is required when the post is being registered
now. Turning threading on for a post that is **already** registered is a
registry edit: add the two keys to its block in `sources.toml` by hand, then
run `just capture-thread <id>`. `ingest-x.py` appends blocks and does not
rewrite them, and appending a second block for the same post would give one
conversation two registry entries and two source pages.

`just ingest-x --thread` exits 0 when the conversation captured, because a
first capture is a change and a change is what the command was run for; a
poll that came back incomplete still exits 20 and a busy writer lock 21. `just
capture-thread` is the raw poll and keeps `capture.py`'s exit codes as the
README states them, exit 10 included.

Some posts are held twice: as their own registered record, with this project's
note on why they matter, and again inside a conversation captured around them.
`part_of = "<head-id>"` on the member's block states that, and both ends of the
relation say so — the member's page names the conversation, and the thread
reader links the post to its own record. The registry cannot notice this by
itself, because whether a post is inside a conversation depends on what a
capture collected, so `just audit` reports a registered post held inside a
thread without `part_of` to declare it. That finding is a one-line registry
edit, never a capture fault.

A thread capture writes the canonical thread text, a structured `<TS>.json`
recording the depth it reached, and one element-only screenshot per post it
saw for the first time, under `archive/x/<id>/<TS>/`. It declares what it did
not reach rather than implying it saw the whole conversation, and it refuses
outright when it collects far less than the previous capture: absence on X is
not deletion, and a capture that under-collected is indistinguishable in the
record from replies having been removed.

### Finding X posts before capture (amended 8 Aug 2026)

`scripts/discover_x_browser.py` reads the home timeline and the small
`[[x_watch]]` registry for new permalinks through the capture browser,
driver-side only, under the `X_BROWSER_DISCOVERY_ENABLED` kill switch. It
writes no capture itself: ID-only candidate metadata stays under `.work/`, the
tracked `DISCOVERY.md` queue receives a relation label and permalink, the
registering `xintake` role assesses the queue with the coverage index in view,
and the driver captures each approved post through the `ingest-x.py` path
above. The agent never reaches the browser. `scripts/discover_x.py`, the
official-API lane this replaced on 8 Aug 2026, is deprecated; no API App or
bearer credential is used.

See `docs/design/discovery-and-x-watch.md` for the safety model and
`docs/operations.md` for the operational detail, including the session-health
failure classes and the lane's own timer.

## nostr capture

`ingest_nostr.py` captures one note plus its replies through `nak` (the second
sanctioned external binary beside gallery-dl, likewise confined to social
capture and posting) into `archive/nostr/<id>/<TS>/`: `event.json` is the
signed event, `event.txt` its flattened text, `replies.json` the fetched
replies where any exist, `meta.json` the sidecar. A re-capture is a new
timestamped directory, so the append-only rule is a property of the layout
here too. Signed events are self-authenticating, so none of the screenshot
provenance gating that X captures need applies. Registered notes are
`[[nostr_post]]` blocks, validated by capture.py but never polled. The
identity, posting and discovery operations are in
[operations.md](operations.md).

## Reviewing detected differences

The immutable snapshots and diffs record every difference detected by the
collection pipeline. `revision-reviews.toml` adds a human interpretation layer
without rewriting that history:

- `source-content` means relevant text served by the publisher changed. It does
  not verify the new claim.
- `capture-noise` means dynamic chrome such as tickers, relative times or
  counters caused the difference.
- `capture-correction` records a collection-method or normalizer correction.
- `unreviewed` is the default for a new difference that has not been classified.

The public source-change page shows reviewed source content first and retains
collection noise, corrections, baselines and errors in separate sections. New
normalizers prevent known chrome from producing future snapshots; the review
layer explains historical captures that remain in the append-only record.
