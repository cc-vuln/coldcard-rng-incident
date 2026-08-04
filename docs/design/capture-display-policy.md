# Design: capture display policy

**Status:** adopted; staging gate, withhold flag on both registry block types
and renderer consultation built; two presentation duties outstanding (see
section 8)
**Date:** 3 Aug 2026

The question this answers: when may a capture held in this archive be
*displayed* on a public page, rather than only proven by its hash?

The site's standing posture is excerpts, not mirrors: `PUBLIC_FULL_TEXT`
defaults to false, source pages show diffs, a 40-line excerpt, hashes and a
link, and the complete captured bodies stay local where they still back every
claim. Displaying captured X post screenshots diverges from that posture, so
this policy had to be written before that display ships broadly. The site
renders held post screenshots on `/record/` and `/record/feed/`, and this
document is the revision that makes that display defensible rather than
accidental. It revises the display rule only. Nothing here changes what is captured, how the archive is written, or
the append-only rule.

## 1. Scope of display

Element-only screenshots of individual public social posts may render on
public pages. That is the whole grant. It rests on the post being short,
self-contained, and itself an event in the incident: what NVK said, what a
researcher claimed, what a victim reported. The statement is the evidence, so
showing the statement is showing the evidence.

Everything longer keeps the excerpt-only treatment. Full articles, forum
threads, blog posts, advisories and any other long-form work continue to
appear as diffs, a bounded excerpt and hashes, never as full mirrors.
`PUBLIC_FULL_TEXT` semantics are unchanged and the flag stays false in every
public build. A screenshot of an article page is a mirror with extra steps
and is not covered by this grant.

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
  beside capture metadata, hashes, revision history and the registry note
  saying why the post matters. They are never presented bare as content.

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

An author's removal request is honoured, per the standing 3 Aug 2026
decision. The route is stated next to displayed media, or one hop from it on
the pages every displayed capture links to, using the project contact address
that already exists; nothing new is invented for this.

Removal is not one action, because of how Pages deployments work:

1. Unstage the capture (remove it from the manifest and `public/`) and
   rebuild, so no current page renders it.
2. Purge the Cloudflare cache from the dashboard, because the deploy token
   cannot do it, or accept the `max-age` wait.
3. Delete stale Pages deployments, which stay reachable at their own
   subdomains after a new deploy goes live. `wrangler pages deployment
   delete` needs `--force`; without it, it prints usage and exits as though
   nothing were wrong.

Removal takes the image off the site. The archival capture itself is retained
under the append-only rule: the record that a statement was made is the
archive's job, and display and retention are separate decisions.

## 6. Presentation duties

Every rendered screenshot carries:

- **Alt text** describing what it is a capture of.
- **The capture timestamp**, so a reader knows when this project observed the
  post, which is not the same fact as when the author posted it.
- **A link to the source page**, where the SHA-256 hashes, the full artefact
  record and the capture history live.

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

Accurate as of 3 Aug 2026, checked against this tree.

Implemented:

- The staging gate: `OWN_HOST_FROM` cutover, withheld-count reporting, and
  explicit rejection of non-timestamp capture directories (section 3). 103
  of 111 registered posts stage; the remainder have no clean capture from
  the dedicated profile yet.
- `withholdsCapturedMedia()` exists in `src/lib/archive.ts`, delegates to
  `withholdsCapturedText()`, and is consulted beside the staging gate by
  every renderer that shows staged media: `EvidenceCard.astro`,
  `CaptureThumb.astro`, `record/index`, `record/feed` and the source page.
- `withhold_text` is honoured on both registry block types: `[[source]]`
  and `[[x_post]]`.
- The source page renders held media alongside the artefact record.
- Rendered screenshots carry alt text and link to the source page (two of
  the three duties in section 6).

Not implemented, in order of urgency:

- **The capture timestamp is not shown beside rendered screenshots.** The
  feed shows the post's own date and the card shows publication metadata;
  neither states when the capture was taken.
- **The removal route is not yet stated next to displayed media** (section
  5). The rationale and route are published on `/about/`; the pointer beside
  the media itself is outstanding.

## 9. Deleted comments in held threads

Section 7 covers a captured post whose original is later deleted. The same
posture applies inside held Reddit threads. When a comment the archive
captured while public is later deleted or removed at the source, the
record keeps what it said: the earlier snapshot is never rewritten, and
the source page marks the comment as deleted and shows the held text under
a "held before deletion" line, so the deletion stays visible as the event
it is. A comment deleted before the archive first saw it keeps its bare
marker; this project does not recover bodies it never held. An author's
removal request to this project is still honoured through the route in
section 5; that is a request to this archive, distinct from a deletion at
the source.
