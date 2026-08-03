import type { APIRoute } from 'astro';

export const prerender = true;

export const GET: APIRoute = ({ site }) => {
  const base = site ?? new URL('https://cc-vuln.org/');
  const sitemap = new URL('sitemap-index.xml', base).href;
  // Crawling follows the same opt-in flag as the robots meta tag. A review
  // deploy on pages.dev should not be crawled at all: the noindex tag would
  // keep it out of an index anyway, but there is no reason to serve it to
  // crawlers under a hostname we do not intend to keep.
  const indexable = import.meta.env.PUBLIC_INDEXABLE === 'true';
  if (!indexable) {
    return new Response('User-agent: *\nDisallow: /\n', {
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  }

  const body = [
    'User-agent: *',
    'Allow: /',
    '',
    '# Snapshot pages show diffs and excerpts, not full mirrors.',
    '# Complete captures are held offline, not mirrored here.',
    '# Machine-readable site orientation is available at /llms.txt.',
    `Sitemap: ${sitemap}`,
    '',
  ].join('\n');

  return new Response(body, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
};
