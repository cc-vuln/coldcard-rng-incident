#!/usr/bin/env python3
"""Generate and verify the archive's fixed synthetic Mk3 RNG test vector.

Standard library only. The BIP32 implementation checks itself against the first
published BIP-0032 vector before it is used for the incident-specific vector.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
import unicodedata
from pathlib import Path


MASK32 = 0xFFFFFFFF
FIELD_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
CURVE_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GENERATOR = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
XPUB_VERSION = 0x0488B21E
ZPUB_VERSION = 0x04B24746

# The 24 index-to-word pairs used by this fixed vector, checked against the
# BIP39 English list at bitcoin/bips commit
# 9783d61f1b9c81231581fee026c8e8cb9499d265. Keeping only the selected entries
# makes the verifier self-contained while still testing the entropy-to-word
# mapping instead of accepting a separately hardcoded mnemonic.
VECTOR_INDEX_WORDS = (
    (139, "badge"),
    (220, "breeze"),
    (970, "junk"),
    (398, "crack"),
    (1244, "oppose"),
    (1530, "satisfy"),
    (263, "can"),
    (1333, "pluck"),
    (1224, "october"),
    (1586, "shock"),
    (44, "airport"),
    (771, "gather"),
    (678, "feel"),
    (973, "keen"),
    (957, "jeans"),
    (1388, "pulse"),
    (1263, "over"),
    (1099, "maximum"),
    (974, "keep"),
    (975, "ketchup"),
    (1001, "large"),
    (167, "belt"),
    (84, "appear"),
    (115, "attack"),
)

VECTOR_INPUT = {
    "uid_word": 0x12345678,
    "systick": 0x00054321,
    "rtc_tr": 0x00123456,
    "rtc_ssr": 0x00000123,
    "prior_word_calls": 0,
    "bip39_passphrase": "",
}


class VerificationError(RuntimeError):
    """A standard or vector invariant did not match its expected value."""


def require(condition: bool, message: str) -> None:
    """Raise even when Python assertions are disabled with ``-O``."""
    if not condition:
        raise VerificationError(message)


class Yasmarang:
    """MicroPython/libngu Yasmarang state transition."""

    def __init__(self, pad: int, n: int, d: int, dat: int = 0) -> None:
        self.pad = pad & MASK32
        self.n = n & MASK32
        self.d = d & MASK32
        self.dat = dat & 0xFF

    def next_word(self) -> int:
        self.pad = (self.pad + self.dat + self.d * self.n) & MASK32
        self.pad = ((self.pad << 3) + (self.pad >> 29)) & MASK32
        self.n = self.pad | 2
        self.d ^= ((self.pad << 31) + (self.pad >> 1)) & MASK32
        self.d &= MASK32
        self.dat ^= (self.pad & 0xFF) ^ (self.d >> 8) ^ 1
        self.dat &= 0xFF
        return (
            self.pad
            ^ (self.d << 5)
            ^ (self.pad >> 18)
            ^ (self.dat << 1)
        ) & MASK32


def vulnerable_bytes(inputs: dict[str, int | str]) -> bytes:
    fallback = Yasmarang(
        int(inputs["uid_word"]) ^ int(inputs["systick"]),
        int(inputs["rtc_tr"]),
        int(inputs["rtc_ssr"]),
    )
    whitener = Yasmarang(0x0A8CE26F, 69, 233)

    for _ in range(int(inputs["prior_word_calls"])):
        fallback.next_word()
        whitener.next_word()

    output = bytearray()
    previous = 0
    while len(output) < 32:
        chip = fallback.next_word()
        require(chip != previous, "fallback emitted a repeated 32-bit word")
        previous = chip
        output.extend(struct.pack("<I", chip ^ whitener.next_word()))
    return bytes(output)


def bip39_indices(entropy: bytes) -> list[int]:
    require(len(entropy) == 32, "BIP39 vector entropy must be 32 bytes")
    checksum = hashlib.sha256(entropy).digest()[0]
    combined = int.from_bytes(entropy + bytes([checksum]), "big")
    return [(combined >> (11 * (23 - index))) & 0x7FF for index in range(24)]


def bip39_seed(phrase: str, passphrase: str) -> bytes:
    password = unicodedata.normalize("NFKD", phrase).encode()
    salt = unicodedata.normalize("NFKD", "mnemonic" + passphrase).encode()
    return hashlib.pbkdf2_hmac("sha512", password, salt, 2048)


def point_add(
    left: tuple[int, int] | None,
    right: tuple[int, int] | None,
) -> tuple[int, int] | None:
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2 and (y1 + y2) % FIELD_P == 0:
        return None
    if left == right:
        slope = (3 * x1 * x1) * pow(2 * y1, -1, FIELD_P)
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, FIELD_P)
    slope %= FIELD_P
    x3 = (slope * slope - x1 - x2) % FIELD_P
    y3 = (slope * (x1 - x3) - y1) % FIELD_P
    return x3, y3


def point_multiply(scalar: int) -> tuple[int, int]:
    require(0 < scalar < CURVE_N, "secp256k1 scalar is out of range")
    result = None
    addend = GENERATOR
    value = scalar
    while value:
        if value & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        value >>= 1
    require(result is not None, "secp256k1 multiplication returned infinity")
    return result


def public_key(private_key: int) -> bytes:
    x, y = point_multiply(private_key)
    return bytes([2 | (y & 1)]) + x.to_bytes(32, "big")


def hash160(value: bytes) -> bytes:
    return hashlib.new("ripemd160", hashlib.sha256(value).digest()).digest()


def fingerprint(private_key: int) -> bytes:
    return hash160(public_key(private_key))[:4]


def base58check(payload: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    raw = payload + checksum
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = alphabet[remainder] + encoded
    zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * zeroes + encoded


def master_node(seed: bytes) -> tuple[int, bytes]:
    digest = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    key = int.from_bytes(digest[:32], "big")
    require(0 < key < CURVE_N, "BIP32 master key is out of range")
    return key, digest[32:]


def hardened_child(node: tuple[int, bytes], index: int) -> tuple[int, bytes]:
    key, chain = node
    child_number = index | 0x80000000
    data = b"\x00" + key.to_bytes(32, "big") + child_number.to_bytes(4, "big")
    digest = hmac.new(chain, data, hashlib.sha512).digest()
    child_key = (int.from_bytes(digest[:32], "big") + key) % CURVE_N
    require(child_key != 0, "BIP32 child derivation produced the zero key")
    return child_key, digest[32:]


def derive_hardened(
    seed: bytes,
    path: tuple[int, ...],
) -> tuple[tuple[int, bytes], bytes, int]:
    node = master_node(seed)
    parent_fingerprint = b"\x00" * 4
    child_number = 0
    for index in path:
        parent_fingerprint = fingerprint(node[0])
        node = hardened_child(node, index)
        child_number = index | 0x80000000
    return node, parent_fingerprint, child_number


def extended_public_key(seed: bytes, path: tuple[int, ...], version: int) -> str:
    node, parent_fingerprint, child_number = derive_hardened(seed, path)
    key, chain = node
    payload = b"".join(
        (
            version.to_bytes(4, "big"),
            bytes([len(path)]),
            parent_fingerprint,
            child_number.to_bytes(4, "big"),
            chain,
            public_key(key),
        )
    )
    return base58check(payload)


def self_test_standards() -> None:
    bip39_reference = hashlib.pbkdf2_hmac(
        "sha512",
        b"abandon abandon abandon abandon abandon abandon abandon abandon "
        b"abandon abandon abandon about",
        b"mnemonicTREZOR",
        2048,
    ).hex()
    expected_bip39_seed = (
        "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e5349553"
        "1f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04"
    )
    require(
        bip39_reference == expected_bip39_seed,
        "BIP39 TREZOR reference seed mismatch",
    )

    bip32_seed = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    require(
        extended_public_key(bip32_seed, (), XPUB_VERSION) == (
            "xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29E"
            "SFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8"
        ),
        "BIP32 vector 1 master xpub mismatch",
    )
    require(
        extended_public_key(bip32_seed, (0,), XPUB_VERSION) == (
            "xpub68Gmy5EdvgibQVfPdqkBBCHxA5htiqg55crXYuXoQRKfDBFA1WEjWgP6LHhw"
            "BZeNK1VTsfTFUHCdrfp1bgwQ9xv5ski8PX9rL2dZXvgGDnw"
        ),
        "BIP32 vector 1 hardened-child xpub mismatch",
    )


def build_vector() -> dict[str, object]:
    raw = vulnerable_bytes(VECTOR_INPUT)
    entropy = hashlib.sha256(raw).digest()
    indices = bip39_indices(entropy)
    expected_indices = [index for index, _ in VECTOR_INDEX_WORDS]
    require(
        indices == expected_indices,
        "entropy-to-BIP39 index mapping does not match the fixed vector",
    )
    phrase = " ".join(word for _, word in VECTOR_INDEX_WORDS)
    seed = bip39_seed(phrase, str(VECTOR_INPUT["bip39_passphrase"]))
    master_key, _ = master_node(seed)
    return {
        "schema": 1,
        "purpose": "Synthetic regression vector, not a real wallet",
        "source_model": "3z/coldcard-mk3-rng-disclosure@e17d833bc02371ef779e66e25a78c755e57039ef",
        "inputs": {
            key: f"0x{value:08x}" if isinstance(value, int) and key != "prior_word_calls" else value
            for key, value in VECTOR_INPUT.items()
        },
        "raw32": raw.hex(),
        "entropy_sha256": entropy.hex(),
        "bip39_indices": indices,
        "bip39_phrase": phrase,
        "bip39_seed": seed.hex(),
        "master_fingerprint": fingerprint(master_key).hex(),
        "account_84_xpub": extended_public_key(seed, (84, 0, 0), XPUB_VERSION),
        "account_84_zpub": extended_public_key(seed, (84, 0, 0), ZPUB_VERSION),
        "account_44_xpub": extended_public_key(seed, (44, 0, 0), XPUB_VERSION),
    }


def main() -> None:
    self_test_standards()
    actual = build_vector()
    vector_path = (
        Path(__file__).resolve().parents[1]
        / "docs/reviews/evidence/mk3-synthetic-vector-e17d833b.json"
    )
    expected = json.loads(vector_path.read_text(encoding="utf-8"))
    require(actual == expected, "generated Mk3 vector does not match held JSON")
    print(
        "mk3 vector ok: raw32, SHA-256, BIP39, master fingerprint and account "
        "extended keys match the fixed synthetic vector"
    )


if __name__ == "__main__":
    main()
