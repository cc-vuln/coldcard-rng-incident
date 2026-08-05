# Project documents

Long-lived supporting documents live here, grouped by purpose. The root
`README.md` is the project overview and command reference, `BACKLOG.md`
remains the current work queue, and `AGENTS.md` remains the project contract
for agents.

## Running the archive

Operator and deployment detail behind the root README's command reference.

- [`capture.md`](capture.md): capture methods, change normalisation, failure
  classes, withdrawn sources, Wayback recovery, X capture and the review layer
- [`operations.md`](operations.md): the recurring capture schedule, the
  one-writer rule, notification delivery, Signal alerting and deployment
- [`publication.md`](publication.md): what published builds show, the editorial
  claim-marker contract and the machine-readable outputs

## Design

Future-facing technical designs and implementation rationale.

- [`design/browser-capture-hygiene.md`](design/browser-capture-hygiene.md):
  keeping ad, consent and tracker noise out of browser captures by
  blocking it at the daemon, and why an extension is the wrong tool
- [`design/capture-display-policy.md`](design/capture-display-policy.md): when
  a captured social-post screenshot may render publicly, and the duties that
  attach to displaying it
- [`design/captures-front-and-centre.md`](design/captures-front-and-centre.md):
  decision record for the 3 Aug 2026 IA review that put captured artefacts
  at the front of the site, and what shipped from it
- [`design/discovery-and-x-watch.md`](design/discovery-and-x-watch.md): periodic
  source discovery and X account watching
- [`design/reddit-json-capture.md`](design/reddit-json-capture.md): replacing
  rendered-page reddit captures with structured thread JSON
- [`design/record-first-focus.md`](design/record-first-focus.md): narrowing
  the site to the public record, the retained material and the completed route
  refit
- [`design/site-information-architecture.md`](design/site-information-architecture.md):
  current record-first navigation, route ownership and progressive disclosure

## Research

Self-contained work packages for open questions that require new evidence.

- [`research/uid-distribution-measurement.md`](research/uid-distribution-measurement.md):
  measure the STM32 UID low-word distribution across real COLDCARD devices

## Reviews

Dated diagnostic reports and the evidence behind time-specific publication
claims.

- [`reviews/slipstream-confirmation-window-2026-08-01.md`](reviews/slipstream-confirmation-window-2026-08-01.md):
  preserve the observed MARA Pool share and mean-block-interval calculation used
  on the migration record (now `/response/migration/`)
- [`reviews/mk3-synthetic-test-vector-2026-08-01.md`](reviews/mk3-synthetic-test-vector-2026-08-01.md):
  fix and independently verify one synthetic Mk3 RNG-to-xpub path
- [`reviews/funds-accounting-2026-08-01.md`](reviews/funds-accounting-2026-08-01.md):
  preserve the pinned inputs, derivation method and live-chain checks behind
  the funds accounting page
- [`reviews/wider-loss-distribution-2026-08-01.md`](reviews/wider-loss-distribution-2026-08-01.md):
  preserve a privacy-safe distribution for the wider attributed source set
- [`reviews/open-source-gap-recheck-2026-08-01.md`](reviews/open-source-gap-recheck-2026-08-01.md):
  bound the public search for the promised review, CVE, audit announcement and
  instagibbs reproduction scripts, and record the official pull requests found

## Placement rules

- Keep the root `README.md` to overview, layout and command reference; put
  operator and deployment detail in `capture.md`, `operations.md` or
  `publication.md`.
- Put proposed technical changes and their rationale in `design/`.
- Put open, evidence-gathering work packages in `research/`.
- Put durable, non-sensitive inputs that directly support a dated publication
  calculation beside its review in `docs/reviews/evidence/`.
- Put temporary audits, screenshots, diagnostics and other generated review
  material in the ignored `.work/` directory, not in `docs/`.
- Do not add one-off document directories at the repository root.
