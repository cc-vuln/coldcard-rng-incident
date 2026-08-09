/**
 * The candidate-space models, in one place.
 *
 * Every value below is a published model's stated input or result, not a
 * measurement of any device. Block, LLFOURN, Coinkite, otaliptus and one
 * community analysis publish candidate-space models or bounds with different
 * attacker-knowledge assumptions. Keeping the constants here, each attributed
 * to its source model, stops the same figure being retyped slightly
 * differently across the model register, explorer, firmware table and
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
  /** A fully known state or Block's conditional public-initial-state path. */
  knownState: '2^0',
  /** Block: the stable-RTC Mk2/Mk3 scenario, log2(80,000) as published. */
  mk23SysTick: '2^16.3',
  /** LLFOURN: modelled MCU identifier states. */
  llfournUid: '2^20',
  /** ineedanamegenerator: stated bound on the Mk3 UID-XOR-SysTick word. */
  communityMk3Word: '2^23',
  /** Block: the at-most-32-bit Mk4-class reseed scenario. */
  mk4Reseed: '2^32',
  /** Coinkite: its rounded Mk3 estimate. */
  coinkiteMk3: '2^40',
  /** Block: the loose Mk2/Mk3 enumeration ceiling, stated as a strict upper bound. */
  mk23LooseCeiling: '2^40.7',
  /** Block: conditional no-reseed loose ceiling. */
  noReseedLooseCeiling: '2^41.3',
  /** otaliptus: lower tentative Mk4 range when RTC behaviour is problematic. */
  otaliptusProblematicLow: '2^52',
  otaliptusProblematicHigh: '2^63',
  /** LLFOURN: later range, derived by subtracting the reported 10 to 14 bits. */
  llfournFollowupLow: '2^58.3',
  llfournFollowupHigh: '2^62.3',
  /** Coinkite: its rounded Mk4-class estimate. */
  coinkiteMk4: '2^72',
  /** Block: the loose Mk4-class enumeration ceiling, stated as a strict upper bound. */
  mk4LooseCeiling: '2^73.3',
  /** LLFOURN: the Mk3 model result. */
  llfournMk3: '2^40.3',
  /** LLFOURN: the initial Mk4-class model result, later narrowed by 10 to 14 bits. */
  llfournMk4: '2^72.3',
  /** otaliptus: tentative Mk4 results when the RTC contributes variation. */
  otaliptusPessimisticAside: '2^75',
  otaliptusWorkingLow: '2^77',
  otaliptusWorkingHigh: '2^88',
} as const;
