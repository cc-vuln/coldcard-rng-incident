/**
 * The headline numbers, in one place.
 *
 * These values recur across several pages. Keep the transaction set and the
 * measurement stage in each note: gross source value, net collector receipts,
 * post-consolidation holdings and live balances are not interchangeable.
 *
 * Each figure carries where it came from and how firmly it is known, so a page
 * can render the status alongside the number instead of a reader having to
 * remember which are checked, reported or derived. A figure can be exact while
 * still depending on a reported attribution of transactions to one operator.
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
    status: 'derived',
    sourceId: 'tftc-who-must-move',
    note: 'Net value received by the first collector in this site\'s local 500-transaction calculation; the value matches TFTC\'s published receipt.',
  },
  /** Rounded for prose, where precision past the decimal point is noise. */
  sweptBtcApprox: {
    value: 'about 594 BTC',
    status: 'derived',
    sourceId: 'tftc-who-must-move',
    note: 'Rounded form of the derived 30 July sweep total.',
  },
  sweptGrossBtc: {
    value: '594.51379184 BTC',
    status: 'derived',
    note: 'Local calculation of gross value consumed from 500 original source addresses, including transaction fees.',
  },
  sweptFeesBtc: {
    value: '0.03656700 BTC',
    status: 'derived',
    note: 'Gross source value minus the first collector receipt for the 500-transaction set.',
  },
  sweptUsd: {
    value: 'about US$38.3M',
    status: 'derived',
    note: 'A contemporary conversion reported for the approximately 594.48 BTC figure, not a fixed valuation.',
  },
  sweptAddresses: {
    value: '500 source addresses',
    status: 'derived',
    note: 'Local count of distinct original source addresses in 500 transactions spanning blocks 960,188 to 960,191.',
  },

  /** The additional 695-transaction conditional candidate set reported by Block. */
  earlierBtc: {
    value: '488.10957948 BTC',
    status: 'derived',
    sourceId: 'clay-earlier-waves-thread',
    note: 'Net value reported in Block\'s captured preliminary thread and reproduced locally, conditional on the unconfirmed common-incident attribution.',
  },
  earlierGrossBtc: {
    value: '488.13939738 BTC',
    status: 'derived',
    note: 'Gross source value of the reported 695-transaction set, including 0.02981790 BTC in source-transaction fees.',
  },
  earlierTxs: {
    value: '695',
    status: 'reported',
    sourceId: 'clay-earlier-waves-thread',
    note: 'Block linked these transactions by a shared on-chain fingerprint in a captured preliminary thread while saying the relationship to the drain was not confirmed.',
  },

  /** Net value at first collectors for the 500-transaction set plus Block's conditional candidate set. */
  totalBtc: {
    value: '1,082.59 BTC',
    status: 'reported',
    sourceId: 'clay-earlier-waves-thread',
    note: 'Block\'s rounded net total, conditional on the unconfirmed attribution of the 695-transaction candidate set to the same drain.',
  },
  totalBtcApprox: {
    value: 'about 1,083 BTC',
    status: 'reported',
    note: 'Rounded form shared by Galaxy\'s gross total and Block\'s net first-collector total.',
  },
  totalGrossBtc: {
    value: '1,082.65318922 BTC',
    status: 'derived',
    note: 'Gross source value for the 500 + 695 attributed transaction sets. It rounds to Galaxy\'s published 1,082.65 BTC.',
  },
  totalCollectorBtc: {
    value: '1,082.58680432 BTC',
    status: 'derived',
    note: 'Exact net first-collector total for the 500-transaction set plus Block\'s reported fingerprint-matched candidate set, whose relationship to the drain was unconfirmed.',
  },
  galaxyGrossBtc: {
    value: '1,082.65 BTC',
    status: 'reported',
    sourceId: 'glxyresearch-flow-map',
    note: 'Galaxy\'s published gross total for a stated 1,196 addresses, covering the 30 July wave that this site reconciles transaction by transaction. Galaxy has since published a larger total spanning three waves.',
  },

  /*
   * Galaxy's 1 August revision. Its scope is wider than the reconciliation
   * published here: it spans three waves and 4,585 addresses, where this site's
   * satoshi-level accounting covers the 500 + 695 transaction sets of 30 July,
   * all inside 01:10-01:51 UTC. The two are not alternative measurements of the same set, so they are
   * kept as separate figures rather than reconciled into one.
   */
  galaxyRevisedTotalBtc: {
    value: '1,367.05 BTC',
    status: 'reported',
    sourceId: 'glxyresearch-third-wave-revision',
    note: 'Galaxy\'s estimated observed size of the incident as of 1 August 2026, across 4,585 addresses and three waves, stated as approximately $88.6m.',
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
   * Kelbie's 2 August tracker update. Like Galaxy's revision, it is wider than
   * the sets reconciled on the funds page, and the post does not itemise which
   * waves its set counts, so it stays a separate reported figure rather than
   * being reconciled against Galaxy's revised total or this site's recheck.
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
  postConsolidationBtc: {
    value: '1,082.56919591 BTC',
    status: 'derived',
    note: 'Theft-linked amount in four final holdings after source and consolidation fees, excluding later inbound dust.',
  },
  totalFeesBtc: {
    value: '0.08399331 BTC',
    status: 'derived',
    note: 'All source and consolidation fees between gross inputs and the four final theft-linked holdings.',
  },
  sourceAddresses: {
    value: '1,195 source addresses',
    status: 'derived',
    note: 'Original source addresses across the attributed 500 + 695 transaction sets. Galaxy publishes 1,196 using an unstated counting method.',
  },

  /** Gross source-value distribution across the 500-address set. */
  lossFloor: {
    value: 'approximately 0.15 BTC',
    status: 'derived',
    note: 'Rounded observed minimum. The exact minimum is 0.14999770 BTC and does not establish operator intent.',
  },
  lossMedian: {
    value: '0.409059365 BTC',
    status: 'derived',
    note: 'Statistical median gross source value within the even 500-address set; the half-satoshi precision comes from averaging the two middle integer-satoshi values.',
  },
  lossLargest: {
    value: '29.89252501 BTC',
    status: 'derived',
    note: 'Largest gross source value within the 500-address set only.',
  },
} as const satisfies Record<string, Figure>;

export type FigureKey = keyof typeof FIG;

/** Bare value, for interpolating into prose. */
export function fig(key: FigureKey): string {
  return FIG[key].value;
}
