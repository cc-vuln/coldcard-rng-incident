import type { APIRoute } from 'astro';
import { meta, stats, tsToIso, xPosts } from '../lib/archive';

export const prerender = true;

export const GET: APIRoute = ({ site }) => {
  const base = site ?? new URL('https://cc-vuln.org');
  const link = (path: string) => new URL(path, base).toString();
  const archive = stats();
  const incident = meta();
  const lastCapture = archive.lastCapture ? tsToIso(archive.lastCapture) : 'none held';
  // Optional lane counts leave the document unchanged when the lane is empty.
  const nostrLine = archive.nostrPosts
    ? `Registered nostr posts: ${archive.nostrPosts}\n`
    : '';
  const xThreadCount = xPosts().filter((post) => post.thread).length;
  const xThreadLine = xThreadCount
    ? `Captured X conversations: ${xThreadCount}\n`
    : '';

  const body = `# cc-vuln.org

> The site is the public record of the July 2026 COLDCARD predictable-RNG incident: it preserves what each party published and how it changed, organises the material, and explains it without adjudicating between the people involved.

The project's historical role is to preserve the contemporaneous public record for posterity, including material later edited or removed. Published estimates of scale remain attributed to the parties that produced them; the record does not turn those estimates into its own comparative ranking.

The record also organises explanations, opinions and speculation chronologically so changes in public interpretation remain legible. Conspiracy theories alleging an inside job or that law-enforcement or intelligence agencies caused or directed the incident are dated and attributed as public reaction. Their inclusion is not evidence that they were true; later retractions, corrections and changes of view belong beside them.

Archive last capture: ${lastCapture}
Registered web sources: ${archive.sources}
Snapshots held: ${archive.snapshots}
Reviewed source-content changes: ${archive.sourceChanges}
Registered social posts: ${archive.xPosts}
${xThreadLine}${nostrLine}
## Interpretation rules

- verified: checked against source code, a repository file or a captured snapshot
- reported: attributed to the person or organisation that said it; inclusion is not endorsement
- derived: calculated from stated inputs with the method shown
- unverified: the project could not confirm the claim
- contested is a separate dispute state, not an evidence basis: parties disagree, so every position and its assumptions should be preserved
- publication time is distinct from capture time
- a revision time is normally a bounded window between the last old state and first new state held
- source-content means relevant served text changed; it does not verify the new claim
- capture-noise and capture-correction remain preserved but are not presented as editorial revisions

## Canonical human-readable pages

- [The record: source register](${link('/record/')})
- [Incident timeline](${link('/record/timeline/')})
- [Funds accounting: each source's current total, what it counts and when it was read](${link('/record/funds/')})
- [Source change record](${link('/record/changes/')})
- [Reference: primary analysis and published code](${link('/record/reference/')})
- [Firmware releases: affected ranges by model, and the release boundaries](${link('/record/firmware/')})
- [Technical reconstruction](${link('/how-it-broke/')})
- [Published candidate models and model explorer](${link('/how-it-broke/entropy/#model-explorer')})
- [Dice, passphrase and threshold-wallet conditions](${link('/how-it-broke/conditions/')})
- [Blast radius: other uses of the affected generators](${link('/how-it-broke/blast-radius/')})
- [Published responses](${link('/response/')})
- [Public statements and actions](${link('/response/statements/')})
- [Migration guidance record](${link('/response/migration/')})
- [Incident-related scams and warnings](${link('/response/scams/')})
- [Developer work: proposals, pull requests and commit reconstruction](${link('/response/developers/')})
- [Claims of AI-assisted discovery and reproduction](${link('/response/ai/')})
- [What was disclosed before: the vendor's disclosure history](${link('/response/disclosure-history/')})
- [Legal context: terms, statutes and public claims activity](${link('/response/legal/')})
- [Editorial standards and corrections](${link('/about/#editorial-standards')})
- [Collection and editorial methods, including coverage limits](${link('/methods/')})
- [How to cite this record](${link('/cite/')})
- [Corrections to this site](${link('/corrections/')})

## Machine-readable records

- [Source register JSON](${link('/record/sources.json')})
- [Source register JSON Schema](${link('/schemas/source-register-v1.json')})
- [Change feed JSON](${link('/record/changes.json')})
- [Build and record state](${link('/version.json')})
- [Sitemap](${link('/sitemap-index.xml')})

## Citation guidance

Full guidance with worked examples: ${link('/cite/')}

Cite the original publisher and link to its source URL. When a claim depends on a prior or revised state, also cite the corresponding cc-vuln.org source record so the capture time, revision window and origin of the held state can be checked. Public pages contain diffs and short excerpts; complete third-party captures remain held locally rather than republished as full mirrors.

A held capture evidences publication, not truth: it establishes what was served from a URL when this archive read it, and the claim inside it is graded separately. Carry the evidence basis across rather than restating a reported claim as verified. States marked provenance wayback were recovered from the Internet Archive and belong to it, not to this project.

This record changes. Every build publishes the commit it was made from at ${link('/version.json')}; cite that commit alongside the URL, because it is what makes a state recoverable. No DOI is minted.

## Incident registry description

${String(incident.description ?? '').trim()}
`;

  return new Response(body, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
