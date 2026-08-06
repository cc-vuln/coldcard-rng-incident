# The archival deposit

What a deposit of this project contains, what it deliberately leaves out, and
the reasoning behind the split. Written 6 Aug 2026, when the decision was taken
and the tooling built. **Nothing has been deposited yet.** `just deposit` stages
and reports; it has no upload path, and where the deposit goes is a separate
decision the operator makes after reading what it prints.

## Why deposit at all

A DOI makes the work citable in academic literature, and a deposit puts a copy
of the project somewhere that does not depend on one machine, one hosting
account or one person continuing to care. Both are worth having for a record
whose subject is material disappearing.

## The decision: project-created material only

The deposit is **every git-tracked file minus four trees**:

| Withheld | Why |
|---|---|
| `archive/snapshots/` | complete copies of other people's pages |
| `archive/x/` | captured social posts and their media |
| `archive/nostr/` | captured notes and their replies |
| `archive/runs/` | per-run operational telemetry |

`archive/nostr/` is classified in advance: it holds no committed capture yet,
and classifying a tree the day it appears is cheaper than discovering it during
a staging run.

Everything else goes in: the registry, the poll record, every classified
difference, the corrections log, the capture tooling, the site, and the method
documentation. That is roughly 15 MB against 485 MB withheld, and it is the
part of the project that is genuinely this project's own work.

The rule is subtractive on purpose. An allowlist omits whatever nobody
remembered to add, and the omission is indistinguishable from a deliberate
exclusion; a denylist of four named trees can be checked against `git ls-files`
by a stranger in one command.

### The gate that makes the subtractive rule safe

A subtractive rule has exactly one failure mode: a tree nobody wrote an
exclusion for is included by default. The first staging run found it. An
`archive/reddit/` tree held a captured first-person victim account, its
screenshot and raw platform metadata, left over from an early capture route for
a source that now polls as `reddit-json`. It matched no exclusion, so it staged,
and only the report's unknown-group bucket showed it.

That tree was then retired outright rather than merely excluded (see below), so
the gate now guards nothing that exists. That is the point of keeping it.
Staging refuses to run at all while any tracked path under `archive/` matches
neither `EXCLUDED` nor `ARCHIVE_INCLUDED`. The next capture backend will create
the next tree, and the deposit must never decide by default whether that tree is
this project's record or somebody else's words. The manifest closes the same gap
from the other side: an archive tree with no purpose-built reader is described
generically rather than skipped.

### The pre-convention leftovers, retired 6 Aug 2026

Nine files from the first days of collection, before the current capture layout
existed, were removed rather than carried into a permanent deposit:

| Removed | What it was |
|---|---|
| `archive/reddit/reddit-drained-timeline/undated/` | the thread's attached image and gallery-dl metadata |
| `archive/x/llfourn-model/undated/` | attached media, byte-identical to three dated captures |
| `archive/x/nvk-apology/undated/` | the same |
| `archive/x/twitter/{LLFOURN,nvk}/` | gallery-dl account metadata for posts captured properly elsewhere |

No snapshot was deleted and no unique record was lost. The two X directories
held attachments whose bytes are identical to those in three dated captures of
each post, verified by hash before removal. The Reddit directory's own thread is
held in three canonical `reddit-json` snapshots carrying the full post text, the
poster's stated wallet address and the `i.redd.it` URLs; what went was the
downloaded image itself, which is attached media that this project's display
policy never publishes in any case.

The removal is recorded in `CHANGELOG.md` with each file's size and hash, so the
deletion is itself on the record. That is the standing shape for anything
removed from `archive/`: the archive is append-only in spirit, and an operator
exception to that should leave a trace a stranger can check.

### Why the captures stay out

Copyright subsists in a blog post, an article and a forum thread whether or not
they were posted publicly. This project holds that material and quotes it under
an archival and research rationale, which is a real and defensible position:
`Authors Guild v. HathiTrust` and `Authors Guild v. Google` both treat copying
for search and preservation as transformative, and Canadian fair dealing reads
research and news reporting broadly after `CCH`. But every one of those
doctrines is materially stronger for **retention and excerpting** than for
**redistributing complete copies**, and `Hachette v. Internet Archive` (2d Cir.
2024) shows the preservation framing does not carry the day by itself.

Two things follow that are specific to a deposit rather than to a repository:

- **The rights warranty.** A depositor affirms they hold the necessary rights.
  Across several hundred authors, none of whom were asked, this project cannot
  support that affirmation, and signing it anyway would be the one dishonest
  act in a record built on saying exactly how each thing is known.
- **Permanence cuts the wrong way here.** A DOI is valuable because it is
  designed not to be retracted, which is precisely what makes it the wrong
  container for material that might need to come down on a valid legal
  complaint. Keeping the retractable route intact is worth more than the
  completeness.

Two axes that are not copyright point the same way. Platform terms, X's in
particular, prohibit redistributing content and bulk archives independently of
copyright. And captured posts are personal data of identifiable people, some of
them certainly EU or UK data subjects; Article 89 gives research real latitude
for holding and processing, but an irretractable public deposit sits badly with
erasure rights in a way local retention does not.

None of this is a change of posture about the record. What a party published
stays in the record, and this project does not withdraw it because the author
would now prefer it gone. That is a statement about the archive, not a claim to
own other people's copyright.

### Why the diffs go in

Diffs carry third-party text in their added and removed lines, so strictly they
are the same class of material as the snapshots. This is the one genuine
judgment call in the split. They are included because they are excerpt-scale,
because the published site already shows them under exactly that rationale, and
because they are the most valuable research artefact after the registry: they
are the record of how each account changed, which is the project's whole
subject. Excluding them would make the deposit consistent at the cost of making
it much less useful. If a later reviewer disagrees, dropping them is a one-line
change to `EXCLUDED` in `scripts/make_deposit.py`.

## The manifest is what keeps this honest

A deposit that excluded the captures and said nothing further would describe a
corpus it does not contain. `scripts/build_manifest.py` generates
`archive/manifest.jsonl` into the deposit: one row per held capture, carrying
the source id, capture time, original URL, provenance, HTTP status, byte sizes
and both content hashes. No captured content.

With it, a reader can see exactly what exists, cite an individual capture by id
and timestamp, verify a copy obtained from the repository or from the project
directly, and know precisely what the deposit is missing. Fields are
allowlisted rather than copied from the sidecar, on the same reasoning as
`scripts/response_headers.py`: a sidecar accumulates collection detail, and a
manifest that forwards whatever it finds will publish the next field somebody
adds.

Captures whose directory is not a timestamp are described too, with a null
capture time and the reason stated. They are not published, but a manifest that
quietly dropped them would understate the corpus, which is the one thing it
must not do.

## Running it

```bash
just manifest --out /tmp/manifest.jsonl   # the description alone
just deposit                              # stage under .work/deposit/
just deposit --tar                        # and write a tarball beside it
```

Staging refuses to run against a working tree with uncommitted changes to
tracked files, because a deposit names a commit and one that does not match its
own contents is worse than none. `--allow-dirty` overrides it for a dry look
and marks the report.

Before writing checksums, the staged tree is scanned for machine paths and for
any operator needle in `site/tools/private-tokens.json`. Path tokens are matched
as paths rather than as substrings: this repository documents those very tokens,
and a scanner that cannot tell a leak from its own specification fails on every
honest run, which teaches an operator to disable it.

## Still to decide

- **Open or restricted.** Zenodo supports restricted records: public DOI and
  metadata, files behind request-access. That is the natural home if the
  captures themselves are ever to be deposited, because it separates
  preservation from redistribution and leaves the access decision with the
  operator.
- **Release tags.** The deposit takes its version from `git describe`, and
  falls back to a short commit because nothing is tagged yet.
- **Software Heritage** carries the same question as Zenodo, since it archives
  the whole repository rather than a curated subset.
