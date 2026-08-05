import type { APIRoute } from 'astro';
import { meta, stats, tsToIso } from '../lib/archive';

export const prerender = true;

export const GET: APIRoute = ({ site }) => {
  const base = site ?? new URL('https://cc-vuln.org');
  const link = (path: string) => new URL(path, base).toString();
  const archive = stats();
  const incident = meta();
  const lastCapture = archive.lastCapture ? tsToIso(archive.lastCapture) : 'none held';

  const body = `# cc-vuln.org

> The site is the public record of the July 2026 COLDCARD predictable-RNG incident: it preserves what each party published and how it changed, organises the material, and explains it without adjudicating between the people involved.

Archive last capture: ${lastCapture}
Registered web sources: ${archive.sources}
Snapshots held: ${archive.snapshots}
Reviewed source-content changes: ${archive.sourceChanges}
Registered social posts: ${archive.xPosts}

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

- [Evidence index](${link('/record/')})
- [Incident timeline](${link('/record/timeline/')})
- [Funds accounting: each source's current total, what it counts and when it was read](${link('/record/funds/')})
- [Source change record](${link('/record/changes/')})
- [Reference: primary analysis and published code](${link('/record/reference/')})
- [Firmware releases: affected ranges by model, and the release boundaries](${link('/record/firmware/')})
- [Technical reconstruction](${link('/how-it-broke/')})
- [Published candidate models and model explorer](${link('/how-it-broke/entropy/#model-explorer')})
- [Dice, passphrase and threshold-wallet conditions](${link('/how-it-broke/conditions/')})
- [Published responses](${link('/response/')})
- [Public statements and actions](${link('/response/statements/')})
- [Migration guidance record](${link('/response/migration/')})
- [Incident-related scams and warnings](${link('/response/scams/')})
- [Editorial standards and corrections](${link('/about/#editorial-standards')})

## Machine-readable records

- [Source register JSON](${link('/record/sources.json')})
- [Source register JSON Schema](${link('/schemas/source-register-v1.json')})
- [Change feed JSON](${link('/record/changes.json')})
- [Sitemap](${link('/sitemap-index.xml')})

## Citation guidance

Cite the original publisher and link to its source URL. When a claim depends on a prior or revised state, also cite the corresponding cc-vuln.org source record so the capture time, revision window and origin of the held state can be checked. Public pages contain diffs and short excerpts; complete third-party captures remain held locally rather than republished as full mirrors.

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
