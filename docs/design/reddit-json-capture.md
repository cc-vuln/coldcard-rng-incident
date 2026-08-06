# Design: reddit capture as structured JSON

**Status:** path B validated and piloted 4 Aug 2026. The daemon speaks
`fetch_json`, capture.py speaks the `reddit-json` method, and
reddit-ai-discovery-thread switched at 20260804T024121Z (raw JSON
artefact plus 157 KB flattening, 469 comments, zero chrome lines).
Path A still needs a maintainer-registered API app before it can be
tested. The remaining four render-era sources switched the same day, and
every reddit source registered since captures as `reddit-json` from its
first snapshot (129 sources as of 5 Aug 2026).
**Date:** 4 Aug 2026

The question this answers: the five held reddit sources are captured by
rendering Reddit's logged-out web frontend in the capture browser and
keeping a PDF plus extracted text. The render carries ad slots, vote
counters, Reply/Share toggles, achievement badges and ranking churn, so
the archive filters that noise after the fact with three normalizers and
still misreads diffs occasionally. What should replace the render?

## Why the render is the wrong thing to capture

Everything the reddit normalizers suppress is frontend chrome that does
not exist in the thread data: promoted ads, engagement counters, badges,
control toggles, and comment ordering that Reddit recomputes per load.
Filtering after capture has already cost one misclassified diff
(reclassified 4 Aug 2026) and three normalizers worth of maintenance.
The thread data itself, post and comments with stable ids, authors, UTC
timestamps, bodies and explicit edited/deleted markers, is available as
one JSON document per thread.

## The constraint found while testing

All three unauthenticated JSON endpoints (`www.reddit.com/.../.json`,
`old.reddit.com/.../.json`, `api.reddit.com/...`) answer 403 with a
challenge page from the capture host (4 Aug 2026). Reddit flags the
VM's egress; this is why the archive renders through the browser at all.
Any JSON design must either authenticate or reuse the challenge-passing
browser session.

## Path B (workable today): browser-session JSON

The capture browser's persistent profile already passes Reddit's
challenges. Drive that session to fetch the thread JSON from within a
reddit.com page context and keep the response body:

- The webbridge daemon gains one action, `fetch_json`, that runs
  `fetch("/comments/<id>/.json?limit=500&raw_json=1")` inside a page
  already on reddit.com and returns the body. Shipped.
- capture.py's reddit sources switch to a new method, `reddit-json`,
  that calls this action and treats the JSON as the capture. Shipped;
  the raw JSON is the held artefact and the flattening is the text.
- No new credentials, no new dependency; the daemon contract and the
  skip-with-event failure mode stay exactly as they are.
- The method switch starts a new baseline per source: the meta.json
  records `baseline_reset` with the last render-era timestamp, no diff
  spans the boundary, and the archive audit exempts baseline resets
  from its no-diff-without-change invariant.
- Open decision, deferred: comment trees beyond the inline limit arrive
  as more-stub lines (12 stubs on the pilot thread at 527 comments).
  Resolving them needs `/api/morechildren`, a read-only POST that the
  daemon's read-only vocabulary currently excludes. Stubs stay declared
  gaps until that exception is decided.

## Path A (preferred long-term): OAuth API

A maintainer-registered script app (reddit.com/prefs/apps) gives
client-credentials OAuth on `oauth.reddit.com`: 100 requests/minute on
the free tier, no browser dependency, clean 401/403 failure modes, and
no challenge surface at all. Five sources at thirty-minute polls are
about 250 requests a day plus `morechildren` calls for deep threads, far
inside the tier. Credentials live with the existing gitignored secrets,
never in sources.toml. Path A cannot be tested until the app exists, so
it is a follow-up, not a blocker: the canonical contract below is
identical under either transport.

## The canonical contract

Whichever path fetches it, the JSON becomes the capture in the existing
snapshot/diff/meta.json shape:

- The raw thread JSON (post plus all comments, `morechildren` resolved)
  is the held artefact, replacing the PDF for these sources.
- The canonical `.txt` is a deterministic flattening, not a render:
  a post header (id, author, created_utc, title, link), then one block
  per comment (id, author, created_utc, edited flag, body) ordered by
  comment id. Ordering by id, never by Reddit's display rank, makes
  ranking churn invisible to the differ.
- `edited` timestamps are kept per comment, so comment edits become
  detectable source-content changes; today the render flattens them
  away.
- Deletions arrive explicitly: `[deleted]` (author) and `[removed]`
  (moderator), where the render today shows a vanished subthread that
  must be inferred.

## What the migration actually deleted

**Recorded 6 Aug 2026, after the fact.** This section did not exist while the
migration ran, which is the root of the problem it describes.

Switching the five sources deleted their pre-migration captures rather than
leaving them beside the new ones: 134 captures taken between 1 and 4 Aug 2026,
402 files, plus their diffs and every corresponding line in
`archive/index.jsonl`. Those sources therefore begin on 4 Aug 2026 in the
register, the change record and the poll log.

| Source | Captures deleted | Range |
|---|---|---|
| `reddit-ai-discovery-thread` | 35 | 3 to 4 Aug |
| `reddit-coldcard-letter-db-leak` | 30 | 3 to 4 Aug |
| `reddit-june-letter-report` | 30 | 3 to 4 Aug |
| `reddit-wallet-brand-link-warning` | 23 | 3 to 4 Aug |
| `reddit-drained-timeline` | 16 | 1 to 3 Aug |

A backup existed outside the repository until 6 Aug 2026 and was then deleted
deliberately, not restored: it was duplicate rendered-page capture of threads
the archive still holds in a better form, from before the capture process had
settled. That is an operator decision about superseded material, and it is
recorded rather than quiet: `/corrections/` carries it, because the site had
claimed an append-only rule the archive did not meet.

The rule that comes out of this, now in `AGENTS.md`: **a migration is not a
licence to delete history.** When a capture method changes, the old captures
stay and the new ones are added beside them. If a deletion is genuinely wanted,
it is decided and written down before it happens.

## What this retires and what it costs

- The `reddit-engagement`, `reddit-achievement-badges` and
  `reddit-chrome` normalizers have nothing to do under JSON capture: the
  noise they filter is not in the data. They stay bound for replay of
  PDF-era snapshots (meta.json records the list per snapshot) and are
  simply not declared on the new captures.

  Revised 6 Aug 2026: that was true of two of the three. `reddit-engagement`
  turned out to be bound to nothing and recorded in no held sidecar, so
  nothing ever replayed it, and it was removed with its test. The other two
  are bound in `SOURCE_NORMALISERS` and stay. The distinction is the one that
  matters for any future retirement: a normalizer is removable only when no
  held `meta.json` names it, because the audit replays from those sidecars.
  `reddit-more-stub-counts` appears in 153 of them and is not removable on
  the grounds of being unbound.
- The PDF and its "as rendered" provenance go away for reddit. Reddit
  sources have never used screenshot display (the capture-display policy
  covers staged X post images only), and excerpt discipline already
  means no full render is published, so the public site loses nothing.
- The first JSON capture of each source starts a new baseline; diffing
  across the PDF-to-JSON boundary would compare a render against a
  flattening and produce one giant artifact diff. The transition is
  recorded per source as a capture-correction revision entry.

## Migration

1. Add the `fetch_json` action to the webbridge daemon and the
   `reddit-json` method to capture.py, with fixtures in test_capture.py
   (a held thread JSON, its flattening, an edited comment, a deleted
   comment, a deep thread needing `morechildren`).
2. Switch one source (`reddit-ai-discovery-thread`) and watch it for a
   day: noise diffs should stop entirely, the 30-minute poll should keep
   passing with the browser session warm.
3. Switch the remaining four sources; record each transition in
   revision-reviews as capture-correction.
4. Register the OAuth app, PoC path A from the VM, and if it passes,
   move the transport behind the same canonical contract.

## Deleted-comment recovery is a policy question, not tooling

Arctic Shift (the maintained Pushshift successor, free, 2005 to present)
can sometimes recover bodies of comments authors later deleted, and is
also the natural replacement for the Wayback cross-check on reddit
sources, since Reddit has blocked the Internet Archive beyond its
homepage since August 2025. Recovering author-deleted bodies is a different
act from preserving what was public when the archive read it: it reconstructs
text this project never held, from a third party, after the author withdrew
it. That needs an explicit decision in `capture-display-policy.md` before any
recovery source is wired in, and the 6 Aug 2026 revision of section 5 does not
settle it either way. Until then, deletions are recorded as deletions.
