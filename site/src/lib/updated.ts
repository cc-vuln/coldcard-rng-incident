/**
 * When each page was last changed, taken from git rather than from a string
 * somebody has to remember to edit.
 *
 * A hand-maintained "updated" line is wrong the moment anyone forgets it, and on
 * a site whose whole argument is that it records when things change, a stale
 * date is worse than none. Git already knows exactly when every page last
 * changed, so this reads it once per build.
 *
 * Deliberately per-file: editing the layout or a shared component does not bump
 * every page in the site, because that would tell the reader something false
 * about the page they are looking at.
 *
 * One floor, added 8 Aug 2026: pages that RENDER the record (they import
 * lib/archive, lib/xmedia, lib/corrections or the tracker/figure data) change
 * whenever the record changes, because whole sections are computed from the
 * registry and archive at build time. A timeline whose "Also on 8 August"
 * sections filled overnight is not a page last updated on the 7th, whatever
 * its prose file's history says. Such pages take the later of their own
 * commit date and the newest commit touching the record inputs.
 */
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { readFileSync, readdirSync, statSync } from 'node:fs';

const REPO = process.env.ARCHIVE_ROOT
  ? resolve(process.env.ARCHIVE_ROOT)
  : resolve(process.cwd(), '..');

/** Map of route path -> ISO 8601 commit date. */
let cache: Map<string, string> | null = null;

/** src/pages/how-it-broke/entropy.astro -> /how-it-broke/entropy/ , src/pages/index.astro -> / */
function fileToRoute(file: string): string | null {
  const m = file.match(/^site\/src\/pages\/(.*)\.astro$/);
  if (!m) return null;
  let p = m[1];
  if (p.includes('[')) return null;      // dynamic routes get their own handling
  p = p.replace(/(^|\/)index$/, '$1');   // index.astro is the directory itself
  return '/' + (p ? p.replace(/\/$/, '') + '/' : '');
}

function load(): Map<string, string> {
  if (cache) return cache;
  const out = new Map<string, string>();
  try {
    // One walk of the history, newest first. The first time a file appears is
    // its most recent change, so later occurrences are ignored.
    const log = execFileSync(
      'git',
      ['log', '--pretty=format:%cI', '--name-only', '--', 'site/src/pages'],
      { cwd: REPO, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 },
    );
    let when = '';
    for (const line of log.split('\n')) {
      const t = line.trim();
      if (!t) continue;
      if (/^\d{4}-\d{2}-\d{2}T/.test(t)) { when = t; continue; }
      const route = fileToRoute(t);
      if (route && when && !out.has(route)) out.set(route, when);
    }
  } catch {
    // No git in the build environment: fall back to build time rather than
    // failing the build or, worse, printing a confidently wrong date.
  }

  try {
    // A review build is often made before its content changes are committed.
    // Mark dirty page routes with the build time so their public update label
    // does not point at the previous commit. Clean publication builds continue
    // to use the stable commit timestamp above.
    const dirtyAt = new Date().toISOString();
    const status = execFileSync(
      'git',
      ['status', '--porcelain=v1', '--untracked-files=all', '--', 'site/src/pages'],
      { cwd: REPO, encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 },
    );
    for (const line of status.split('\n')) {
      if (line.length < 4) continue;
      const file = line.slice(3).split(' -> ').at(-1) ?? '';
      const route = fileToRoute(file);
      if (route) out.set(route, dirtyAt);
    }
  } catch {
    // The commit-derived dates above remain usable if status is unavailable.
  }
  try {
    // The record-data floor. One commit date for the inputs the data-driven
    // pages render from; then every page file that imports those libs takes
    // the later of its own date and this one.
    const floor = execFileSync(
      'git',
      ['log', '-1', '--pretty=format:%cI', '--',
        'sources.toml', 'revision-reviews.toml', 'corrections.toml',
        'archive', 'site/src/data', 'site/src/lib'],
      { cwd: REPO, encoding: 'utf8', maxBuffer: 1024 * 1024 },
    ).trim();
    if (floor) {
      const pagesDir = resolve(REPO, 'site/src/pages');
      const stack = [pagesDir];
      while (stack.length) {
        const dir = stack.pop()!;
        for (const name of readdirSync(dir)) {
          const full = resolve(dir, name);
          if (statSync(full).isDirectory()) { stack.push(full); continue; }
          if (!name.endsWith('.astro')) continue;
          const body = readFileSync(full, 'utf8');
          if (!/lib\/(archive|xmedia|corrections|trackers|figures|x-thread)/.test(body)) continue;
          const route = fileToRoute(`site/src/pages/${full.slice(pagesDir.length + 1)}`);
          if (!route) continue;
          const own = out.get(route);
          if (!own || Date.parse(own) < Date.parse(floor)) out.set(route, floor);
        }
      }
    }
  } catch {
    // The per-file dates above remain usable if the floor cannot be read.
  }

  cache = out;
  return out;
}

export function lastUpdated(pathname: string): string | null {
  const p = pathname.endsWith('/') ? pathname : pathname + '/';
  return load().get(p) ?? null;
}

/** "1 Aug 2026" */
export function humanDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  });
}
