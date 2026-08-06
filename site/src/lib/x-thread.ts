/**
 * Reading a captured X conversation for display.
 *
 * capture.py's `x-thread` method writes two things per capture: the canonical
 * text, which is what the archive diffs and reviews, and a structured record
 * beside it. This reads the structured record rather than re-parsing the text,
 * because the text's block delimiter is a convention and the record is data.
 *
 * Two separate decisions live here and must not be confused, which is why they
 * are separate functions:
 *
 *   withholding  removes a post's text and image from the page entirely.
 *                A publication decision, made in the registry, answered by
 *                withholdsPost() in archive.ts.
 *   muting       de-emphasises a low-signal reply and keeps it on the page,
 *                one click from its screenshot. A presentation decision, made
 *                here, from mechanical properties of the post only.
 *
 * Muting never reads what a reply argues or whether this project agrees with
 * it. Every predicate is length, emptiness, shape or duplication, and the page
 * prints which one fired so a reader can disagree with the rule. It is
 * computed at build time and never written into the archive: every reply is
 * held identically, and changing the rule re-renders without a re-capture.
 *
 * See docs/design/capture-display-policy.md sections 1a and 1b.
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import {
  isoToHuman, tsToIso, withholdsCapturedMedia, withholdsPost, xPostById, xPosts,
  type XPost,
} from './archive';
import threadMediaManifest from '../data/x-thread-media.json';

const REPO = process.env.ARCHIVE_ROOT
  ? resolve(process.env.ARCHIVE_ROOT)
  : resolve(process.cwd(), '..');
const SNAPSHOTS = join(REPO, 'archive', 'snapshots');

/** Below this many characters a reply is applause, not evidence.
 *  Calibrated 6 Aug 2026 on the clay_garrett thread, where the applause ran
 *  14 to 30 characters and the substantive replies 95 to 295. Stated on the
 *  page rather than tuned quietly. */
export const MUTE_SHORT_CHARS = 40;

/** How many replies render before the reader asks for more. */
export const REPLY_REVEAL = 12;

export type ThreadRole = 'ancestor' | 'focal' | 'self-thread' | 'reply';
export type Band = 'foreground' | 'standard' | 'muted';

export interface ThreadPost {
  status: string;
  role: ThreadRole;
  author: string;
  name: string | null;
  created: string | null;
  createdHuman: string | null;
  media: number;
  text: string;
  url: string;
  band: Band;
  /** Why this post is muted, or why it is foregrounded. Shown on the page. */
  reason: string | null;
  shot: { src: string; captured: string; capturedHuman: string } | null;
}

export interface ThreadCapture {
  id: string;
  ts: string;
  iso: string;
  url: string;
  author: string;
  posts: ThreadPost[];
  gaps: string[];
  depth: Record<string, unknown>;
  counts: { ancestors: number; selfThread: number; replies: number; muted: number };
}

const byPost = threadMediaManifest as Record<
  string, Record<string, { src: string; name: string; captured: string }>
>;

/** Every handle registered anywhere in this project's own source register. */
let _registered: Set<string> | null = null;
function registeredAuthors(): Set<string> {
  if (_registered) return _registered;
  _registered = new Set(
    xPosts().map((p) => (p.author ?? '').toLowerCase()).filter(Boolean),
  );
  return _registered;
}

/** The newest structured record held for a thread source, if any. */
function newestRecord(id: string): { ts: string; record: any } | null {
  const dir = join(SNAPSHOTS, id);
  if (!existsSync(dir)) return null;
  const files = readdirSync(dir)
    .filter((f) => f.endsWith('.json') && !f.endsWith('.meta.json'))
    .sort();
  for (const file of files.reverse()) {
    try {
      const record = JSON.parse(readFileSync(join(dir, file), 'utf8'));
      if (Array.isArray(record?.posts)) {
        return { ts: file.replace(/\.json$/, ''), record };
      }
    } catch {
      // A record that will not parse is not a reason to show nothing; fall
      // through to the one before it.
    }
  }
  return null;
}

function normaliseText(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

/**
 * Why this reply is low signal, or null if it is not.
 *
 * Mechanical only. No sentiment, no keywords, no agreement with anything the
 * site says, and no property of the author beyond presence in this project's
 * own register, which only ever promotes and never mutes.
 */
function muteReason(post: any, duplicates: Set<string>): string | null {
  const text = normaliseText(post.text ?? '');
  if (!text) return 'no text captured';
  if (duplicates.has(text)) return 'identical to another reply in this capture';
  if (/^(@\w+\s*)+$/.test(text)) return 'mentions only';
  if (!/[\p{L}\p{N}]/u.test(text)) return 'no letters or digits';
  if (/^https?:\/\/\S+$/.test(text)) return 'a bare link';
  if (text.length < MUTE_SHORT_CHARS) return `under ${MUTE_SHORT_CHARS} characters`;
  return null;
}

/**
 * Read the newest capture of one thread for display.
 *
 * Returns null when the post is not a thread source, has no capture yet, or
 * is withheld: a withheld source renders neither text nor media, and a
 * conversation is no exception.
 */
export function threadCapture(id: string): ThreadCapture | null {
  const post = xPostById(id);
  if (!post?.thread) return null;
  if (withholdsCapturedMedia(post)) return null;
  const held = newestRecord(id);
  if (!held) return null;

  const raw: any[] = held.record.posts ?? [];
  const focalAuthor = (held.record.author ?? '').toLowerCase();
  const staged = byPost[id] ?? {};

  // Duplicate detection needs the whole set first: bot amplification is only
  // visible by comparing replies against each other.
  const seen = new Map<string, number>();
  for (const p of raw) {
    if (p.role !== 'reply') continue;
    const t = normaliseText(p.text ?? '');
    if (t) seen.set(t, (seen.get(t) ?? 0) + 1);
  }
  const duplicates = new Set(
    [...seen.entries()].filter(([, n]) => n > 1).map(([t]) => t),
  );

  const posts: ThreadPost[] = [];
  for (const p of raw) {
    const status = String(p.status ?? '');
    // Publication decision first: a withheld post contributes nothing, not
    // its text, not its image, not a muted stub of itself.
    if (withholdsPost(post as XPost, status)) continue;

    const role: ThreadRole = p.role ?? 'reply';
    const author = String(p.author ?? '');
    let band: Band = 'standard';
    let reason: string | null = null;

    if (role !== 'reply') {
      band = 'foreground';
    } else if (author.toLowerCase() === focalAuthor) {
      // The author answering questions in their own thread is usually the
      // most load-bearing material in it.
      band = 'foreground';
      reason = 'the thread author answering in their own thread';
    } else if (registeredAuthors().has(author.toLowerCase())) {
      // A lookup in this project's register, not a judgement of merit.
      band = 'foreground';
      reason = 'this account is registered elsewhere in the record';
    } else {
      const muted = muteReason(p, duplicates);
      if (muted) { band = 'muted'; reason = muted; }
    }

    const shotEntry = staged[status];
    posts.push({
      status,
      role,
      author,
      name: p.name ?? null,
      created: p.created ?? null,
      createdHuman: p.created ? isoToHuman(p.created) : null,
      media: Number(p.media ?? 0),
      text: String(p.text ?? ''),
      url: `https://x.com/${author}/status/${status}`,
      band,
      reason,
      shot: shotEntry
        ? {
            src: shotEntry.src,
            captured: shotEntry.captured,
            capturedHuman: isoToHuman(shotEntry.captured),
          }
        : null,
    });
  }

  return {
    id,
    ts: held.ts,
    iso: tsToIso(held.ts),
    url: held.record.url ?? post.url,
    author: held.record.author ?? post.author,
    posts,
    gaps: held.record.gaps ?? [],
    depth: held.record.depth ?? {},
    counts: {
      ancestors: posts.filter((p) => p.role === 'ancestor').length,
      selfThread: posts.filter((p) => p.role === 'self-thread').length,
      replies: posts.filter((p) => p.role === 'reply').length,
      muted: posts.filter((p) => p.band === 'muted').length,
    },
  };
}

/** Whether a registered post holds a captured conversation worth rendering. */
export function hasThread(id: string): boolean {
  return threadCapture(id) !== null;
}
