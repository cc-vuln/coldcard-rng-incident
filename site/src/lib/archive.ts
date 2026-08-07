/**
 * Build-time data layer over the capture archive.
 *
 * Everything the site says about "what changed when" is derived from files the
 * capture tool wrote, not from prose someone typed. That is the whole point: a
 * claim on a page should be traceable to a held snapshot.
 */
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { parse as parseToml } from 'smol-toml';

const REPO = process.env.ARCHIVE_ROOT
  ? resolve(process.env.ARCHIVE_ROOT)
  : resolve(process.cwd(), '..');
const ARCHIVE = join(REPO, 'archive');
const SNAPSHOTS = join(ARCHIVE, 'snapshots');
const DIFFS = join(ARCHIVE, 'diffs');

export type Tier = 1 | 2 | 3;

export interface Source {
  id: string;
  title?: string;
  url: string;
  org?: string;
  kind?: string;
  tier?: Tier;
  note?: string;
  /**
   * When the source itself published, where that is established.
   *
   * Deliberately separate from capture timestamps. What a reader cares about is
   * when Coinkite put something up, not when this project happened to fetch it.
   * Absent rather than guessed when we cannot establish it.
   */
  published?: string;
  /**
   * The origin no longer serves this source.
   *
   * Set when the material has been withdrawn, deleted or otherwise stopped
   * resolving, verified through a genuinely independent network path so it is
   * not mistaken for a block on this collector. Multiple clients on one host
   * do not count when they share its resolver. The source stops being polled
   * and the held capture becomes the only remaining copy, which is the case
   * the archive was built for. `goneNote` records what was observed, not why.
   */
  gone?: boolean;
  gone_since?: string;
  gone_status?: string;
  gone_note?: string;
}

/** Sources the origin no longer serves, most recently withdrawn first. */
export function goneSources(): Source[] {
  return sources()
    .filter((s) => s.gone)
    .sort((a, b) => ((a.gone_since ?? '') < (b.gone_since ?? '') ? 1 : -1));
}

/**
 * How sources are grouped for readers, which is not how they are grouped for
 * the poller.
 *
 * `tier` answers "how often should this be checked", a mechanism question about
 * mutability and value. The record index was displaying tiers under content
 * headings, which crushed two unrelated axes together and produced nonsense: a
 * victim's Reddit thread filed under "Vendor advisories" because it is worth
 * polling often, and Block's technical disclosure, the most important primary
 * source here, filed under "Reporting and analysis" beside a news aggregator.
 *
 * Grouping is derived from `kind`, which already records what a thing is.
 */
export type SourceGroup =
  | 'vendor' | 'legal' | 'public-record' | 'research' | 'analysis' | 'community' | 'repo' | 'reporting' | 'monitor' | 'firsthand';

const KIND_GROUP: Record<string, SourceGroup> = {
  'vendor-advisory': 'vendor',
  'vendor-statement': 'vendor',
  'vendor-response': 'vendor',
  'vendor-docs': 'vendor',
  'vendor-legal': 'vendor',
  'vendor-terms': 'vendor',
  'vendor-index': 'vendor',
  'vendor-releases': 'vendor',
  'custody-guidance': 'vendor',
  // A named organisation speaking for itself without being a vendor in the
  // record's sense: a nonprofit, a trade body, a funder.
  'org-statement': 'vendor',
  'government-legal': 'legal',
  'government-record': 'public-record',
  'research': 'research',
  'independent-analysis': 'research',
  'independent-technical-analysis': 'research',
  'analysis': 'analysis',
  'community-discussion': 'community',
  'repo-file': 'repo',
  'repo-pr': 'repo',
  'repo-patch': 'repo',
  'repo-commit': 'repo',
  'reporting': 'reporting',
  'chain-monitor': 'monitor',
  'victim-account': 'firsthand',
  'aggregator': 'reporting',
};

export function groupOf(src: Source): SourceGroup {
  return KIND_GROUP[src.kind ?? ''] ?? 'reporting';
}

export interface XPost {
  id: string;
  title?: string;
  url: string;
  author: string;
  org?: string;
  posted?: string;
  why?: string;
  tag?: string;
  /** A conversation captured by capture.py and reviewed like a web source. */
  thread?: boolean;
  tier?: Tier;
  /** The id of a thread-enabled post whose captured conversation also holds
      this post. Some posts are held twice: as their own registered record,
      with this project's own note on why they matter, and again inside a
      conversation captured around them. This states the relation so the two
      copies are one declared thing rather than two unconnected ones.
      Validated in capture.py: the target must exist and hold a conversation,
      and a conversation's head can never be a member of anything. */
  part_of?: string;
  /** Honoured on both registry block types: a withheld post renders neither
      its captured text nor its capture image. See withholdsCapturedMedia. */
  withhold_text?: boolean;
  /** Status ids withheld from an otherwise published conversation, text and
      image together. The per-status form of withhold_text; see withholdsPost. */
  withhold_posts?: string[];
}

/**
 * A registered nostr post. Mirrors [[x_post]]: the registry block names the
 * post and why it is evidence, and captures live beside the X captures under
 * `archive/nostr/<id>/<TS>/` (event.json, event.txt, meta.json, optionally
 * replies.json), one directory per capture, exactly like `archive/x/`.
 *
 * `author` is the author's npub in full; shorten it for display with
 * shortNpub(). `url` is the njump (or equivalent) permalink a reader can open.
 */
export interface NostrPost {
  id: string;
  title?: string;
  url: string;
  author: string;
  org?: string;
  posted?: string;
  why?: string;
  tag?: string;
  withhold_text?: boolean;
}

/**
 * npub1pfuvza…rvp9w2: long enough to recognise and to search against, short
 * enough to scan on a card. The full npub stays in the registry and the JSON
 * feeds.
 */
export function shortNpub(npub: string): string {
  return npub.length > 20 ? `${npub.slice(0, 12)}…${npub.slice(-6)}` : npub;
}

const TITLE_WORDS: Record<string, string> = {
  cc: 'COLDCARD', coldcard: 'COLDCARD', coinkite: 'Coinkite',
  mk: 'Mk4/Mk5', mk3: 'Mk3', q: 'Q', libngu: 'libngu', pr: 'PR',
  tftc: 'TFTC', theblock: 'The Block', bitcoinmag: 'Bitcoin Magazine',
  optech: 'Bitcoin Optech', keychainx: 'KeychainX', coin360: 'Coin360',
  faq: 'FAQ', nvk: 'NVK', llfourn: 'LLFOURN', kloaec: 'KLoaec',
  benowhere: 'BEN0WHERE', glxyresearch: 'Galaxy Research',
  robhamilton: 'Rob Hamilton', kevinkelbie: 'Kevin Kelbie',
  otaliptus: 'otaliptus', unchained: 'Unchained',
};

/** Human label for a registry item. Explicit titles win; IDs remain stable keys. */
/**
 * First `words` words of a registry note, for card excerpts.
 *
 * The card wall clamps its excerpt to four lines in CSS, but the full string
 * was still shipped in the HTML, and the same note is published in full on the
 * source's own page. Truncating at the source keeps the wall to what it
 * actually shows; provenance stays whole at /record/sources/<id>/.
 */
export function summary(text: string | undefined, words = 30): string {
  const flat = (text ?? '').trim().split(/\s+/).filter(Boolean);
  if (flat.length <= words) return flat.join(' ');
  return flat.slice(0, words).join(' ').replace(/[,;:.]$/, '') + '\u2026';
}

export function displayTitle(item: Source | XPost | NostrPost): string {
  if (item.title) return item.title;
  return item.id.split('-').map((word) =>
    TITLE_WORDS[word] ?? word.charAt(0).toUpperCase() + word.slice(1)
  ).join(' ');
}

const KIND_LABELS: Record<string, string> = {
  'vendor-advisory': 'Vendor advisory',
  'vendor-statement': 'Vendor statement',
  'vendor-response': 'Vendor response',
  'vendor-docs': 'Vendor documentation',
  'vendor-legal': 'Legal terms',
  'vendor-terms': 'Legal terms',
  'vendor-index': 'Vendor publication index',
  'vendor-releases': 'Firmware release index',
  'custody-guidance': 'Custody guidance',
  'org-statement': 'Organisation statement',
  'government-legal': 'Government legislation',
  'government-record': 'Government vulnerability record',
  'research': 'Primary technical research',
  'independent-analysis': 'Independent primary analysis',
  'independent-technical-analysis': 'Independent technical analysis',
  'analysis': 'Secondary analysis',
  'community-discussion': 'Community discussion',
  'repo-file': 'Repository file',
  'repo-pr': 'Repository pull request',
  'repo-patch': 'Repository patch',
  'repo-commit': 'Repository commit',
  'reporting': 'Reporting',
  'chain-monitor': 'Chain monitor',
  'victim-account': 'First-hand account',
  'aggregator': 'Aggregator',
  'social-post': 'Social post or thread',
};

export function kindLabel(kind?: string): string {
  return KIND_LABELS[kind ?? ''] ?? 'Source';
}

export interface XArtifact {
  path: string;
  name: string;
  format: string;
  bytes: number;
}

let _xFiles: string[] | null = null;
function walkFiles(dir: string): string[] {
  if (!existsSync(dir)) return [];
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkFiles(path));
    else if (entry.isFile()) out.push(path);
  }
  return out;
}

/**
 * Captured artefacts held for one registered X post.
 *
 * Since the 2 Aug 2026 layout migration every capture is its own directory,
 * `archive/x/<post-id>/<TS>/`, and the file name states what a file is:
 * `post.*` is the post itself, `attachment-*` is media the post carried.
 * Consumers no longer infer either from a filename, which is what published
 * an attached photo in place of a post the first time this was built.
 */
export function xArtifacts(post: XPost): XArtifact[] {
  const dir = join(ARCHIVE, 'x', post.id);
  if (!existsSync(dir)) return [];
  const out: XArtifact[] = [];
  for (const capture of readdirSync(dir, { withFileTypes: true })) {
    if (!capture.isDirectory()) continue;
    const captureDir = join(dir, capture.name);
    for (const entry of readdirSync(captureDir, { withFileTypes: true })) {
      if (!entry.isFile()) continue;
      const path = join(captureDir, entry.name);
      out.push({
        path: `archive/x/${post.id}/${capture.name}/${entry.name}`,
        name: entry.name,
        format: entry.name.split('.').pop()?.toUpperCase() ?? 'FILE',
        bytes: statSync(path).size,
      });
    }
  }
  return out.sort((a, b) => a.path.localeCompare(b.path));
}

export interface NostrCapture {
  /** Capture directory timestamp: 20260801T001731Z. */
  ts: string;
  iso: string;
  /** Every file in this capture directory (event.json, event.txt, meta.json,
      optionally replies.json), repo-relative paths, sorted. */
  files: XArtifact[];
}

/**
 * Every capture held for one registered nostr post, oldest first.
 *
 * Same layout rule as the X lane: `archive/nostr/<id>/<TS>/`, a new directory
 * per capture, so the append-only rule is a property of the layout rather
 * than something a writer has to remember. `event.txt` is the flattened text
 * the site excerpts; `event.json` is the raw signed event and stays the
 * artefact a reader can recheck against any relay.
 */
export function nostrCaptures(post: NostrPost): NostrCapture[] {
  const dir = join(ARCHIVE, 'nostr', post.id);
  if (!existsSync(dir)) return [];
  const out: NostrCapture[] = [];
  for (const capture of readdirSync(dir, { withFileTypes: true })) {
    if (!capture.isDirectory()) continue;
    const captureDir = join(dir, capture.name);
    const files: XArtifact[] = [];
    for (const entry of readdirSync(captureDir, { withFileTypes: true })) {
      if (!entry.isFile()) continue;
      const path = join(captureDir, entry.name);
      files.push({
        path: `archive/nostr/${post.id}/${capture.name}/${entry.name}`,
        name: entry.name,
        format: entry.name.split('.').pop()?.toUpperCase() ?? 'FILE',
        bytes: statSync(path).size,
      });
    }
    files.sort((a, b) => a.path.localeCompare(b.path));
    out.push({ ts: capture.name, iso: tsToIso(capture.name), files });
  }
  return out.sort((a, b) => a.ts.localeCompare(b.ts));
}

/**
 * The flattened event text of one held nostr capture, '' when not held.
 * This is what public pages excerpt, under the same rule as web snapshots:
 * excerpts, not mirrors, and nothing at all when the post withholds text.
 */
export function nostrEventText(post: NostrPost, ts: string): string {
  const p = join(ARCHIVE, 'nostr', post.id, ts, 'event.txt');
  return existsSync(p) ? readFileSync(p, 'utf8') : '';
}

/**
 * Whether a source's captured text may be reproduced publicly.
 *
 * Set `withhold_text = true` on a source in sources.toml to hold its captured
 * bodies back: the site then shows that the source is held, when it was
 * captured and that it changed, but not the text.
 * Diffs and excerpts count as reproduction; two added lines are still two
 * lines of the thing.
 *
 * No source sets it today. Everything captured here is material its author
 * published, and an archive that holds something it will not show asks the
 * reader to take its word for what it says.
 *
 * One function, because this once lived in three pages that disagreed, and the
 * page that withheld nothing was the one publishing a first-hand account.
 */
export function withholdsCapturedText(src?: { withhold_text?: boolean }): boolean {
  return src?.withhold_text === true;
}

/**
 * The same flag governs captured media. A screenshot of a post reproduces the
 * post at least as fully as its text does, so a source whose text is withheld
 * must never render its capture image either. Every renderer that shows staged
 * media (EvidenceCard, the feed, the source page) consults this beside the
 * OWN_HOST_FROM staging gate; staging alone is a provenance check, not a
 * publication decision. See docs/design/capture-display-policy.md.
 */
export function withholdsCapturedMedia(src?: { withhold_text?: boolean }): boolean {
  return withholdsCapturedText(src);
}

/**
 * Per-status withholding inside a captured conversation.
 *
 * `withhold_text` withholds a whole source. `withhold_posts` is its per-status
 * form: a list of status ids whose text and image are withheld from a thread
 * that is otherwise published. One flag per source is too coarse for a
 * conversation, where a single phishing reply would otherwise force the choice
 * between publishing it and withholding the entire thread. A phishing reply
 * rendered as a screenshot cannot be defanged by escaping its URL, because the
 * URL is pixels.
 *
 * Withholding a whole source withholds every post in it, so the source-level
 * answer wins. See docs/design/capture-display-policy.md section 1b.
 *
 * This is not muting. Muting de-emphasises a low-signal reply and keeps it on
 * the page; this removes the post's text and image from the page entirely.
 * Keeping them in separate functions is deliberate: a renderer that confuses
 * them either buries evidence or publishes something held back.
 */
export function withholdsPost(
  src: { withhold_text?: boolean; withhold_posts?: string[] } | undefined,
  status: string,
): boolean {
  if (withholdsCapturedText(src)) return true;
  return (src?.withhold_posts ?? []).includes(status);
}

export interface Snapshot {
  sourceId: string;
  ts: string;            // 20260801T001731Z
  iso: string;           // 2026-08-01T00:17:31Z
  textSha256: string;
  rawSha256?: string;
  bytes?: number;
  chars: number;
  event?: string;
  /** 'wayback' when recovered from the Internet Archive rather than polled. */
  provenance?: string;
  waybackUrl?: string;
  hasDiff: boolean;
}

export interface ChangeEvent {
  ts: string;
  iso: string;
  id: string;
  event: 'first' | 'changed' | 'unchanged' | 'error';
  added?: number;
  removed?: number;
  error?: string;
  textSha256?: string;
}

/** 20260801T001731Z -> 2026-08-01T00:17:31Z */
export function tsToIso(ts: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(ts);
  if (!m) return ts;
  const [, y, mo, d, h, mi, s] = m;
  return `${y}-${mo}-${d}T${h}:${mi}:${s}Z`;
}

export function tsToHuman(ts: string): string {
  const iso = tsToIso(ts);
  return isoToHuman(iso);
}

export function isoToHuman(iso: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
    });
  }
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
  }) + ' UTC';
}

let _cfg: any = null;
function config(): any {
  if (!_cfg) _cfg = parseToml(readFileSync(join(REPO, 'sources.toml'), 'utf8'));
  return _cfg;
}

export function meta() {
  return config().meta ?? {};
}

export function sources(): Source[] {
  return (config().source ?? []) as Source[];
}

export function xPosts(): XPost[] {
  return (config().x_post ?? []) as XPost[];
}

export function xPostById(id: string): XPost | undefined {
  return xPosts().find((p) => p.id === id);
}

export function nostrPosts(): NostrPost[] {
  return (config().nostr_post ?? []) as NostrPost[];
}

export function nostrPostById(id: string): NostrPost | undefined {
  return nostrPosts().find((p) => p.id === id);
}

export function sourceById(id: string): Source | undefined {
  const web = sources().find((s) => s.id === id);
  if (web) return web;

  // `capture.py` projects a thread-enabled [[x_post]] into a pollable source
  // under the same id. Mirror that projection here so its snapshots, diffs
  // and additive reviews remain one record all the way through the site.
  const post = xPostById(id);
  if (!post?.thread) return undefined;
  return {
    id: post.id,
    title: post.title,
    url: post.url,
    org: post.org,
    kind: 'social-thread',
    tier: post.tier,
    note: post.why,
    published: post.posted,
  };
}

export type RevisionReviewStatus =
  | 'source-content' | 'capture-noise' | 'capture-correction' | 'unreviewed';

export interface RevisionReview {
  sourceId: string;
  ts: string;
  status: RevisionReviewStatus;
  summary: string;
}

let _reviews: RevisionReview[] | null = null;
function revisionReviews(): RevisionReview[] {
  if (_reviews !== null) return _reviews;
  const path = join(REPO, 'revision-reviews.toml');
  if (!existsSync(path)) return (_reviews = []);
  const parsed = parseToml(readFileSync(path, 'utf8')) as any;
  const allowed = new Set<RevisionReviewStatus>([
    'source-content', 'capture-noise', 'capture-correction', 'unreviewed',
  ]);
  const seen = new Set<string>();
  _reviews = (parsed.revision ?? []).map((r: any) => {
    const key = `${r.source}@${r.timestamp}`;
    if (seen.has(key)) throw new Error(`duplicate revision review: ${key}`);
    if (!sourceById(r.source)) throw new Error(`revision review has unknown source: ${key}`);
    if (!/^\d{8}T\d{6}Z$/.test(r.timestamp ?? '')) {
      throw new Error(`revision review has invalid UTC timestamp: ${key}`);
    }
    if (!allowed.has(r.status)) throw new Error(`revision review has invalid status: ${key}`);
    if (typeof r.summary !== 'string' || !r.summary.trim()) {
      throw new Error(`revision review has no summary: ${key}`);
    }
    seen.add(key);
    return {
      sourceId: r.source,
      ts: r.timestamp,
      status: r.status,
      summary: r.summary,
    };
  });
  return _reviews;
}

export function reviewForRevision(sourceId: string, ts: string): RevisionReview {
  return revisionReviews().find((r) => r.sourceId === sourceId && r.ts === ts) ?? {
    sourceId, ts, status: 'unreviewed',
    summary: 'This detected difference has not yet been reviewed for capture noise.',
  };
}

/** Every stored snapshot for a source, oldest first. */
export function snapshots(sourceId: string): Snapshot[] {
  const dir = join(SNAPSHOTS, sourceId);
  if (!existsSync(dir)) return [];
  const out: Snapshot[] = [];
  for (const f of readdirSync(dir).filter((f) => f.endsWith('.txt')).sort()) {
    const ts = f.replace(/\.txt$/, '');
    const metaPath = join(dir, `${ts}.meta.json`);
    let m: any = {};
    if (existsSync(metaPath)) {
      try { m = JSON.parse(readFileSync(metaPath, 'utf8')); } catch { /* keep going */ }
    }
    const text = readFileSync(join(dir, f), 'utf8');
    out.push({
      sourceId, ts, iso: tsToIso(ts),
      textSha256: m.text_sha256 ?? '',
      rawSha256: m.raw_sha256,
      bytes: m.bytes,
      chars: text.length,
      event: m.event,
      method: m.method,
      provenance: m.provenance,
      waybackUrl: m.wayback_url,
      hasDiff: existsSync(join(DIFFS, sourceId, `${ts}.diff`)),
    });
  }
  return out;
}

export function snapshotText(sourceId: string, ts: string): string {
  const p = join(SNAPSHOTS, sourceId, `${ts}.txt`);
  return existsSync(p) ? readFileSync(p, 'utf8') : '';
}

export function diffText(sourceId: string, ts: string): string | null {
  const p = join(DIFFS, sourceId, `${ts}.diff`);
  return existsSync(p) ? readFileSync(p, 'utf8') : null;
}

/** All change/first/error events, newest first. Unchanged polls are excluded. */
export function changeLog(): ChangeEvent[] {
  const p = join(ARCHIVE, 'index.jsonl');
  if (!existsSync(p)) return [];
  const evs: ChangeEvent[] = [];
  for (const line of readFileSync(p, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    try {
      const e = JSON.parse(line);
      if (e.event === 'unchanged') continue;
      evs.push({
        ts: e.ts, iso: tsToIso(e.ts), id: e.id, event: e.event,
        added: e.diff_added, removed: e.diff_removed,
        error: e.error, textSha256: e.text_sha256,
      });
    } catch { /* a malformed line is not worth failing a build over */ }
  }
  return evs.sort((a, b) => b.ts.localeCompare(a.ts));
}

/** When was this source last confirmed unchanged? Bounds when an edit happened. */
export function lastPolled(sourceId: string): string | null {
  const p = join(ARCHIVE, 'index.jsonl');
  if (!existsSync(p)) return null;
  let last: string | null = null;
  for (const line of readFileSync(p, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    try {
      const e = JSON.parse(line);
      // Latest by timestamp, not by file position. The log is append-ordered by
      // when a run happened, and Wayback backfill appends older observations to
      // the end, so trusting file order reports an earlier time as the most
      // recent one.
      if (e.id === sourceId && e.event !== 'error' && (!last || e.ts > last)) last = e.ts;
    } catch { /* ignore */ }
  }
  return last;
}

/**
 * Whether a source is currently readable, and since when it has not been.
 *
 * `lastPolled` answers "when did we last look" and treats a challenge page as
 * a look; `lastCaptureError` answers "did the most recent look fail" and only
 * counts hard errors. Neither answers the question a page asks when it puts a
 * source's own current number in front of a reader: is this figure still being
 * refreshed, and if not, since when. A challenge or a vanished domain leaves
 * the last good capture in place and everything downstream looks healthy,
 * which is exactly when a reader is most likely to be misled.
 *
 * `state` names the newest poll's outcome, so a page can say "unreachable
 * since" rather than print a collector's own error text at a reader.
 */
export interface PollHealth {
  /** Newest poll that produced usable text: first, changed or unchanged. */
  lastGood: string | null;
  /** Newest poll of any outcome. */
  lastAttempt: string | null;
  state: 'ok' | 'unreachable' | 'challenged' | 'guard-miss' | 'skipped' | 'never-polled';
  /** Oldest poll in the unbroken run of failures at the tail, when failing. */
  failingSince: string | null;
}

const SUCCESS_EVENTS = new Set(['first', 'changed', 'unchanged']);

const FAILURE_STATES: Record<string, PollHealth['state']> = {
  error: 'unreachable',
  blocked: 'challenged',
  skipped: 'skipped',
};

/**
 * A blocked poll means one of two very different things.
 *
 * capture.py records `blocked` with `failure: challenged` both when a
 * publisher interposes a challenge page and when the response simply no
 * longer contains this registry's own guard strings. The second is our
 * problem, not theirs: coldcard.rip rebuilt its page on 5 August 2026 and
 * every poll from then read as "challenged" while the site was serving 200s
 * to anyone who asked. Telling a reader a source is blocking us when the
 * source is fine is the worse error of the two, so they are separated here.
 */
function blockedState(e: any): PollHealth['state'] {
  const guard = Array.isArray(e.missing_required_text) && e.missing_required_text.length > 0;
  return guard || typeof e.min_chars === 'number' ? 'guard-miss' : 'challenged';
}

export function pollHealth(sourceId: string): PollHealth {
  const p = join(ARCHIVE, 'index.jsonl');
  const polls: { ts: string; event: string; entry: any }[] = [];
  if (existsSync(p)) {
    for (const line of readFileSync(p, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      try {
        const e = JSON.parse(line);
        if (e.id === sourceId && typeof e.ts === 'string') {
          polls.push({ ts: e.ts, event: String(e.event ?? ''), entry: e });
        }
      } catch { /* a malformed line is not worth failing a build over */ }
    }
  }
  // By timestamp, not file order: Wayback backfill appends older observations
  // to the end of the log, and the tail is what this function reasons about.
  polls.sort((a, b) => a.ts.localeCompare(b.ts));
  if (!polls.length) {
    return { lastGood: null, lastAttempt: null, state: 'never-polled', failingSince: null };
  }

  const lastAttempt = polls[polls.length - 1].ts;
  let lastGood: string | null = null;
  let failingSince: string | null = null;
  for (let i = polls.length - 1; i >= 0; i -= 1) {
    if (SUCCESS_EVENTS.has(polls[i].event)) { lastGood = polls[i].ts; break; }
    failingSince = polls[i].ts;
  }

  const newest = polls[polls.length - 1];
  const state = SUCCESS_EVENTS.has(newest.event)
    ? 'ok'
    : newest.event === 'blocked'
      ? blockedState(newest.entry)
      : FAILURE_STATES[newest.event] ?? 'unreachable';
  return {
    lastGood,
    lastAttempt,
    state,
    failingSince: state === 'ok' ? null : failingSince,
  };
}

/**
 * The error from a source's most recent poll, when that poll failed.
 *
 * `lastPolled` deliberately skips error events, so a source that has stopped
 * being reachable still reports its last good capture and looks healthy. That
 * is the right answer for "when did we last hold this" and the wrong one for
 * "is this still working", which is what a reader deserves to be told when a
 * publisher blocks the archive outright.
 */
export function lastCaptureError(sourceId: string): string | null {
  const p = join(ARCHIVE, 'index.jsonl');
  if (!existsSync(p)) return null;
  let latestTs: string | null = null;
  let latestErr: string | null = null;
  for (const line of readFileSync(p, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    try {
      const e = JSON.parse(line);
      if (e.id !== sourceId) continue;
      if (!latestTs || e.ts > latestTs) {
        latestTs = e.ts;
        latestErr = e.event === 'error' ? (e.error ?? 'capture failed') : null;
      }
    } catch { /* ignore */ }
  }
  return latestErr;
}

export interface Revision {
  sourceId: string;
  ts: string;
  iso: string;
  /** Recovered from the Internet Archive rather than caught by our poller. */
  inherited: boolean;
  /**
   * The state being revised was itself recovered from the Internet Archive.
   *
   * `inherited` grades only the later snapshot, which is the wrong half of the
   * comparison to hide. A difference caught live against a Wayback baseline is
   * still a difference this project observed, but the "before" text is not one
   * it collected, and a reader weighing the diff should be told so.
   */
  baselineInherited: boolean;
  added: number;
  removed: number;
  /**
   * When the edit actually happened, as far as anyone can tell from outside.
   *
   * We almost never know this exactly. What we know is that the page said one
   * thing at `afterTs` and a different thing at `ts`, so the edit landed
   * somewhere in between. Publishing the later capture time as though it were
   * the revision time would be stating something we did not observe, and it is
   * the capture schedule talking rather than the publisher.
   */
  windowStart: string;
  windowEnd: string;
  review: RevisionReview;
}

/**
 * Every second-or-later state we hold for a source, newest first.
 *
 * Derived from the snapshots on disk rather than from `changed` events in the
 * log, because those two are not the same thing. A revision recovered from the
 * Internet Archive is written as a snapshot but logged as `first`: our poller
 * never saw it change, we reconstructed afterwards that it had. Counting only
 * live-caught events reported zero revisions on a site whose source pages were
 * visibly displaying a diff, which is exactly the wrong way round for an
 * archive built to record that advisories get edited.
 *
 * `inherited` keeps the distinction visible rather than flattening it.
 */
export function revisions(): Revision[] {
  const out: Revision[] = [];
  for (const s of sources()) {
    const snaps = snapshots(s.id);
    // Index 0 is the earliest state held: it revises nothing. From there, a
    // revision is a snapshot whose text hash differs from its predecessor's.
    //
    // Hash comparison rather than snapshot count, because Wayback recovery can
    // legitimately store a state we already held: block-disclosure has two
    // snapshots with one text_sha256 between them. Counting stored files would
    // report that as a revision of a page that never changed, which inflates
    // the one number this archive should be most careful about.
    for (let i = 1; i < snaps.length; i++) {
      const sn = snaps[i];
      if (sn.textSha256 && sn.textSha256 === snaps[i - 1].textSha256) continue;
      // Counted off the diff on disk, so the totals agree with what the source
      // page renders from that same file.
      const lines = (diffText(s.id, sn.ts) ?? '').split('\n');
      out.push({
        sourceId: s.id,
        ts: sn.ts,
        iso: sn.iso,
        inherited: sn.provenance === 'wayback',
        baselineInherited: snaps[i - 1].provenance === 'wayback',
        added: lines.filter((l) => l.startsWith('+') && !l.startsWith('+++')).length,
        removed: lines.filter((l) => l.startsWith('-') && !l.startsWith('---')).length,
        windowStart: snaps[i - 1].ts,
        windowEnd: sn.ts,
        review: reviewForRevision(s.id, sn.ts),
      });
    }
  }
  return out.sort((a, b) => b.ts.localeCompare(a.ts));
}

export interface ArchiveStats {
  sources: number;
  snapshots: number;
  /** Second-or-later states held, however they were obtained. */
  revisions: number;
  /** Reviewed differences in relevant source content. */
  sourceChanges: number;
  /** Differences awaiting a capture-noise review. */
  unreviewed: number;
  /** Preserved differences caused by collection or dynamic page chrome. */
  collectionDifferences: number;
  /** The subset our own poller caught live. */
  changes: number;
  xPosts: number;
  nostrPosts: number;
  firstCapture: string | null;
  lastCapture: string | null;
  bytes: number;
}

export function stats(): ArchiveStats {
  const srcs = sources();
  let snaps = 0, bytes = 0;
  let first: string | null = null, last: string | null = null;
  for (const s of srcs) {
    for (const sn of snapshots(s.id)) {
      snaps++;
      bytes += sn.bytes ?? 0;
      if (!first || sn.ts < first) first = sn.ts;
      if (!last || sn.ts > last) last = sn.ts;
    }
  }
  const revs = revisions();
  return {
    sources: srcs.length,
    snapshots: snaps,
    revisions: revs.length,
    sourceChanges: revs.filter((r) => r.review.status === 'source-content').length,
    unreviewed: revs.filter((r) => r.review.status === 'unreviewed').length,
    collectionDifferences: revs.filter((r) =>
      r.review.status === 'capture-noise' || r.review.status === 'capture-correction'
    ).length,
    changes: changeLog().filter((e) => e.event === 'changed').length,
    xPosts: xPosts().length,
    nostrPosts: nostrPosts().length,
    firstCapture: first,
    lastCapture: last,
    bytes,
  };
}

/** Parse a unified diff into rendered hunks. */
export interface DiffLine { kind: 'add' | 'del' | 'ctx' | 'meta'; text: string; }
export function parseDiff(raw: string): DiffLine[] {
  return raw.split('\n').filter((l) => l.length).map((l): DiffLine => {
    if (l.startsWith('+++') || l.startsWith('---') || l.startsWith('@@')) {
      return { kind: 'meta', text: l };
    }
    if (l.startsWith('+')) return { kind: 'add', text: l.slice(1) };
    if (l.startsWith('-')) return { kind: 'del', text: l.slice(1) };
    return { kind: 'ctx', text: l.replace(/^ /, '') };
  });
}
