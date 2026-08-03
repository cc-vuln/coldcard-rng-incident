# The capture browser

Some sources only render inside a real browser: X and Reddit answer scripted
fetches with a challenge, and JS-hydrated trackers return an empty shell. For
those, `scripts/capture.py` and `scripts/ingest-x.py` talk to a small daemon
over plain HTTP on `127.0.0.1:10086`.

This directory holds a reference implementation of that daemon. **It is not
the only one.** The contract is the protocol below, so if you already run
something that speaks it, point the project at that instead and skip this
entirely.

## Why it lives apart from `scripts/`

`scripts/` is stdlib-only on purpose: the capture and archive tooling should
still run in ten years without a dependency tree to rot. A browser cannot be
stdlib-only. Keeping it here, with its own virtual environment, means the
archive tooling stays portable and the browser stays replaceable.

## The protocol

One endpoint, `POST http://127.0.0.1:10086/command`, taking

```json
{"action": "...", "args": {...}, "session": "a-name"}
```

and answering `{"ok": true, "data": {"success": true, ...}}`, or
`{"ok": false, "error": "..."}`. A `session` is an independent tab, so two
callers do not fight over one page.

| action | args | returns | used by |
|---|---|---|---|
| `list_tabs` | none | `tabs[]` | availability probe |
| `navigate` | `url`, `newTab` | `url` | both |
| `evaluate` | `code` | `value` | text extraction |
| `save_as_pdf` | `path`, `paper_format`, `print_background` | `path` | rendered page captures |
| `close_tab` | none | | both |
| `cdp` | `method`, `params` | CDP result | element screenshots |
| `close_session` | none | | `ingest-x.py` |

Read-only by construction: there is no action that posts, follows, likes or
sends anything. Keep it that way. A capture tool that can write to a borrowed
session is a liability, not a feature.

## Setup

```bash
just install-capture-browser     # venv + Playwright Chromium, into .capture-browser/
just capture-browser             # run the daemon in the foreground
```

Then, once, for sources that need a signed-in view:

```bash
just capture-login               # opens a browser; sign in yourself
```

On a headless machine that prints a VNC address and a one-time password to
reach it through an SSH tunnel. Nothing types a credential for you, and the
project stores none: the session lives only in the browser profile at
`.capture-browser/profile`, which is gitignored.

Stop the daemon before signing in. It holds an exclusive lock on the profile,
and `just capture-login` will tell you so rather than failing obscurely.

To run it permanently, see `webbridge.service.example`.

## Which account you sign in as matters

Whatever account you use appears in the captures. Not only in the obvious
place: an element-only screenshot of a post still includes the reply row
underneath it, which carries the signed-in account's avatar.

So:

- Sign in with an account that belongs to the project, not to you personally
- Screenshots are published only if they were taken by the host that captured
  them, gated on a cutover timestamp in `site/tools/stage-x-media.mjs`. A
  capture from an unknown session is withheld, whatever it looks like
- Never add an image-inspection heuristic and treat a capture as cleared. A
  profile picture is a few hundred pixels and moves with the layout; no
  measurement of the image finds it

## Clipping a post, end to end

```bash
just capture-browser &                       # daemon running
just ingest-x 'https://x.com/user/status/123' '' '' 'why this matters'
```

`ingest-x.py` writes an element-only screenshot and a verbatim text sidecar,
and registers the post in `sources.toml` unless you pass `--no-register`.
Posts carrying media can also be pulled with `just capture-x`, which uses
gallery-dl; that tool downloads **media only**, so a text-only post produces
nothing there and needs `ingest-x`.
