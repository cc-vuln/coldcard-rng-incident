import type { APIRoute } from 'astro';
import {
  displayTitle, kindLabel, lastPolled, revisions, snapshots, sources, stats,
  tsToIso, xArtifacts, xPosts,
} from '../../lib/archive';

export const prerender = true;

export const GET: APIRoute = () => {
  const archiveStats = stats();
  const revisionList = revisions();
  const body = {
    schema: 'https://cc-vuln.org/schemas/source-register-v1.json',
    incident: 'coldcard-entropy-2026',
    archive_last_capture: archiveStats.lastCapture
      ? tsToIso(archiveStats.lastCapture)
      : null,
    interpretation: {
      publication_time: 'When the source says it published, when established.',
      capture_time: 'When this project observed and stored a source state.',
      revision_window: 'The bounded interval between the last old state and first new state held.',
      source_content: 'Relevant text served by the publisher changed. This does not verify the new claim.',
      capture_noise: 'The detected difference came from dynamic chrome or collection mechanics.',
      unreviewed: 'The difference has not yet been reviewed for capture noise.',
      gone: 'The origin stopped serving this source. It is no longer polled, and the held capture is the only remaining copy known to this archive.',
    },
    web_sources: sources().map((source) => {
      const copies = snapshots(source.id);
      const sourceRevisions = revisionList.filter((r) => r.sourceId === source.id);
      return {
        id: source.id,
        title: displayTitle(source),
        url: source.url,
        organisation: source.org ?? null,
        kind: source.kind ?? null,
        role: kindLabel(source.kind),
        publication_time: source.published ?? null,
        note: source.note ?? null,
        gone: source.gone
          ? {
              since: source.gone_since ?? null,
              http_status: source.gone_status ?? null,
              observed: source.gone_note ?? null,
            }
          : null,
        capture: {
          status: copies.length ? 'held' : 'not-yet-held',
          copies: copies.length,
          first_observed: copies.length ? copies[0].iso : null,
          last_observed: copies.length ? copies[copies.length - 1].iso : null,
          last_checked: lastPolled(source.id)
            ? tsToIso(lastPolled(source.id)!)
            : null,
        },
        differences: sourceRevisions.map((revision) => ({
          observed_at: revision.iso,
          window_start: tsToIso(revision.windowStart),
          window_end: tsToIso(revision.windowEnd),
          status: revision.review.status,
          summary: revision.review.summary,
          inherited_from_wayback: revision.inherited,
          baseline_inherited_from_wayback: revision.baselineInherited,
          added_lines: revision.added,
          removed_lines: revision.removed,
        })),
      };
    }),
    social_posts: xPosts().map((post) => {
      const artifacts = xArtifacts(post);
      return {
        id: post.id,
        title: displayTitle(post),
        url: post.url,
        author: post.author,
        organisation: post.org ?? null,
        posted: post.posted ?? null,
        role: post.tag ?? 'social-statement',
        why_registered: post.why ?? null,
        capture: {
          status: artifacts.length ? 'held' : 'registered-only',
          artefact_count: artifacts.length,
        },
      };
    }),
  };

  return new Response(JSON.stringify(body, null, 2) + '\n', {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
