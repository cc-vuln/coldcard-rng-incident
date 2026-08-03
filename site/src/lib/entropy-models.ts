/**
 * The candidate-space models, in one place.
 *
 * Every value below is a published model's stated input or result, not a
 * measurement of any device. Three sources publish candidate-space models
 * with different attacker-knowledge assumptions: Block's engineering
 * disclosure, LLFOURN's posted model and follow-ups, and Coinkite's
 * technical backgrounder. Keeping the constants here, each attributed to
 * its source model, stops the same figure being retyped slightly
 * differently across the attack estimator, the firmware table and the
 * multisig arithmetic.
 */

/** Block: SysTick timer states in the stable-RTC Mk2/Mk3 scenario, about 2^16.29. */
export const SYSTICK_STATES = 80_000;

/** LLFOURN: modelled MCU-identifier (UID) states, about 2^20. */
export const UID_STATES = 2 ** 20;

/** LLFOURN: modelled button-press timing variants. */
export const BUTTON_VARIANTS = 16;

/** Block: ceiling when the effective 32-bit UID_low32 XOR SysTick word is entirely unknown. */
export const FULL_UNKNOWN_BITS = 32;

/** Block: a successful Mk4-class reseed carries at most 32 bits into one libngu state word. */
export const RESEED_BITS = 32;

/** LLFOURN: Mk3 model, 2^20 UIDs times 80,000 timing states times 16 button variants, about 2^40.3. */
export const LLFOURN_MK3_BITS = Math.log2(UID_STATES * SYSTICK_STATES * BUTTON_VARIANTS);

/** LLFOURN: initial Mk4-class model, the Mk3 space plus the 32-bit reseed, about 2^72.3. */
export const LLFOURN_MK4_BITS = LLFOURN_MK3_BITS + RESEED_BITS;

/** Bit figures rendered in prose and tables, formatted as they should appear. */
export const BITFIG = {
  /** Block: the stable-RTC Mk2/Mk3 scenario, log2(80,000) as published. */
  mk23SysTick: '2^16.3',
  /** Block: the at-most-32-bit Mk4-class reseed scenario. */
  mk4Reseed: '2^32',
  /** Block: the loose Mk2/Mk3 enumeration ceiling, stated as a strict upper bound. */
  mk23LooseCeiling: '2^40.7',
  /** Block: the loose Mk4-class enumeration ceiling, stated as a strict upper bound. */
  mk4LooseCeiling: '2^73.3',
  /** LLFOURN: the Mk3 model result. */
  llfournMk3: '2^40.3',
  /** LLFOURN: the initial Mk4-class model result, later narrowed by 10 to 14 bits. */
  llfournMk4: '2^72.3',
} as const;
