/**
 * The state this build is, as data.
 *
 * Cited work needs a resolvable version. This is the machine-readable half of
 * /cite/: the commit that produced the pages a reader is looking at, and the
 * size and freshness of the record at that commit, so an "accessed on" date in
 * somebody's footnote can be turned back into a specific state of the archive.
 */
import type { APIRoute } from 'astro';
import { meta, stats, tsToIso } from '../lib/archive';
import { buildVersion } from '../lib/version';

export const prerender = true;

export const GET: APIRoute = ({ site }) => {
  const base = site ?? new URL('https://cc-vuln.org');
  const link = (path: string) => new URL(path, base).toString();
  const archive = stats();
  const version = buildVersion();

  const body = {
    site: 'cc-vuln.org',
    incident: meta().incident ?? 'coldcard-entropy-2026',
    build: {
      commit: version.commit,
      commit_time: version.commitTime,
      tag: version.tag,
      matches_commit: version.matchesCommit,
      built: version.built,
    },
    record: {
      web_sources: archive.sources,
      social_posts: archive.xPosts,
      nostr_posts: archive.nostrPosts,
      snapshots: archive.snapshots,
      held_revisions: archive.revisions,
      reviewed_source_changes: archive.sourceChanges,
      first_capture: archive.firstCapture ? tsToIso(archive.firstCapture) : null,
      last_capture: archive.lastCapture ? tsToIso(archive.lastCapture) : null,
    },
    citation: {
      guidance: link('/cite/'),
      attribution: 'cc-vuln.org',
      corrections: link('/corrections/'),
      source_register: link('/record/sources.json'),
      change_feed: link('/record/changes.json'),
      repository: 'https://github.com/cc-vuln/coldcard-rng-incident',
      content_license: 'https://creativecommons.org/licenses/by/4.0/',
      code_license: 'https://spdx.org/licenses/MIT.html',
      third_party_material:
        'Captured material remains its authors’ copyright and is neither licence above.',
    },
    interpretation: {
      commit:
        'The repository state these pages were built from. Quote it when citing this record, so the state read can be recovered.',
      matches_commit:
        'True when every tracked file matched that commit at build time. False means the build carried edits the commit does not contain.',
      built:
        'When this build ran. Later than the commit time by however long the build was deferred.',
      last_capture:
        'The newest source state held anywhere in the archive at build time. It is not a promise that every source was reachable then; per-source poll health is in the source register.',
    },
  };

  return new Response(JSON.stringify(body, null, 2) + '\n', {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
