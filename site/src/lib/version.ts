/**
 * What state of the record a given build is.
 *
 * A citation to a living archive is worthless if the reader cannot say which
 * state they read. "Accessed 6 August 2026" only resolves to something if the
 * project publishes what it was serving that day, so every build stamps the
 * commit it was made from in the footer and at /version.json, and the citation
 * guidance tells people to quote it.
 *
 * Read from git for the same reason lib/updated.ts is: a hand-maintained
 * version string is wrong the moment somebody forgets it, and on this site a
 * stale one would be worse than none.
 */
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const REPO = process.env.ARCHIVE_ROOT
  ? resolve(process.env.ARCHIVE_ROOT)
  : resolve(process.cwd(), '..');

export interface BuildVersion {
  /** Full commit hash, or null where git was not available at build time. */
  commit: string | null;
  /** First 12 characters: enough to name a commit, short enough to print. */
  short: string | null;
  /** ISO 8601 commit date. */
  commitTime: string | null;
  /** Nearest release tag, once the project starts tagging releases. */
  tag: string | null;
  /**
   * Whether every tracked file matched the commit above when this was built.
   *
   * False means the build carried edits that are not in the named commit, so
   * the commit does not fully reproduce what a reader is looking at. Untracked
   * files are ignored deliberately: a poll that lands a new capture between a
   * commit and a build is normal here and does not change the published text.
   */
  matchesCommit: boolean | null;
  /** ISO 8601 build time. */
  built: string;
}

let cache: BuildVersion | null = null;

function git(args: string[]): string | null {
  try {
    return execFileSync('git', args, {
      cwd: REPO,
      encoding: 'utf8',
      maxBuffer: 4 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return null;
  }
}

export function buildVersion(): BuildVersion {
  if (cache) return cache;

  const commit = git(['rev-parse', 'HEAD']) || null;
  const status = git(['status', '--porcelain=v1', '--untracked-files=no']);

  cache = {
    commit,
    short: commit ? commit.slice(0, 12) : null,
    commitTime: git(['log', '-1', '--pretty=format:%cI']) || null,
    tag: git(['describe', '--tags', '--abbrev=0']) || null,
    // null rather than true when git could not answer: an unknown state must
    // not be published as a clean one.
    matchesCommit: status === null ? null : status.length === 0,
    built: new Date().toISOString(),
  };
  return cache;
}
