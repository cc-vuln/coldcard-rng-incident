/**
 * The headline numbers, in one place.
 *
 * These values recur across several pages. Keep the transaction set and the
 * measurement stage in each note: gross source value, net collector receipts,
 * post-consolidation holdings and live balances are not interchangeable.
 *
 * Every figure is a published number with a named source; this site aggregates
 * and dates figures rather than producing its own chain accounting. Each
 * figure carries how firmly it is known, so a page can render the status
 * alongside the number instead of a reader having to remember which are
 * checked, reported or derived. A figure can be exact while still depending on
 * a reported attribution of transactions to one operator.
 */
export interface Figure {
  /** Rendered value, formatted as it should appear in prose. */
  value: string;
  /** How firmly it is known. */
  status: 'verified' | 'reported' | 'derived';
  /** Registered source id, for linking to the copy we hold. */
  sourceId?: string;
  /** One line a page can show beside the number. */
  note: string;
}

export const FIG = {
  /** TFTC's published net receipt for the 500-transaction set. */
  sweptBtc: {
    value: '594.47722484 BTC',
    status: 'reported',
    sourceId: 'tftc-who-must-move',
    note: 'TFTC\'s published net value received by the first collector for the 500-transaction set.',
  },
  /** Rounded for prose, where precision past the decimal point is noise. */
  sweptBtcApprox: {
    value: 'about 594 BTC',
    status: 'reported',
    sourceId: 'tftc-who-must-move',
    note: 'Rounded form of TFTC\'s published 30 July sweep receipt.',
  },
  sweptAddresses: {
    value: '500 source addresses',
    status: 'reported',
    sourceId: 'tftc-who-must-move',
    note: 'Reported count of distinct source addresses in the 500-transaction set spanning blocks 960,188 to 960,191.',
  },

  /** The additional 695-transaction conditional candidate set reported by Block. */
  earlierBtc: {
    value: '488.10957948 BTC',
    status: 'reported',
    sourceId: 'clay-earlier-waves-thread',
    note: 'Net value reported in Block\'s captured preliminary thread, conditional on the unconfirmed common-incident attribution.',
  },
  earlierTxs: {
    value: '695',
    status: 'reported',
    sourceId: 'clay-earlier-waves-thread',
    note: 'Block linked these transactions by a shared on-chain fingerprint in a captured preliminary thread while saying the relationship to the drain was not confirmed.',
  },

  galaxyGrossBtc: {
    value: '1,082.65 BTC',
    status: 'reported',
    sourceId: 'glxyresearch-flow-map',
    note: 'Galaxy\'s published gross total for a stated 1,196 addresses, covering the 30 July waves. Galaxy has since published larger totals spanning three and more waves.',
  },

  /*
   * Galaxy's 1 August revision. Its scope is wider than its own 31 July
   * figure: three waves and 4,585 addresses against the 30 July set inside
   * 01:10-01:51 UTC. The two are not alternative measurements of the same
   * set, so they are kept as separate dated figures.
   */
  galaxyRevisedTotalBtc: {
    value: '1,367.05 BTC',
    status: 'reported',
    sourceId: 'glxyresearch-third-wave-revision',
    note: 'Galaxy\'s estimated observed size of the incident as of 1 August 2026, across 4,585 addresses and three waves, stated as approximately $88.6m. Superseded as Galaxy\'s top line by the 3 August confirmed total below.',
  },
  galaxyRevisedAddresses: {
    value: '4,585 addresses',
    status: 'reported',
    sourceId: 'glxyresearch-third-wave-revision',
    note: 'Address count behind Galaxy\'s 1 August revised total.',
  },
  thirdWaveBtc: {
    value: '207.7294 BTC',
    status: 'reported',
    sourceId: 'glxyresearch-third-wave-revision',
    note: 'The third wave Galaxy identified on 1 August 2026, drained from addresses it suspects were COLDCARD-generated.',
  },

  /*
   * Kelbie's 2 August tracker update. The post does not itemise which waves
   * its set counts, so it stays a separate reported figure rather than being
   * read against Galaxy's totals.
   */
  kelbieTrackerBtc: {
    value: '1,367 BTC',
    status: 'reported',
    sourceId: 'kevinkelbie-tracker-update',
    note: 'The total Kelbie\'s tracker stated on 2 August 2026, with coins followed from victim addresses to their current position.',
  },
  kelbieTrackerAddresses: {
    value: '4,620 drained addresses',
    status: 'reported',
    sourceId: 'kevinkelbie-tracker-update',
    note: 'The address count behind Kelbie\'s 2 August tracker total.',
  },
  attackerHoldingsBtc: {
    value: '1,366.3865 BTC',
    status: 'reported',
    sourceId: 'glxyresearch-attacker-holdings',
    note: 'Galaxy\'s reported total under attacker control as of 1 August 2026, with every endpoint attacker address stated to be fully unspent on chain.',
  },

  /*
   * Galaxy's 3 August top-line revision, the second in three days. Wider than
   * the 1 August figure again: three waves now confirmed through victim
   * reports, plus 14 smaller incidents. The suspected fourth wave stays
   * outside the confirmed total for lack of victim confirmation.
   */
  galaxyConfirmedBtc: {
    value: '1,596 BTC',
    status: 'reported',
    sourceId: 'glxy-losses-exceed-100m',
    note: 'Galaxy\'s high-confidence stolen total as of 3 August 2026, across three confirmed waves and 14 smaller incidents, stated as exceeding US$100M.',
  },
  galaxyConfirmedAddresses: {
    value: 'about 7,300 addresses',
    status: 'reported',
    sourceId: 'glxy-losses-exceed-100m',
    note: 'Address count behind Galaxy\'s 3 August confirmed total.',
  },
  galaxyInclWave4Btc: {
    value: '2,055 BTC',
    status: 'reported',
    sourceId: 'glxy-wave4-caveat',
    note: 'Galaxy\'s total including the suspected fourth wave, stated as about US$130m. Galaxy has not promoted it into its top-line numbers for lack of victim confirmation.',
  },
} as const satisfies Record<string, Figure>;

export type FigureKey = keyof typeof FIG;

/** Bare value, for interpolating into prose. */
export function fig(key: FigureKey): string {
  return FIG[key].value;
}
