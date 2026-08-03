/**
 * Per-model firmware boundaries for the entropy regression.
 *
 * These values are the most normative facts on the site: which firmware, on
 * which model, generated affected seeds, and which release fixes generation.
 * They were previously maintained by hand in two places (the risk overview
 * table and the firmware record), which is how a correction lands on one page
 * and not the other. Both pages now read from here; presentation stays with
 * the page.
 *
 * Values are checked against the firmware release histories, release commits
 * and the signed-release record; see /record/firmware/ for the evidence and
 * per-row sourcing notes.
 *
 * Where a range here is wider than the one the vendor currently publishes, the
 * divergence is carried as data (`vendorAffectedFrom`, `vendorNote` and
 * `vendorCorroboration`) rather than as prose on one page, so every table that
 * prints a range can also print the disagreement and the outside evidence
 * bearing on it, with the same limits attached each time.
 */

export interface ModelRange {
  /** Display name, e.g. 'Mk3'. */
  model: string;
  /**
   * How the not-affected range reads on the risk overview. Where a last safe
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
   * One or two sentences on why this site classifies the wider range, and what
   * the repository record does not settle. Set together with
   * `vendorAffectedFrom`.
   */
  vendorNote: string | null;
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
 * Date pin for the vendor-stated boundaries recorded above. Vendor advisories
 * are mutable during an incident, and this one has already been revised once.
 */
export const VENDOR_RANGE_AS_OF = '1 August 2026';

/**
 * The Mk2 and Mk3 lower-bound divergence, stated once. Neither boundary is a
 * typo: the vendor's advisory starts at 4.0.1, and the repository record puts
 * the affected path in the 4.0.0 release commit twelve days earlier.
 */
const VENDOR_RANGE_NOTE =
  'This site classifies v4.0.0 as affected because its release commit ' +
  '910e306e contains the affected generation path, its pinned libngu revision ' +
  'carries the guard defect, and the signed-release record published by ' +
  'Coinkite lists the 17 March 2021 image. What the record does not settle ' +
  'is how widely v4.0.0 was installed during the twelve days before v4.0.1 on ' +
  '29 March 2021.';

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
  /** Why this site classifies the wider range, and what remains unestablished. */
  note: string;
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
 * Every recorded vendor divergence, grouped. Both the risk overview and the
 * firmware record render from this, so the footnote and the table row cannot
 * state different boundaries.
 */
export function vendorDivergences(): VendorDivergence[] {
  const groups = new Map<string, VendorDivergence>();
  for (const m of MODEL_RANGES) {
    if (!m.vendorAffectedFrom || !m.vendorNote || !m.affectedFrom || !m.affectedTo) continue;
    const key =
      `${m.affectedFrom}|${m.vendorAffectedFrom}|${m.affectedTo}|` +
      `${m.vendorNote}|${m.vendorCorroboration}`;
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
