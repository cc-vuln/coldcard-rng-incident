/**
 * Data source for the homepage record band.
 *
 * The band exists to show the archive rather than describe it, so this
 * assembles the n most recent events a front-door reader should see: sources
 * entering the record, reviewed changes to published source content, and
 * registered X posts. It is a deliberately minimal sibling of the fuller
 * assembly in src/pages/record/feed.astro, not an import from it: pages are
 * not libraries, and the band needs far less than the feed carries.
 *
 * Two narrowing decisions, both on purpose:
 * - only `source-content` revisions qualify. Collection noise and unreviewed
 *   differences belong on /record/changes/, not on the front door
 * - a thumbnail is offered only when a staged screenshot exists AND the
 *   source does not withhold captured media. Staging is a provenance check;
 *   withholding is the publication decision, and both must pass
 */
import {
  revisions, snapshots, sources, xPosts, displayTitle, tsToIso,
  withholdsCapturedMedia,
} from './archive';
import { xMedia } from './xmedia';

export interface LatestEvent {
  /** Registry id of the source or post the event belongs to. */
  id: string;
  kind: 'first-capture' | 'source-change' | 'post';
  /** Display title of the source or post. */
  title: string;
  /** Attribution: the organisation, or @author for an X post. */
  by: string;
  /** UTC timestamp of the event, ISO 8601. */
  iso: string;
  /** The source page, where the artefacts and hashes live. */
  href: string;
  /** Staged screenshot path, or null when none may be shown. */
  thumb: string | null;
}

/** The n most recent record events, newest first. */
export function latestEvents(n: number): LatestEvent[] {
  const srcById = new Map(sources().map((s) => [s.id, s]));
  const events: LatestEvent[] = [];

  // Reviewed changes to published source content: the archive's reason to exist.
  // Chain monitors are excluded: their numbers change every poll by nature, so
  // they would permanently occupy every slot and the band would never show a
  // capture. Their churn belongs on /record/changes/.
  for (const rev of revisions()) {
    if (rev.review.status !== 'source-content') continue;
    const src = srcById.get(rev.sourceId);
    if (!src) continue;
    if (src.kind === 'chain-monitor') continue;
    events.push({
      id: src.id,
      kind: 'source-change',
      title: displayTitle(src),
      by: src.org ?? '',
      iso: tsToIso(rev.ts),
      href: `/record/sources/${src.id}/`,
      thumb: null,
    });
  }

  // First capture of each source: when it entered the record.
  for (const src of sources()) {
    const snaps = snapshots(src.id);
    if (!snaps.length) continue;
    events.push({
      id: src.id,
      kind: 'first-capture',
      title: displayTitle(src),
      by: src.org ?? '',
      iso: snaps[0].iso,
      href: `/record/sources/${src.id}/`,
      thumb: null,
    });
  }

  // Registered X posts, dated by when they were posted, with the held
  // screenshot where one is staged and not withheld.
  for (const post of xPosts()) {
    if (!post.posted) continue;
    const shots = withholdsCapturedMedia(post) ? [] : xMedia(post.id);
    events.push({
      id: post.id,
      kind: 'post',
      title: displayTitle(post),
      by: `@${post.author}`,
      iso: post.posted,
      href: `/record/sources/${post.id}/`,
      thumb: shots[0]?.src ?? null,
    });
  }

  const sorted = events
    .filter((e) => e.iso)
    .sort((a, b) => (a.iso < b.iso ? 1 : a.iso > b.iso ? -1 : 0));

  // The band exists to show the archive, so at least two slots should carry
  // a capture image when the archive holds any. Take the newest n, then swap
  // the oldest imageless entries for the newest staged posts not already
  // chosen, and restore newest-first order.
  const chosen = sorted.slice(0, n);
  const spare = sorted.slice(n).filter((e) => e.thumb);
  const wanted = Math.min(2, n);
  while (chosen.filter((e) => e.thumb).length < wanted && spare.length) {
    const swapOut = [...chosen].reverse().find((e) => !e.thumb);
    if (!swapOut) break;
    chosen.splice(chosen.indexOf(swapOut), 1, spare.shift()!);
  }
  return chosen.sort((a, b) => (a.iso < b.iso ? 1 : a.iso > b.iso ? -1 : 0));
}
