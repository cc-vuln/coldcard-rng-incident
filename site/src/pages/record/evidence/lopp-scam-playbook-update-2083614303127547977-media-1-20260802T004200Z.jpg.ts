import type { APIRoute } from 'astro';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export const prerender = true;

/*
 * The Telegram impersonation screenshot Jameson Lopp published on 1 August 2026.
 *
 * Served from the held artefact rather than a copy under site/public, so the
 * bytes a reader sees on /safety/scams/ are the bytes recorded in the archive
 * and hashed on the source's evidence record. Copying it would create a second
 * original that could drift from the first.
 */
export const GET: APIRoute = () => {
  const repo = process.env.ARCHIVE_ROOT
    ? resolve(process.env.ARCHIVE_ROOT)
    : resolve(process.cwd(), '..');
  const body = readFileSync(resolve(
    repo,
    'archive/x/lopp-scam-playbook-update/20260802T004200Z/attachment-1.jpg',
  ));

  return new Response(body, {
    headers: {
      'Content-Type': 'image/jpeg',
      'Cache-Control': 'public, max-age=31536000, immutable',
      'X-Content-Type-Options': 'nosniff',
    },
  });
};
