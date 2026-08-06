#!/usr/bin/env python3
"""Shared plumbing for the project's nostr tools.

Stdlib only, per repo policy. The one external binary is nak (fiatjaf's
nostr CLI), located with shutil.which. Run through the venv; the just
recipes load .env before invoking the tools. The bech32/nip19 helpers here
are the one copy: the posting, discovery and ingest tools all import them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

NAK_INSTALL_HINT = (
    "nak not found on PATH. Install fiatjaf's nostr CLI "
    "(https://github.com/fiatjaf/nak); the project expects "
    "~/.local/bin/nak."
)


class ConfigError(Exception):
    """Usage or configuration problem; the posting tools exit 2."""


class PublishError(Exception):
    """nak failed or no relay accepted the event; the posting tools exit 1."""


def nak_path() -> str:
    path = shutil.which("nak")
    if path is None:
        raise ConfigError(NAK_INSTALL_HINT)
    return path


def secret_key() -> str:
    key = os.environ.get("NOSTR_SECRET_KEY", "").strip()
    if not key:
        raise ConfigError(
            "NOSTR_SECRET_KEY is unset. Set it in .env to the project "
            "identity's nsec (see .env.example)."
        )
    return key


def write_relays() -> list[str]:
    raw = os.environ.get("NOSTR_WRITE_RELAYS", "")
    relays = [part.strip() for part in raw.split(",") if part.strip()]
    if not relays:
        raise ConfigError(
            "NOSTR_WRITE_RELAYS is unset or empty. Set it in .env to a "
            "comma-separated list of wss:// relay URLs (see .env.example)."
        )
    for relay in relays:
        if not relay.startswith(("wss://", "ws://")):
            raise ConfigError(
                f"NOSTR_WRITE_RELAYS entry is not a relay URL: {relay}"
            )
    return relays


def confirm_or_refuse(summary: str, assume_yes: bool) -> None:
    """The write gate shared by both posting tools.

    Publishing requires --yes, or an interactive confirmation when stdin is
    a terminal. Anything else refuses with ConfigError (exit 2).
    """
    print(summary)
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise ConfigError(
            "refusing to publish without confirmation: pass --yes, or run "
            "interactively so the text can be confirmed at the terminal"
        )
    answer = input("Publish? Type 'yes' to proceed: ").strip().lower()
    if answer != "yes":
        raise ConfigError("not confirmed; nothing was published")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PUBLISH_OK_RE = re.compile(r"^publishing to (\S+)\.\.\. success\.$")


def publish_event(kind: int, content: str, tags: list[str] | None = None) -> dict:
    """Sign and publish one event through nak; return what happened.

    Returns {"event": <event dict>, "id": <hex>, "note1": <bech32>,
    "accepted": [relay urls that acknowledged], "relays": [all write relays]}.
    Raises PublishError when nak fails or no relay accepted the event.
    """
    nak = nak_path()
    relays = write_relays()
    args = [nak, "event", "--sec", secret_key(), "-k", str(kind)]
    staging = None
    try:
        if content.startswith("@"):
            # nak reads -c from a file when the value starts with '@', so a
            # note whose first character is '@' would be read as a path.
            # Write the text ourselves and use that mechanism deliberately.
            staging = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="nostr-content-",
                delete=False,
            )
            staging.write(content)
            staging.close()
            content = "@" + staging.name
        args += ["-c", content]
        for tag in tags or []:
            args += ["-t", tag]
        args += relays
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=120,
        )
    finally:
        if staging is not None:
            os.unlink(staging.name)

    stderr = _ANSI_RE.sub("", proc.stderr)
    if proc.returncode != 0:
        detail = stderr.strip() or "no error output"
        raise PublishError(f"nak exited {proc.returncode}: {detail}")

    event = None
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            break
    if not isinstance(event, dict) or not event.get("id"):
        raise PublishError(
            f"nak produced no event JSON on stdout: {proc.stdout.strip()!r}"
        )

    accepted = []
    for line in stderr.splitlines():
        match = _PUBLISH_OK_RE.match(line.strip())
        if not match:
            continue
        host = match.group(1)
        for relay in relays:
            if relay.split("://", 1)[1] == host:
                accepted.append(relay)
                break
    if not accepted:
        detail = stderr.strip() or "no relay reported success"
        raise PublishError(f"no relay accepted the event: {detail}")

    return {
        "event": event,
        "id": event["id"],
        "note1": note1(event["id"]),
        "accepted": accepted,
        "relays": relays,
    }


# NIP-19 bech32 (BIP-173, original bech32 rather than bech32m). Local because
# nak v0.20.2 has no `encode note` target. This is the one copy: discover and
# ingest import from here, and the vectors in test_ingest_nostr.py check it.

_BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _bech32_polymod(values: list[int]) -> int:
    chk = 1
    generators = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i, generator in enumerate(generators):
            if (top >> i) & 1:
                chk ^= generator
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data, frombits: int, tobits: int, pad: bool = True) -> list[int]:
    acc = 0
    bits = 0
    result = []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or value >> frombits:
            raise ValueError("bech32 data value out of range")
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            result.append((acc >> bits) & maxv)
    if pad:
        if bits:
            result.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ValueError("invalid bech32 padding")
    return result


def bech32_encode(hrp: str, payload: bytes) -> str:
    data = _convertbits(payload, 8, 5)
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + data + [0] * 6) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_ALPHABET[d] for d in data + checksum)


def bech32_decode(text: str) -> tuple[str, bytes]:
    # No 90-character limit: that bound is for BIP173 addresses, and nip19
    # nevent1 strings with relay hints routinely exceed it.
    if text != text.lower():
        raise ValueError("not a valid bech32 string")
    pos = text.rfind("1")
    if pos < 1 or pos + 7 > len(text):
        raise ValueError("not a valid bech32 string")
    hrp = text[:pos]
    try:
        data = [_BECH32_ALPHABET.index(c) for c in text[pos + 1:]]
    except ValueError:
        raise ValueError("not a valid bech32 string") from None
    if _bech32_polymod(_bech32_hrp_expand(hrp) + data) != 1:
        raise ValueError("bad bech32 checksum")
    return hrp, bytes(_convertbits(data[:-6], 5, 8, pad=False))


def note1(event_id_hex: str) -> str:
    """The note1 bech32 form of a 32-byte event id, per NIP-19."""
    raw = bytes.fromhex(event_id_hex)
    if len(raw) != 32:
        raise ValueError(f"event id is not 32 bytes: {event_id_hex!r}")
    return bech32_encode("note", raw)


def decode_event_ref(ref: str) -> str:
    """Resolve a note1/nevent1/hex input to the 32-byte hex event id.

    Pure bech32, no subprocess: the decode must work offline so registry
    lookups are deterministic, and nip19 decoding is a fixed local transform.
    """
    ref = ref.strip()
    if HEX64_RE.match(ref):
        return ref
    lowered = ref.lower()
    if lowered.startswith("note1") or lowered.startswith("nevent1"):
        hrp, payload = bech32_decode(lowered)
        if hrp == "note":
            if len(payload) != 32:
                raise ValueError("note1 payload is not 32 bytes")
            return payload.hex()
        if hrp == "nevent":
            # TLV: the type-0 entry is the 32-byte event id; relay and
            # author hints in the other entries are nak fetch's business.
            cursor = 0
            while cursor + 2 <= len(payload):
                t, length = payload[cursor], payload[cursor + 1]
                value = payload[cursor + 2:cursor + 2 + length]
                if len(value) != length:
                    raise ValueError("truncated nevent1 TLV")
                if t == 0 and length == 32:
                    return value.hex()
                cursor += 2 + length
            raise ValueError("nevent1 carries no event id")
    raise ValueError(
        f"not a note1, nevent1 or 64-char hex event id: {ref!r}"
    )
