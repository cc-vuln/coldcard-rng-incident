# Contributing

This project collects, preserves and explains what is known about the July
2026 COLDCARD predictable-RNG incident, for readers who are not inside the
rooms where the discourse happens. Coverage this broad only works
crowdsourced: most of the useful material is found by people, not by
scrapers. You do not need to be a developer to contribute, and the most
valuable contributions usually are not code.

## Contributing information

No technical setup needed. Two doors, equal rank:

- **Email**: info@cc-vuln.org
- **GitHub**: open an issue (templates exist once you are there)

What we are looking for:

- **Resources we have missed.** Threads, articles, talks, tools, analyses,
  recovery guides, anything genuinely useful on this incident. One line on
  what it is and who made it is enough; a link is the minimum.
- **Corrections.** Anything wrong on cc-vuln.org, with something that lets
  us re-check: a link, a document, a calculation.
- **Sources worth tracking.** Pages whose future edits matter (vendor
  statements, official advisories, litigation documents). These get polled
  and diffed, which is heavier machinery than a library listing, so say why
  the page's change history matters.
- **Context.** You were part of an exchange we archived, or you can date or
  attribute something we list as unverified. Attribution and dating
  corrections are some of the most valuable submissions there are.

What happens to a submission: it enters a queue as unreviewed, and nothing
appears on the site without operator review. This is not gatekeeping for its
own sake. The aftermath of this incident includes an active scam wave aimed
at exactly the readers this site serves, and a poisoned "recovery tool" in
our listings would be the worst failure this project could have. Listing is
never endorsement, and review latency is honest: this is a small operation.

Three rules that apply to information in any form here:

1. **Report and attribute; do not adjudicate.** Where sources disagree, we
   show all of them and what each assumes. Submissions phrased as verdicts
   will be reworked into attribution or declined.
2. **Everything material carries an evidence basis**: verified, reported,
   derived or unverified, and contested where sources disagree. If you can
   move a claim from unverified to verified with a checkable artefact, that
   is a contribution of the first order.
3. **No personal information.** No victim addresses beyond what published
   chain monitors already enumerate, nothing identifying a private
   individual. This applies to submissions as hard as it applies to the
   site.

## Clipping a post yourself

If you want to add a social post to the record rather than only telling us
about one, the tooling is in this repository and works on a clone:

```
just install-capture-browser         # a venv and Chromium, under .capture-browser/
just capture-login                   # sign in once, in a real browser window
just capture-browser &               # the capture daemon (start after signing in)
just ingest-x 'https://x.com/user/status/123' '' '' 'why this belongs'
```

`capture-browser/README.md` documents the daemon, the protocol it speaks, and
the headless case where signing in goes through a VNC tunnel. If you already
run something that speaks that protocol, point the project at it with
`WEBBRIDGE_PORT` and skip the install entirely.

Two things to know before you do this:

- **Sign in as a project account, not as yourself.** Whatever account you use
  appears in the capture, including in places that are easy to miss: an
  element-only screenshot still shows the signed-in avatar in the reply row
  underneath the post.
- **Screenshots you capture will not appear on the site.** Publication is
  gated on which host took the capture, so a contributor's screenshots are
  held in the archive and withheld from the published pages until the
  maintainers re-capture or clear them. Send the post and the reason; the clip
  itself is a bonus rather than a requirement.

## Contributing code and content changes

The repository is the site plus the capture machinery behind it. Additional
ground rules when you touch it:

- **The archive is append-only.** Never rewrite or delete a snapshot, diff
  or index entry. A wrong capture is part of the record; correct by adding,
  and classify the difference in `revision-reviews.toml`.
- **The capture stack must still work in ten years.** Python under
  `scripts/` is stdlib-only by policy. Site dependencies stay inside
  `site/`.
- **Normalizer changes need proof.** Run `just dry-run` and show that
  existing sources still report `same`. A false positive silently corrupts
  the change record, which is the one thing this repository exists to get
  right.
- **Plain words at the front door.** Landing and standfirst copy is written
  for a frightened device owner. Precision lives one click deeper. Read the
  site philosophy in `AGENTS.md` before editing page copy.
- **Do not weaken the gates.** `just check-claims` (evidence markers) and
  `site/tools/check-public-output.mjs` (published-output scan) both must
  pass; any change that loses a marker fails the build by design.

Setup:

```
python3 -m venv .venv          # scripts run through .venv, stdlib only
just test-capture              # capture regression tests
just check-claims              # evidence marker gate
cd site && npm ci              # site is Node 22 + Astro, confined to site/
npx astro build                # dev server is broken (known Vite issue); build + preview
```

`just dry-run` polls every source without writing. Browser-rendered sources
report SKIPPED without a local rendering daemon; that is a recorded gap, not
an error to fix. Small fixes can go straight to a PR; for new tracked
sources or behaviour changes, an issue first saves everyone time. All
commits to `main` are signed.

## House style

Three conventions the tree already follows. They are written down here
because publication means strangers will write in it too.

- **No em-dashes.** There are none in the site's prose. Use a comma, a
  colon, or two sentences.
- **British spelling in prose** (normalise, artefact, behaviour, licence as
  the noun). Identifiers keep whatever the code calls them: the registry key
  is `normalizers`, and it stays that way inside backticks.
- **Seed words at the front door, mnemonic deeper in.** Plain words where a
  frightened reader arrives; the precise term where the subject is the
  encoding. "Recovery phrase" appears only inside a quotation. Likewise
  "sweep" is what the attacker did; an owner moves funds.

## Licensing of contributions

Code contributions are accepted under MIT. Text and data contributions to
the project's original content are accepted under CC BY 4.0. Links submitted
to the library imply no license at all: the linked material stays its
author's. See `LICENSE` and `LICENSE-CONTENT.md`.
