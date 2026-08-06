/**
 * The project's own corrections, read from corrections.toml at the repo root.
 *
 * Kept beside the archive layer rather than inside it because it is the one
 * record here that is about this project rather than about the incident: the
 * archive answers "what did they say and when did it change", this answers the
 * same question about us.
 */
import { readFileSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { parse as parseToml } from 'smol-toml';

const REPO = process.env.ARCHIVE_ROOT
  ? resolve(process.env.ARCHIVE_ROOT)
  : resolve(process.cwd(), '..');

export type CorrectionKind = 'correction' | 'clarification' | 'withdrawal';

export interface Correction {
  date: string;
  pages: string[];
  kind: CorrectionKind;
  summary: string;
  said?: string;
  says?: string;
  why?: string;
  credit?: string;
}

/** The day this log opened. Corrections before it are in the repo history. */
export const CORRECTIONS_SINCE = '2026-08-06';

const KINDS: CorrectionKind[] = ['correction', 'clarification', 'withdrawal'];

const KIND_LABEL: Record<CorrectionKind, string> = {
  correction: 'Correction',
  clarification: 'Clarification',
  withdrawal: 'Withdrawn',
};

export function kindLabel(kind: CorrectionKind): string {
  return KIND_LABEL[kind];
}

let cache: Correction[] | null = null;

/** Newest first. */
export function corrections(): Correction[] {
  if (cache) return cache;
  const path = join(REPO, 'corrections.toml');
  if (!existsSync(path)) return (cache = []);

  const raw = parseToml(readFileSync(path, 'utf8')) as any;
  const rows = (raw.correction ?? []) as any[];

  cache = rows
    .map((row, index) => {
      // A malformed entry fails the build rather than rendering as a blank
      // card. A corrections log that silently drops a correction is worse
      // than not having one.
      const at = `corrections.toml entry ${index + 1}`;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(String(row.date ?? ''))) {
        throw new Error(`${at}: date must be YYYY-MM-DD`);
      }
      if (!KINDS.includes(row.kind)) {
        throw new Error(`${at}: kind must be one of ${KINDS.join(', ')}`);
      }
      if (!String(row.summary ?? '').trim()) {
        throw new Error(`${at}: summary is required`);
      }
      const pages = Array.isArray(row.pages) ? row.pages.map(String) : [];
      if (pages.length === 0) {
        throw new Error(`${at}: pages must list at least one route`);
      }
      if (row.kind !== 'withdrawal' && !String(row.says ?? '').trim()) {
        throw new Error(`${at}: says is required unless the claim was withdrawn`);
      }
      return {
        date: String(row.date),
        pages,
        kind: row.kind as CorrectionKind,
        summary: String(row.summary).trim(),
        said: row.said ? String(row.said).trim() : undefined,
        says: row.says ? String(row.says).trim() : undefined,
        why: row.why ? String(row.why).trim() : undefined,
        credit: row.credit ? String(row.credit).trim() : undefined,
      } satisfies Correction;
    })
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));

  return cache;
}
