import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';
import { TextDecoder } from 'node:util';
import { fileURLToPath } from 'node:url';

const outputRoot = fileURLToPath(new URL('../dist/', import.meta.url));
const decoder = new TextDecoder('utf-8', { fatal: true });

// Operator-identifying needles live OUTSIDE the tracked tree, in
// site/tools/private-tokens.json (gitignored), precisely so that this script
// can be published without carrying the strings it exists to catch. The file
// is an array of {label, needle, caseSensitive?} objects. It must exist: a
// deploy gate that silently runs without its operator list is not a gate.
// A public clone that has no operator details to protect creates it as [].
const privateTokensPath = fileURLToPath(
  new URL('./private-tokens.json', import.meta.url));
if (!existsSync(privateTokensPath)) {
  console.error(
    `public output check: missing ${privateTokensPath}. Create it (operator ` +
    'needles as [{"label":..., "needle":...}], or [] in a clone with nothing ' +
    'to protect) before building.');
  process.exit(1);
}
const privateTokens = JSON.parse(readFileSync(privateTokensPath, 'utf-8'));

const forbiddenTokens = [
  ...privateTokens,
  { label: '/Users/', needle: '/Users/', caseSensitive: true },
  { label: '/home/', needle: '/home/', caseSensitive: true },
  { label: '/private/tmp/', needle: '/private/tmp/', caseSensitive: true },
  { label: '.local/state', needle: '.local/state', caseSensitive: true },
  { label: 'routes.yaml', needle: 'routes.yaml' },
  { label: 'NOTIFY=', needle: 'NOTIFY=', caseSensitive: true },
  { label: 'SITE_URL', needle: 'SITE_URL', caseSensitive: true },
  { label: 'PUBLIC_', needle: 'PUBLIC_', caseSensitive: true },
  { label: 'CF_PAGES', needle: 'CF_PAGES', caseSensitive: true },
  { label: 'wrangler', needle: 'wrangler' },
  { label: 'due-state', needle: 'due-state' },
  { label: 'recurring_capture', needle: 'recurring_capture', caseSensitive: true },
  { label: 'generated_from', needle: 'generated_from', caseSensitive: true },
  { label: 'recheck_priority', needle: 'recheck_priority', caseSensitive: true },
  { label: '30-minute', needle: '30-minute' },
  { label: 'authenticated Chrome capture', needle: 'authenticated Chrome capture' },
  { label: 'captured through Chrome', needle: 'captured through Chrome' },
  { label: 'just publish', needle: 'just publish' },
  { label: 'pages.dev', needle: 'pages.dev' },
];

// A review build deliberately sets its canonical host to <project>.pages.dev,
// so every canonical, og:url and sitemap entry contains that string by design.
// Forbidding it unconditionally makes the gate and `build-preview` mutually
// exclusive: the preview cannot pass a check that rejects the hostname it was
// told to use. The token stays enforced for a publication build, which is the
// case that actually matters, since a real deploy should never mention the
// pages.dev host at all.
let previewHost = false;
try {
  const configuredHost = new URL(process.env.SITE_URL ?? '').hostname.toLowerCase();
  previewHost = configuredHost === 'pages.dev' || configuredHost.endsWith('.pages.dev');
} catch {
  // An absent or invalid SITE_URL is not permission to relax the publication gate.
}
if (previewHost) {
  const i = forbiddenTokens.findIndex((t) => t.label === 'pages.dev');
  if (i >= 0) forbiddenTokens.splice(i, 1);
}

// `localhost` is intentionally not forbidden. Local preview references are not
// evidence of a host, account or automation detail escaping into publication.

// Keep exceptions narrow and reviewable. An upstream quotation may be allowed
// only by naming its generated file, exact excerpt and expected match count.
// Never add broad file or directory exclusions here.
const allowedUpstreamExcerpts = [
  // {
  //   file: 'record/sources/example/index.html',
  //   token: 'wrangler',
  //   exactText: 'An exact, short excerpt as it appears in the generated file.',
  //   expectedMatches: 1,
  //   reason: 'Verbatim text retained from the named upstream source.',
  // },
];

// Archive hashes are useful inside the capture and audit pipeline, but a
// published hash does not authenticate who made a browser capture. Keep the
// public source records from regaining the retired integrity presentation.
// These checks are deliberately route-scoped because editorial pages discuss
// SHA-256 as part of the firmware incident itself.
const publicArchiveHashTokens = [
  {
    label: 'social artefact sha256 field',
    needle: '"sha256":',
    matchesFile: (file) => file === 'record/sources.json',
  },
  {
    label: 'source-page SHA-256 prefix',
    needle: 'SHA-256 prefix',
    matchesFile: (file) => file.startsWith('record/sources/') && file.endsWith('/index.html'),
  },
  {
    label: 'source-page text sha256',
    needle: 'text sha256',
    matchesFile: (file) => file.startsWith('record/sources/') && file.endsWith('/index.html'),
  },
  {
    label: 'source-page integrity record',
    needle: 'Integrity record',
    matchesFile: (file) => file.startsWith('record/sources/') && file.endsWith('/index.html'),
  },
];

const knownTextExtensions = new Set([
  '.atom',
  '.css',
  '.csv',
  '.htm',
  '.html',
  '.ini',
  '.js',
  '.json',
  '.map',
  '.md',
  '.mjs',
  '.rss',
  '.svg',
  '.toml',
  '.tsv',
  '.txt',
  '.webmanifest',
  '.xml',
  '.yaml',
  '.yml',
]);

const knownBinaryExtensions = new Set([
  '.avif',
  '.br',
  '.gif',
  '.gz',
  '.ico',
  '.jpeg',
  '.jpg',
  '.mp3',
  '.mp4',
  '.otf',
  '.pdf',
  '.png',
  '.ttf',
  '.wasm',
  '.webp',
  '.woff',
  '.woff2',
  '.zip',
]);

function displayPath(path) {
  return relative(outputRoot, path).split(sep).join('/');
}

function filesUnder(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name === '_astro' && entry.isDirectory()) return [];
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return filesUnder(path);
    return entry.isFile() ? [path] : [];
  });
}

function decodeTextFile(path) {
  const extension = extname(path).toLowerCase();
  if (knownBinaryExtensions.has(extension)) return null;

  const bytes = readFileSync(path);
  if (!knownTextExtensions.has(extension) && bytes.includes(0)) return null;

  let text;
  try {
    text = decoder.decode(bytes);
  } catch {
    if (knownTextExtensions.has(extension)) {
      throw new Error(`${displayPath(path)}: expected text output is not valid UTF-8`);
    }
    return null;
  }

  if (knownTextExtensions.has(extension) || extension === '') return text;

  const sample = text.slice(0, 8192);
  if (!sample) return text;
  const controls = [...sample].filter((character) => {
    const code = character.codePointAt(0);
    return code < 32 && character !== '\n' && character !== '\r' && character !== '\t';
  }).length;
  return controls / sample.length <= 0.02 ? text : null;
}

function validateAllowlist() {
  const problems = [];
  const knownLabels = new Set(forbiddenTokens.map(({ label }) => label));
  const seen = new Set();

  for (const [index, rule] of allowedUpstreamExcerpts.entries()) {
    const at = `allowlist entry ${index + 1}`;
    const key = `${rule.file}\0${rule.token}\0${rule.exactText}`;
    if (seen.has(key)) problems.push(`${at}: duplicates an earlier entry`);
    seen.add(key);

    if (
      typeof rule.file !== 'string' ||
      rule.file.startsWith('/') ||
      rule.file.split('/').includes('..') ||
      rule.file.split('/').includes('_astro')
    ) {
      problems.push(`${at}: file must be a safe generated path outside _astro`);
    }
    if (!knownLabels.has(rule.token)) problems.push(`${at}: token is not in the forbidden list`);
    if (
      typeof rule.exactText !== 'string' ||
      rule.exactText.length > 500 ||
      rule.exactText.length < String(rule.token ?? '').length + 8
    ) {
      problems.push(`${at}: exactText must add context and contain at most 500 characters`);
    } else if (
      typeof rule.token === 'string' &&
      !rule.exactText.toLowerCase().includes(rule.token.toLowerCase())
    ) {
      problems.push(`${at}: exactText does not contain the named token`);
    }
    if (!Number.isInteger(rule.expectedMatches) || rule.expectedMatches < 1 || rule.expectedMatches > 10) {
      problems.push(`${at}: expectedMatches must be an integer from 1 to 10`);
    }
    if (typeof rule.reason !== 'string' || rule.reason.trim().length < 12) {
      problems.push(`${at}: reason must explain why the upstream text is retained`);
    }

    rule.usedMatches = 0;
  }

  return problems;
}

function matchingExcerptRule(file, text, token, matchIndex) {
  for (const rule of allowedUpstreamExcerpts) {
    if (rule.file !== file || rule.token !== token.label) continue;
    if (rule.usedMatches >= rule.expectedMatches) continue;

    let excerptIndex = text.indexOf(rule.exactText);
    while (excerptIndex !== -1) {
      const excerptEnd = excerptIndex + rule.exactText.length;
      if (matchIndex >= excerptIndex && matchIndex + token.needle.length <= excerptEnd) {
        rule.usedMatches += 1;
        return rule;
      }
      excerptIndex = text.indexOf(rule.exactText, excerptIndex + 1);
    }
  }
  return null;
}

function locationOf(text, index) {
  const before = text.slice(0, index);
  const line = before.split('\n').length;
  const lastNewline = before.lastIndexOf('\n');
  return { line, column: index - lastNewline };
}

function matchesFor(text, token, file) {
  const haystack = token.caseSensitive ? text : text.toLowerCase();
  const needle = token.caseSensitive ? token.needle : token.needle.toLowerCase();
  const matches = [];
  let index = haystack.indexOf(needle);

  while (index !== -1) {
    if (!matchingExcerptRule(file, text, token, index)) matches.push(index);
    index = haystack.indexOf(needle, index + needle.length);
  }

  return matches;
}

if (!existsSync(outputRoot) || !statSync(outputRoot).isDirectory()) {
  console.error('public output check failed: site/dist is missing; build the site first');
  process.exit(1);
}

const configurationProblems = validateAllowlist();
const headersPath = join(outputRoot, '_headers');
if (!existsSync(headersPath)) {
  configurationProblems.push('site/dist/_headers is missing');
} else {
  const headers = readFileSync(headersPath, 'utf-8');
  const fontDirective = headers.match(/(?:^|;\s*)font-src\s+([^;\n]+)/m);
  const fontSources = fontDirective?.[1].trim().split(/\s+/) ?? [];
  if (!fontSources.includes("'self'")) {
    configurationProblems.push(
      "site/dist/_headers CSP must allow 'self' in font-src for the bundled fonts",
    );
  }
}
if (configurationProblems.length) {
  console.error(`public output check failed (${configurationProblems.length} configuration problem(s)):`);
  for (const problem of configurationProblems) console.error(`- ${problem}`);
  process.exit(1);
}

const findings = [];
const archiveHashFindings = [];
let scannedFiles = 0;

for (const path of filesUnder(outputRoot).sort()) {
  let text;
  try {
    text = decodeTextFile(path);
  } catch (error) {
    configurationProblems.push(error.message);
    continue;
  }
  if (text === null) continue;
  scannedFiles += 1;

  const file = displayPath(path);
  for (const token of forbiddenTokens) {
    const matches = matchesFor(text, token, file);
    if (!matches.length) continue;
    const first = locationOf(text, matches[0]);
    findings.push({
      file,
      token: token.label,
      count: matches.length,
      line: first.line,
      column: first.column,
    });
  }

  for (const token of publicArchiveHashTokens) {
    if (!token.matchesFile(file)) continue;
    const matches = matchesFor(text, token, file);
    if (!matches.length) continue;
    const first = locationOf(text, matches[0]);
    archiveHashFindings.push({
      file,
      token: token.label,
      count: matches.length,
      line: first.line,
      column: first.column,
    });
  }
}

for (const [index, rule] of allowedUpstreamExcerpts.entries()) {
  if (rule.usedMatches !== rule.expectedMatches) {
    configurationProblems.push(
      `allowlist entry ${index + 1}: expected ${rule.expectedMatches} match(es), found ${rule.usedMatches}`,
    );
  }
}

if (scannedFiles === 0) {
  configurationProblems.push('site/dist contains no text output; build the site first');
}

if (configurationProblems.length) {
  console.error(`public output check failed (${configurationProblems.length} configuration problem(s)):`);
  for (const problem of configurationProblems) console.error(`- ${problem}`);
  process.exit(1);
}

if (findings.length) {
  const groups = new Map();
  for (const finding of findings) {
    const group = groups.get(finding.token) ?? [];
    group.push(finding);
    groups.set(finding.token, group);
  }
  const totalMatches = findings.reduce((sum, finding) => sum + finding.count, 0);
  console.error(
    `public output check failed: ${totalMatches} operational token match(es) in ` +
      `${new Set(findings.map(({ file }) => file)).size} file(s)`,
  );

  for (const [token, tokenFindings] of groups) {
    const tokenMatches = tokenFindings.reduce((sum, finding) => sum + finding.count, 0);
    console.error(`- ${token}: ${tokenMatches} match(es) in ${tokenFindings.length} file(s)`);
    for (const finding of tokenFindings.slice(0, 5)) {
      const suffix = finding.count > 1 ? ` (${finding.count} matches)` : '';
      console.error(`  ${finding.file}:${finding.line}:${finding.column}${suffix}`);
    }
    if (tokenFindings.length > 5) {
      console.error(`  ... ${tokenFindings.length - 5} more file(s)`);
    }
  }
  process.exit(1);
}

if (archiveHashFindings.length) {
  const totalMatches = archiveHashFindings.reduce(
    (sum, finding) => sum + finding.count, 0);
  console.error(
    `public output check failed: ${totalMatches} retired archive-hash ` +
      'presentation match(es)',
  );
  for (const finding of archiveHashFindings) {
    const suffix = finding.count > 1 ? ` (${finding.count} matches)` : '';
    console.error(
      `- ${finding.token}: ${finding.file}:${finding.line}:${finding.column}${suffix}`,
    );
  }
  process.exit(1);
}

console.log(
  `public output check ok: ${scannedFiles} text file(s), no operational tokens ` +
    'or public archive hashes found',
);
