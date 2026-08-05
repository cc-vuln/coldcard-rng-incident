# Design: record-first focus

**Status:** implemented 5 August 2026.

## Decision

The site is the public record of the July 2026 COLDCARD predictable-RNG
incident: it preserves what each party published and how it changed, organises
the material, and explains it without adjudicating between the people involved.

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
