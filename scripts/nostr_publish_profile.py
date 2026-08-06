#!/usr/bin/env python3
"""Publish the project's kind-0 profile and kind-10002 relay list.

Both kinds are replaceable events, so this tool is repeatable: run it again
when the profile text or the write-relay set changes, and relays keep the
newest version. The kind-10002 list is the NIP-65 announcement of the
relays the project identity publishes to; it is built from
NOSTR_WRITE_RELAYS, so the list always matches where it was sent.

This is a WRITE tool, run by hand by the operator. Manual use only; there
is no agent path. Publishing requires an explicit --yes flag, or an
interactive confirmation when stdin is a terminal.

Configuration comes from the environment (just loads .env):

  NOSTR_SECRET_KEY     nsec1... secret key of the project identity
  NOSTR_WRITE_RELAYS   comma-separated wss:// relay URLs to publish to

Stdlib only, per repo policy. The one external binary is nak (fiatjaf's
nostr CLI), located with shutil.which. Run through the venv:

  .venv/bin/python scripts/nostr_publish_profile.py [--yes]

or via just:

  just nostr-publish-profile --yes

Exit codes: 0 published, 1 relay/publish failure, 2 usage or config error.
"""

from __future__ import annotations

import argparse
import json
import sys

from nostr_common import ConfigError, PublishError, confirm_or_refuse
from nostr_common import publish_event, write_relays

PROFILE = {
    "name": "cc-vuln",
    "about": "Public record of the July 2026 COLDCARD predictable-RNG "
             "incident. https://cc-vuln.org",
    "nip05": "_@cc-vuln.org",
    "website": "https://cc-vuln.org",
    "picture": "https://cc-vuln.org/og.png",
}


def report(label: str, result: dict) -> None:
    print(f"{label} event id: {result['id']}")
    print(f"{label} note1:    {result['note1']}")
    print(f"{label} accepted by {len(result['accepted'])}/"
          f"{len(result['relays'])} relay(s):")
    for relay in result["accepted"]:
        print(f"  {relay}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true",
                    help="publish without interactive confirmation")
    a = ap.parse_args()

    try:
        relays = write_relays()
        content = json.dumps(PROFILE, separators=(",", ":"))
        tags = [f"r={relay}" for relay in relays]
        summary = (
            f"About to publish the project profile to {len(relays)} "
            "relay(s):\n"
            + "\n".join(f"  {relay}" for relay in relays)
            + f"\n--- kind-0 content (exactly as published) ---\n{content}\n"
            + "--- kind-10002 tags ---\n"
            + "\n".join(f"  {tag}" for tag in tags)
            + "\n-------------------------"
        )
        confirm_or_refuse(summary, a.yes)

        profile = publish_event(0, content)
        report("kind-0", profile)
        relay_list = publish_event(10002, "", tags)
        report("kind-10002", relay_list)
    except ConfigError as exc:
        print(f"nostr_publish_profile.py: {exc}", file=sys.stderr)
        return 2
    except PublishError as exc:
        print(f"nostr_publish_profile.py: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
