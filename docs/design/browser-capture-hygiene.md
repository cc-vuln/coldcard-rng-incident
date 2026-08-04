# Design: browser capture hygiene

**Status:** implemented 4 Aug 2026, with two mechanism changes found the
same day: Playwright route interception crashed the renderer on heavy
pages, and the full 3,517-host list exceeded the exec argument limit, so
blocking is DNS-level (`--host-resolver-rules` at launch) against the
committed `capture-browser/ad-hosts.txt`, and the font/media
resource-type blocking was dropped with the route handler.
**Date:** 4 Aug 2026

The question this answers: sources captured through the browser method
carry advertising, consent walls, newsletter modals and tracker chrome
into the held PDF and the extracted text. The archive already filters
some of this per source with normalizers (theblock tickers, cryptonews
chrome, newsbit sidebar, substack engagement). Can the noise be stopped
at the browser instead, without touching publisher content?

## Options investigated

### 1. Request blocking in the daemon (recommended)

The webbridge daemon already owns a Playwright persistent context, so
one `context.route("**/*", handler)` can refuse requests to known ad and
tracking hosts before anything renders. Consequences: ad slots never
load, pages fetch faster, extraction and PDF output carry no ad
creative, and the blocking is deterministic and recorded.

The list as shipped: `capture-browser/ad-hosts.txt`, about 75 hosts
curated from Peter Lowe's ad and tracking server list plus hosts observed
in this archive's own captures. Committed, so every snapshot can be
replayed against exactly the list that was active; a weekly-fetched list
would have made snapshots non-reproducible.

Shape as shipped:

- The daemon maps the listed hosts to localhost through Chromium's own
  `--host-resolver-rules` at launch. Ad requests fail at resolution with
  zero per-request overhead. `WEBBRIDGE_BLOCK_MODE=off` disables it.
- The list name, mechanism and retrieval date travel in the daemon's
  `blocklist_info` response and land in each browser snapshot's meta.json,
  so the archive audit can always explain why an ad is absent.
- Validation instead of a shadow week: every browser source was captured
  through the daemon with blocking active and compared against the held
  production snapshots (all matched within live-value noise), plus an X
  home-timeline render to confirm the signed-in session still works.
- Reddit's Promoted slots are first-party, served inside Reddit's own
  payload, so network blocking does not touch them; the reddit-chrome
  normalizer remains the tool for that noise.

### 2. An ad-blocking extension (not recommended)

Full uBlock Origin is Manifest V2 and has not run on stable Chrome since
Chrome 138 (July 2025); the MV3 successors (uBlock Origin Lite, AdGuard
MV3, Ghostery) work but are a poor fit here. Playwright extensions need
headed or new-headless mode, which conflicts with the daemon's plain
headless launch; filter lists update only through Chrome Web Store
review, on the store's schedule; and an extension is exactly the kind of
opaque, unversioned dependency the daemon's stdlib-and-replaceable
design exists to avoid. Route blocking gives the same network-level
effect, deterministically, with the list version on record per snapshot.

### 3. Consent walls and newsletter modals (small complement)

Cookie-consent overlays (OneTrust, Didomi) and newsletter modals are
usually first-party served, so request blocking does not touch them.
A small evaluate pass in the daemon handles the common cases: click the
known reject/dismiss buttons where present, hide fixed overlays by
injected CSS. Kept deliberately tiny and expanded only when a held
capture shows a wall in the text.

### 4. Resource-type blocking (dropped)

Aborting font and media requests would have cut PDF bloat, but it lived
in the route handler that crashed the renderer, and DNS-level blocking
cannot express resource types. Dropped with the mechanism change; most
of the bloat came from ad creative, which the host blocking already
removes on third-party-ad sources.

## Evidence boundaries

Blocking third-party ad and tracker networks does not alter anything the
publisher served editorially; the precedent is already set by the reddit
chrome normalizer, which removes the same category of noise after
capture. What must stay visible is documented in the same way: every
browser snapshot records the blocklist name, mechanism and retrieval date
in meta.json, the list itself is committed and reviewable, and
docs/capture.md states the policy. Consent and modal suppression is
limited to overlays, never article body content, and the evaluate pass
is part of the daemon's protocol surface so its behaviour is reviewable.
