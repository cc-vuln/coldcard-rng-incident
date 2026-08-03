/**
 * Stage captured X post screenshots into the site's public directory.
 *
 * The archive lives outside site/, so Astro cannot serve from it directly.
 * This copies the screenshots belonging to registered [[x_post]] entries into
 * site/public/x-media/ and writes a manifest the pages read at build time.
 *
 * Capturing evidence and never showing it wastes the capture: the point of a
 * screenshot is that a reader can see what was said without trusting our
 * transcription. Copies are byte-identical and the manifest carries the same
 * SHA-256 the source page cites, so what is displayed is provably the held
 * artefact.
 *
 * Three exclusions, none of them incidental:
 *
 *  1. Only element screenshots of the post itself are staged: the PNGs written
 *     by ingest-x.py directly under archive/x/. Files under gallery-dl/ and
 *     files carrying a -media- infix are media *attached to* a post, and
 *     showing an attached photo where a reader expects the post would
 *     misrepresent the capture.
 *  2. Only X post screenshots are staged. Reddit captures live under
 *     archive/reddit/ and are shown through their own source pages, not as
 *     post-card images here.
 *  3. PUBLIC_X_MEDIA=false (the default) stages nothing at all, so a public
 *     build cannot ship media until that policy decision is made deliberately.
 */
import { createHash } from 'node:crypto';
import { copyFileSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { basename, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));
const xDir = join(root, 'archive', 'x');
const outDir = fileURLToPath(new URL('../public/x-media/', import.meta.url));
const manifestPath = fileURLToPath(new URL('../src/data/x-media.json', import.meta.url));

const enabled = process.env.PUBLIC_X_MEDIA === 'true';

function registeredPosts() {
  // Deliberately not a TOML parser: the two fields needed here are flat, and
  // a regex keeps this tool independent of the site's node_modules (smol-toml
  // is there for the build), so staging works even before npm install.
  const toml = readFileSync(join(root, 'sources.toml'), 'utf8');
  const posts = [];
  for (const block of toml.split(/\n\[\[/).slice(1)) {
    if (!block.startsWith('x_post]]')) continue;
    const id = block.match(/\bid\s*=\s*"([^"]+)"/)?.[1];
    const url = block.match(/\burl\s*=\s*"([^"]+)"/)?.[1];
    if (id && url) posts.push({ id, url });
  }
  return posts;
}

/**
 * The moment the archive started capturing from its own host.
 *
 * Every capture before this was taken in a session whose signed-in account
 * cannot be cleared for publication. Those screenshots carry that session:
 * the account name in the site's navigation on a whole-window shot, and the
 * account's avatar in the reply row even on an element-only crop. Neither is
 * detectable by measuring the image, so the test is when it was taken, not
 * what it looks like.
 *
 * Re-capture a post after the cutover and it becomes publishable
 * automatically.
 */
const OWN_HOST_FROM = '20260802T150000Z';

/** Post screenshots only: <post-id>/<TS>/post.png, by path, not by name. */
function screenshotsFor(postId) {
  const dir = join(xDir, postId);
  if (!existsSync(dir)) return [];
  const out = [];
  for (const capture of readdirSync(dir, { withFileTypes: true })) {
    if (!capture.isDirectory()) continue;
    // Must look like a capture timestamp AND be at or after the cutover.
    // A plain string comparison is not enough: directories such as 'undated'
    // sort *after* any digit string, so "undated" < "20260802..." is false
    // and an undated pre-cutover capture sailed straight through.
    if (!/^\d{8}T\d{6}Z$/.test(capture.name)) continue;  // no capture time recorded
    if (capture.name < OWN_HOST_FROM) continue;             // pre-cutover capture
    const shot = join(dir, capture.name, 'post.png');
    if (existsSync(shot)) out.push({ ts: capture.name, path: shot });
  }
  // Newest capture first: the current state of a post is the one to show.
  return out.sort((a, b) => b.ts.localeCompare(a.ts));
}

rmSync(outDir, { recursive: true, force: true });
const manifest = {};
let skippedAttachments = 0;

if (enabled) {
  for (const post of registeredPosts()) {
    const mine = screenshotsFor(post.id);
    if (!mine.length) continue;
    const destDir = join(outDir, post.id);
    mkdirSync(destDir, { recursive: true });
    manifest[post.id] = mine.map(({ ts, path }) => {
      const name = `${ts}.png`;
      copyFileSync(path, join(destDir, name));
      return {
        src: `/x-media/${post.id}/${name}`,
        name,
        bytes: statSync(path).size,
        sha256: createHash('sha256').update(readFileSync(path)).digest('hex'),
        archivePath: `archive/x/${post.id}/${ts}/post.png`,
      };
    });
  }
  // Reported for transparency: these exist and are deliberately not published.
  const walk = (dir) => existsSync(dir)
    ? readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
        e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)])
    : [];
  skippedAttachments = walk(xDir).filter((p) => basename(p).startsWith('attachment-')).length;
}

mkdirSync(fileURLToPath(new URL('../src/data/', import.meta.url)), { recursive: true });
writeFileSync(manifestPath, JSON.stringify(manifest, null, 1) + '\n');

const count = Object.values(manifest).reduce((n, a) => n + a.length, 0);
const registered = registeredPosts().length;
const published = Object.keys(manifest).length;
console.log(enabled
  ? `x media staged: ${count} post screenshot(s) across ${published} of ${registered} post(s); ` +
    `${registered - published} post(s) have no capture from this host yet and are withheld; ` +
    `${skippedAttachments} attached-media file(s) and all reddit captures never staged`
  : 'x media staging disabled (PUBLIC_X_MEDIA not true); manifest emptied');
