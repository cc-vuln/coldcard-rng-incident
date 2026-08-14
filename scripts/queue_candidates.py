#!/usr/bin/env python3
"""Queue operator-dropped URLs in the structured discovery store.

The discovery lanes find candidates on their own schedules, but a person can
also place one URL per line in ``.work/operator-candidates.txt``. Both intake
drivers run this command before building their packet. Recognised URLs become
ordinary structured candidates, with two deliberate differences from a sieve
observation: they are forced to Pending and carry operator priority, which
puts them at the head of the generated lane view.

Identity is the platform's stable native object, not the spelling of a URL.
Reddit aliases and X/Twitter permalink variants therefore cannot create two
candidates. A drop can promote an existing Deferred candidate and can add
operator priority to an existing Pending candidate; it never reopens an
assessed verdict. Registered and already-prioritised candidates are consumed
as duplicates. Unrecognised URLs remain in the drop file for a person.

The canonical store owns locking, event durability and rendering. The ignored
drop file is rewritten only after reconciliation succeeds. Stdlib only.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discovery_common
import hydrate_candidates
import registry_store
from discovery_store import DiscoveryStore, candidate_key, url_identity

DROP = discovery_common.WORK / "operator-candidates.txt"

HEADER = ("# One candidate URL per line; `#` lines are comments. The intake "
          "drivers queue what they recognize and leave the rest here.\n")

X_STATUS = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/([^/?#]+)/status/(\d+)"
    r"(?:[/?#].*)?",
    re.IGNORECASE,
)


def build_observation(url: str, today: str) -> dict | None:
    """Return one public operator observation, or ``None`` if unsupported."""
    match = X_STATUS.fullmatch(url)
    if match:
        handle, status = match.groups()
        # The X hydrator recognises x.com permalinks. Canonicalising legacy
        # twitter.com spellings here preserves the native status identity and
        # keeps the resulting generated line routable to the X intake lane.
        canonical_url = f"https://x.com/{handle}/status/{status}"
        observation = {
            "platform": "x",
            "url": canonical_url,
            "createdAt": today,
            "title": (f"@{handle} post "
                      "(text available during approved intake)"),
            "label": f"X @{handle}",
        }
    else:
        classified = hydrate_candidates.classify_url(url, include_x=False)
        if classified is None:
            return None
        platform, _native_id, _reader = classified
        observation = {
            "platform": platform,
            "url": url,
            "createdAt": today,
            "title": "operator-supplied candidate",
            "author": "",
            "ncomments": 0,
            "label": "operator drop",
        }

    observation["display_line"] = discovery_common.intake_line(observation)
    # ``foundAt`` is both public provenance and the transaction core's stable
    # event clock. Retrying the same drop therefore reuses the same bounded
    # observation instead of inventing a new operation timestamp.
    observation["foundAt"] = today
    observation["priority"] = "operator"
    observation["state"] = "pending"
    return observation


def registered_urls() -> dict[str, str]:
    """Recognised registered URLs mapped to their source identifiers."""
    registry = registry_store.load(ROOT)
    found: dict[str, str] = {}
    for table in ("source", "x_post", "nostr_post"):
        for record in registry.get(table, []):
            url = record.get("url")
            source_id = record.get("id")
            if not isinstance(url, str) or not url \
                    or not isinstance(source_id, str) or not source_id:
                continue
            try:
                url_identity(url, strict=True)
            except ValueError:
                continue
            found[url] = source_id
    return found


def queue(urls: list[str], today: str) -> tuple[list[str], list[str]]:
    """Queue recognised work. Return ``(queued_urls, unrecognised_urls)``."""
    store = DiscoveryStore(ROOT)
    existing = {candidate["identity"]: candidate
                for candidate in store.list_candidates()}
    known = registered_urls()
    registered = {
        candidate_key(*url_identity(url, strict=True)): (url, source_id)
        for url, source_id in known.items()
    }
    known_to_reconcile = {
        url: source_id
        for identity, (url, source_id) in registered.items()
        if identity in existing
        and existing[identity].get("state") in {"pending", "deferred"}
    }

    observations: list[dict] = []
    queued: list[str] = []
    left: list[str] = []
    for url in urls:
        observation = build_observation(url, today)
        if observation is None:
            print("queue-candidates: not a recognized candidate URL, left "
                  f"in the drop file: {url}", file=sys.stderr)
            left.append(url)
            continue

        identity = candidate_key(*url_identity(observation["url"], strict=True))
        candidate = existing.get(identity)
        duplicate = identity in registered or (
            candidate is not None
            and (
                candidate.get("state") in {"assessed", "human-review"}
                or (candidate.get("state") == "pending"
                    and candidate.get("priority") == "operator")
            )
        )
        if duplicate:
            print(f"queue-candidates: already queued or registered: {url}")
            continue

        observations.append(observation)
        queued.append(observation["url"])
        # Suppress a second spelling of the same native object in this drop.
        # The real projection is written by reconcile_observations below.
        existing[identity] = {
            "identity": identity,
            "state": "pending",
            "priority": "operator",
        }

    # A registered drop can expose a queued projection that still needs its
    # explicit terminal event. Limit that reconciliation to affected native
    # identities: an unrelated unrecognised drop should not make this small
    # command scan and settle the entire registry.
    if observations or known_to_reconcile:
        store.reconcile_observations(
            observations, known_urls=known_to_reconcile)
    return queued, left


def main() -> int:
    if not DROP.exists():
        return 0
    urls = []
    for raw in DROP.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    if not urls:
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    queued, left = queue(urls, today)
    # Reconcile first, then spend the ignored drop file. A failed store write
    # leaves every URL available for a harmless retry.
    discovery_common.atomic_text(
        DROP, HEADER + "".join(f"{url}\n" for url in left))
    if queued:
        print(f"queue-candidates: queued {len(queued)} operator-supplied "
              "candidate(s) as priority Pending work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
