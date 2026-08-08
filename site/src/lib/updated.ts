/**
 * When the site was last changed, taken from git rather than from a string
 * somebody has to remember to edit.
 *
 * One date for every page: the commit the build was made from. Per-page
 * stamps were tried first (the page file's last prose commit) and they lied
 * in both directions — a page rendering record data computed at build time
 * said "7 August" while its sections filled on the 8th, and the record moves
 * every day now that the pipeline commits hourly. The site is rebuilt
 * wholesale from the record, so the honest freshness statement is a single
 * one: this build was made from that commit.
 */
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const REPO = process.env.ARCHIVE_ROOT
  ? resolve(process.env.ARCHIVE_ROOT)
  : resolve(process.cwd(), '..');

let cache: string | null = null;

function load(): string | null {
  if (cache) return cache;
  try {
    // A review build is often made before its content changes are committed.
    // Any dirty page file means the build shows uncommitted work, so the
    // stamp is the build time; a clean build uses the commit date.
    const status = execFileSync(
      'git',
      ['status', '--porcelain=v1', '--untracked-files=all', '--', 'site/src/pages'],
      { cwd: REPO, encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 },
    ).trim();
    if (status) {
      cache = new Date().toISOString();
      return cache;
    }
    const head = execFileSync(
      'git', ['log', '-1', '--pretty=format:%cI'],
      { cwd: REPO, encoding: 'utf8', maxBuffer: 1024 * 1024 },
    ).trim();
    cache = head || null;
  } catch {
    // No git in the build environment: no stamp, rather than a confidently
    // wrong one.
    cache = null;
  }
  return cache;
}

export function lastUpdated(_pathname: string): string | null {
  return load();
}

/** "1 Aug 2026" */
export function humanDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
  });
}
