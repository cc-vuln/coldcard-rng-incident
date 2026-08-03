# Design: periodic discovery and X account watching

**Status:** known-URL capture and tier-aware scheduling built; X watching,
discovery, nightly audit and backup proposed
**Date:** 3 Aug 2026

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
- `[[x_watch]]`, `archive/inbox.jsonl`, topic discovery, deletion classification
  and unattended X authentication described below are not built.

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

- **Machine discovers, human registers.** `sources.toml` stays hand-curated.
  The proposed discovery path fills an inbox (`archive/inbox.jsonl`); a person writes the
  `why` before anything becomes a source. Nothing auto-discovered appears on
  the public site. The site's impartiality rests on every claim having a
  human-attributed status, and a cron job cannot supply that.
- **Same provenance discipline as Wayback captures.** Machine-discovered
  captures carry their own provenance marker (`x-watch`, `discovery`) and are
  never presented as curated captures.
- **Stdlib-only Python**, with `gallery-dl` the one exception and confined to
  social capture. New discovery code follows the same rule: HN and GitHub both
  offer unauthenticated JSON APIs, so no new dependency is needed.
- **Read-only on authenticated sessions.** No posting, following, liking. Timeline
  reads only. This is a hard rule from AGENTS.md and the whole design stays
  inside it.
- **Alert on signal, never on activity.** A run that found nothing says
  nothing.

## 3. The pivotal constraint: X authentication vs automation

`capture-x.sh` reads cookies from a browser profile (`X_COOKIES_BROWSER`, configured
in `.env`). That works for manual
runs, but an unattended watcher must survive the profile being locked by the
running daemon and sessions going stale without anyone at a keyboard.
`docs/capture.md` already names the portable answer: a Netscape-format
`cookies.txt`, swapped in for `--cookies-from-browser`.

Proposed mechanics:

- The cookies file lives outside the repo at
  `~/.local/state/coldcard-archive/x-cookies.txt` (a live session must never
  be committable). Exported once by hand, from any browser.
- `capture-x.sh` and the new watcher both take `--cookies` pointing at it.
- Sessions rot. The watcher treats gallery-dl auth-failure output as a
  distinct condition and alerts **"X session stale, refresh cookies"** rather
  than failing silently or storing a login wall as if it were a post. Refresh
  remains a documented manual step until an automated path is proved reliable.
- Failure mode deliberately avoided: gallery-dl extractor breakage when X
  changes internals. The ritual is `.venv/bin/pip install -U gallery-dl`, and
  a failing extractor must alert differently from "no new posts", because
  otherwise the archive goes quiet and nobody notices.

### Where the capture browser fits

The capture browser (`capture-browser/webbridge.py`) is a headless Chromium
the repository ships, holding the project's own signed-in sessions behind a
daemon on localhost. It does not replace gallery-dl for X timelines.

1. **Registered browser sources.** Implemented. `capture.py` drives the
   capture browser over local HTTP for sources marked `capture = "browser"`,
   records the method in metadata, and treats an unavailable browser as an
   incomplete poll. This path holds the Reddit victim thread and the
   JS-hydrated chain tracker.
2. **Individual X posts that gallery-dl cannot retrieve.** Implemented through
   `ingest-x.py`, which captures an element-only screenshot and text sidecar,
   then registers the post. It is interactive and is not the timeline watcher.
3. **Cookie refresh for unattended X capture.** Not implemented. Browser-assisted
   export may be possible, but must be proved against the actual browser and
   gallery-dl session before it becomes part of the operating design.

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

Seed list, from BACKLOG and the existing registry: nvk, LLFOURN, theinstagibbs,
Rob Hamilton (AnchorWatch), Peter Todd, otaliptus, KLoaec, PortlandHODL,
darosior, dhruvbansal, PraveenPerera, glxyresearch, clay_garrett, unchained,
plus Coinkite/COLDCARD corporate accounts if distinct from nvk.

### Mechanics

New script `scripts/x-watch.py`, stdlib, driving gallery-dl by subprocess:

1. For each `[[x_watch]]` handle, run gallery-dl against
   `https://x.com/<handle>` with `--cookies`, `--write-metadata`, and
   `--download-archive ~/.local/state/coldcard-archive/x-seen.txt`, which
   gives exact per-post dedupe for free.
2. Parse the new JSON sidecars: post id, timestamp, full text, reply / quote /
   retweet structure, media list.
3. Write captures under a timestamped watcher path in `archive/x/`, append one line per
   new post to `archive/inbox.jsonl` with `provenance: "x-watch"`.
4. Return a structured result to the due-state runner. Do not reuse
   `capture.py`'s exit 10 contract without also integrating the result with the
   notifier's incomplete-run handling.

Retweets are kept and marked as retweets: a watched party amplifying someone
is attention signal, and the retweeted content is captured with it.

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
  of 1 Aug 2026. The known victim thread is captured through the browser backend;
  topic-wide discovery remains a manual gap.

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

X timelines, direct-post availability checks, topic discovery, nightly archive
and build audits, and off-machine backup remain candidate jobs. They are not
part of the scheduled service described above. Deployment is never a scheduled
job.

## 7. What never reaches the site automatically

The inbox is operational, not editorial. The public site continues to render
only registered sources and registered posts. A discovered item becomes public
by being promoted, which means a person read it, wrote the `why`, and chose
its evidence basis (verified, reported, derived or unverified, plus contested
where sources disagree). This is the
same gate that keeps primary research distinct from reporting on it, and it
is what the archive's credibility rests on.

Also unchanged: the unnamed blockchain-services provider stays unnamed
regardless of what any watched account posts, and nothing identifying a
private individual gets published. Watching is not publishing.

## 8. Remaining implementation order

1. Move unattended X authentication from live Chrome extraction to a tested
   cookies file. Register the remaining items named in `BACKLOG.md`.
2. Add the `[[x_watch]]` registry, watcher and inbox. Feed its structured
   result into the existing due-state and notification model.
3. Add direct-post availability checks and a human-reviewed `just promote`
   path. Add cookie refresh only after it has been demonstrated safely.
4. Add HN and GitHub topic discovery to the same inbox.
5. Add nightly archive/build/link audits and a separate daily backup only after
   alerting, retention and restore procedures are defined.

## 9. Honest limits

- Timeline capture is forward-looking. gallery-dl may backfill some recent
  posts, but X profile coverage in the Internet Archive is incomplete. Posts
  from before the watcher starts may be unrecoverable.
- Rate limiting is controlled by X. Two hours with jitter is a proposed starting
  cadence, not a verified safe limit. Back off and record 429 responses rather
  than pushing through.
- None of this is a legal evidentiary chain, same caveat as the README. For
  any post that turns out to matter, the belt-and-braces move is a Wayback
  submission recorded with its permalink.
