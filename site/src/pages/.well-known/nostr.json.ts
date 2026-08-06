import type { APIRoute } from 'astro';

export const prerender = true;

/*
 * NIP-05 verification for the project's nostr announcements key. The `_`
 * name is the bare-domain identifier, so a client resolving _@cc-vuln.org
 * asks for this file and compares the returned pubkey against the key it is
 * checking.
 *
 * The key comes from PUBLIC_NOSTR_PUBKEY_HEX (repo-root .env), like the other
 * PUBLIC_ build-time identity values. When it is unset (a fresh clone) the
 * endpoint still answers, with an empty names map: no key was ever
 * advertised, so there is nothing to verify, and clients treat an empty map
 * the same as an unknown name. The /cite/ page omits its nostr line in that
 * case, so the two never disagree.
 */
export const GET: APIRoute = () => {
  const pubkey = import.meta.env.PUBLIC_NOSTR_PUBKEY_HEX || '';
  const names: Record<string, string> = pubkey ? { _: pubkey } : {};
  return new Response(JSON.stringify({ names }) + '\n', {
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'public, max-age=3600',
    },
  });
};
