import type { APIRoute } from 'astro';
import {
  displayTitle, revisions, sourceById, tsToIso,
} from '../../lib/archive';

export const prerender = true;

export const GET: APIRoute = ({ site }) => {
  const base = site ?? new URL('https://cc-vuln.org');
  const url = (path: string) => new URL(path, base).toString();
  const items = revisions().map((revision) => {
    const source = sourceById(revision.sourceId)!;
    const windowStart = tsToIso(revision.windowStart);
    const windowEnd = tsToIso(revision.windowEnd);
    return {
      id: `${revision.sourceId}@${revision.ts}`,
      url: url(`/record/sources/${revision.sourceId}/`),
      title: `${displayTitle(source)}: ${revision.review.status}`,
      content_text: `${revision.review.summary} The difference was observed within the bounded interval ${windowStart} to ${windowEnd}.`,
      date_published: revision.iso,
      tags: [revision.review.status, source.kind ?? 'source'],
      _cc_vuln: {
        source_id: revision.sourceId,
        original_url: source.url,
        observed_at: revision.iso,
        revision_window_start: windowStart,
        revision_window_end: windowEnd,
        classification: revision.review.status,
        inherited_from_wayback: revision.inherited,
        baseline_inherited_from_wayback: revision.baselineInherited,
        added_lines: revision.added,
        removed_lines: revision.removed,
      },
    };
  });

  const feed = {
    version: 'https://jsonfeed.org/version/1.1',
    title: 'cc-vuln.org source change record',
    home_page_url: url('/record/changes/'),
    feed_url: url('/record/changes.json'),
    description: 'Reviewed source-content changes, unreviewed differences and preserved collection noise. Feed timestamps are observation times; bounded revision windows are carried per item.',
    items,
  };

  return new Response(JSON.stringify(feed, null, 2) + '\n', {
    headers: {
      'Content-Type': 'application/feed+json; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
