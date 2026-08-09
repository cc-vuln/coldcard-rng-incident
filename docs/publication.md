# Publication

What a public build of the site shows, the editorial contract its claims
follow, and the machine-readable outputs it generates. How a build is produced
and deployed is covered in [operations.md](operations.md). The full reasoning
behind the display policy is the adopted design document
[design/capture-display-policy.md](design/capture-display-policy.md).

## What the published site shows

Published builds show diffs, a short excerpt and a link for captured pages: the
full text stays local, where it still backs every claim
(`PUBLIC_FULL_TEXT` is false for any public build).

Snapshot hashes remain in the local sidecars. The archive audit recomputes the
extracted-text hash to detect later mutation of that text, while the raw-byte
hash remains capture metadata. Neither independently authenticates who made a
browser capture. The public site and source register therefore do not publish
them as provenance or tamper-resistance evidence.

Screenshots of short social posts are the deliberate exception. A post is
brief, public, and its author is named; showing the capture lets a reader see
what was said rather than trusting this project's transcription, which is the
whole point of holding it. Published screenshots carry attribution and a link
to the original, and the staging tool copies the displayed file directly from
the held artefact. Longer works stay excerpted.

One thing is never published: media *attached to* a post, because an attached
photo is not the post and showing one in its place would misrepresent the
capture. Everything else held here is material its author published, and it is
shown. Publication is off by default and enabled per build (`PUBLIC_X_MEDIA`).

Published material stays published. Since 6 Aug 2026 this project does not
undertake to withdraw a captured post because its author asks: a record whose
contents can be edited by the parties in it does not do the job it exists for,
and the posts held here are short, public, attributed statements that were
themselves events in the incident. What does come down is material that was
never public, personal data, and anything this project has got wrong, which is
handled as a correction and logged at `/corrections/`.

A complaint with a legal basis is not an author preference and is not answered
by the paragraph above. A copyright complaint, a court order or a demonstrated
legal obligation is assessed on its merits and acted on where it is good.

Screenshot publication is gated on when a capture was taken, never on what it
looks like. A screenshot taken in a signed-in browser carries that session:
on a whole-window capture the account name sits in the site's own navigation,
and on an element-only capture the account's avatar still appears in the reply
row, which no image measurement detects reliably. So `stage-x-media.mjs`
publishes only captures from the dedicated capture profile, gated on
`OWN_HOST_FROM`, and reports how many posts it withheld. Do not add an
image-inspection heuristic and call a capture cleared, and reject a capture
directory that is not a timestamp explicitly: `"undated" < "20260802..."` is
false, because letters sort after digits. Anything that could reproduce
captured text asks `withholdsCapturedText()` in `lib/archive.ts`, including
diffs and excerpts; keep that rule in one function, because copies of a policy
drift and the copy that withholds nothing is the one that leaks.

## Editorial claim markers

The public site separates evidence basis from dispute state. Each editorial
claim marker must use one of `verified`, `reported`, `derived` or `unverified`,
must say exactly what it applies to, and may separately be marked `contested`.
Verified and reported markers link to evidence a reader can re-check. Public
builds run `just check-claims` and fail if a marker or editorial page falls
outside that contract. In prose, link the attributed statement or named source
directly at its first relevant mention. A linked claim marker below the paragraph
does not replace that inline source link.

## Machine-readable publication

The static build generates `/llms.txt`, `/record/sources.json`, its dereferenceable
`/schemas/source-register-v1` schema, `/record/changes.json` and `/version.json`
from the same registry, archive and review metadata used by the human pages. Dated derived
claims may also expose a narrow, non-sensitive held input under
`/record/evidence/` so readers can reproduce arithmetic after a live endpoint
moves on. `llms.txt` is citation and interpretation guidance, not a
crawler-control mechanism. Crawler access remains a deliberate `robots.txt`
policy, and complete third-party captures remain local.

## Citation and versioning

`/cite/` is the human guidance and `CITATION.cff` the repository metadata. Both
say the same thing first: cite the publisher, then this archive as the state and
the evidence that the state existed. The author is the project, entered as an
entity, because the site is published pseudonymously.

A citation to a record that changes has to name which state was read, so every
build stamps the commit it was made from in the page footer and at
`/version.json`, together with the size and freshness of the record at that
commit. `matches_commit` is false when the build carried edits that commit does
not contain, which is normal for a review build and forbidden for a deploy:
`just check-version-exact` runs after an indexable build and before upload,
requiring the stamp, current `HEAD` and tracked tree to agree. Nothing here is
hand-maintained: `src/lib/version.ts` reads git, for the same reason
`src/lib/updated.ts` does.

No DOI is minted yet, and the route to one was decided on 7 Aug 2026: an open
Zenodo record of project-created material only, tagged `vYYYY.MM.DD`, with
Software Heritage deliberately skipped. The obvious route — depositing the whole
repository — is the one not taken, because it would publish third-party captures
under an identifier designed to be hard to retract. A deposit limited to the
registry, the review classifications, the poll index, the tooling and the site
carries no such problem, and `scripts/build_manifest.py` describes every capture
left out so the deposit never understates the corpus. The reasoning, the
exclusions and the steps are in [`deposit.md`](deposit.md).

## Corrections

Corrections to published claims are appended to `corrections.toml` and rendered
at `/corrections/`, newest first, quoting the wording that was wrong. The
corrected page also carries the correction where the claim was: a log nobody
passing the claim would see, or a quiet edit with no index, is half a policy.
The log opened 6 Aug 2026.

Three things are deliberately not corrections. A source editing its own page is
the record's subject and belongs in `revision-reviews.toml`. Rewording,
restructuring and tooling work belong in `CHANGELOG.md`. A request to remove
correctly reported material is neither, and is declined: what a party published
is the record.
