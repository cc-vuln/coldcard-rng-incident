# Fixed synthetic Mk3 RNG test vector

Checked 1 Aug 2026. This record turns the default synthetic inputs from the 3z
Mk3 reproduction into fixed expected outputs that can be regression-tested
without third-party Python packages.

This is not a recovered wallet, a candidate-search tool or evidence about the
distribution of real device state. The mnemonic and extended public keys are
synthetic and must never receive funds.

## Inputs

- Source model: `3z/coldcard-mk3-rng-disclosure`
- Pinned source commit: `e17d833bc02371ef779e66e25a78c755e57039ef`
- UID low word: `0x12345678`
- SysTick value: `0x00054321`
- RTC time register: `0x00123456`
- RTC subsecond register: `0x00000123`
- Prior 32-bit output calls: `0`
- BIP39 passphrase: empty

The fixed output record is held at
[`evidence/mk3-synthetic-vector-e17d833b.json`](evidence/mk3-synthetic-vector-e17d833b.json).

## Verification method

`scripts/verify_mk3_vector.py` independently implements the two Yasmarang state
transitions, their XOR output, SHA-256, BIP39 bit grouping and seed stretching,
secp256k1 point multiplication, hardened BIP32 derivation and extended-key
serialization. It uses only the Python standard library.

The fixed entropy-to-word mapping is checked against the selected entries in
the BIP39 English list at bitcoin/bips commit
`9783d61f1b9c81231581fee026c8e8cb9499d265`. Verification gates use explicit
exceptions, so `python -O` cannot remove them.

Before checking the incident-specific vector, the verifier checks its BIP39
seed stretching against the published `TREZOR` reference vector and checks its
BIP32 serialization against the first BIP-0032 master and hardened-child
vectors. The generated raw bytes, SHA-256 entropy and mnemonic also match the
pinned 3z proof-of-concept output.

Run it with:

```bash
just test-vectors
```

The result fixes one exact path from stated inputs to raw bytes, entropy,
mnemonic, BIP39 seed, master fingerprint and account-level xpub/zpub values. It
does not validate the source model's real-device call-count, timing or
candidate-distribution assumptions.
