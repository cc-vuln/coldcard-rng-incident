# Design: capture display policy

**Status:** adopted; staging gate, withhold flag on both registry block types
and renderer consultation built; public hash presentation retired 5 Aug 2026;
display grant extended to posts inside a captured conversation 6 Aug 2026
(sections 1a and 1b), with `withhold_posts` as the per-status form
**Date:** 3 Aug 2026, revised 5 and 6 Aug 2026

The question this answers: when may a capture held in this archive be
*displayed* on a public page, rather than represented only by metadata?

The site's standing posture is excerpts, not mirrors: `PUBLIC_FULL_TEXT`
defaults to false, source pages show diffs, a 40-line excerpt and a link, and
the complete captured bodies stay local where they still back every
claim. Displaying captured X post screenshots diverges from that posture, so
this policy had to be written before that display ships broadly. The site
renders held post screenshots on `/record/` and `/record/changes/`, and this
document is the revision that makes that display defensible rather than
accidental. It revises the display rule only. Nothing here changes what is captured, how the archive is written, or
the append-only rule.

The 5 August revision separates internal archive checks from public evidence.
Snapshot hashes remain in sidecars. The archive audit recomputes the
extracted-text hash to detect later mutation of that text; the raw-byte hash
remains capture metadata. They do not authenticate who made a browser capture
or make that capture independently reproducible, so source pages and the public
source register do not publish them as provenance or tamper-resistance claims.

## 1. Scope of display

Element-only screenshots of individual public social posts may render on
public pages. That is the whole grant. It rests on the post being short,
self-contained, and itself an event in the incident: what NVK said, what a
researcher claimed, what a victim reported. The statement is the evidence, so
showing the statement is showing the evidence.

Everything longer keeps the excerpt-only treatment. Full articles, forum
threads, blog posts, advisories and any other long-form work continue to
appear as diffs and a bounded excerpt, never as full mirrors.
`PUBLIC_FULL_TEXT` semantics are unchanged and the flag stays false in every
public build. A screenshot of an article page is a mirror with extra steps
and is not covered by this grant.

### 1a. Posts inside a captured conversation

**Added 6 Aug 2026, with X thread capture.** The grant extends to the
individual posts of a captured X conversation: the focal post, any ancestor it
replies to, the author's own continuation posts, and replies.

The reasoning is that nothing about those posts is different in kind. Each is
still a short, public, attributed statement, and in the threads this archive
captures the conversation is itself the incident event: the questions put to
Block's attribution thread and the answers given are the record, not
decoration around it. A self-thread is the clearest case, since it is one
statement split by a character limit and reading it whole is reading one post.

What keeps this from becoming a mirror of a conversation, and what the
presentation must therefore do:

- **Bounded initial reveal.** The focal post and the author's own chain render
  expanded. Replies sit behind a disclosure, revealed progressively. A reader
  arrives at the record, not at a rebuilt timeline.
- **Oldest first**, which is the archive's order and not the platform's
  ranking. The page is not reproducing X's editorial choices about what to
  show first.
- **Every post links to its original**, so the reader can leave for the source.
- **The declared gaps are shown.** A capture that stopped somewhere says so.
  Presenting a bounded collection as a complete conversation would be the
  substitution this policy exists to prevent.
- **Replies carry a standing note** that they are unmoderated third-party
  material reproduced as record, and that inclusion is not endorsement. This is
  the registry's existing posture, on a surface where it now matters more.

### 1b. Muting is presentation, withholding is publication

Two different mechanisms, and they must not be confused in the code or on the
page.

**Muting** de-emphasises a low-signal reply: it collapses from a screenshot to
a one-line attributed row, still carrying handle, timestamp, text and a link,
with a control to show its image. Nothing is hidden and nothing is removed. It
exists because a viral thread's replies are mostly applause and the evidence
should not be buried in it. Every muting predicate is a mechanical property of
the post (length, emptiness, mentions-only, symbols-only, duplication, bare
link), never a reading of what the reply argues or whether this project agrees
with it, and the rule is printed on the page so a reader can disagree with it.
Muting is computed at build time and never written into the archive.

**Withholding** removes material from the page. `withhold_text` on a registry
entry withholds a whole source, text and media together. `withhold_posts` is
its per-status form, added for conversations: a list of status ids whose text
and image are withheld from a thread that is otherwise published.

The per-status form exists because one flag per source is too coarse here. A
conversation is not a single author's work, and the scam wave documented on
`/response/scams/` means reply sections in this incident carry phishing. A
phishing reply rendered as a screenshot cannot be defanged by escaping a URL,
because the URL is pixels. Without `withhold_posts` the only available
responses would be to publish it or to withhold an entire conversation, and
neither is right.

The capture still holds a withheld post. Display and retention remain separate
decisions, exactly as in section 5.

## 2. Why displaying a post is defensible quotation

A short attributed post is displayed as quotation and record, on four legs:

- **Factual archival purpose.** The screenshot documents that a statement was
  made, by whom, and in what words, in a project whose job is recording what
  parties said and when it changed. It is evidence, not decoration.
- **Full attribution.** Every displayed post carries its author handle, its
  date, and a link to the canonical original, so the reader can go to the
  source rather than stopping here.
- **No substitution effect.** A screenshot of one post does not stand in for
  the platform or for the author's feed. Nobody reads this archive instead of
  X. The display serves readers checking the record, not readers seeking the
  author's content.
- **Commentary and provenance context.** Posts render inside the record:
  beside capture metadata, revision history and the registry note saying why
  the post matters. They are never presented bare as content.

This reasoning is also stated publicly on `/about/`, so a displayed author can
inspect the rationale rather than having to ask for it.

## 3. The provenance gate

Only captures this project took on the capture host render publicly. The rule
is when a capture was taken, never what it looks like, because inspection
cannot clear an image: a signed-in session's avatar is a few hundred pixels
and moves with the layout, and measurement heuristics (width, aspect ratio)
pass exactly the captures they should catch.

Concretely, restating the AGENTS.md rules so policy and code agree:

- `stage-x-media.mjs` stages only captures dated at or after `OWN_HOST_FROM`,
  and reports how many posts it withheld. Captures from before the cutover
  never stage, whatever they contain.
- A capture directory whose name is not a timestamp (`undated`) is rejected
  explicitly. String comparison does not do this: `"undated" < "20260802..."`
  is false because letters sort after digits.
- No image-inspection heuristic ever clears a capture. Not width, not aspect
  ratio, not anything measured from the pixels. A capture is publishable
  because of where and when it was taken, or it is not publishable.

## 4. Withhold semantics: one flag, text and media together

`withhold_text = true` in `sources.toml` withholds captured media as well as
captured text. A screenshot of a post reproduces the post at least as fully
as its extracted text does, so there is no coherent state in which a source's
text is withheld and its image renders. One flag surface, one answer.

In code that is `withholdsCapturedMedia()` in `src/lib/archive.ts`, a sibling
of `withholdsCapturedText()` that delegates to it. Every renderer that shows
staged media must consult it beside the staging gate: staging establishes
provenance (this capture is ours to show), the withhold flag is the
publication decision (this source's material may be shown). The two are
independent questions and passing one does not answer the other. Page-local
copies of a withhold rule drift until the leakiest copy wins, so both rules
live in exactly one function each.

Publishability follows the standing 3 Aug 2026 decision: material its author
published is publishable here, first-hand accounts included. A source that
must be held back sets `withhold_text = true` on its registry entry, which
withholds its text and media together; no source sets it today.

## 5. Removal and correction

**Revised 6 Aug 2026.** This section previously undertook to honour an
author's removal request. It no longer does. Material its author published
publicly stays in the record, and this project does not withdraw it because
the author has changed their mind about having said it. The posts held here
are short, public, attributed statements that were themselves events in the
incident; a record whose contents can be edited by the parties in it cannot
answer the question it exists to answer, which is what each party actually
said and when they changed it.

Four things still come down, and the mechanics below are what takes them
down:

- **Material that was never public.** Personal data, anything identifying a
  private individual, anything reached through a session rather than
  published. This is the `SECURITY.md` route and it is unchanged.
- **Anything this project got wrong.** A misattributed post, a capture filed
  under the wrong source, a screenshot that does not show what the page says
  it shows. That is a correction, logged at `/corrections/`.
- **Anything the operator decides to hold back** for reasons of its own, via
  `withhold_text` on the registry entry.
- **Anything a valid legal complaint requires.** A copyright complaint, a court
  order, or a demonstrated legal obligation. Assessed on its merits: neither
  deferred to automatically, nor refused on the strength of the paragraph
  above. An author's preference is not a legal basis, and a legal basis is not
  an author's preference; do not let the first paragraph of this section be
  read as a reply to the second kind of letter.

Taking something down is not one action, because of how Pages deployments
work:

1. Unstage the capture (remove it from the manifest and `public/`) and
   rebuild, so no current page renders it.
2. Purge the Cloudflare cache from the dashboard, because the deploy token
   cannot do it, or accept the `max-age` wait.
3. Delete stale Pages deployments, which stay reachable at their own
   subdomains after a new deploy goes live. `wrangler pages deployment
   delete` needs `--force`; without it, it prints usage and exits as though
   nothing were wrong.

Unstaging takes the image off the site. The archival capture itself is retained
under the append-only rule: the record that a statement was made is the
archive's job, and display and retention are separate decisions. The one
exception remains redaction of this project's own leaked personal data, where
the sidecar hashes still record what was held.

## 6. Presentation duties

Every rendered screenshot carries:

- **Alt text** describing what it is a capture of.
- **The capture timestamp**, so a reader knows when this project observed the
  post, which is not the same fact as when the author posted it.
- **A link to the source page**, where the capture time, original link,
  registry note and any revision history live.

And one prohibition: wayback-recovered material is never presented as a
capture this project took. `provenance: wayback` is displayed as inherited
wherever such material appears, matching the snapshot-timeline treatment.
Since the staging gate admits only this host's own captures, no wayback image
should be stageable at all; if that ever changes, the provenance mark comes
with it.

## 7. When the original is deleted

Display gets easier to defend when the original is gone, not harder. A
screenshot of a vanished post is the record of a statement that can no longer
be checked anywhere else, which is the case this archive was built for, and
the substitution argument disappears entirely: there is nothing left to
substitute for.

The duty that comes with it: the page must say the original no longer
resolves, mirroring the existing gone-source treatment (`gone`, `gone_since`,
`gone_note`), so a reader is never left believing the link beside the image
still works. Post deletion detection is phase 2 of the discovery design and
is not built; until it is, a post's gone state is recorded manually when
observed.

## 8. What is implemented today, and what is not

Accurate as of 5 Aug 2026, checked against this tree.

Implemented:

- The staging gate: `OWN_HOST_FROM` cutover, withheld-count reporting, and
  explicit rejection of non-timestamp capture directories (section 3). All
  308 registered posts stage.
- `withholdsCapturedMedia()` exists in `src/lib/archive.ts`, delegates to
  `withholdsCapturedText()`, and is consulted beside the staging gate by
  every renderer that shows staged media: `EvidenceCard.astro`,
  `CaptureThumb.astro`, `record/index`, `record/changes/index.astro` and the
  source page.
- `withhold_text` is honoured on both registry block types: `[[source]]`
  and `[[x_post]]`.
- The source page renders held media alongside its capture time, original link,
  registry context and any revision history.
- Rendered screenshots carry alt text and link to the source page (two of
  the three duties in section 6).

Not implemented, in order of urgency:

- **The capture timestamp is not shown beside rendered screenshots.** The
  feed shows the post's own date and the card shows publication metadata;
  neither states when the capture was taken.
- ~~The removal route is not yet stated next to displayed media~~ (section
  5). Retired 6 Aug 2026 with the removal undertaking itself. What is stated
  on `/about/` is now the display rationale and the correction route.

## 9. Deleted comments in held threads

Section 7 covers a captured post whose original is later deleted. The same
posture applies inside held Reddit threads. When a comment the archive
captured while public is later deleted or removed at the source, the
record keeps what it said: the earlier snapshot is never rewritten, and
the source page marks the comment as deleted and shows the held text under
a "held before deletion" line, so the deletion stays visible as the event
it is. A comment deleted before the archive first saw it keeps its bare
marker; this project does not recover bodies it never held. A deletion at the
source is not a request to this archive and does not remove what was captured
while the comment was public; section 5 lists what does come down.
