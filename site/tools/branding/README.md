# Brand assets: open public-record refocus

Regenerated banner/OG/social set for cc-vuln.org reflecting the refocused
framing: an open-source collection and explanation of the public record of
the COLDCARD RNG incident. Drawn in the site's newspaper register: warm
paper `#fbf0d9`, ink `#332612`, ochre accent `#a8580a`, double-rule frame,
Instrument Serif headline, IBM Plex Mono labels.

## Regenerate

    .venv/bin/python site/tools/branding/gen-brand-assets.py

Needs Pillow. Output lands in `out/` beside the script. The two assets
the site serves are copies, so publishing a change is two steps:

    cp site/tools/branding/out/og.png site/tools/branding/out/favicon.svg site/public/

`github-social.png`, `x-banner.png` and `profile.png` are uploaded by
hand to GitHub and X; nothing in the build reads them. Edit the `COPY` block at the top of
`gen-brand-assets.py` to change wording. Fonts in `fonts/` are OFL
(Instrument Serif, IBM Plex Mono), same family as `site/tools/fonts/`.

## Files and where they go

| File                 | Size      | Use |
|----------------------|-----------|-----|
| `og.png`             | 1200x630  | `site/public/og.png`, Open Graph and Twitter `summary_large_image` |
| `github-social.png`  | 1280x640  | GitHub repo social preview (repo Settings, Social preview) |
| `x-banner.png`       | 1500x500  | X/Twitter profile header (text sits right of the avatar overlay) |
| `profile.png`        | 512x512   | X/Twitter and GitHub avatar |
| `favicon.svg`        | vector    | `site/public/favicon.svg` |

## Copy

- Kicker: AN OPEN-SOURCE ARCHIVE
- Headline: The public record, collected and explained.
- Sub: Every source, snapshot, and revision of the COLDCARD RNG incident,
  openly archived. Every claim links to the record.
- Footer: PRIMARY SOURCES / SNAPSHOTS / DIFFS / SOURCED EXPLANATIONS

## Keeping the alt text honest

The banner's wording is repeated in the `og:image:alt` attribute in
`site/src/layouts/Base.astro`, because a link preview and a screen reader
should describe the same picture. If the `COPY` block below changes the
headline, change that attribute in the same commit.
