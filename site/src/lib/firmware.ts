/**
 * Per-model firmware boundaries for the entropy regression.
 *
 * These values are the most normative facts on the site: which firmware, on
 * which model, generated affected seeds, and which release fixes generation.
 * They were previously maintained by hand in two places (the retired risk
 * overview table and the firmware record), which is how a correction lands on
 * one page and not the other. The firmware record now reads from here;
 * presentation stays with the page.
 *
 * The affected ranges follow Block's published engineering analysis; the
 * release dates come from held captures of the vendor release histories and
 * the signed-release record. See /record/firmware/ for the evidence and
 * per-row sourcing notes.
 *
 * Where a range here is wider than the one the vendor currently publishes, the
 * divergence is carried as data (`vendorAffectedFrom`, `vendorNote`,
 * `vendorReason` and `vendorCorroboration`) rather than as prose on one page,
 * so every table that prints a range can also print the disagreement, the
 * vendor's own stated reason for it and the outside evidence bearing on it,
 * with the same limits attached each time.
 */

export interface ModelRange {
  /** Display name, e.g. 'Mk3'. */
  model: string;
  /**
   * How the not-affected range reads on the firmware record. Where a last safe
   * release exists it is derived from `lastSafe`; models that launched inside
   * the affected window carry a phrase instead.
   */
  notAffected: string;
  /** Last release before the affected range, when one exists ('v3.2.2'). */
  lastSafe: string | null;
  /** First affected release, null when no affected range is identified. */
  affectedFrom: string | null;
  /** Last affected release. */
  affectedTo: string | null;
  /** Vendor fix release for new generation, null where none is named. */
  fix: string | null;
  /** The qualifier the fix cell must carry wherever the fix is shown. */
  fixCaveat: string;
  /**
   * The first affected release as the vendor currently publishes it, recorded
   * only where it differs from `affectedFrom`. The two boundaries are both
   * deliberate rather than a typo on either side, so the divergence has to be
   * visible at the tables a reader actually consults, not only in the evidence
   * pages underneath them.
   */
  vendorAffectedFrom: string | null;
  /**
   * One or two sentences recording which published analysis the wider range
   * follows, what the vendor publishes instead, and what the held captures do
   * not settle. Set together with `vendorAffectedFrom`.
   */
  vendorNote: string | null;
  /**
   * The vendor's own published reason for its narrower boundary, where it has
   * given one. This is carried separately from `vendorNote` because the two do
   * different work: the note records both published positions, the reason is
   * the other party's own account of its boundary. A divergence with an
   * unexplained vendor boundary and one
   * with a stated vendor boundary are different things to put in front of a
   * reader, and only the second can be reported rather than merely recorded.
   * Null until the vendor states a reason.
   */
  vendorReason: string | null;
  /**
   * Independent third-party evidence bearing on the same lower bound, stated
   * once and with its own limits attached, so that no page can print the
   * corroboration without also printing what it does not establish. The
   * expanded treatment, including the verbatim wording and the analyst's own
   * scope disclaimer, lives in the #vendor-lower-bound callout on
   * /record/firmware/. Set together with `vendorAffectedFrom`.
   */
  vendorCorroboration: string | null;
}

/**
 * Date pin for the vendor-stated boundaries recorded above: the date through
 * which the record establishes that the vendor still publishes this range.
 * Vendor advisories are mutable during an incident, and this one has already
 * been revised once. Snapshots are written only when the extracted text
 * changes, so the pin moves on unchanged polls as well as on new states: the
 * advisory's last changed state is 1 August 2026 at 18:44 UTC and the
 * backgrounder's is 4 August 2026, both giving 4.0.1 through 4.1.9, and both
 * pages were polled without change through 9 August 2026.
 */
export const VENDOR_RANGE_AS_OF = '9 August 2026';

/**
 * The Mk2 and Mk3 lower-bound divergence, stated once. Neither boundary is a
 * typo: the vendor's advisory starts at 4.0.1, and Block's published analysis
 * starts at 4.0.0, a build the captured release history dates twelve days
 * earlier.
 */
const VENDOR_RANGE_NOTE =
  'Two captured positions bound this range from below. Block, whose published ' +
  'analysis this range follows, gives v4.0.0 to v4.1.9 and dates the affected ' +
  'path to "released firmware v4.0.0 on March 17, 2021"; Coinkite\'s captured ' +
  'advisory starts at v4.0.1. The captured vendor release history dates the ' +
  '4.0.0 build to 17 March 2021, twelve days before v4.0.1, and the captured ' +
  'signed-release record lists the 4.0.0 image. What the held captures do not ' +
  'settle is how widely v4.0.0 was installed during those twelve days before ' +
  'v4.0.1 on 29 March 2021.';

/**
 * Coinkite's own published reason for starting at 4.0.1, added after its
 * disclosure history of 4 August 2026 gave one. Before that page the narrower
 * boundary was unexplained, and this site could only record the disagreement;
 * it can now report both positions and say what they actually differ over,
 * which is whether the 17 March 2021 build reached the public, not what the
 * build contained. Both sides agree on the code.
 */
const VENDOR_RANGE_REASON =
  'Coinkite gave its reason in the security disclosure history it published on ' +
  '4 August 2026: v4.0.0 was "built, signed, and tested internally, but its ' +
  'binary was never released publicly", making v4.0.1 the first public 4.x ' +
  'binary, so "the affected-user range therefore begins at v4.0.1". Block, ' +
  'whose range is v4.0.0 to v4.1.9, instead describes the affected path as ' +
  'first appearing "in released firmware v4.0.0 on March 17, 2021". The two ' +
  'accounts differ over whether that build reached the public rather than over ' +
  'what it contained.';

/**
 * Third-party chain analysis bearing on the same lower bound, summarised in one
 * sentence that carries its own limit. Galaxy's finding is about when coins
 * were first received rather than about which firmware produced any seed, and a
 * twelve-day gap between the two candidate releases is too narrow for coin
 * creation dates to separate them, so this is corroboration of the onset and
 * not an adjudication of the boundary.
 */
const VENDOR_RANGE_CORROBORATION =
  'Chain analysis published by Galaxy Research on 1 August 2026 reports that ' +
  'no coin drained in its first three sweep waves was created before roughly ' +
  'block 674,951 on 17 March 2021, which is the v4.0.0 release date rather ' +
  'than the 29 March v4.0.1 date, so it corroborates a March 2021 onset ' +
  'without settling which of the two releases marks the boundary.';

export const MODEL_RANGES: ModelRange[] = [
  {
    model: 'Mk1',
    notAffected: 'all published firmware',
    lastSafe: null,
    affectedFrom: null,
    affectedTo: null,
    fix: null,
    fixCaveat: 'not applicable',
    vendorAffectedFrom: null,
    vendorNote: null,
    vendorReason: null,
    vendorCorroboration: null,
  },
  {
    model: 'Mk2',
    notAffected: 'v3.2.2 or earlier',
    lastSafe: 'v3.2.2',
    affectedFrom: 'v4.0.0',
    affectedTo: 'v4.1.9',
    fix: 'v4.2.0',
    fixCaveat: 'named by the vendor for Mk2 and Mk3; binary not independently checked here',
    vendorAffectedFrom: 'v4.0.1',
    vendorNote: VENDOR_RANGE_NOTE,
    vendorReason: VENDOR_RANGE_REASON,
    vendorCorroboration: VENDOR_RANGE_CORROBORATION,
  },
  {
    model: 'Mk3',
    notAffected: 'v3.2.2 or earlier',
    lastSafe: 'v3.2.2',
    affectedFrom: 'v4.0.0',
    affectedTo: 'v4.1.9',
    fix: 'v4.2.0',
    fixCaveat: 'published binary not independently checked here',
    vendorAffectedFrom: 'v4.0.1',
    vendorNote: VENDOR_RANGE_NOTE,
    vendorReason: VENDOR_RANGE_REASON,
    vendorCorroboration: VENDOR_RANGE_CORROBORATION,
  },
  {
    model: 'Mk4',
    notAffected: 'not before its affected line',
    lastSafe: null,
    affectedFrom: 'v5.0.0',
    affectedTo: 'v5.5.1',
    fix: 'v5.6.0',
    fixCaveat: 'published binary not independently checked here',
    vendorAffectedFrom: null,
    vendorNote: null,
    vendorReason: null,
    vendorCorroboration: null,
  },
  {
    model: 'Mk5',
    notAffected: 'not before launch',
    lastSafe: null,
    affectedFrom: 'v5.5.0',
    affectedTo: 'v5.5.1',
    fix: 'v5.6.0',
    fixCaveat: 'published binary not independently checked here',
    vendorAffectedFrom: null,
    vendorNote: null,
    vendorReason: null,
    vendorCorroboration: null,
  },
  {
    model: 'Q',
    notAffected: 'none in the published release history before the fix',
    lastSafe: null,
    affectedFrom: 'v0.0.3Q',
    affectedTo: 'v1.4.1Q',
    fix: 'v1.5.0Q',
    fixCaveat: 'published binary not independently checked here',
    vendorAffectedFrom: null,
    vendorNote: null,
    vendorReason: null,
    vendorCorroboration: null,
  },
];

/**
 * A divergence between this site's affected range and the vendor's, grouped
 * across the models that share it so a table prints one footnote rather than
 * one per row.
 */
export interface VendorDivergence {
  /** Models sharing this divergence, in table order. */
  models: string[];
  /** How the models read together in a sentence, e.g. 'Mk2 and Mk3'. */
  modelsPhrase: string;
  /** This site's first affected release. */
  affectedFrom: string;
  /** The vendor's first affected release. */
  vendorAffectedFrom: string;
  /** The shared upper bound, which both sides agree on. */
  affectedTo: string;
  /** Which published analysis the wider range follows, and what remains unestablished. */
  note: string;
  /**
   * The vendor's own published reason for its narrower boundary. Null where the
   * vendor has not given one, which is the case a page must render differently:
   * an unexplained boundary is a disagreement, a stated one is two positions.
   */
  reason: string | null;
  /**
   * Independent evidence bearing on the same boundary, with its own limit
   * attached. Null where no third-party corroboration is recorded.
   */
  corroboration: string | null;
}

function phrase(models: string[]): string {
  if (models.length < 2) return models.join('');
  return `${models.slice(0, -1).join(', ')} and ${models[models.length - 1]}`;
}

/**
 * Every recorded vendor divergence, grouped. The firmware record renders both
 * its table rows and its footnote from this, so the two cannot state
 * different boundaries.
 */
export function vendorDivergences(): VendorDivergence[] {
  const groups = new Map<string, VendorDivergence>();
  for (const m of MODEL_RANGES) {
    if (!m.vendorAffectedFrom || !m.vendorNote || !m.affectedFrom || !m.affectedTo) continue;
    const key =
      `${m.affectedFrom}|${m.vendorAffectedFrom}|${m.affectedTo}|` +
      `${m.vendorNote}|${m.vendorReason}|${m.vendorCorroboration}`;
    const found = groups.get(key);
    if (found) {
      found.models.push(m.model);
      found.modelsPhrase = phrase(found.models);
      continue;
    }
    groups.set(key, {
      models: [m.model],
      modelsPhrase: m.model,
      affectedFrom: m.affectedFrom,
      vendorAffectedFrom: m.vendorAffectedFrom,
      affectedTo: m.affectedTo,
      note: m.vendorNote,
      reason: m.vendorReason,
      corroboration: m.vendorCorroboration,
    });
  }
  return [...groups.values()];
}

/** Lookup by model name; throws so a typo fails the build, not the reader. */
export function modelRange(model: string): ModelRange {
  const found = MODEL_RANGES.find((m) => m.model === model);
  if (!found) throw new Error(`no firmware range recorded for model ${model!}`);
  return found;
}
