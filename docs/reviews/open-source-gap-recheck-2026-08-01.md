# Public-source gap recheck

Checked between 06:20 and 06:24 UTC on 1 Aug 2026. This is a bounded search
record, not proof that a document does not exist or that no private work is in
progress.

## Coinkite technical review or postmortem

The registered Coinkite blog index was polled again at `20260801T062151Z`. Its
normalised text hash remained
`90133b94768e2e824a99778dc8ba6099197aa5e30c3228201fadb8d428d33ea0`, with no
new post detected. The current `Coldcard/firmware` clone was fetched and updated
to commit `9a88e1a5c32c69ba7ace7f22c26da90f8442b745`; a repository search did not find
a formal review, postmortem or audit publication.

This does not close the promised-review gap. It records only that no matching
public artefact was found on the checked official blog and firmware-repository
surfaces at the stated time.

## CVE record

NVD CVE API keyword searches for `COLDCARD` and `Coinkite` each returned one
record: `CVE-2019-14356`, the earlier Mk1/Mk2 OLED side-channel report. Neither
query returned a record for the 2026 predictable-RNG incident.

Keyword matching can miss a record that uses different product or vendor text,
and a request may exist before publication. The result therefore supports only
the narrower statement that no incident-specific assignment was identified by
these official-database keyword searches.

## Independent audit announcement

The same Coinkite blog, current firmware repository, and incident-related public
pull-request search produced no independent-audit announcement. No claim is made
about private review or vendor engagements that have not been announced.

## Gregory Sanders reproduction scripts

The public `instagibbs` GitHub account, repository search, code search, and all
three rendered pages of the account's public gist index were checked for
`COLDCARD`, `Yasmarang`, `libngu`, `pyb_rng`, and related Mk2/Mk3 RNG terms. No
matching public script was found.

This result does not contradict Sanders's captured reproduction statements. It
means the archive still lacks the two scripts he said he used and cannot inspect
their inputs, method, runtime split or output checks.

## New records found during the recheck

The search did identify four official pull-request records worth retaining:

- `Coldcard/firmware#689`, the merged Mk3 source hotfix
- `Coldcard/firmware#690`, the merged Edge source and release-history hotfix
- `switck/libngu#60`, an open full-width reseed proposal
- `Coldcard/firmware#691`, its open firmware-side companion

All four are registered in `sources.toml` and captured by the archive. The open
companion proposals are not part of the published hotfixes and should not be
described as merged remediation.

A broader GitHub response search also retained three high-signal downstream
records: Bitcoin.org's merged listing removal, SatSigner's open entropy-audit
proposal and SeedSigner's closed, withdrawn camera-entropy proposal. Generic
hardening changes and incident summaries that supplied no new primary evidence
were not added merely because they mentioned COLDCARD.
