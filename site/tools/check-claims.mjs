/*
 * The epistemic claim-marker contract for editorial pages.
 *
 * Every editorial page must scope what it asserts: a <Claim> marker states
 * the claim (scope), its footing (basis) and its source, and verified or
 * reported claims must link to re-checkable evidence. This walks every page
 * in src/pages before a public build and fails on any marker that breaks
 * the contract, then holds the figures register in src/lib/figures.ts to
 * the same basis vocabulary.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../src/pages/', import.meta.url));
const figuresPath = fileURLToPath(new URL('../src/lib/figures.ts', import.meta.url));
const failures = [];
let claimCount = 0;
const claimFiles = new Set();
const basisCounts = { verified: 0, reported: 0, derived: 0, unverified: 0 };
let contestedCount = 0;

const markerExemptPages = new Set([
  '404.astro',
  'affected/index.astro',
  'about.astro',
  'record/changes/index.astro',
  'record/index.astro',
  'record/feed.astro',
  'record/sources/[id].astro',
]);

function filesUnder(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

function prop(tag, name) {
  return tag.match(new RegExp(`\\b${name}="([^"]*)"`))?.[1];
}

for (const file of filesUnder(root).filter((path) => extname(path) === '.astro')) {
  const input = readFileSync(file, 'utf8');
  const display = relative(root, file);
  const tags = [...input.matchAll(/<Claim\b[\s\S]*?\/>/g)];
  const starts = input.match(/<Claim\b/g)?.length ?? 0;

  if (starts !== tags.length) {
    failures.push(`${display}: every Claim marker must be a complete self-closing tag`);
  }
  if (starts === 0 && !markerExemptPages.has(display)) {
    failures.push(`${display}: editorial pages require at least one scoped evidence marker`);
  }

  for (const match of tags) {
    const tag = match[0];
    claimCount += 1;
    claimFiles.add(display);
    const line = input.slice(0, match.index).split('\n').length;
    const at = `${display}:${line}`;
    const basis = prop(tag, 'basis');
    const scope = prop(tag, 'scope');
    const source = prop(tag, 'source');
    const href = prop(tag, 'href');

    if (/\bstatus=/.test(tag)) failures.push(`${at}: use basis=, not the legacy status=`);
    if (!['verified', 'reported', 'derived', 'unverified'].includes(basis)) {
      failures.push(`${at}: basis must be verified, reported, derived or unverified`);
    } else {
      basisCounts[basis] += 1;
    }
    if (!scope?.trim()) failures.push(`${at}: scope is required`);
    if (!source?.trim()) failures.push(`${at}: source is required`);
    if (/\bcontested=/.test(tag)) failures.push(`${at}: contested is a boolean flag, not a value`);
    if (/\scontested(?:\s|\/?>)/.test(tag)) contestedCount += 1;
    if (['verified', 'reported'].includes(basis) && !href?.trim()) {
      failures.push(`${at}: ${basis} claims must link to re-checkable evidence`);
    }
  }
}

// The figures register sits outside the <Claim> pipeline, and once carried an
// invented fifth status that nothing validated. Hold it to the same contract.
{
  const figures = readFileSync(figuresPath, 'utf8');
  for (const match of figures.matchAll(/status:\s*'([^']*)'/g)) {
    const line = figures.slice(0, match.index).split('\n').length;
    if (!['verified', 'reported', 'derived', 'unverified'].includes(match[1])) {
      failures.push(
        `lib/figures.ts:${line}: figure status '${match[1]}' is not one of ` +
        `verified, reported, derived or unverified`,
      );
    }
  }
}

if (failures.length) {
  console.error(`claim check failed (${failures.length} problems):`);
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(
  `claim check ok: ${claimCount} markers across ${claimFiles.size} pages ` +
  `(${basisCounts.verified} verified, ${basisCounts.reported} reported, ` +
  `${basisCounts.derived} derived, ${basisCounts.unverified} unverified; ` +
  `${contestedCount} also contested)`,
);
