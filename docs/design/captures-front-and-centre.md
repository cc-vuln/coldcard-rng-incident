# Design: captures front and centre

**Status:** decided 3 Aug 2026; P1 and most of P2 shipped the same day. Public
hash presentation was retired 5 Aug 2026. The remaining capture-timestamp
duty is tracked in `BACKLOG.md` section 0 and
`design/capture-display-policy.md` section 8, not here.
**Date:** 3 Aug 2026, revised 5 Aug 2026

The record-first refit of 5 August superseded this document's owner-triage,
phase-switch and navigation decisions. Its capture-display findings and policy
remain in force. The current route and navigation contract is in
`design/site-information-architecture.md`.

This is the decision record of the 3 Aug 2026 information-architecture
review, "captures front and centre". The review itself is generated working
material and stays in the ignored `.work/` directory; this document is the
committed account of what it found, what was decided and what shipped.

## What the review found

The archive is the site's main contribution, but the site presented it as
its fourth section:

- The primary nav demoted the record to item four of six, with the most
  generic label on the bar.
- The homepage described the archive in prose but never showed it: no
  thumbnail, no latest changes, no freshness stamp, even though
  `stats().lastCapture` was already computed and nothing consumed it.
- The register at /record/ led with nine groups of web-source metadata
  cards; the first actual capture appeared 42 percent down a 311 KB page.
- The per-source page, the destination of every card and feed click,
  showed an X capture as a hash table and never rendered the screenshot,
  so a reader clicked a card bearing an image and landed on a page with
  less evidence than the card.
- The feed at /record/changes/, the best captures-first page on the site and
  roughly 80 percent of the parked captured-tweet-timeline idea, had
  exactly one inbound link sitewide and was absent from the record subnav.
- Display had outrun the written policy: 62 staged post screenshots were
  already rendering on two pages, so the policy revision that had to
  precede broad display was overdue rather than optional.

The review also found what already worked, and none of it was to be
displaced: the triage front door, the disclosure shape, the grouped
source taxonomy, the vanished-sources aside, the per-source revision history,
the excerpt discipline, the staging gate, the changes page's
three-way separation, the JSON feeds and the no-JS progressive reveal.

## The decision

A hybrid of the evidence wall and the feed, with a clear division of
labour:

- **The evidence wall is the register's face.** It answers "what does
  this archive hold" spatially, and it degrades correctly: cards without
  staged media fall back to excerpts.
- **The feed is the chronology.** It answers "what happened, in order"
  and already interleaves captures with detected changes, which no wall
  can.
- **The ledger wall was rejected as a new surface.** A dense chronological
  ledger of uniform rows is essentially what /record/changes/ already is,
  and it is the least legible of the three directions to a non-technical
  reader. The changes page keeps that identity.
- **The precondition:** the artefact must render on its own source page
  first. Whichever wall treatment is chosen, every wall click still ended
  at a hash table until that shipped.
- **The policy gate:** broad public display of captured post screenshots
  required the written policy revision first. It is at
  `design/capture-display-policy.md` and stated publicly on /about/.

## What shipped

P1, highest leverage:

- **R1, artefact-first source pages.** The staged screenshot leads the
  per-source page as a figure. The artefact hash table that originally shipped
  beneath it was retired on 5 Aug 2026 because it did not independently
  authenticate the capture.
- **R2, policy revision.** Written before the next deploy, at
  `design/capture-display-policy.md`.
- **R3, homepage record band.** Stat tiles, a freshness line driven by
  the previously unused `stats().lastCapture`, the latest three feed
  entries and the vanished-sources one-liner, all below the triage door,
  which is untouched.
- **R4, feed in the subnav.** Plus links from the homepage band and the
  record path card.

P2, structural:

- **R5, capture wall leading /record/**, with a freshness stamp; the
  grouped taxonomy below it unchanged.
- **R6, phase-aware nav.** The record moves up while the incident phase
  is active without displacing "Am I affected?", and the phase switch
  reorders the nav as it already reordered the homepage grid.
- **R7, thumbnails beside citing entries.** A shared `CaptureThumb`
  component placed 41 thumbnails inside rungs and entry bodies on
  /record/timeline/, /record/reference/#analysis and /response/, one per dated
  entry, never above Answers.
- **R8, named evidence journeys.** One line per journey in the record
  band: what the vendor said and when, who did the primary analysis,
  what was captured in order.

P3, refinements:

- **R9, a "with screenshots" facet on the feed**, stamped only on
  entries whose image actually rendered; the separate captured-tweet
  timeline idea converged into the feed rather than a new page.
- **R10, register freshness stamp**, shipped with the /record/ wall.
- **R11, per-source og images.** Not pursued after the record-first refit. The
  canonical source page already carries the capture and its context; generating
  a separate social card for every source is presentation polish rather than a
  record gap.

Also shipped from the policy outline: one withhold surface, with
`withholdsCapturedMedia()` consulted by every media renderer, so the
text-withhold flag and the media gate are no longer independent
mechanisms.

## What this did not change

- The active-phase hero, the checkdoor and SeedWarning, and the
  front-door plainness rule. Everything above sits below or beside the
  triage answer, never in front of it.
- The disclosure shape. Captures were added at the rungs, not promoted
  above the Answers.
- The register's grouped-by-what-a-source-is taxonomy and the
  vanished-sources aside at the top of /record/.
- The per-source revision apparatus: unified diffs, the Internet Archive
  cross-check, explicit wayback provenance and bounded revision windows.
- The excerpt discipline, the withhold flags and the OWN_HOST_FROM
  staging gate.
- The changes page's three-way separation of source content, collection
  differences and operational events.
- The JSON feeds, /llms.txt and the claim-marker system.
- The INCIDENT_PHASE mechanism itself; it was extended to the nav, not
  replaced.
- The feed's no-JS progressive reveal; it remains the template for any
  wall built later.
