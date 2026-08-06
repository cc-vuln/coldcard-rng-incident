#!/usr/bin/env python3
"""Offline regression tests for ingest_nostr.py.

Pure parts only: bech32/hex input classification, registry lookup, event.txt
flattening and reply dedupe/sort/truncation. No network, no nak.

Run with: PYTHONPATH=scripts .venv/bin/python -m unittest scripts/test_ingest_nostr.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest_nostr as ingest  # noqa: E402

# Verified against `nak decode` (v0.20.2) on the capture host.
KNOWN_HEX = "e6618db6961dc7b91478e0fa78c4c1b6699009981526693bd5e273972550860c"
KNOWN_NPUB = "npub1uescmd5krhrmj9rcura833xpke5eqzvcz5nxjw74ufeewf2sscxq4g7chm"


def event(event_id: str, pubkey: str = KNOWN_HEX, created_at: int = 1785985539,
          content: str = "hello nostr") -> dict:
    return {
        "kind": 1,
        "id": event_id,
        "pubkey": pubkey,
        "created_at": created_at,
        "tags": [],
        "content": content,
        "sig": "ab" * 64,
    }


def hex_id(byte: int) -> str:
    return f"{byte:02x}" * 32


class Bech32Tests(unittest.TestCase):
    def test_npub_roundtrip_against_nak_vector(self) -> None:
        encoded = ingest.bech32_encode("npub", bytes.fromhex(KNOWN_HEX))
        self.assertEqual(encoded, KNOWN_NPUB)
        hrp, payload = ingest.bech32_decode(KNOWN_NPUB)
        self.assertEqual(hrp, "npub")
        self.assertEqual(payload.hex(), KNOWN_HEX)

    def test_bad_checksum_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ingest.bech32_decode(KNOWN_NPUB[:-1] + "q")

    def test_mixed_case_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ingest.bech32_decode(KNOWN_NPUB[:10].upper() + KNOWN_NPUB[10:])


class DecodeEventRefTests(unittest.TestCase):
    def test_hex_passthrough(self) -> None:
        self.assertEqual(ingest.decode_event_ref(KNOWN_HEX), KNOWN_HEX)

    def test_note1(self) -> None:
        note1 = ingest.bech32_encode("note", bytes.fromhex(KNOWN_HEX))
        self.assertEqual(ingest.decode_event_ref(note1), KNOWN_HEX)

    def test_nevent1_with_relay_hint_tlv(self) -> None:
        # TLV: type 0 = 32-byte id, type 1 = relay hint.
        relay = b"wss://relay.damus.io"
        tlv = bytes([0, 32]) + bytes.fromhex(KNOWN_HEX) \
            + bytes([1, len(relay)]) + relay
        nevent1 = ingest.bech32_encode("nevent", tlv)
        self.assertEqual(ingest.decode_event_ref(nevent1), KNOWN_HEX)

    def test_nevent1_without_id_entry_is_rejected(self) -> None:
        tlv = bytes([1, 4]) + b"wss:"
        nevent1 = ingest.bech32_encode("nevent", tlv)
        with self.assertRaisesRegex(ValueError, "no event id"):
            ingest.decode_event_ref(nevent1)

    def test_wrong_hrp_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a note1"):
            ingest.decode_event_ref(KNOWN_NPUB)

    def test_garbage_is_rejected(self) -> None:
        for bad in ("", "note1", "0x" + KNOWN_HEX, KNOWN_HEX.upper(),
                    KNOWN_HEX[:63], "z" * 64):
            with self.assertRaises(ValueError, msg=bad):
                ingest.decode_event_ref(bad)


class RegistryLookupTests(unittest.TestCase):
    def write_sources(self, body: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "sources.toml"
        path.write_text(body)
        return path

    def entry(self, entry_id: str, url: str) -> str:
        return (f'[[nostr_post]]\nid = "{entry_id}"\nurl = "{url}"\n'
                'author = "npub1xyz"\norg = "nostr"\n')

    def test_match_on_note1_in_url(self) -> None:
        note1 = ingest.bech32_encode("note", bytes.fromhex(KNOWN_HEX))
        path = self.write_sources(
            self.entry("someone-" + KNOWN_HEX[:8], f"https://njump.me/{note1}"))
        found = ingest.find_registration(KNOWN_HEX, path)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "someone-" + KNOWN_HEX[:8])

    def test_match_on_hex_in_url(self) -> None:
        path = self.write_sources(
            self.entry("someone-" + KNOWN_HEX[:8],
                       f"https://example.test/{KNOWN_HEX}"))
        self.assertIsNotNone(ingest.find_registration(KNOWN_HEX, path))

    def test_match_on_id_suffix(self) -> None:
        path = self.write_sources(
            self.entry("someone-" + KNOWN_HEX[:8], "https://njump.me/note1other"))
        self.assertIsNotNone(ingest.find_registration(KNOWN_HEX, path))

    def test_unrelated_entries_do_not_match(self) -> None:
        other = "ff" * 32
        note1 = ingest.bech32_encode("note", bytes.fromhex(other))
        path = self.write_sources(
            self.entry("someone-" + other[:8], f"https://njump.me/{note1}"))
        self.assertIsNone(ingest.find_registration(KNOWN_HEX, path))

    def test_missing_or_broken_registry_is_no_match(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "nope.toml"
        self.addCleanup(lambda: missing.unlink(missing_ok=True))
        self.assertIsNone(ingest.find_registration(KNOWN_HEX, missing))
        broken = self.write_sources("this is [ not toml")
        self.assertIsNone(ingest.find_registration(KNOWN_HEX, broken))


class NormalizeRepliesTests(unittest.TestCase):
    def test_dedupe_first_occurrence_wins(self) -> None:
        first = event(hex_id(1), content="first")
        dupe = event(hex_id(1), content="later copy from another relay")
        result = ingest.dedupe_events([first, dupe, event(hex_id(2))])
        self.assertEqual([e["content"] for e in result], ["first", "hello nostr"])

    def test_dedupe_excludes_the_note_itself(self) -> None:
        result = ingest.dedupe_events([event(hex_id(1)), event(hex_id(2))],
                                      exclude_id=hex_id(1))
        self.assertEqual([e["id"] for e in result], [hex_id(2)])

    def test_sort_oldest_first_ties_by_id(self) -> None:
        a = event(hex_id(3), created_at=100)
        b = event(hex_id(1), created_at=50)
        c = event(hex_id(2), created_at=50)
        ordered, truncated = ingest.normalize_replies([a, b, c])
        self.assertEqual([e["id"] for e in ordered],
                         [hex_id(1), hex_id(2), hex_id(3)])
        self.assertFalse(truncated)

    def test_cap_truncates_and_reports(self) -> None:
        replies = [event(hex_id(i), created_at=i) for i in range(210)]
        ordered, truncated = ingest.normalize_replies(replies, cap=200)
        self.assertEqual(len(ordered), 200)
        self.assertTrue(truncated)
        # The cap keeps the oldest 200, not an arbitrary subset.
        self.assertEqual(ordered[0]["id"], hex_id(0))
        self.assertEqual(ordered[-1]["id"], hex_id(199))


class FlattenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.main = event(hex_id(0), created_at=1785985539,
                          content="line one\nline two")
        self.note1 = ingest.bech32_encode("note", bytes.fromhex(hex_id(0)))

    def flatten(self, replies, truncated=False) -> str:
        return ingest.flatten_event_text(
            self.main, replies, self.note1, "20260806T030000Z",
            "nak req -i from wss://relay.damus.io", "nak version v0.20.2",
            truncated)

    def test_header_and_verbatim_body(self) -> None:
        text = self.flatten([])
        self.assertIn(f"url:      https://njump.me/{self.note1}", text)
        self.assertIn(f"event id: {hex_id(0)}", text)
        self.assertIn(f"author:   {KNOWN_NPUB}", text)
        self.assertIn("posted:   2026-08-06T03:05:39Z (created_at 1785985539)",
                      text)
        self.assertIn("--- note text (verbatim) ---\n\nline one\nline two",
                      text)
        self.assertIn("--- replies (0) ---", text)
        self.assertNotIn("replies.json", text)

    def test_replies_section_single_lined_in_given_order(self) -> None:
        # Sorting is normalize_replies' job; flatten renders the order main()
        # hands it, oldest first.
        older = event(hex_id(1), created_at=1785985600,
                      content="multi\nline reply")
        newer = event(hex_id(2), created_at=1785985700, content="second")
        text = self.flatten([older, newer])
        self.assertIn("--- replies (2) ---", text)
        self.assertIn("[2026-08-06T03:06:40Z] ", text)
        self.assertIn("multi line reply", text)
        self.assertNotIn("multi\nline reply", text)
        self.assertLess(text.index("multi line reply"), text.index("second"))

    def test_truncation_is_marked_in_the_header(self) -> None:
        text = self.flatten([event(hex_id(1))], truncated=True)
        self.assertIn(f"--- replies (1) --- [truncated at {ingest.REPLY_CAP}]",
                      text)


if __name__ == "__main__":
    unittest.main()
