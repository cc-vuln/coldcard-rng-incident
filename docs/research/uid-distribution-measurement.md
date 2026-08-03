# Research plan: measure the STM32 UID low word across real COLDCARD devices

**Status:** open as of 1 Aug 2026

**Needs:** samples from multiple physical COLDCARDs and purchase batches. A few
devices can reveal collisions or clustering, but cannot establish a population
entropy estimate.

**Why it matters:** this measurement would constrain one material input to the
published attack-cost models.

---

## The question

Published candidate-count models differ because they make different assumptions
about the UID word, SysTick, RTC state and prior RNG calls:

- **Block** gives a known-UID, unknown-SysTick ceiling of 80,000 candidates
  (about 2^16.29) for a normal Mk2/Mk3 cold boot. If the effective
  `UID_low32 XOR SysTick` value is entirely unknown, its ceiling is 2^32. For
  Mk4-class devices, the secure-element reseed contributes at most another 2^32
  possibilities once the fallback state and call history are fixed.
- **Coinkite** gives preliminary estimates of about 40 bits for Mk3 and about 72
  bits for Mk4, Mk5 and Q under its stated attack assumptions, without publishing
  a decomposition of every term.
- **LLFOURN** models `|U| = 2^20`, `|T| = 80,000` and 16 plausible prior-call
  histories. That produces about 2^40.3 candidates for Mk3 and about 2^72.3
  after adding the Mk4-class 2^32 reseed term. Coinkite links to this model from
  its technical backgrounder.

Measuring the UID word would constrain only `|U|`. It would not settle the
SysTick, RTC, call-history or per-candidate derivation-cost assumptions. No
population measurement of this word has been identified in this archive as of
1 Aug 2026.

## Why the low word may carry less than 32 bits of variation

Verified against source, and the reason this is worth measuring rather than
assuming:

- MicroPython seeds its fallback PRNG with
  `*(uint32_t *)MP_HAL_UNIQUE_ID_ADDRESS`. That is **a single 32-bit read**.
- `MP_HAL_UNIQUE_ID_ADDRESS` is `0x1fff7590` for STM32L4
  (`ports/stm32/mpconfigboard_common.h`). The three COLDCARD firmware board
  targets all use STM32L4-family MCUs: the legacy target uses STM32L475xx, while
  the Mk4-class and Q targets use STM32L4S5xx.
- The STM32 unique ID is **96 bits**, and MicroPython knows it (`machine_info()`
  prints all twelve bytes, commented "96 bits").
- ST documents the 96-bit value as a composition of die coordinates, wafer
  number and lot number. Only the first word reaches the fallback PRNG:

  | Address | Field | Read by the PRNG? |
  |---|---|---|
  | `0x1FFF7590` | die X coordinate in bits 15:0; Y coordinate in bits 31:16 | **yes** |
  | `0x1FFF7594` | remaining UID fields, including wafer and lot data | no |
  | `0x1FFF7598` | remaining lot data | no |

The public ST material reviewed here does not establish that the coordinate
half-words use binary-coded decimal. An ST forum sample is `0x004A0029`; the ST
moderator confirmed that X is the lower half-word and Y the upper half-word, but
left the encoding question unanswered. `0x4A` is not valid BCD, so the sample
must not be presented as proof of BCD encoding. Record and compare the raw
half-words.

If that field really does carry only twelve to sixteen bits of variation in
practice, models that allocate 20 to 32 unknown bits to the UID term overstate
that term. Block's known-UID scenarios are unaffected because they allocate it
zero unknown bits.

**This inference is mine and it is unmeasured.** It could be wrong. That is
precisely why somebody should check it.

## What to do

1. On each COLDCARD you own or have permission to test, read the full 96-bit
   unique ID. Possible routes include:
   - a device or firmware diagnostic that exposes the UID
   - `machine.info()` or `machine.unique_id()` in a suitable MicroPython build
   - a debugger reading `0x1FFF7590`, `0x1FFF7594`, `0x1FFF7598`
2. Record the model, the three raw 32-bit words in hex, and the approximate
   purchase date or batch if known.
3. Split the low word into raw X and Y half-words. Do not assume an encoding
   unless ST documents it for the exact MCU.

## What to publish

The useful output is the **distribution**, not the individual values.

- Sample count and selection method, broken down by model and batch
- The observed raw X and Y half-word ranges
- How many distinct low-word values appear per batch
- Whether devices bought together cluster, and how tightly
- Collision counts and the empirical distribution. Report an entropy estimate
  only if the sample is large and representative enough to support one.

Publish the aggregate. **Do not publish per-device IDs tied to devices holding
funds.** A chip ID is not a secret in the cryptographic sense, but publishing one
alongside "this device generated a seed on affected firmware" narrows an
attacker's search for that specific person. Report ranges and counts.

## What would change as a result

- A tightly clustered low word would reduce the UID term in models that treat it
  as unknown. The rest of each model would still need separate validation.
- A broad distribution approaching the full 32-bit word would support a larger
  UID term, but would not validate assumptions about SysTick, RTC or call history.
- Either result would replace one assumption with evidence and narrow the range
  of defensible models.

## Prior work to build on, not duplicate

- Gregory Sanders (instagibbs) reported recovery against a physical Mk3 on
  v4.1.3 on 30 Jul 2026. It was an *owned-device* recovery with the UID and xpub
  available, not a measurement of a blind remote search. His reproduction
  scripts have not been captured or published here.
- No public test-vector set has been identified in this archive as of 1 Aug 2026
  that maps a stated `(UID, SysTick, RTC, call-count)` tuple to an expected
  entropy value, mnemonic and xpub. That is a separate contribution.

## Sources checked

- [Block's captured technical report](../../archive/snapshots/block-disclosure/20260801T001731Z.txt)
- [Coinkite's captured technical backgrounder](../../archive/snapshots/coinkite-backgrounder/20260801T001731Z.txt)
- [LLFOURN's captured model](../../archive/x/llfourn-model/20260802T223320Z/post.txt)
- [Coldcard MicroPython fallback source, pinned commit](https://github.com/Coldcard/micropython/blob/8a56be66601e5f21f15a76bfc932c9beb8f7cdee/ports/stm32/rng.c#L75-L99)
- [Coldcard firmware board targets, pinned commit](https://github.com/Coldcard/firmware/tree/9a88e1a5c32c69ba7ace7f22c26da90f8442b745/stm32)
- [ST's STM32L4 electronic-signature description](https://www.st.com/resource/en/product_training/stm32l4_system_esign.pdf)
- [ST forum sample and moderator clarification](https://community.st.com/stm32-mcus-products-25/parsing-uid-fields-on-stm32l476-wafer-x-y-coordinates-and-bcd-encoding-154063)

## If you do this

Send it to info@cc-vuln.org and it goes into the archive with attribution,
or publish it yourself and send the link. Either is fine. The point is that the
number exists somewhere public.

Contradicting the inference above is just as useful as confirming it, and will be
recorded the same way.
