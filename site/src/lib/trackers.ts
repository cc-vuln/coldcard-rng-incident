/**
 * The community chain monitors' own headline figures, read from this
 * archive's most recent capture of each one.
 *
 * The funds page used to carry these as literals with a hand-typed capture
 * time. That is correct on the day it is typed and silently wrong afterwards:
 * the trackers restate their totals continuously, and the archive already
 * holds every restatement. This reads the number back out of the capture
 * instead, so the published figure and the copy backing it can never disagree.
 *
 * It is a reading of held evidence, not a live feed. Nothing here contacts a
 * tracker: the value is as fresh as the last capture that reached it, and a
 * built page is as fresh as the build. Both times are carried on the reading
 * so a page can state them rather than imply currency it does not have.
 *
 * Three failure modes, all of them visible rather than silent:
 *
 * - a tracker rebuilds its page and the anchor no longer matches. Older
 *   snapshots are tried in turn, and the reading says which capture it came
 *   from; if none match, the pinned figure is published as pinned
 * - a tracker stops answering. The figure stands, with the capture health
 *   beside it, because "unchanged since 4 Aug" and "unreadable since 5 Aug"
 *   are very different statements to put in front of a reader
 * - a tracker changes its page without changing its total. `heldSince` walks
 *   back through consecutive captures that read the same, so the page can say
 *   when the number last moved rather than when the page last moved
 *
 * Every reader anchors on the tracker's own labelling of its headline, not on
 * position in the text: these pages reorder constantly and a positional read
 * would report a wave subtotal as the total.
 */
import { pollHealth, snapshots, snapshotText, tsToHuman } from './archive';

export interface TrackerNumbers {
  /** Headline total in BTC, digits as the tracker states them. */
  btc: string;
  /** Distinct source addresses behind that total, when the page states one. */
  addresses?: string;
  /** One further published figure worth carrying beside the headline. */
  detail?: string;
}

interface TrackerSpec {
  /** Registry id, which is also the snapshot directory. */
  id: string;
  /** How the tracker names itself, for a card label. */
  label: string;
  by: string;
  read: (text: string) => TrackerNumbers | null;
  /**
   * Last figure checked by hand, published only if no held capture parses.
   * Its date is the capture it was taken from, so a pinned reading still
   * dates honestly.
   */
  pin: TrackerNumbers & { at: string };
}

/** Digits only, so a numeric sort key survives the tracker's own formatting. */
function toNumber(btc: string): number {
  return Number(btc.replace(/,/g, ''));
}

/** Two decimals with thousands separators, for the summary cards. */
function round(btc: string): string {
  const n = toNumber(btc);
  return Number.isFinite(n)
    ? n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : btc;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const SPECS: TrackerSpec[] = [
  {
    id: 'coldcard-watch',
    label: 'coldcardwatch.com',
    by: 'anonymous operator',
    // The headline is the only bare "N,NNN.NNNNBTC" line above the fold; the
    // most-recent-drain figure further down is a fraction of a coin, so first
    // match is the total. Anchoring the address count on the tracker's own
    // "addresses drained, verified" caption keeps it tied to the same claim.
    read(text) {
      const btc = /^([\d,]+\.\d+)BTC$/m.exec(text)?.[1];
      if (!btc) return null;
      const addresses = /^([\d,]+)\naddresses drained, verified$/m.exec(text)?.[1];
      return { btc, addresses };
    },
    pin: { btc: '1,366.5774', addresses: '4,580', at: '20260804T080612Z' },
  },
  {
    id: 'coldcard-rip-tracker',
    label: 'coldcard.rip',
    by: 'Kevin Kelbie',
    // The standfirst sentence states swept, addresses and received-after-fees
    // together, which is the one place the page commits to all three at once.
    // Its "BTC swept" card actually carries the post-fee figure.
    read(text) {
      const m = /took ([\d,]+\.\d+) BTC from ([\d,]+) addresses; destinations received ([\d,]+\.\d+) BTC/
        .exec(text);
      if (!m) return null;
      // The page's own asserted freshness, which is not the same as ours: it
      // is when the operator last rebuilt the data, and can sit well behind
      // the capture. Reformatted to match the other times on the page.
      // "Data snapshot" before the 5 August rebuild, "Snapshot" after it. Both
      // are matched so the walk-back through older captures keeps parsing.
      const stamp = /[Ss]napshot\s+(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):\d{2}Z/.exec(text);
      const received = `${m[3]} BTC received after fees`;
      return {
        btc: m[1],
        addresses: m[2],
        detail: stamp
          ? `${received}; its own data snapshot ${Number(stamp[3])} ${MONTHS[Number(stamp[2]) - 1]} ${stamp[4]}:${stamp[5]} UTC`
          : received,
      };
    },
    pin: {
      btc: '1,433.13',
      addresses: '5,477',
      detail: '1,432.48 BTC received after fees; its own data snapshot 3 Aug 22:59 UTC',
      at: '20260803T234445Z',
    },
  },
  {
    id: 'coldcard-hack-tracker',
    label: 'coldcard-hack-tracker',
    by: 'SamSamskies',
    // "TOTAL STOLEN" survived the 2 August five-wave rebuild that renamed the
    // section headings around it; the wave count is the page's own summary
    // line under the total.
    read(text) {
      const btc = /TOTAL STOLEN\s+([\d,]+\.\d+)\s*BTC/.exec(text)?.[1];
      if (!btc) return null;
      const waves = /(\d+) waves ·/.exec(text)?.[1];
      return { btc, detail: waves ? `${waves} labelled waves` : undefined };
    },
    pin: {
      btc: '1,923.80748125',
      detail: '8 labelled waves',
      at: '20260805T054916Z',
    },
  },
];

export interface TrackerReading {
  id: string;
  label: string;
  by: string;
  /** The source page, where the capture and its history live. */
  href: string;
  /** Headline total as the tracker states it, without the unit. */
  btc: string;
  /** Numeric key for table sorting. */
  btcSort: number;
  /** Two-decimal form, for a summary card. */
  btcRounded: string;
  addresses: string | null;
  detail: string | null;
  /**
   * `current`  read from the newest held capture
   * `lagging`  the newest capture no longer parses; an older one does
   * `pinned`   no held capture parses; the hand-checked figure is published
   */
  state: 'current' | 'lagging' | 'pinned';
  /** Capture the figure was read from. */
  readAt: string | null;
  readAtLabel: string;
  /** Same capture, compact: "4 Aug 08:06 UTC". */
  readAtShort: string;
  /** Oldest consecutive capture reading the same total: when it last moved. */
  heldSince: string | null;
  heldSinceLabel: string;
  heldSinceShort: string;
  /** True when the figure has stood since before the capture it was read from. */
  hasHeld: boolean;
  /** Newest capture that reached the tracker at all. */
  checkedLabel: string;
  /** Empty when the source is answering normally. */
  healthLabel: string;
  /** The same, compact: "not resolving here since 4 Aug 09:06 UTC". */
  healthShort: string;
  /** Set when the reading is not from the newest held capture. */
  stateShort: string;
  /** Capture liveness tag: is this record still being refreshed? */
  liveness: { label: string; tone: 'ok' | 'warn' | 'bad' };
  /** One sentence, for a table cell that has room for prose. */
  provenance: string;
}

// Named, because "the site" on this site reads as this site. A figure standing
// still is ordinary; a figure standing still because the tracker stopped
// answering is the thing a reader has to be told.
const HEALTH_PHRASES: Record<string, (label: string) => string> = {
  unreachable: (label) => `${label} has not resolved for this archive since`,
  challenged: (label) => `${label} has served a challenge to this archive since`,
  'guard-miss': (label) => `${label} has not matched what this archive expects to find since`,
  skipped: (label) => `this archive has not been able to read ${label} since`,
};

// The card version of the same states, for a field rather than a sentence.
const HEALTH_SHORT: Record<string, string> = {
  unreachable: 'not resolving here since',
  challenged: 'serving a challenge since',
  'guard-miss': 'not matching its capture guard since',
  skipped: 'unread since',
};

/**
 * The tag at the top of a card: is this record still being refreshed?
 *
 * Deliberately about the capture and not about the tracker's own claims. A
 * source can be answering perfectly while publishing a figure nobody has
 * confirmed; that is what the evidence markers are for. This says only whether
 * the last attempt to read it worked, which is the one thing a reader cannot
 * infer from the figure itself.
 */
const LIVENESS: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad' }> = {
  ok: { label: 'live', tone: 'ok' },
  unreachable: { label: 'unreachable here', tone: 'bad' },
  challenged: { label: 'blocked', tone: 'bad' },
  'guard-miss': { label: 'guard miss', tone: 'warn' },
  skipped: { label: 'paused', tone: 'warn' },
  'never-polled': { label: 'not read', tone: 'warn' },
};

const STATE_SHORT: Record<string, string> = {
  lagging: 'from an older capture; the page has changed shape',
  pinned: 'pinned by hand; no held capture parses',
};

/**
 * 20260804T080612Z -> "4 Aug 08:06 UTC".
 *
 * The cards carry three or four of these and are read by scanning, so the
 * year goes: every capture in this record is 2026, and the full form is one
 * click away on the source page.
 */
function shortTime(ts: string | null): string {
  const m = ts && /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(ts);
  if (!m) return '';
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[4]}:${m[5]} UTC`;
}

function readOne(spec: TrackerSpec): TrackerReading {
  const snaps = snapshots(spec.id);
  const href = `/record/sources/${spec.id}/`;

  // Newest first: the first capture that still parses wins, so one rebuilt
  // page degrades to a dated older reading instead of to nothing.
  let found: { numbers: TrackerNumbers; ts: string; index: number } | null = null;
  for (let i = snaps.length - 1; i >= 0; i -= 1) {
    const numbers = spec.read(snapshotText(spec.id, snaps[i].ts));
    if (numbers) { found = { numbers, ts: snaps[i].ts, index: i }; break; }
  }

  const numbers = found ? found.numbers : spec.pin;
  const readAt = found ? found.ts : spec.pin.at;
  const state = !found ? 'pinned' : found.index === snaps.length - 1 ? 'current' : 'lagging';

  // Walk back while the total reads the same. The page changes far more often
  // than the number does, so the capture a figure came from is not the date
  // that figure has stood since.
  let heldSince = readAt;
  if (found) {
    for (let i = found.index - 1; i >= 0; i -= 1) {
      const earlier = spec.read(snapshotText(spec.id, snaps[i].ts));
      if (!earlier || earlier.btc !== numbers.btc) break;
      heldSince = snaps[i].ts;
    }
  }

  const health = pollHealth(spec.id);
  const phrase = HEALTH_PHRASES[health.state] ?? HEALTH_PHRASES.skipped;
  const healthLabel = health.state === 'ok' || !health.failingSince
    ? ''
    : `${phrase(spec.label)} ${tsToHuman(health.failingSince)}`;

  // The table version: the same four facts as the card's fields, in a sentence,
  // for a cell that has room for one. Short times throughout, because a cell
  // carrying three full timestamps stops being read.
  const readPhrase = state === 'pinned'
    ? `Pinned by hand at the capture of ${shortTime(readAt)}; no held capture parses`
    : state === 'lagging'
      ? `Read from the capture of ${shortTime(readAt)}, not the newest held`
      : `Read from the capture of ${shortTime(readAt)}`;
  const heldPhrase = heldSince && heldSince !== readAt
    ? `, unchanged since ${shortTime(heldSince)}`
    : '';

  return {
    id: spec.id,
    label: spec.label,
    by: spec.by,
    href,
    btc: numbers.btc,
    btcSort: toNumber(numbers.btc),
    btcRounded: round(numbers.btc),
    addresses: numbers.addresses ?? null,
    detail: numbers.detail ?? null,
    state,
    readAt,
    readAtLabel: tsToHuman(readAt),
    readAtShort: shortTime(readAt),
    heldSince,
    heldSinceLabel: tsToHuman(heldSince),
    heldSinceShort: shortTime(heldSince),
    hasHeld: Boolean(heldSince && heldSince !== readAt),
    checkedLabel: health.lastGood ? tsToHuman(health.lastGood) : 'not yet read',
    healthLabel,
    healthShort: healthLabel
      ? `${HEALTH_SHORT[health.state] ?? HEALTH_SHORT.skipped} ${shortTime(health.failingSince)}`
      : '',
    stateShort: STATE_SHORT[state] ?? '',
    liveness: LIVENESS[health.state] ?? LIVENESS['never-polled'],
    provenance: `${readPhrase}${heldPhrase}.${healthLabel
      ? ` Source ${HEALTH_SHORT[health.state] ?? HEALTH_SHORT.skipped} ${shortTime(health.failingSince)}.`
      : ''}`,
  };
}

let warned = false;

/** Every tracker reading, in registry order. */
export function trackerReadings(): TrackerReading[] {
  const readings = SPECS.map(readOne);
  // One line per degraded reading, once per build. A tracker that renames its
  // headline is a five-minute fix to a regex, but only if somebody is told.
  if (!warned) {
    warned = true;
    for (const r of readings) {
      if (r.state === 'pinned') {
        console.warn(`trackers: ${r.id} publishes a PINNED figure, no held capture parses`);
      } else if (r.state === 'lagging') {
        console.warn(`trackers: ${r.id} reads from ${r.readAt}, not its newest capture`);
      }
      if (r.healthLabel) console.warn(`trackers: ${r.id} ${r.healthLabel}`);
    }
  }
  return readings;
}

/** Rounded low and high of the current readings, for a standfirst. */
export function trackerRange(readings: TrackerReading[]): { low: string; high: string } {
  const sorted = [...readings].sort((a, b) => a.btcSort - b.btcSort);
  return {
    low: round(sorted[0].btc),
    high: round(sorted[sorted.length - 1].btc),
  };
}
