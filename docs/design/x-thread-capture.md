# Design: X thread capture

**Status:** designed 6 Aug 2026, not built. Decisions in section 3 are taken.
Section 8 requires a revision to `capture-display-policy.md` before any of the
presentation layer ships.
**Date:** 6 Aug 2026

The question this answers: the archive captures a Reddit, Stacker News or
BitcoinTalk thread as a whole conversation, with canonical text, diffs, review
classification and deletion annotation. It captures an X post as one post. Where
the conversation around a post is itself the evidence, the record holds the
caption and drops the artefact. What should replace that?

## 1. The gap, stated from the registry

Of 308 `[[x_post]]` entries, 49 reference the thread or its replies in their
title or `why`. Three do so in terms that make the single-post capture plainly
insufficient on its own reading:

- `trustwallet-wasm-update`: "The held capture covers the first post of the
  ten-post thread."
- `clay-attribution`: "the complete thread says the provider's logs matched the
  workflow and that Block saw no evidence of knowing participation."
- `bitcoindevs-explainer-thread`: registered as an explainer thread.

`ingest-x.py` already loads the whole conversation. `EXTRACT_JS` walks
`document.querySelectorAll("article")`, finds the focal post, and keeps a single
`replyTo` line truncated to 280 characters. Everything else the page rendered is
discarded at extraction time. This design is mostly about not throwing it away.

## 2. What a read-only probe established

Probed 6 Aug 2026 against `x.com/clay_garrett/status/2083247006139503065` from
the capture host, navigate and evaluate only, in a probe session, tab closed
afterwards. Five findings shape the contract.

**The conversation is present without scrolling.** 18 `<article>` elements on
first paint: the focal post, the author's own `2/` and `3/` continuations, then
replies. 33 `cellInnerDiv` containers.

**X virtualises the list, so the DOM is a window and not a document.** Across
ten scroll rounds the article count stayed between 26 and 34 while the total
grew. By the end `ingest-x.py`'s own focal-post selector returned no match: the
focal post had been evicted from the DOM. A thread capture must accumulate
posts as it scrolls and cannot read the DOM once at the end. This is also why
each post's screenshot must be taken while that post is on screen.

**Replies render in ranked order, not chronological order.** Article 5 was
19:03, article 6 was the next day at 07:35, article 12 was 18:18. This is the
same churn `flatten_reddit_thread` defeats by sorting on comment id. Status ids
are snowflakes and sort chronologically, so the same fix applies unchanged.

**`document.body.scrollHeight` is not a completion signal.** The trace ran 7194,
7491, 7218, 7620, 7469, 15270, 14624, 14049, 13343, 12953. It grows on lazy
load and shrinks as virtualised rows are replaced by estimates. End of thread
has to be "no new status id for N consecutive rounds", plus explicitly declared
unexpanded `Show more` controls.

**Two fields cannot be read the obvious way.** The focal `<article>` carried two
`<time>` elements, only one of which had an href matching its own status; the
first was not the post's time. And one article's rendered name block yielded
`@X` while its status link said `0xAnthraX`. Author identity and post time both
come from the status link and its associated `<time>`, never from the name
block's `innerText`.

## 3. Decisions taken

1. **Scope.** Capture the focal post, any ancestor it replies to, the author's
   own contiguous continuation posts, and replies to a declared scroll cap.
   Declare depth and unexpanded controls as gaps rather than implying
   completeness X does not offer.
2. **Registry and refresh.** Poll under the same `x_post` id. No second
   registry entry, no second source page.
3. **Display.** An element-only screenshot per captured post, replies included.
   This is the widest of the options considered and it requires the policy
   revision in section 8.
4. **Coverage.** Opt-in per post. A curated thread tier, not all 308.

## 4. Registry shape

`[[x_post]]` gains two optional keys. Nothing else about the block changes and
no existing entry is touched.

```toml
[[x_post]]
id = "clay-attribution"
title = "Operator attribution report"
url = "https://x.com/clay_garrett/status/2083247006139503065"
author = "clay_garrett"
org = "Block"
posted = "2026-07-31T17:42:45Z"
thread = true
tier = 3
why = """..."""
```

`thread = true` makes the entry pollable. `tier` gives it a cadence in the
existing scheduler lanes. An `x_post` without `thread` is exactly what it is
today: registered, ingested once, never polled.

One id is the point of this shape. Every downstream consumer in this repo is
keyed by source id, so a thread-enabled post gets snapshots, `index.jsonl`
events, diffs, `revision-reviews.toml` classification, `/record/changes/`
entries and `pollHealth()` without any of them learning a new concept. The
alternative, a separate `[[source]]` with `capture = "x-thread"` cross-linked to
the post, was rejected: it splits X material across both block types and gives
one conversation two source pages.

Validation additions in `validate_sources`, which already iterates both sections
for id and URL uniqueness:

- `thread` must be a bool.
- `tier` is required when `thread` is true, and forbidden otherwise, so a
  pollable entry always states its cadence.
- The archive audit's method allowlist gains `x-thread`, with the artefact check
  that an `x-thread` capture has a `<TS>.json` beside its `<TS>.txt`.

## 5. The capture contract

### Layout

One capture, one timestamp, two directories:

```
archive/snapshots/<id>/<TS>.txt        canonical thread text (the diffed thing)
archive/snapshots/<id>/<TS>.json       structured record (the held artefact)
archive/snapshots/<id>/<TS>.meta.json  sidecar, method "x-thread"
archive/x/<id>/<TS>/post.png           focal post, unchanged from today
archive/x/<id>/<TS>/post.txt           focal sidecar, unchanged from today
archive/x/<id>/<TS>/thread-<status>.png   element-only shot, one per post
```

`post.png` and `post.txt` keep their present names and meaning, so `xArtifacts`,
`stage-x-media.mjs` and every existing consumer keep working on a thread-enabled
post without modification.

### Canonical text

Deterministic by construction, in the shape `flatten_reddit_thread` established:

```
thread: 2083247006139503065
url: https://x.com/clay_garrett/status/2083247006139503065
author: clay_garrett

post: 2083247006139503065
role: focal
author: clay_garrett
name: Clay Garrett
created: 2026-07-31T17:42:45Z
media: 0
body:
1/ During our investigation of the Coldcard drain yesterday, we identified ...

post: 2083247007808774228
role: self-thread
...

gap: "Show more" control present, not expanded
gap: reply cap reached; X ranking governs which replies loaded
```

Posts are partitioned by role in the fixed order ancestor, focal, self-thread,
reply, and sorted by status id ascending within each partition. Id order is
chronological order and is immune to ranking churn. Role partitioning keeps a
newly landed reply from displacing the author's own chain in the diff.

Counts, scroll rounds, dry rounds and cap values live in `meta.json`, not in the
canonical text. They are facts about this project's collection, they change on
every capture, and putting them in the diffed text would make every poll report
a change. The `gap:` lines carry the qualitative statement a reader of a
40-line excerpt needs.

Only six fields are extracted per post: status id, handle, display name, the
`datetime` attribute, `tweetText` innerText, and a media count. Engagement
counters, relative timestamps, verified badges and the reply-control row are
never read, so the churn the Reddit normalizers exist to suppress does not
enter the text in the first place. No `x-thread` source should need a
normalizer binding, and if one does, that is a signal the extractor is reading
too much.

### Truncated posts must be expanded before extraction

Found while validating the extractor on 6 Aug 2026, and it is the most
consequential thing in this document.

X serves a long post cut off. Post 2 of the `clay_garrett` attribution thread
held 275 characters in `textContent`, with no CSS clamp and no note-tweet
node: the rest was genuinely not in the DOM, behind a
`data-testid="tweet-text-show-more-link"` button. Expanding it produced 397
characters, and the recovered clause was:

> ...no evidence that the provider knowingly participated in or facilitated
> the suspected theft.

That is the clause `clay-attribution`'s own registry note quotes as what the
complete thread says. An extractor that reads the DOM as served would have
captured this thread, filed it as a whole conversation, and silently omitted
the sentence the entry exists to preserve. Seven posts needed expanding on a
three-round capture of that one thread.

So expansion is mandatory and runs before every extraction pass, including
after each scroll, because a post scrolled into view arrives truncated like
any other.

Expanding stays within the daemon's read-only contract, and the scope is
deliberately narrow: it clicks exactly one `data-testid`, it asks the platform
for more of a post already on screen, and the probe confirmed `location.href`
and the article count are unchanged by it. It is not a general click
primitive and must not become one.

**This is also a live defect in `ingest-x.py`**, independent of thread work: it
reads `tweetText` the same way and has always captured long posts truncated.
Fixed 6 Aug 2026 by calling the same `expand_truncated()`, with the extractor
now reporting a `truncated` flag and the capture refusing rather than writing a
body that stops mid-sentence.

Finding the affected captures needed a correction of its own, worth recording
because the obvious method does not work. Body length looks like a detector and
is not: held bodies pile up between 240 and 320 characters, but that bulge is
mostly X's ordinary 280-character limit, so a complete tweet and a truncated
long post land in the same band and cannot be told apart by length. Two
early estimates drawn that way (22, then 70) were both unreliable, and a
hand-picked "obvious" case turned out to be a complete post that genuinely ends
`"...it fails closed. Fix:"` with no expander on the page at all.

What length does bound is the search. A truncated render is never short and
never long, because X caps it near 275 characters: a held body of 50 characters
was never cut, and one of 800 characters proves X rendered the post in full. So
bodies in [220, 320] are the candidate set, 73 of the 308 registered posts by
their newest capture, and the only sound test within it is to re-read the page
and look for the expander.

`ingest-x.py --skip-unchanged` is what makes that safe to run in bulk: it
compares the freshly expanded text against the newest held body and writes
nothing when they match, so the pass self-selects and a post that was never
truncated costs a page load and no archive write.

**Audit result, 6 Aug 2026: all 73 candidates re-read, none was truncated.**
Zero recovered, zero failed, no archive write. The failure was checked before
the result was believed, because a pass that silently expands nothing looks
exactly like a pass that finds nothing: running `ingest-x.py`'s own extraction
against the known-truncated `clay_garrett` continuation post reported
`truncated: true` at 275 characters, then `truncated: false` at 397 after
expansion, so the detection path demonstrably works and 73 of 73 unchanged is
a true negative.

The reason is worth keeping, because it says where the exposure actually is.
**X does not truncate the focal post of its own permalink page.** It truncates
posts rendered in list context: thread continuations, replies, timeline
entries. `ingest-x.py` only ever captured focal posts on their own permalinks,
which is why nothing held was affected, and the truncated post that proved the
defect is a self-thread continuation this archive had never captured at all.

So the defect is real and the writer fix stands, but it repairs nothing
retrospectively. It is a precondition for the thread work rather than a debt
against held material: the moment a capture reads posts in list context, which
is the whole of section 5, every one of them arrives truncated.

### Roles

Assigned in the first extraction pass, before any scrolling, while the
un-virtualised head of the conversation is intact:

- `focal`: the registered status id.
- `ancestor`: articles rendered above the focal post.
- `self-thread`: same author as the focal post, rendered contiguously below it,
  up to the first article by another author.
- `reply`: everything else.

Role is recorded per status id in `meta.json`. If a later capture assigns a
different role to a status id it has seen before, the capture records that in
its sidecar rather than silently rewriting the partition. Role derivation is the
one heuristic in this design and it should be visible when it moves.

### Screenshots

Taken during the scroll pass, using the existing `ISOLATE_JS` clone technique,
while the post is in the DOM.

A status id is screenshotted **on first sight**, and re-shot only when its
extracted text changes. At tier 3 a fifty-reply thread would otherwise write
200 PNGs a day of images it already holds. A capture directory therefore holds
the shots taken in that capture, not every shot for that thread, and the site
composes across directories to find the newest held shot per status id. That is
the same composition `annotateHeldDeletionsText` already does across earlier
snapshots, and the append-only rule is unaffected: nothing is ever rewritten.

### Where the code goes

A new `scripts/x_thread.py` holds the browser work and returns
`(canonical_text, structured_record, screenshots)`. `capture.py` gains method
`x-thread` that calls it and writes through the normal snapshot path;
`ingest-x.py` imports the same module for the manual first capture. Stdlib only,
as with every other capture backend: the browser is reached over HTTP.

Browser work happens before the archive lock is taken, as `ingest-x.py` already
does. A thread capture is 60 to 120 seconds on first sight and considerably less
afterwards, and it must not hold the writer lock for that.

## 6. Absence is not deletion

This is the one place where the Reddit model does not transfer and the
difference matters.

A deleted Reddit comment leaves a `[deleted]` or `[removed]` tombstone in the
JSON, which is exactly what `reddit-deletions.ts` keys on to show the held body
under a "held before deletion" line. A deleted X reply leaves nothing. It is
absent, and so is a reply that ranking pushed below the scroll cap, and so is
one that failed to hydrate.

The archive must never call the second and third of those a deletion. The
resolution is to keep the existing vocabulary and let the judgement land where
judgement already lands:

- The canonical text lists what this capture observed. A reply that is absent
  produces a removal in the diff, like any other text that went away.
- `meta.json` records the depth reached, so a reviewer can see whether this
  capture went as deep as the one before it.
- Classification happens in `revision-reviews.toml`. A reply that vanished
  while the capture reached the same depth is `source-content`. A reply that
  vanished from a shallower capture is `capture-noise`. No new review status is
  needed and the site does not adjudicate.

The operational cost is real and was sized on the pilot rather than guessed.
The review agent prompt needs the X-thread case added, or every ranking shift
arrives as an unreviewed diff and blocks a build.

### What the pilot measured, and the two guards it forced

First captures of `clay-attribution` on 6 Aug 2026 produced 31,779 characters,
135 posts and 43 screenshots. Then three things went wrong in ways worth
keeping, because each is a quiet failure rather than an error.

**A binding cap turns a capture into a sample.** Two captures two minutes apart
differed by +113 -99 lines. Both had hit the 120-reply cap, so which replies
they held was decided by X's ranking rather than by the conversation: 10
replies dropped out and 12 appeared, none of which was an event. Allowed to run
to the end, the same thread converged on 146 replies in 34 rounds with nothing
declared. The caps are therefore safety valves, not operating points, and
`capped` in the depth record is the signal that a capture is a ranked sample
whose diffs are noise. `REPLY_CAP` is 500 and `SCROLL_ROUNDS_MAX` 120.

**Dryness alone does not mean the conversation ended.** With the caps raised, a
capture of the same thread returned 45 replies, `capped: false`, and **no
declared gaps**: it had gone quiet for four rounds mid-page while X's loader
lagged, and treated that as convergence. Its diff was `-826` lines. This is the
worst outcome the design can produce, because a capture that quietly stops a
third of the way through is indistinguishable, in the record, from 88 replies
having been deleted.

The fix is that a quiet round is only convergence when the viewport has
actually reached the bottom of loaded content, which is where X appends more.
`atBottom` now comes back with every extraction. Quiet mid-page counts as a
stall, waits, and retries; a persistent stall declares
`gap: loading stalled before the end of the conversation` instead of finishing
silently. Quiet at the bottom waits a grace period and re-reads before it
counts toward the dry streak, because the first look at the bottom is the
unreliable one.

**And a guard for when that is not enough**, because both failures above were
detectable only by comparing against the previous capture. `fetch_x_thread`
reads `replies_observed` from the newest held structured record and refuses the
capture when this poll collected less than 75 percent of it (above a floor of
20 replies, so small threads are unaffected). The refusal is recorded as an
error the poll reports, rather than a snapshot written. This deliberately
prefers a visible gap in collection to an invisible fabrication of deletion. It
also means a genuinely mass-deleted thread will refuse repeatedly and need a
human to look, which is the correct way round for an archive.

**With all three in place the lane behaves.** The next poll pair of the same
thread, about seventy minutes apart, produced **+55 -0**: 146 replies observed
against 140, six genuinely new replies from six named accounts, no reply
leaving the capture and eight screenshots taken rather than 149. That is the
shape a thread diff is supposed to have, against +113 -99 when the cap bound
and -826 when the loader stalled. Diff volume is therefore additions only in
the steady state, which is a review burden proportional to what actually
happened rather than to how the page loaded.

The five pilot diffs are classified in `revision-reviews.toml` and are worth
reading as a set, because they are a worked example of the vocabulary: two
`capture-noise` for the capped pair, one `capture-noise` for the stall, one
`capture-correction` for the recovery, and one `source-content` for the six
real replies.

A held screenshot is what survives a deletion. The image of a reply captured
while it was public is the record that it was made, which is section 7 of the
display policy operating exactly as written.

## 7. Presentation layer

### The source page

`record/sources/[id].astro` currently hides the snapshot timeline, the noise
table and the verification aside for social posts with `hidden={isSocial}`. For
a thread-enabled post those become `hidden={isSocial && !hasThread}`: the
conversation has a revision history, so it gets the history presentation that
every polled source gets.

Above it, a new `ThreadReader.astro`:

- Ancestor, if any, marked as context the author was replying to.
- The focal post, then the self-thread chain, screenshots expanded by default.
  A three-post chain is one statement split by a character limit and reads as
  one thing.
- Replies behind a `Disclosure`, oldest first, with progressive reveal. Fifty
  full-width PNGs is not a page; the initial reveal is bounded and the rest
  loads on request, with `loading="lazy"` throughout.
- Every rendered shot carries its alt text, its **capture timestamp** and a link
  to the original. The capture timestamp beside media is an outstanding duty in
  section 8 of the display policy; a thread reader is the wrong place to add a
  fiftieth instance of a missing one, so it ships with the timestamp.
- The declared gaps rendered as text, not hidden in the excerpt. A reader must
  be able to see that the capture stopped somewhere and that ranking chose what
  loaded.

### Muting low-signal replies

The probe's reply set was mostly not evidence. Alongside a 295-character
question from Kevin Kelbie and a substantive challenge from
@BitcoinCoderBob sat "go get em, champ." (17 characters), "Wow. well done" (14),
"Good shit thank you" (19), and two replies with no extracted text at all,
carrying an image. Rendering those at the same visual weight as the author's own
answers buries the evidence in applause.

The rule is **de-emphasise, never omit, and print the rule on the page.**
Nothing captured is hidden. A muted reply collapses from a screenshot to a
one-line attributed row carrying its handle, timestamp, text and a link to the
original, with a control that shows its screenshot. The reader is told how many
replies are muted and which predicate muted each one, and can expand all of
them.

This stays inside "report and attribute, do not adjudicate" because every
predicate is a mechanical property of the post, never a reading of what it
argues or whether this project agrees with it.

Three bands:

- **Foregrounded**, screenshot expanded by default. Any of: the reply is by the
  focal post's own author, which is how a thread's questions get answered and is
  often the most load-bearing material in it; the author is registered elsewhere
  in `sources.toml`, which is a lookup rather than a judgement of merit; or a
  page on this site cites that status id.
- **Standard**, screenshot inside the bounded progressive reveal.
- **Muted**, collapsed to the one-line row.

Muting predicates, all computed from the six extracted fields, all recorded so
the page can name the one that fired:

- `short`: extracted text below a stated threshold. On the probe sample the
  applause replies ran 14 to 30 characters and the substantive ones 95 to 295,
  so a threshold near 40 separates them. It is a named constant in one place and
  is stated on the page, not tuned quietly.
- `no-text`: zero extracted characters, media only.
- `mentions-only`: nothing but `@handle` tokens and whitespace.
- `symbols-only`: no letter or digit characters at all.
- `duplicate`: normalised text identical to another reply in the same capture,
  which is what bot amplification looks like.
- `link-only`: a bare URL and nothing else. Overlaps the scam-reply case and is
  the reason that case usually gets caught before anyone reads it.

Never predicates: sentiment, keywords, agreement with anything the site says,
follower counts, or any property of the author beyond presence in this
project's own register. Absence from the register never mutes. Presence only
ever promotes.

Muting is computed at build time in `x-thread.ts` from the canonical text and is
never written into the archive. Every reply is held identically; how loudly it
renders is a property of a page, so changing the rule re-renders without a
re-capture. This is the same separation that keeps normalizers from rewriting
held text.

Muting is not withholding, and the two must not be confused in the code or on
the page. `withhold_posts` removes a reply's screenshot and says that it did.
Muting changes presentation only, and the material stays one click away.

### Supporting code

- `site/src/lib/x-thread.ts`: parse the canonical text into structured posts,
  resolve the newest held screenshot per status id across capture directories,
  and apply the gates. It calls `withholdsCapturedMedia()` and the staging
  manifest; it does not reimplement either.
- `stage-x-media.mjs`: stage `thread-<status>.png` under the same
  `OWN_HOST_FROM` gate and the same explicit rejection of non-timestamp
  directories. The manifest gains per-status entries under the post id. The
  withheld count reporting extends to them.
- `xmedia.ts`: a `xThreadMedia(postId)` accessor beside `xMedia(postId)`.
- Astro's built-in `astro:assets` handles downscaling if payload measurement
  says it is needed. No new site dependency for a first cut.

### Elsewhere on the site

- `/record/changes/` picks up thread revisions with no work, because the id is
  the same one it already lists.
- `/record/sources.json` and `/llms.txt` declare which posts hold a thread and
  state the declared-gap semantics, so a machine reader does not take a capture
  for a complete conversation.
- Cards and `CaptureThumb` gain a thread marker and a held-post count. The
  thumbnail itself stays the focal post.
- `/record/timeline/` is left alone. The focal post is the dated event; its
  continuation posts are the same statement and should not become separate
  moments.

## 8. The display policy revision this requires

`capture-display-policy.md` section 1 grants display of element-only screenshots
of **individual** public posts and explicitly keeps forum threads to diffs and a
bounded excerpt. Rendering fifty reply screenshots is not covered by that grant
as written, and shipping it without amending the policy would make the policy
describe something the site no longer does.

The revision to write, before any of section 7 ships:

- **Extend the grant to posts within a captured conversation**, on the ground
  that each remains an individually short, public, attributed post, and that
  the conversation is the incident event in the cases being captured.
- **State the boundary that keeps it from becoming a mirror**: bounded initial
  reveal, oldest-first order, every post linking to its original, and the
  declared gaps shown. The reader is being shown the record, not offered a
  substitute for reading the thread on X.
- **Add a per-status withhold.** The current model has one flag per source, and
  it is too coarse here. The probe surfaced apparent image-spam accounts among
  the replies, and the scam wave documented on `/response/scams/` means reply
  sections in this incident carry phishing. A phishing reply rendered as an
  image cannot be defanged by escaping a URL, because the URL is pixels.
  `withhold_posts = ["2083...", ...]` on the registry entry, honoured by the
  same one function that answers `withholdsCapturedMedia()`, lets one reply be
  pulled without withholding a whole conversation. The capture still holds it:
  display and retention stay separate decisions.
- **Carry a standing note on the thread reader** that replies are unmoderated
  third-party material, reproduced as record, and that inclusion is not
  endorsement. This is the registry's existing posture applied to a surface
  where it now matters more.

The removal posture is settled and needs no change for this work. `AGENTS.md`,
`/corrections/`, the CHANGELOG and section 9 of the display policy all carry the
6 Aug 2026 withdrawal of the undertaking to honour an author's removal request.
A reply author is in the same position as any other author here: what they
published publicly is the record, and the routes that still take material down
are the ones in section 5, none of which is a change of mind.

What this design does change is volume. It multiplies the number of third
parties whose material the site displays by roughly fifty per captured thread.
That is a reason to get the per-status withhold and the muting rules below
right, because they are what let one bad reply be handled without touching a
conversation, not a reason to revisit the posture.

## 9. Implementation order

1. ~~`scripts/x_thread.py`: scroll, accumulate, extract, role-assign,
   screenshot, flatten.~~ **Done 6 Aug 2026.** `scripts/x_thread.py` plus
   `scripts/test_x_thread.py`, 30 tests wired into `just test-capture`. The
   browser is injected, so the capture loop is tested by replaying recorded
   extraction passes with no network. Validated live against the
   `clay_garrett` attribution thread (27 posts, 24 replies, 7 expanded, 23
   screenshots) and the `afilini` libngu thread (focal plus a four-post
   self-thread, converging cleanly on the dry-round rule, confirmed against a
   deliberately generous 18-round run that found nothing further). No archive
   write: the module returns artefacts and the caller writes.
2. ~~`capture.py`: the `x-thread` method, `validate_sources` rules, audit
   allowlist and artefact check.~~ **Done 6 Aug 2026.** Also `pollable_sources()`
   feeding all four call sites that previously read `cfg["source"]` directly,
   and a fix to `check_publishable.py`, which read `withhold_text` from
   `[[source]]` only and so would not have covered a withheld conversation.
3. `ingest-x.py --thread`, and a `just capture-thread <id>` recipe.
4. Pilot: **`clay-attribution` registered and live at tier 3 since 6 Aug 2026**;
   `trustwallet-wasm-update` and `bitcoindevs-explainer-thread` still to add.
   Watch the diff shape across several days before adding more: additions-only
   is the healthy state, and any removal is worth reading before it is
   classified.
5. The display policy revision in section 8, including the per-status withhold.
6. `stage-x-media.mjs`, `x-thread.ts`, `ThreadReader.astro`, the source page.
7. Registry declarations in `sources.json` and `/llms.txt`, then the card
   markers.
8. Review agent prompt: the X-thread absence case, so ranking churn classifies
   as `capture-noise` without a human touching every one.

## 10. Risks

- **Diff volume.** The largest one. A fixed scroll cap, a slow cadence and step
  4's measurement exist to size it before it becomes a standing tax on the
  review gate.
- **Bot heuristics.** This is authenticated session scrolling, which is more
  activity than any current X use in this repo. Cap the rounds, keep the tier
  slow, keep the curated set small, and treat a challenge as a skip-with-event
  like every other refusal, never as something to work around.
- **Role heuristic drift.** X can change how it renders a self-thread. The
  role-change record in `meta.json` is what makes that visible rather than
  silent.
- **Extractor fragility.** Every selector here is X's internal
  `data-testid` vocabulary, which is not a contract. The mitigation is the same
  one `capture.py` already uses: a capture that cannot find what it expects
  fails and records a skip, and never writes a partial thread as though it were
  a whole one.
