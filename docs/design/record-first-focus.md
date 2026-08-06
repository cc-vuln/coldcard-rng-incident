# Design: record-first focus

**Status:** implemented 5 August 2026. A post-implementation review the same
day verified the gates and the retired-route redirects. Its residual items were
fixed the same day and remain recorded under "Post-implementation review"
below as completion history.

## Decision

The site is the public record of the July 2026 COLDCARD predictable-RNG
incident: it preserves what each party published and how it changed, organises
the material, and explains it without adjudicating between the people involved.

The record has a historical purpose as well as an immediate one. The scale
already documented supports the provisional editorial assessment that this is
likely to rank among the most consequential incidents in Bitcoin's history,
without presenting that assessment as a settled comparative ranking. The
archive is intended to preserve the contemporaneous public record for
posterity, including material later edited or removed.

The record also preserves the evolution of public interpretation. Explanations,
opinions and speculation are organised in time, including rapid changes and
conspiracy theories alleging an inside job or that law-enforcement or
intelligence agencies caused or directed the incident. Those theories are dated
and attributed as public reaction, never presented as evidence that they were
true, and later changes of view remain beside the original claim.

A page belongs when it preserves incident material, organises that material, or
explains the incident using preserved material. General bitcoin-security
guidance does not belong, even when it is sound. Published security and
migration guidance remains in scope when it is attributed and presented as part
of the record.

This replaces the earlier model in which the site also classified an owner's
wallet and offered a response journey.

## Route decisions

The affectedness and procedural routes were retired:

- /affected/ and /risk/ no longer classify an owner or wallet.
- /risk/estimator/ no longer combines device, dice and passphrase inputs for a
  personal scenario.
- /safety/seed-words/ and the repeated seed warning were removed.
- The site-authored migration checklist, scam ranking and legal action checklist
  were removed.

Retained material moved according to what it documents:

- Dice, passphrase and threshold-wallet code findings, calculations and changing
  guidance moved to /how-it-broke/conditions/.
- The source-labelled device models became a narrower comparison at
  /how-it-broke/entropy/#model-explorer.
- Published migration guidance, the first-spend race, direct-submission
  evidence, conflicts and claimed outcomes moved to /response/migration/.
- Documented scam artefacts, attributed reports, vendor warnings, the earlier
  paper campaign, vendor absence and legitimate lookalikes moved to
  /response/scams/.
- Affected firmware ranges remain canonical at /record/firmware/.

The source registry, snapshots, differences and review classifications were not
removed or rewritten.

## URL continuity

Every retired public path has a permanent redirect in site/public/_redirects.
Section and legacy fragments lead to the closest retained heading. Existing
source records and evidence endpoints retain their URLs.

## Front door and navigation

The landing page has one record-first state. The incident phase switch, triage
door and owner-oriented safety banner were removed. Primary navigation is:

Overview | The record | How it broke | Responses | About

The record leads with current archive statistics, recent entries, source-change
history and named research journeys. Technical and response pages remain
readings of that record.

## Content guardrails

- Report and attribute published guidance. Do not convert it into this site's
  procedure.
- Preserve technical qualifications when moving material.
- Keep observed events, first-person reports, warnings and derived analysis
  visibly distinct.
- Retain operational contribution safety: this project does not accept recovery
  words or private keys.
- Preserve old URLs with redirects rather than keeping stale pages alive.
- Do not remove or rewrite archive history as part of a presentation refit.

## Deferred scope

A general security library, periodic editorial digest and personal wallet-risk
widgets remain out of scope. They should be reconsidered only if they directly
serve the record rather than broadening the site into a general bitcoin
security service.

## Acceptance

The refit is complete when claims, links and the production build pass; the old
owner-triage routes no longer build; every old route resolves by redirect; and
the mission stated here matches AGENTS.md, README.md, the About page and
/llms.txt.

Verified 5 August 2026, post-implementation review: `just check-claims` passes
(254 markers across 17 pages: 57 verified, 159 reported, 11 derived, 27
unverified, 26 also contested); `just check-links` passes (617 pages, every
internal link and anchor resolves, redirect targets included); the mission
sentence is verbatim in AGENTS.md, README.md, the About page and /llms.txt.
The 5 Aug poll left a window of unreviewed diffs, which the review timer
handles in the normal way.

## Post-implementation review (5 Aug 2026)

An independent sweep of the refitted site confirmed the intent landed:
retired-route references are gone from prose and links, the site-authored scam
ranking and migration procedure are out, the Dettmer capsule and
passphrase-repair duplications have single homes, section naming is uniform,
and the new pages (conditions, migration, statements, firmware, the rebuilt
reference and timeline) keep the marker discipline. Residual items, all small:

- **Persisting from before the refit**
  - "Adjudication" still names the site's own claim-checking at
    `response/developers.astro:243` and `response/ai.astro:351`; every other
    use is the mission-consistent "we do not adjudicate".
  - `/response/scams/` still makes the "vendor instruction and scam pretext
    are the same sentence" point three times (lines 82-83, 95-101, 193-196).
  - `/record/funds/` KPI cards hardcode 1,596 and 2,055 (lines 96, 101) with
    matching `figures.ts` keys unused there, and mix `fig()` with literals in
    the same notes (lines 98, 176, 446, 451).
  - `/record/` still links funds as "Theft accounting" (line 215), labels
    registered posts "posts captured" (line 174, the homepage says
    "X posts registered" for the same number), and its h1 is "Evidence"
    (line 158) against a "The record" title and nav.
  - "Talip" (`record/firmware.astro:110`, `record/reference.astro:132`)
    versus "otaliptus" elsewhere (`about.astro:64`, `index.astro:72`).
  - Relative-time framing that will go stale on `/record/funds/`: "within the
    last four days" (line 82), "now moved twice in three days" (lines 926-927).
- **Introduced by the refit and firmware split**
  - `record/reference.astro` has no `#firmware` anchor or pointer, while
    `record/firmware.astro:8-9` says one was kept; old
    `/record/reference/#firmware` deep links land on the page top.
  - "Firmware releases" still credited to the reference page in link copy at
    `record/index.astro:217` and the landing path card (`index.astro`).
  - `how-it-broke/conditions.astro:529` names the retired "moving-funds page"
    in a Claim source; `lib/entropy-models.ts:10` still says "the attack
    estimator" in a comment.
  - `about.astro:95-96` standfirst identical to the meta description.
  - `/llms.txt` labels `/record/` "Evidence index" and omits five built
    pages (developers, ai, disclosure-history, legal, blast-radius).
  - Within-page repetition worth a judgement call: "does not repair the
    mnemonic" three times on `/how-it-broke/conditions/`, and the timeline's
    collapsed side-by-side table re-narrating two landmarks.
- **Flagged, not an error:** an uncommitted working-tree edit to the landing
  page drops the one-sentence explanation of what a COLDCARD is from the
  hero; against the plain-words rule for the front door, that sentence is
  worth keeping when the edit is finished.

Every residual item above was fixed the same day, except the timeline's
collapsed side-by-side table, which was judged to be the designed layering
(a comparison view inside a closed rung over narrative landmarks) and left
as is. After the fixes: `just check-claims` green (254 markers, 17 pages),
`just check-links` green, production build green (618 pages).
