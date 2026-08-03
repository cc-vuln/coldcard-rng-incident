import type { APIRoute } from 'astro';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export const prerender = true;

export const GET: APIRoute = () => {
  const repo = process.env.ARCHIVE_ROOT
    ? resolve(process.env.ARCHIVE_ROOT)
    : resolve(process.cwd(), '..');
  const body = readFileSync(resolve(
    repo,
    'docs/reviews/evidence/mempool-pools-1m-20260801T055613Z.json',
  ), 'utf8');

  return new Response(body, {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=31536000, immutable',
      'X-Content-Type-Options': 'nosniff',
    },
  });
};
