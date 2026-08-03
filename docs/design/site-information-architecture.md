# Design: site information architecture, reader intent and progressive disclosure

**Status:** the navigation, the disclosure pattern and the phase flag are
built. The library, the digest and the diagrams are not.

The question this answers: the site has grown four distinct identities that are
currently presented as equal siblings in a content-type navigation. How should
they be delineated so each reader finds their path without the others getting
in the way, and how does the site stay useful as the incident ages?

## 0. Foundations

Four premises the site rests on, stated by the operator on 2 Aug 2026. Every
IA decision below should be checkable against them:

1. **Not everyone is on Bitcoin twitter.** The reader we build for is outside
   the room where the discourse happens. Insider material gets captured,
   translated and explained; jargon is never a prerequisite.
2. **There are many disparate resources.** The incident's coverage is
   scattered across threads, repos, videos and articles. Collecting and
   organising it is itself the product.
3. **Crowdsourced material leads to the most comprehensive coverage.**
   Contribution is a product feature with intake on the site itself, not a
   developer courtesy in the repo.
4. **Open source wins.** The code, the method and the record are open and
   frictionlessly reusable. Openness is the trust story: nothing here asks to
   be believed, everything asks to be checked.

The consequence of taking these together: the site is a clearinghouse where
scattered, insider-locked discourse becomes legible, checkable and as
complete as the crowd can make it. Provenance is not the mission; it is the
quality bar the clearinghouse applies. The epistemic model becomes more
load-bearing under crowdsourcing, not less, because ungraded crowd input
reproduces exactly the noise the reader is being rescued from.

---

Current route inventory, verified against the working tree on 3 Aug 2026:

```
/                     landing
/affected/            triage hub sequencing /risk/* and /safety/*
/how-it-broke/        index, entropy, blast-radius
/record/              index, sources/[id], changes, feed, timeline, funds,
                      firmware, analysis, repos, evidence/*
/response/            index, developers, legal, ai
/risk/                index, estimator, mitigations, moving-funds
/safety/              scams, seed-words
/about
```

## 1. The four identities

Each section serves a different reader in a different state. Naming them makes
the merged-identity problem concrete:

1. **Triage.** `/risk/` and `/safety/` answer "am I affected and what do I do
   right now", for a reader who may have just been told their savings are
   gone. This is the reader the project philosophy already centres.
2. **Explainer.** `/how-it-broke/` teaches how the failure worked, for a
   reader with time and curiosity rather than fear.
3. **The record.** `/record/` is the provenance machine: sources, captured
   changes, accounting. Its readers are researchers, journalists and the
   parties themselves. It is the part nobody else on the internet provides.
4. **Aftermath.** `/response/` tracks how vendors, developers, lawyers and
   the ecosystem responded. Part reporting, part precedent analysis.

The identities are not the problem. Three things are:

- The top navigation is organised by content type, so nothing tells a reader
  which door is theirs. Triage is split across two top-level sections
  (`/risk/` and `/safety/`) whose boundary is invisible to a frightened
  reader.
- Depth is inconsistent. Some pages layer plain summary over detail well;
  others open at full precision. There is no shared disclosure pattern, so
  each page re-invents one.
- Nothing is designed for time. The landing assumes an active emergency. As
  the incident ages, triage urgency decays while the record and the explainer
  appreciate, and today's layout has no way to shift that weight without a
  redesign.

## 2. Reader model

Five readers, in descending order of design priority today:

| Reader | State | Needs first | Section |
|---|---|---|---|
| Affected owner | frightened, non-technical | am I affected, what do I do | triage |
| Returning owner | oriented, checking back | what changed since last visit | record/changes |
| Researcher or journalist | methodical | citable evidence, provenance | record |
| Developer or vendor | technical | mechanism, fix, precedent | explainer, aftermath |
| Curious learner | no stake | how it worked, lessons | explainer |

The design rule the project already has, restated as IA: the frightened owner
outranks the curious learner at the front door, and precision belongs one
click deeper. This proposal keeps that rule and extends it: every section
front door states which reader it serves, and hands the other readers to
their door in one click.

## 3. Proposed navigation

Intent-based labels over content-type labels. URLs do not change; this is a
regrouping of the same pages plus two new hub pages.

```
Am I affected?    -> new hub /affected/ wrapping /risk/* and /safety/*
What happened?    -> /how-it-broke/ (unchanged routes, new front door)
The record        -> /record/ (unchanged)
The aftermath     -> /response/ (unchanged)
About             -> /about
```

Decisions folded into this:

- `/risk/` and `/safety/` keep their URLs; published links and llms.txt stay
  valid. The new `/affected/` hub is one page that sequences them as a single
  journey: check exposure, then act, then avoid scams. The estimator stays
  the centrepiece.
- No verdict creep: intent labels are questions the reader brings, not
  conclusions the site draws. "Am I affected?" is answered by the reader
  using the estimator, not by the site adjudicating.
- Each section front door carries a one-line "this section is X; it is not Y"
  statement. Example for the record: "This section preserves what each party
  said and how it changed. It does not judge who is right; see The aftermath
  for how the parties responded."

## 4. Progressive disclosure as a component, not a habit

One shared pattern, used by every editorial page, so depth behaves the same
way everywhere:

```
standfirst      plain words, rounded figures, who this page is for
answer          the 3-5 sentences that resolve the page's question
detail ladder   expandable blocks, precise, evidence-marked
artefacts       links to captures, hashes, method notes
```

Rules:

- The standfirst and answer never contain a symbol name, an exact satoshi
  amount or an evidence-status vocabulary lesson. The detail ladder is where
  precision lives, and it is allowed to be as exact as the source allows.
- Detail blocks are HTML `<details>` driven, server-rendered, no JS required
  to open. Deep links can force a block open via URL fragment so citations
  into the ladder still work.
- Evidence markers stay in the ladder and artefact layers where they already
  live. `just check-claims` coverage is unchanged; the component makes marker
  placement mechanical rather than per-page judgement.

## 5. Visual explainers

Four canonical diagrams, each the front-door visual for its section. All
render client-side like the ported explainer diagrams, each with a text
fallback and each carrying its own evidence scope note.

1. **Seed generation, promised vs delivered.** Two-lane flow: the entropy
   pipeline as documented, over the pipeline as shipped. Front door of
   `/how-it-broke/`. Basis: verified against the firmware sources already
   cited on `/how-it-broke/entropy`.
2. **Exposure decision tree.** Device model, firmware era, seed origin,
   passphrase use, leading to the estimator, not to a verdict. Front door of
   `/affected/`. Basis: derived from the same tables the estimator uses.
3. **Disclosure and response timeline.** Horizontal band chart of who said
   what when, linked to captured sources for every node. Front door of
   `/record/timeline`, excerpted on the landing. Basis: verified, every node
   is a capture.
4. **Funds flow.** Sweep totals as currently attributed, with explicit
   unknown segments and the competing totals shown side by side. Lives on
   `/record/funds`. Basis: derived, with the contested figures marked as the
   page already does in prose.

Diagram claims are claims: each carries a scope note naming its basis, and
check-claims coverage extends to the pages that host them unchanged.

## 5a. The library: a second content tier

The record cannot scale to comprehensive coverage and should not try. Two
tiers with different machinery and different promises:

- **The record** (exists): what the parties said. Tracked, diffed,
  append-only, heavyweight. Its promise is provenance.
- **The library** (new): everything useful anyone has made. Threads, talks,
  tools, analyses, guides. Indexed and graded, not diff-tracked. Its promise
  is coverage.

A library entry is one block in a new `library.toml`: url, title, one-line
description of what it is and who made it, category, date added, and a
status (`indexed` after operator review, `unreviewed` in the intake queue).
Entries carry the same evidence-basis vocabulary where they make claims worth
grading. The library page renders from the registry at build time, grouped
by category, newest first, with an explicit note that listing is not
endorsement. Entries that later warrant provenance tracking graduate to
`sources.toml`; the two registries stay separate on purpose.

Navigation: the library lives inside The record's section of the nav as
`/record/library/`, with the front-door line distinguishing them: the record
preserves what parties said; the library indexes what everyone else made.

**Safety constraint, non-negotiable.** The incident's aftermath is an active
scam wave aimed at exactly this site's readers. A poisoned "recovery tool"
entering the library is the worst possible failure of the crowd model. So:
open intake, gated publication. Nothing renders on the site without operator
review; submissions enter as `unreviewed` and invisible; anything executable
or wallet-touching gets flagged prominently even after review; and the site
says all of this plainly on the library page.

## 5b. Contribution intake and the digest

Intake is a site feature with two front doors of equal rank, because not
everyone is on GitHub either:

- **Email**: info@cc-vuln.org, with a suggested one-line format on the page
- **GitHub**: an issue template ("Suggest a resource") once the repo is
  public; a PR adding a `library.toml` block for those who prefer it

Every content page carries a quiet footer line: suggest a resource, flag an
error. The repo's CONTRIBUTING.md is reoriented information-contributor
first, developer second: the first screen of it must make sense to someone
who has never cloned a repository.

**The digest** (later slice): a plain-words briefing surface built on the
change feed: what changed in the record, what entered the library, what the
open questions are. Cadence follows events rather than a calendar. This is
the translation layer for the not-on-twitter reader and stays out of scope
until the library exists.

## 5c. Licensing

Open source wins only if reuse is frictionless, so licensing is explicit and
split by what the project can actually license:

- **Code** (scripts/, site/, tooling): MIT, copyright "cc-vuln"
- **Original content** (editorial pages, docs, diagrams, and project-created
  data such as registries, metadata and hashes): CC BY 4.0. Attribution
  keeps the citation chain intact; no ShareAlike, because mirror friction
  costs more than copyleft protects here
- **Archived third-party material** (snapshots, captured text, media): not
  the project's to license. Copyright remains with the original authors;
  held for research and archival purposes; published output carries excerpts
  under the same rationale the site already applies

`LICENSE` (MIT) and `LICENSE-CONTENT.md` (scope statement plus the CC BY 4.0
reference and the third-party carve-out) live at the repo root, and the
README's licensing section states the three-way split in plain words.

## 6. Designing for incident age

One site-level mode flag, `INCIDENT_PHASE`, build-time, two values:

- `active` (today): landing leads with triage. Hero is "what happened and
  does it include you", the affected-hub card is first and largest.
- `steady` (later): landing leads with what this site is, the record and the
  explainer rise, triage remains one click away but stops shouting. The
  trigger for flipping is editorial judgement, recorded in the changelog,
  not automatic.

This is one conditional in the landing layout and card ordering, not a second
design. Building it now costs little; retrofitting it during a redesign later
costs the redesign.

## 7. Wireframes

Landing, `active` phase:

```
+--------------------------------------------------------------+
| cc-vuln.org       Am I affected?  What happened?  The record |
|                                        The aftermath | About |
+--------------------------------------------------------------+
|                                                              |
|  COLDCARD wallets generated with affected firmware           |
|  produced guessable keys. Funds have been swept.             |
|  Plain-words hero. No figures beyond rounded BTC total.      |
|                                                              |
|  +------------------------------+  +----------------------+  |
|  |  AM I AFFECTED?              |  |  timeline excerpt    |  |
|  |  device + firmware + seed    |  |  latest 3 nodes from |  |
|  |  origin -> estimator         |  |  disclosure timeline |  |
|  |  (largest card, first)       |  |  -> /record/timeline |  |
|  +------------------------------+  +----------------------+  |
|                                                              |
|  +---------------+  +---------------+  +------------------+  |
|  | WHAT HAPPENED | | THE RECORD     | | THE AFTERMATH     |  |
|  | promised vs   | | N sources, M   | | vendors, devs,    |  |
|  | delivered     | | tracked changes| | legal, precedent  |  |
|  | diagram thumb | | latest change  | |                   |  |
|  +---------------+  +---------------+  +------------------+  |
|                                                              |
|  footer: about, contact, machine access (/llms.txt), method  |
+--------------------------------------------------------------+
```

Landing, `steady` phase (same components, reordered):

```
|  hero: what this site is: the preserved record of the        |
|  July 2026 COLDCARD incident                                 |
|                                                              |
|  +----------------------+  +------------------------------+  |
|  | THE RECORD           |  | WHAT HAPPENED                |  |
|  | sources, changes,    |  | promised vs delivered        |  |
|  | accounting           |  | diagram thumb                |  |
|  +----------------------+  +------------------------------+  |
|  +----------------------+  +------------------------------+  |
|  | STILL ON AFFECTED    |  | THE AFTERMATH                |  |
|  | FIRMWARE? triage     |  | responses and precedent      |  |
|  | remains one click    |  |                              |  |
|  +----------------------+  +------------------------------+  |
```

Affected hub, `/affected/`:

```
+--------------------------------------------------------------+
| Am I affected?                                               |
| This section helps you check and act. It does not decide     |
| for you, and it cannot see your device.                      |
+--------------------------------------------------------------+
|  [ exposure decision tree diagram ]                          |
|     device model -> firmware era -> seed origin -> use of    |
|     passphrase -> "check precisely" -> /risk/estimator       |
|                                                              |
|  1 CHECK   estimator, dice, multisig, passphrase   (/risk/*) |
|  2 ACT     migrating, moving funds                 (/risk/*) |
|  3 AVOID   scams, seed-word handling             (/safety/*) |
|                                                              |
|  standfirst rule applies: no jargon above the fold           |
+--------------------------------------------------------------+
```

Editorial page with the disclosure component (example: /record/funds):

```
+--------------------------------------------------------------+
| Funds                                                        |
| standfirst: roughly NNN BTC swept; totals differ by source   |
| answer: 3-5 sentences, rounded, attributed                   |
+--------------------------------------------------------------+
| [ funds flow diagram: attributed / unknown / contested ]     |
+--------------------------------------------------------------+
| > Exact totals and how each is derived          [detail]     |
| > Where the numbers disagree and why            [detail]     |
| > Method and pinned inputs                      [detail]     |
|   (each block evidence-marked, deep-linkable)                |
+--------------------------------------------------------------+
| artefacts: captures, hashes, review docs                     |
+--------------------------------------------------------------+
```

## 8. Implementation steps

1. Add the disclosure component (standfirst, answer, detail ladder,
   artefacts) and refit one page (`/record/funds`) as the proving case
   (done 2 Aug 2026)
2. Licensing files and CONTRIBUTING reorientation (information contributors
   first), so the crowd mechanism is ready the moment the repo goes public
3. Build `/affected/` hub; regroup navigation to the four intent labels;
   add the "is X, not Y" line to each section front door
4. Add `INCIDENT_PHASE` to the landing layout with `active` as current
5. `library.toml`, the `/record/library/` page and the intake footer line,
   with the gated-publication rule wired in from the first entry
6. Diagram 2 (exposure tree) and diagram 3 (timeline), as they are pure
   presentation of existing verified data
7. Diagram 1 (promised vs delivered) and diagram 4 (funds flow), which need
   an evidence-scope note each
8. Refit remaining editorial pages onto the disclosure component
   (done 3 Aug 2026, together with the merges below)
9. The digest surface, once the library has enough flow to brief on
10. `just check-claims` and the public-output gate run unchanged throughout;
    any page that loses a marker in the refit fails the build

## 8a. What the refit changed (3 Aug 2026)

Step 8 sat undone while steps 3 to 7 shipped, and the result was the failure
this document was written to prevent: 29 editorial pages, 71,391 words, 26 of
them opening at full precision with no ladder. The refit applied the section 4
shape everywhere and merged the pages whose separation only existed because
nothing had forced the question.

| | before | after |
|---|---|---|
| editorial pages | 29 | 22 |
| editorial words | 71,391 | 51,400 |
| visible before opening a rung | 56,370 | 38,335 |
| full triage path | 15,674 w, 11 pages, 65 min | 10,258 w, 8 pages, 43 min |
| research path | 24,845 w, 7 pages, 104 min | 16,319 w, 6 pages, 68 min |
| evidence markers | 173 | 173 |

Merges, each retired route redirected to the rung holding its content:

- `/how-it-broke/promised/`, `/how-it-broke/the-fix/` -> `/how-it-broke/`
- `/risk/dice/`, `/risk/passphrase/`, `/risk/multisig/` -> `/risk/mitigations/`
- `/risk/migrating/` -> `/risk/moving-funds/#threshold-wallets`
- `/record/disclosure/` -> `/record/timeline/`
- `/response/precedent/` -> `/response/#precedent`

Two lessons worth keeping. First, the disclosure component is not a per-page
choice: leaving it optional is what produced the flat pages, so section 4's
"used by every editorial page" is now enforced by habit and recorded as a
standing rule in `BACKLOG.md`. Second, "every existing URL still resolves" was
an acceptance criterion with nothing checking it; the merges broke 163 internal
links, including one nav entry rendered on all 136 source pages. `just
check-links` now enforces it, redirect targets included.

## 9. Acceptance criteria

- A reader who owns a COLDCARD and nothing else reaches the estimator from
  the landing in one click without meeting a symbol name on the way
- Every existing URL still resolves; nav regrouping breaks no published link
- Each of the four sections opens with its "is X, not Y" line
- The landing can flip to `steady` by changing one build flag
- check-claims coverage identical or better after every step
- No new runtime dependencies; diagrams follow the existing client-side
  pattern

## 10. Open questions

- Does the `/affected/` hub replace `/risk/` and `/safety/` index pages or
  sit above them? Proposal: sit above; the index pages stay as quiet lists
- Should the timeline diagram live on the landing in `active` phase, or is
  the excerpt card enough? Proposal: excerpt card only, diagram stays deep
- Diagram styling: hand-drawn draw.io exports versus generated client-side
  SVG. Proposal: client-side, consistent with the ported explainer
