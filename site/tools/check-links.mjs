/*
 * Internal link integrity for the built site.
 *
 * Pages get merged and routes get retired; nothing else in the build notices
 * when a link, a nav entry or a generated index still points at a page that no
 * longer exists. Those are silent 404s for readers and for anyone following a
 * citation, which is the one failure this archive cannot afford.
 *
 * Checks every internal href and src in dist: the route must exist, and a
 * fragment must match an id actually present on the target page. Fragments are
 * checked because the disclosure ladder is deep-linkable by design.
 */
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join, relative, posix } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
if (!existsSync(dist)) {
  console.error('link check: no dist/ — run the build first');
  process.exit(1);
}

const files = (function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)]
  );
})(dist);

const htmlFiles = files.filter((f) => f.endsWith('.html'));
const routeOf = (f) => '/' + relative(dist, f).replace(/index\.html$/, '').replace(/\\/g, '/');
const pages = new Map(htmlFiles.map((f) => [routeOf(f), readFileSync(f, 'utf8')]));
const assets = new Set(files.map((f) => '/' + relative(dist, f).replace(/\\/g, '/')));
const idsOf = new Map();
const ids = (route) => {
  if (!idsOf.has(route)) {
    const html = pages.get(route) ?? '';
    idsOf.set(route, new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])));
  }
  return idsOf.get(route);
};

const failures = [];
const SECTION_ROOTS = ['/response/', '/how-it-broke/', '/record/'];

const sectionNavItems = (html) => {
  const navBlock = html.match(/<nav\b(?=[^>]*\baria-label="Section")[^>]*>([\s\S]*?)<\/nav>/);
  if (!navBlock) return [];
  return [...navBlock[1].matchAll(/<a\b[^>]*\bhref="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)].map((m) => ({
    href: m[1],
    label: m[2].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim(),
  }));
};

const primaryNavItems = (html) => {
  const navBlock = html.match(/<nav\b(?=[^>]*\baria-label="Primary")[^>]*>([\s\S]*?)<\/nav>/);
  if (!navBlock) return [];
  return [...navBlock[1].matchAll(/<a\b[^>]*\bhref="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)].map((m) => ({
    href: m[1],
    label: m[2].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim(),
  }));
};

// A static editorial page should never depend on an index card or a footer
// link for discovery. Every such route gets its own section-navigation entry.
// Individual source records are collection items, so /record/ is their direct
// navigation home rather than hundreds of links in the section bar.
const sectionItemsByRoot = new Map();
for (const section of SECTION_ROOTS) {
  const sectionItems = sectionNavItems(pages.get(section) ?? '');
  sectionItemsByRoot.set(section, sectionItems);
  const sectionHrefs = new Set(sectionItems.map((item) => item.href));
  const editorialRoutes = [...pages.keys()].filter((route) =>
    route.startsWith(section)
    && !route.startsWith('/record/sources/')
  );
  for (const route of editorialRoutes) {
    if (!sectionHrefs.has(route)) {
      failures.push(`section navigation: ${route} has no direct entry under ${section}`);
    }
  }
}

for (const route of pages.keys()) {
  if (!route.startsWith('/record/sources/')) continue;
  const html = pages.get(route) ?? '';
  const sourcesLocation = /<a\b(?=[^>]*\bclass="[^"]*\bsubnav__link\b[^"]*")(?=[^>]*\bhref="\/record\/")(?=[^>]*\baria-current="location")[^>]*>/;
  if (!sourcesLocation.test(html)) {
    failures.push(`section navigation: ${route} is not located under Sources`);
  }
}

// Every section is one visible reading sequence. Each page's Next card must
// follow the sub-navigation order, use the same label, and wrap from the final
// item to the first. This is deliberately checked from built HTML so custom
// layouts and ordinary Article pages are held to the same contract.
for (const [section, sectionItems] of sectionItemsByRoot) {
  for (let i = 0; i < sectionItems.length; i += 1) {
    const current = sectionItems[i];
    const expected = sectionItems[(i + 1) % sectionItems.length];
    const html = pages.get(current.href) ?? '';
    const next = html.match(/<a\b(?=[^>]*\bclass="[^"]*\bartnav__next\b[^"]*")(?=[^>]*\bhref="([^"]+)")[^>]*>([\s\S]*?)<\/a>/);
    if (!next) {
      failures.push(`section sequence: ${current.href} has no Next link under ${section}`);
      continue;
    }
    const nextLabel = next[2].match(/<span\b[^>]*\bclass="[^"]*\bartnav__label\b[^"]*"[^>]*>([\s\S]*?)<\/span>/);
    const label = nextLabel
      ? nextLabel[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim()
      : '';
    if (next[1] !== expected.href) {
      failures.push(`section sequence: ${current.href} Next points to ${next[1]}, expected ${expected.href}`);
    }
    if (label !== expected.label) {
      failures.push(`section sequence: ${current.href} Next label is "${label}", expected "${expected.label}"`);
    }
  }
}

// The timeline is the record section's front door. The source register keeps
// /record/, but a later navigation refit once silently pointed the primary
// "The record" link back there. Treat the chosen default as a route contract,
// just like the retired-route contracts below.
const overview = pages.get('/') ?? '';
const recordNav = /<a\b(?=[^>]*\bclass="[^"]*nav__link[^"]*")(?=[^>]*\bhref="\/record\/timeline\/")[^>]*>\s*The record\s*<\/a>/;
if (!recordNav.test(overview)) {
  failures.push('primary navigation: The record must link to /record/timeline/');
}

// Every reader-facing HTML page must be visible in primary or section
// navigation. The 404 page is a recovery surface, and individual source pages
// are collection items located under the Sources entry rather than peers in a
// navigation row.
const directNavHrefs = new Set(primaryNavItems(overview).map((item) => item.href));
for (const sectionItems of sectionItemsByRoot.values()) {
  for (const item of sectionItems) directNavHrefs.add(item.href);
}
for (const route of pages.keys()) {
  if (route === '/404.html' || route.startsWith('/record/sources/')) continue;
  if (!directNavHrefs.has(route)) {
    failures.push(`navigation coverage: ${route} has no direct primary or section entry`);
  }
}

for (const [route, html] of pages) {
  const body = html.replace(/<!--[\s\S]*?-->/g, '');
  for (const m of body.matchAll(/(?:href|src)="(\/[^"]*)"/g)) {
    const raw = m[1];
    if (raw.startsWith('//')) continue;
    const [pathPart, frag] = raw.split('#');
    let target = pathPart || route;
    if (assets.has(target)) continue;                 // a file: og.png, fonts, json
    if (!target.endsWith('/')) target += '/';
    if (!pages.has(target)) {
      failures.push(`${route} -> ${raw}  (no such page)`);
      continue;
    }
    if (frag && !ids(target).has(decodeURIComponent(frag))) {
      failures.push(`${route} -> ${raw}  (no such anchor on ${target})`);
    }
  }
}

/*
 * Retired routes are served by public/_redirects, which is the mechanism that
 * keeps the "every published URL still resolves" acceptance criterion true
 * after a merge. A redirect pointing at a page or anchor that does not exist
 * is the same silent 404, one hop later, so it is checked here too.
 */
const redirectsFile = fileURLToPath(new URL('../public/_redirects', import.meta.url));
if (existsSync(redirectsFile)) {
  for (const line of readFileSync(redirectsFile, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const [from, to] = t.split(/\s+/);
    if (!to || !to.startsWith('/')) continue;
    if (pages.has(from)) {
      failures.push(`_redirects: ${from} still exists as a page, so the redirect is dead`);
    }
    const [p2, f2] = to.split('#');
    let target = p2 || '/';
    // A redirect may point at a generated endpoint rather than a route:
    // sources.json, changes.json, llms.txt and the schema documents are
    // files in dist, not pages, and appending a slash to one of those
    // invents a directory that will never exist.
    const leaf = target.split('/').pop();
    if (leaf && leaf.includes('.')) {
      if (!existsSync(join(dist, target.replace(/^\//, '')))) {
        failures.push(`_redirects: ${from} -> ${to}  (no such file in dist)`);
      }
      continue;
    }
    if (!target.endsWith('/')) target += '/';
    if (!pages.has(target)) {
      failures.push(`_redirects: ${from} -> ${to}  (no such page)`);
    } else if (f2 && !ids(target).has(decodeURIComponent(f2))) {
      failures.push(`_redirects: ${from} -> ${to}  (no such anchor on ${target})`);
    }
  }
}

if (failures.length) {
  console.error(`link check FAILED: ${failures.length} broken internal link(s)`);
  for (const f of failures.slice(0, 60)) console.error('  ' + f);
  if (failures.length > 60) console.error(`  ...and ${failures.length - 60} more`);
  process.exit(1);
}
console.log(`link check ok: ${pages.size} pages, every internal link and anchor resolves`);
