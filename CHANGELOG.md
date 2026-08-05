# Changelog

## [Unreleased]

- Correct the coldcard.rip capture guard, which had recorded 44 blocked polls
  against a heading the operator's rebuild removed while the site served 200s.
  Register and first-capture the five routes that rebuild moved the evidence onto (`coldcard-rip-sweep`, `-flow`, `-routes`, `-ledger`,
  `-attribution`), and separate a guard miss from a publisher challenge in
  `pollHealth`, so a page the archive cannot parse is never reported as a
  source that is blocking us.
- Tag each tracker card with its capture liveness (live, offline, blocked,
  guard miss), and show provenance as fields rather than a paragraph.
- Read the community trackers' headline totals on `/record/funds/` out of the
  held captures instead of carrying them as literals, with the capture each
  figure came from, when it last moved and whether the source is still
  answering. `check-trackers.mjs` fails a build that falls back to a pinned
  figure.
- Add `scripts/publish-scheduled.sh` and its example units: an opt-in timer
  that publishes only from a clean tree, and skips rather than fails when work
  is in progress, a difference is unreviewed or nothing has changed.
- Capture and integrate the 5 August incident-source intake, including 62 X posts, five web resources, CKTRIPWIRE monitoring, migration reports, scam fallout, prior-warning accounts and updated attacker estimates.
- First commit.
