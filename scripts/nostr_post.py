#!/usr/bin/env python3
"""Post a kind-1 text note from the project's nostr identity.

This is a WRITE tool, run by hand by the operator. Its purpose is narrow:
announcements of record updates (new captures, corrections, notable review
outcomes) from the project identity. It is manual use only; there is no
agent path, and nothing here is wired to a timer or to any automated flow.

Safety: the exact text is echoed before anything is signed, and publishing
requires an explicit --yes flag, or an interactive confirmation when stdin
is a terminal. Without either, the tool refuses with exit 2 and nothing
leaves the machine.

Configuration comes from the environment (just loads .env):

  NOSTR_SECRET_KEY     nsec1... secret key of the project identity
  NOSTR_WRITE_RELAYS   comma-separated wss:// relay URLs to publish to

Stdlib only, per repo policy. The one external binary is nak (fiatjaf's
nostr CLI), located with shutil.which. Run through the venv:

  .venv/bin/python scripts/nostr_post.py [--yes] text of the note
  echo "text of the note" | .venv/bin/python scripts/nostr_post.py --yes

or via just:

  just nostr-post --yes "text of the note"

Exit codes: 0 published, 1 relay/publish failure, 2 usage or config error.
"""

from __future__ import annotations

import argparse
import sys

from nostr_common import ConfigError, PublishError, confirm_or_refuse
from nostr_common import publish_event, write_relays


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true",
                    help="publish without interactive confirmation")
    ap.add_argument("text", nargs="*",
                    help="note text; read from stdin when omitted")
    a = ap.parse_args()

    if a.text:
        text = " ".join(a.text)
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        ap.print_help(sys.stderr)
        print("\nnostr_post.py: no note text given (pass it as arguments, "
              "or pipe it on stdin)", file=sys.stderr)
        return 2
    text = text.strip()
    if not text:
        print("nostr_post.py: empty note text; refusing to publish",
              file=sys.stderr)
        return 2

    try:
        relays = write_relays()
        summary = ("About to publish this kind-1 note to "
                   f"{len(relays)} relay(s):\n"
                   + "\n".join(f"  {relay}" for relay in relays)
                   + f"\n--- note text (exactly as published) ---\n{text}\n"
                   + "------------------------------------------")
        confirm_or_refuse(summary, a.yes)
        result = publish_event(1, text)
    except ConfigError as exc:
        print(f"nostr_post.py: {exc}", file=sys.stderr)
        return 2
    except PublishError as exc:
        print(f"nostr_post.py: {exc}", file=sys.stderr)
        return 1

    print(f"published event id: {result['id']}")
    print(f"note1:              {result['note1']}")
    print(f"accepted by {len(result['accepted'])}/{len(result['relays'])} "
          "relay(s):")
    for relay in result["accepted"]:
        print(f"  {relay}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
