# Design: current site information architecture

**Status:** implemented 5 August 2026. This document replaces the earlier
reader-triage and incident-phase design.

## Foundation

The site is the public record of the July 2026 COLDCARD predictable-RNG
incident: it preserves what each party published and how it changed, organises
the material, and explains it without adjudicating between the people involved.

The record is the product. Technical explanations and response pages are
readings of it. They cite preserved material and carry the same evidence-basis
contract as the register.

## Primary navigation

Overview | The record | How it broke | Responses | About

- Overview introduces the incident and demonstrates the archive through current
  statistics, recent entries and named paths into the record.
- The record contains the timeline, funds accounting, source register, source
  changes, reference index and individual evidence records.
  Its primary navigation link opens the timeline; the source register remains
  available at `/record/`.
- How it broke contains the source-level reconstruction, published candidate
  models, technical conditions and call-site blast radius.
- Responses organises what vendors, researchers, developers, custody providers,
  lawyers and community participants published. Its index is a map of four
  record types: public statements, technical work, published guidance and
  warnings, and accountability material. Its section navigation links every
  response article directly and follows the same order as each page's Next
  link, so the conceptual grouping on the index never hides a destination.
- About states the project identity, method, evidence labels, limits and
  correction process.

## Route inventory

    /
    /record/
      timeline/
      funds/
      changes/
      reference/
      sources/[id]/
      evidence/*
    /how-it-broke/
      entropy/
      conditions/
      blast-radius/
    /response/
      statements/
      migration/
      scams/
      developers/
      ai/
      disclosure-history/
      legal/
    /about/

Machine-readable records remain at /record/sources.json,
/record/changes.json, /schemas/source-register-v1.json and /llms.txt.

Retired owner-triage, risk and safety paths resolve through permanent redirects
to the nearest retained record or explainer.

## Page shape

Editorial pages use progressive disclosure:

1. A standfirst in plain language.
2. A short Answer that states what the record supports.
3. Detailed sections or disclosures carrying technical precision.
4. Claim markers and links to re-checkable evidence beside the material they
   support.

The landing page uses rounded figures and plain terms. Exact accounting and
technical symbols belong on the deeper pages where their assumptions can be
shown.

## Ownership

Facts have one canonical home:

- Source states and changes belong to the source record and change feed.
- Firmware release boundaries belong to the reference.
- Candidate-space models belong to the entropy page.
- Dice, passphrase and threshold mechanics belong to technical conditions.
- Transaction totals and attribution differences belong to funds accounting.
- Migration recommendations and claimed outcomes belong to the migration
  response record.
- Scam artefacts and warnings belong to the scams response record.
- Vendor, provider and community statements about their own actions belong to
  the public-statements response record.
- Earlier weak-key incidents and same-day events involving other products are
  reference material, not incident responses.

Other pages may give a short gloss and link to the canonical home. They should
not maintain a second tutorial or calculator.

## Time

There is no active or steady incident phase. The front door always leads with
the record. Freshness is expressed by capture timestamps, source health and the
change feed rather than by swapping the site's mission.

## Acceptance criteria

- Every editorial page passes the belonging test in AGENTS.md.
- Every material claim has a scoped evidence marker.
- Every retired published route resolves through site/public/_redirects.
- Navigation and section kickers use the names above.
- Every static editorial route in a section has a direct entry in that
  section's navigation. Response Next links follow the same route order.
- just check-claims, just check-links and just build-site pass.
