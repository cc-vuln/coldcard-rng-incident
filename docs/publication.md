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
Any author who wants their captured post removed from the published site can
say so at the contact address and it will be taken down; the archive keeps its
own copy, because the record of what was said is separate from what the site
displays.

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
`/schemas/source-register-v1` schema and `/record/changes.json` from the same
registry, archive and review metadata used by the human pages. Dated derived
claims may also expose a narrow, non-sensitive held input under
`/record/evidence/` so readers can reproduce arithmetic after a live endpoint
moves on. `llms.txt` is citation and interpretation guidance, not a
crawler-control mechanism. Crawler access remains a deliberate `robots.txt`
policy, and complete third-party captures remain local.
