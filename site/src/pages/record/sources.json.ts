import type { APIRoute } from 'astro';
import {
  displayTitle, kindLabel, lastPolled, nostrCaptures, nostrPosts, revisions,
  pollHealth, snapshots, sources, stats, tsToIso, xArtifacts, xPosts,
} from '../../lib/archive';
import { threadCapture } from '../../lib/x-thread';

export const prerender = true;

const tally = (values: string[]): Record<string, number> => {
  const counts = new Map<string, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return Object.fromEntries([...counts].sort(([a], [b]) => a.localeCompare(b)));
};

export const GET: APIRoute = () => {
  const archiveStats = stats();
  const revisionList = revisions();
  const sourceList = sources();
  const xPostList = xPosts();
  const nostrPostList = nostrPosts();
  const revisionsBySource = new Map<string, typeof revisionList>();
  for (const revision of revisionList) {
    const grouped = revisionsBySource.get(revision.sourceId) ?? [];
    grouped.push(revision);
    revisionsBySource.set(revision.sourceId, grouped);
  }
  const sourceCopies = new Map(
    sourceList.map((source) => [source.id, snapshots(source.id)]),
  );
  const publicationDates = [
    ...sourceList.map((source) => source.published ?? ''),
    ...xPostList.map((post) => post.posted ?? ''),
    ...nostrPostList.map((post) => post.posted ?? ''),
  ].filter(Boolean).sort();
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
    coverage: {
      denominators: {
        web_sources: sourceList.length,
        x_posts: xPostList.length,
        nostr_posts: nostrPostList.length,
      },
      web_sources_by_kind: tally(
        sourceList.map((source) => source.kind ?? 'unspecified'),
      ),
      web_sources_by_organisation: tally(
        sourceList.map((source) => source.org ?? 'unspecified'),
      ),
      social_posts_by_platform: {
        nostr: nostrPostList.length,
        x: xPostList.length,
      },
      social_posts_by_organisation: tally([
        ...xPostList.map((post) => post.org ?? 'unspecified'),
        ...nostrPostList.map((post) => post.org ?? 'unspecified'),
      ]),
      web_sources_by_current_poll_state: tally(
        sourceList.map((source) => pollHealth(source.id).state),
      ),
      web_sources_by_capture_count: tally(
        sourceList.map((source) => {
          const count = sourceCopies.get(source.id)!.length;
          return count === 0 ? 'zero' : count === 1 ? 'one' : 'multiple';
        }),
      ),
      publication_date_range: {
        known_items: publicationDates.length,
        earliest: publicationDates[0] ?? null,
        latest: publicationDates[publicationDates.length - 1] ?? null,
      },
      capture_date_range: {
        first: archiveStats.firstCapture
          ? tsToIso(archiveStats.firstCapture)
          : null,
        last: archiveStats.lastCapture
          ? tsToIso(archiveStats.lastCapture)
          : null,
        snapshots: archiveStats.snapshots,
      },
    },
    web_sources: sourceList.map((source) => {
      const copies = sourceCopies.get(source.id)!;
      const sourceRevisions = revisionsBySource.get(source.id) ?? [];
      const checked = lastPolled(source.id);
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
          last_checked: checked ? tsToIso(checked) : null,
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
    social_posts: [
      ...xPostList.map((post) => {
        const artifacts = xArtifacts(post);
        const conversationCopies = post.thread ? snapshots(post.id) : [];
        const conversation = post.thread ? threadCapture(post.id) : null;
        return {
          id: post.id,
          title: displayTitle(post),
          url: post.url,
          author: post.author,
          platform: 'x',
          organisation: post.org ?? null,
          posted: post.posted ?? null,
          role: post.tag ?? 'social-statement',
          why_registered: post.why ?? null,
          relation: {
            kind: post.thread
              ? 'conversation-head'
              : post.part_of
                ? 'conversation-member'
                : 'single-post',
            head_id: post.part_of ?? null,
          },
          capture: {
            status: artifacts.length || conversationCopies.length ? 'held' : 'registered-only',
            artefact_count: artifacts.length,
            conversation_copies: conversationCopies.length,
            conversation_posts: conversation?.posts.length ?? 0,
            conversation_replies: conversation?.counts.replies ?? 0,
            conversation_gaps: conversation?.gaps ?? [],
          },
        };
      }),
      // Nostr posts join the same array rather than a parallel one: a
      // consumer that already reads social posts should see them, and the
      // platform field says which lane each came from. author is the npub.
      ...nostrPostList.map((post) => {
        const held = nostrCaptures(post).length;
        return {
          id: post.id,
          title: displayTitle(post),
          url: post.url,
          author: post.author,
          platform: 'nostr',
          organisation: post.org ?? null,
          posted: post.posted ?? null,
          role: post.tag ?? 'social-statement',
          why_registered: post.why ?? null,
          relation: { kind: 'single-post', head_id: null },
          capture: {
            status: held ? 'held' : 'registered-only',
            artefact_count: held,
          },
        };
      }),
    ],
  };

  return new Response(JSON.stringify(body, null, 2) + '\n', {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
