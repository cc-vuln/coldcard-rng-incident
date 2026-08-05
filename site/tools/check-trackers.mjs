/*
 * The funds page's tracker figures must come from a held capture.
 *
 * lib/trackers.ts reads each community tracker's headline out of the newest
 * snapshot that its reader still parses. When a tracker rebuilds its page the
 * reader stops matching, and the library degrades in two steps: to an older
 * capture that still parses (`lagging`), then to the hand-checked pinned
 * figure (`pinned`). Both are honest on the page, and both are invisible in a
 * build log nobody reads.
 *
 * So the state is published as a data attribute and checked here:
 *
 *   pinned   fails. The published number is no longer read from any capture
 *            and will sit there, frozen and wrong, until somebody notices.
 *            Fix the reader, or move the pin forward deliberately
 *   lagging  warns. The figure is real and dated, just not the newest held
 *            capture, which usually means the tracker changed its page today
 *
 * A tracker that has stopped answering is NOT a failure here: the page states
 * that beside the figure, and refusing to build because a third party went
 * down would be the wrong end to fix it from.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const page = fileURLToPath(new URL('../dist/record/funds/index.html', import.meta.url));

let html;
try {
  html = readFileSync(page, 'utf8');
} catch {
  console.error(`tracker check: ${page} not built. Run the site build first.`);
  process.exit(1);
}

const found = [...html.matchAll(
  /data-tracker="([^"]+)"\s+data-tracker-state="([^"]+)"/g,
)].map(([, id, state]) => ({ id, state }));

if (!found.length) {
  console.error(
    'tracker check: the funds page published no tracker readings. Either the ' +
    'cards lost their data-tracker attributes or lib/trackers.ts returned ' +
    'nothing; both mean the page is no longer showing what it claims to.',
  );
  process.exit(1);
}

const pinned = found.filter((t) => t.state === 'pinned');
const lagging = found.filter((t) => t.state === 'lagging');

for (const t of lagging) {
  console.warn(
    `tracker check: ${t.id} is published from an older capture than the newest ` +
    'held. Its page has probably changed shape; check the reader in ' +
    'site/src/lib/trackers.ts.',
  );
}

if (pinned.length) {
  console.error(`tracker check failed (${pinned.length} pinned):`);
  for (const t of pinned) {
    console.error(
      `- ${t.id}: no held capture parses, so the page is publishing a pinned ` +
      'figure. Fix its reader in site/src/lib/trackers.ts, or move its pin ' +
      'forward against a capture you have checked by hand.',
    );
  }
  process.exit(1);
}

console.log(
  `tracker check ok: ${found.length} reading${found.length === 1 ? '' : 's'}, ` +
  `${found.length - lagging.length} from the newest held capture` +
  `${lagging.length ? `, ${lagging.length} lagging` : ''}`,
);
