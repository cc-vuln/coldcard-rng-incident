# Changelog

Sections are dated by the day the change shipped, UTC. There are no release
numbers: the citable identifier is the commit each build was made from,
published in the footer and at `/version.json`.

## 2026-08-06

### The record

- Record, and stop claiming otherwise, that the archive has not always been
  append-only. The 4 Aug 2026 reddit-json migration deleted the pre-migration
  captures of five Reddit sources (134 captures, 402 files, 1 to 4 Aug), their
  diffs and their lines in `archive/index.jsonl`, so those sources begin on
  4 Aug. Nothing recorded it at the time; it was found on 6 Aug while auditing
  for superseded material before a deposit. The out-of-repository backup was
  deleted deliberately rather than restored, as duplicate rendered-page capture
  of threads still held in better form from before the capture process settled.
  First entry in `/corrections/`; the `/cite/` commitment is now dated and
  scoped to what is true, `README.md` states the gap in the poll log, and
  `AGENTS.md` and the migration's design record carry the rule that came out of
  it: a migration is not a licence to delete history.
- Correct the claim that `coldcard-watch` was gone. The 53 failed polls
  established only that this capture host's filtering resolver blocks
  `coldcardwatch.com`. Public DNS resolves it, and a direct HTTPS check with
  that public answer returned the live tracker with status 200. The source is
  polling again, its last held reading remains visibly stale until the host's
  resolver is fixed, and the capture card now says "unreachable here" rather
  than "offline". The error is recorded at `/corrections/` and marked on every
  page where it appeared.
- Recover the original three-post @intangiblecoins wave-4 thread and its two
  linked Pastebins, and present them beside the two later corrections.
- Recover both files in profedustream's Smash transfer, and register the
  announcement, transaction graph and method note separately.
- Add the nostr lane: project identity and NIP-05, manual posting, NIP-50
  discovery in probation, and `ingest_nostr.py` capture into
  `archive/nostr/<id>/<TS>/`. `nak` joins gallery-dl as a sanctioned binary.

### The site

- Name and link the sources that have disappeared, rather than stating a count
  a reader cannot follow. The register gains a Status facet, present only while
  something is gone.
- Show the archive capture time beside every published screenshot, separately
  from the source's publication time.
- State the archive's historical purpose: preserve the contemporaneous record
  for posterity, and keep changing explanations legible. Conspiracy theories
  stay dated and attributed as reaction, not evidence.
- Publish citation apparatus: `/cite/`, `CITATION.cff`, and `/version.json`
  plus a footer stamp linking the commit each build was made from.
- Add a corrections log: `corrections.toml`, rendered at `/corrections/`,
  empty at the outset and saying so.

### Policy

- Withdraw the undertaking to honour author removal requests (superseding the
  3 Aug 2026 position). Material its author published publicly stays; what
  still comes down is material that was never public, personal data, and
  anything this project got wrong. The licensing rationale is unchanged.
- Distinguish an author's preference from a complaint with a legal basis.
  Declining to withdraw correctly reported material is not a refusal to answer
  a copyright complaint or a court order.

### Capture and tooling

- Fix a shell syntax check that had never checked anything. `just test-capture`
  ran `bash -n` over a list of nine scripts, and `bash -n a.sh b.sh` parses
  only `a.sh`, making the rest positional parameters. Every shell script but
  `capture-x.sh` had therefore gone unparsed since the list was written,
  including all four agent drivers and `agent-run-common.sh`, which carries the
  containment they share. The check now loops one file per interpreter, and
  `publish-scheduled.sh`, which the publish timer runs unattended and which had
  fallen off the list entirely, is covered for the first time.
- Give the discovery lanes a shared module instead of a shared lane.
  `discover_stackernews.py` held the keyword sieve, the seen-state helpers and
  the whole DISCOVERY.md write path, so Reddit, BitcoinTalk, nostr and X all
  imported a peer lane: the Stacker News module owned the intake header
  describing nostr and a line formatter branching on X candidates. That code is
  now `scripts/discovery_common.py`, imported as a peer by all five lanes, and
  it has direct tests for the first time (`scripts/test_discovery_common.py`):
  assessed verdicts survive a run verbatim, a dismissed thread is not
  re-queued, and a registered thread leaves Pending. Behaviour is unchanged,
  checked by rebuilding each lane's registered-URL set against the live
  registry and comparing it to the old implementation's.
- Drive the test, byte-compile and shell-parse gates from globs rather than
  three hand-maintained lists. Two files had already fallen off them. A new
  script is now checked from the moment it exists.
- Collapse the four site builds to one body. `build-site`, `build-site-full`,
  `build-preview` and `build-site-indexable` repeated the same steps and
  differed only in Astro's environment and, for the full-text build, in
  skipping the output gates it is designed to fail. They are now `_astro` and
  `_gates` plus one line each, so the gate list cannot drift between the local
  build and the one that gets published.
- Contain the four unattended agents, on the assumption that a prompt
  injection in a captured thread works. They ran as the account that owns the
  tree, with the whole of `.env` exported into their environment, so a
  successful injection reached the nostr posting key, the Cloudflare deploy
  token, the X bearer token and `AGENTS.local.md`. Five layers now answer it,
  none of which depends on the model: the agent runs as `cc-agent` with an
  environment built from an allowlist (`scripts/run-agent.sh`); two of the
  three units deny loopback, and the capture browser, whose `evaluate` and
  `cdp` actions are arbitrary JavaScript and raw DevTools inside signed-in
  sessions, now requires a token the agent account cannot read; candidate
  bodies are fetched by the driver before the agent starts
  (`scripts/hydrate_candidates.py`), so no agent makes network requests and
  the X lane never holds the bearer token; first captures moved to the driver,
  so no agent writes `archive/` or fetches an address it just chose; and
  `scripts/agent_guard.py` checks everything a run produced against its role's
  remit, scanning for secret values and handing the registry delta to
  `scripts/check_registry.py`, which pins hosts, the Stacker News query and
  what may change on an existing source. A rejected run is reported and left
  exactly as the agent left it, because what an injection tried to do is worth
  keeping. The reasoning, and the two gaps it does not close, are in
  `docs/design/agent-sandbox.md`; the setup is in `docs/operations.md`, and
  the drivers refuse to run until it is done rather than running unprotected.
  Applied and proved on the capture host the same day, which corrected three
  things that had been asserted rather than measured: `IPAddressAllow=any`
  with `IPAddressDeny=localhost` blocks nothing, because an allow entry beats
  a deny entry whatever its prefix; `NoNewPrivileges` has to be set false
  explicitly or the privilege drop fails; and the repository root needs the
  sticky bit as well as group write, so a provider can create its scratch
  directory without being able to unlink a tracked file. The first live run
  also found a bug in the queue check itself, which read `DISCOVERY.md`'s
  third section asymmetrically and rejected a clean intake with eleven false
  positives. All four are now covered by tests or by `just audit-sandbox`.
- Close the last gap in that containment: an agent can no longer reach a host
  of its choosing. `scripts/agent_proxy.py` refuses a CONNECT to anything not
  in `registry_hosts.toml` or `agent_egress_hosts.toml`, and an nftables rule
  drops everything else from the agent account. The rule is keyed on uid
  rather than on a systemd unit, because `discover-community` runs the
  driver's own Reddit hydration and the agent in one cgroup and `IPAddress*`
  cannot tell a parent from a child; a uid also covers manual runs, which no
  unit setting reaches. The policy question this was blocked on dissolved
  once hydration moved to the driver: only the sweep reads the open web, and
  what it may read is already the registry's own host list, because anything
  it finds has to be registerable to be worth reading. Verified on the host:
  from the agent account, direct HTTPS, the capture browser and the cloud
  metadata endpoint all time out, while an allowed host answers through the
  proxy and a denied one is refused. Provider telemetry stays refused on
  purpose: an agent here reads victim accounts, and a crash reporter is a
  route for fragments of that to leave the host.
- Make the unattended agent provider swappable. Three providers each get a
  wrapper in `/usr/local/bin` speaking the same `-p` contract and a credential
  under the agent account, so `REVIEW_AGENT_BIN`, `X_REVIEW_AGENT_BIN` and
  `CLAIM_SWEEP_AGENT_BIN` can name different models and be compared on the
  same queue. Which providers they are stays out of the repository, with
  `.env` and `AGENTS.local.md`: an archive whose agents read
  attacker-adjacent material should not publish which models read it. Fixed
  in passing: one provider's config file, which holds an API key, was
  world-readable.
- Add curated X-thread capture. `scripts/x_thread.py` reads a thread through the
  capture browser and `capture.py` gains an `x-thread` method, with registry
  validation and audit support, so a `thread = true` `[[x_post]]` writes
  canonical text, a flattened transcript and a record of the depth reached.
  `clay-attribution` is the tier 3 pilot; its last poll pair added 55 lines and
  removed none, which is the healthy shape, because a removal is either a real
  deletion or under-collection and the text alone does not tell them apart.
  Design in `docs/design/x-thread-capture.md`, remaining work in `BACKLOG.md`.
- Expand truncated X posts before capture. `ingest-x.py` calls
  `expand_truncated()` before reading and again before the screenshot pass,
  records the expansion in the sidecar, and refuses a capture whose text is
  still behind a show-more control. `--skip-unchanged` lets a repair pass write
  only where text actually recovers. No held capture needed repairing: all 73
  candidates were re-read and none recovered, because X truncates posts
  rendered in list context and this script has only ever captured focal posts
  on their own permalinks. The fix is therefore a precondition for thread
  capture, where every post is read in list context, rather than a repair.
- Move both Hacker News sources to the Algolia item API
  (`hn-maxwell-mechanism`, `hn-dettmer-writeup-thread`). HN answers this exit
  with a persistent 429, still 429 on single requests 20 seconds apart, so it
  is the address rather than the cadence. The API dates every comment
  absolutely, which retires `relative-time` for these two, and `hn-api-points`
  suppresses score churn. `url` stays the human permalink the site links. The
  stored text is JSON, so site excerpts from these two read worse than the
  rendered page did; the flattener is open in `BACKLOG.md`.
- Record why a poll failed, not just that it did. Failure events carry a
  `diagnosis` slug and, where the origin gave one, `http_status`, alongside the
  unchanged `failure` word. Only the derived slug is stored, never the headers
  behind it. `just diagnose` groups current failures by cause.
- Prepare the archival deposit without making one: `make_deposit.py` stages
  every tracked file minus the captured bodies and has no upload path;
  `build_manifest.py` describes all 1,768 held captures with their hashes and
  none of their content. Staging refuses on a dirty tree, on an unclassified
  `archive/` path, or on a machine path in the staged output. Reasoning in
  `docs/deposit.md`.
- Mirror capture.py's thread-enabled X-post projection in the site's source
  lookup, so those snapshots and reviews resolve during a build.
- Describe X-thread captures correctly in the deposit manifest: a
  `thread = true` `[[x_post]]` also writes into `archive/snapshots/`.
- Remove the unbound `reddit-engagement` normalizer, its table entry and its
  test. It appears in no held sidecar, so nothing replays it;
  `reddit-more-stub-counts` is likewise unbound but appears in 153 and stays.
- Warn in the justfile that `just agent-maintenance` word-splits its arguments:
  a quoted compound command runs only its first word and exits 0.
- Allow "just published" in a held Reddit thread through the public-output
  gate. The token exists to catch this project's recipe name, not the verb.

### Recorded exception

Nine pre-convention capture files from the first days of collection were
retired rather than carried into a permanent deposit. No snapshot was deleted
and no unique record was lost: the two X directories held attachments
byte-identical to those in three dated captures of each post, and the Reddit
thread is held in three canonical `reddit-json` snapshots carrying its full
text, the poster's stated address and the image URLs. What went was the
downloaded image itself, which the display policy never publishes.

```
196855  9c09ed7f43b2ec26  archive/reddit/reddit-drained-timeline/undated/1vb6teq Wallet Drained Timeline.jpg
 12000  0ce758a8f79ae0d1  archive/reddit/reddit-drained-timeline/undated/1vb6teq Wallet Drained Timeline.json
 11929  189fe41e999f89be  archive/reddit/reddit-drained-timeline/undated/info.json
 33506  13db23547ec4e481  archive/x/llfourn-model/undated/attachment-1.png
  2731  4819859f00c1372d  archive/x/llfourn-model/undated/2082990000896147942_1.png.json
  5109  fe6efdb081f32a41  archive/x/nvk-apology/undated/attachment-1.jpg
  2292  6152503e8f9a8081  archive/x/nvk-apology/undated/2083216713693151552_1.jpg.json
  2575  fdcf86722f257705  archive/x/twitter/LLFOURN/info.json
  2120  b3a1af5ee7f54bdc  archive/x/twitter/nvk/info.json
```

This is a recorded operator exception, and the standing shape for any future
one.

## 2026-08-05

- Refocus the public site on the record itself: retire the affectedness,
  risk-overview, personal-estimator, seed-handling and moving-funds pages,
  redirect every retired route, and remove `INCIDENT_PHASE` (recorded in
  `docs/design/record-first-focus.md`).
- Capture the 5 August intake: 62 X posts, five web resources, CKTRIPWIRE
  monitoring, migration reports, scam fallout, prior-warning accounts and
  updated attacker estimates.
- Register and first-capture the five coldcard.rip routes the operator's
  rebuild moved the evidence onto, and separate a guard miss from a publisher
  challenge in `pollHealth`, so a page the archive cannot parse is never
  reported as a source that is blocking us. The guard had recorded 44 blocked
  polls while the site served 200s.
- Read the community trackers' headline totals out of the held captures instead
  of carrying them as literals, with the capture, the last movement and whether
  the source still answers. `check-trackers.mjs` fails a build that falls back
  to the pinned figure.
- Tag each tracker card with its capture liveness, and show provenance as
  fields rather than a paragraph.
- Add `scripts/publish-scheduled.sh` and its example units: an opt-in timer
  that publishes only from a clean tree, and skips rather than fails.

## 2026-08-03

- First commit.
