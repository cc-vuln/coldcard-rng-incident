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
- Reddit vote and collapsed-reply counters, while retaining post and comment text
- the COLDCARD FAQ's page-wide rolling footer date, while retaining page edits
- Slipstream's live block height and fee readings, while retaining portal terms
- current blog chrome around the historical Android and Unciphered disclosures

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

X blocks unauthenticated reads. `gallery-dl` borrows a logged-in session from
Chrome:

```bash
just capture-x        # Chrome must be CLOSED for cookie extraction on macOS
```

This is read-only: it fetches URLs already listed in `sources.toml` and posts,
follows and likes nothing. If cookie extraction fails, export cookies to a
Netscape-format file and swap `--cookies-from-browser chrome` for
`--cookies /path/to/cookies.txt` in `scripts/capture-x.sh`.

The script is compatible with macOS Bash 3.2. Each URL downloads into a private
staging directory and is accepted only if gallery-dl produced an artefact whose
name contains the requested post ID. A zero-result exit is a failure, not a
successful capture. Accepted captures land in `archive/x/<post-id>/<TS>/`, with media named
`attachment-N`, so an earlier capture is never overwritten and an attachment
is never mistaken for the post itself.

For posts that gallery-dl cannot retrieve, the authenticated-browser bridge can
capture the rendered post without browser navigation, account or trend chrome:

```bash
just ingest-x 'https://x.com/example/status/123' example-analysis independent-analysis 'Why this post matters.'
```

This writes an element-only PNG and verbatim text sidecar under `archive/x/`,
then registers the post in `sources.toml`. The capture helper selects the exact
status ID, including when the post embeds another post by the same author, and
isolates a rendered clone before taking long screenshots so X cannot reflow the
article out of the clip. Set `CAPTURE_BROWSER_SESSION` to a task-specific value
when recovering from stale daemon tab state.

Registered posts are first-class public evidence records under
`/record/sources/<id>/`. Each record reports whether attributable local
capture material is held. When a screenshot passes the publication gates, the
record displays it with its capture time and a link to the original post.

### Finding X posts before capture

`scripts/discover_x.py` watches the small `[[x_watch]]` registry for new
permalinks. It uses documented read-only endpoints in the official X API,
writes no capture and cannot register a post by itself. ID-only candidate
metadata stays under `.work/`; hydrated API text is not retained. The tracked
`DISCOVERY.md` queue receives a relation label and permalink. During explicit
X triage, one official post lookup supplies text transiently to the assessment
agent. Accepted candidates return to the `ingest-x.py` path above.

The command is manual and opt-in while its account-health outcomes are being
proved. See `docs/design/discovery-and-x-watch.md` for the safety model and
`docs/operations.md` for the exact probation commands. Do not add it to the
community timer merely because the manual command returns successfully once.

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
