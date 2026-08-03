#!/usr/bin/env python3
"""Generate cc-vuln.org brand assets (refocus: open-source public record).

Draws in the site's newspaper register: warm paper, ochre accent,
Instrument Serif headline, IBM Plex Mono labels, double-rule frame.

Outputs (into ./out):
  og.png             1200x630   Open Graph / Twitter summary_large_image
  github-social.png  1280x640   GitHub repo social preview
  x-banner.png       1500x500   X/Twitter profile header
  profile.png         512x512   avatar / profile picture
  favicon.svg                   matching site favicon

Copy: headline and labels reflect the refocused framing of the site as
an open-source collection and explanation of the public record of the
COLDCARD RNG incident. Edit the COPY block below and re-run.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
FONTS = ROOT / "fonts"
OUT = ROOT / "out"

# Palette (site tokens)
PAPER = (251, 240, 217)      # --paper: warm paper
INK = (51, 38, 18)           # dark brown ink
MUTED = (138, 133, 122)      # --ink-3
ACCENT = (168, 88, 10)       # --accent: ochre

COPY = {
    "wordmark": "CC-VULN.ORG",
    "dateline": "SINCE JULY 2026",
    "kicker": "AN OPEN-SOURCE ARCHIVE",
    "headline": ["The public record,", "collected and explained."],
    "sub": ("Every source, snapshot, and revision of the COLDCARD RNG "
            "incident, openly archived. Every claim links to the record."),
    "footer": "PRIMARY SOURCES  /  SNAPSHOTS  /  DIFFS  /  SOURCED EXPLANATIONS",
}


def serif(size, italic=False):
    name = "InstrumentSerif-Italic.ttf" if italic else "InstrumentSerif-Regular.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


def mono(size, medium=False):
    name = "IBMPlexMono-Medium.ttf" if medium else "IBMPlexMono-Regular.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


def frame(d, w, h, inset, ink=INK):
    """Newspaper double rule: thick outer line, thin inner line."""
    d.rectangle([inset, inset, w - inset, h - inset], outline=ink, width=6)
    g = 12
    d.rectangle([inset + g, inset + g, w - inset - g, h - inset - g],
                outline=ink, width=2)


def masthead(d, x, y, scale=1.0):
    """Brand mark + wordmark left, dateline right. Returns height used."""
    s = int(26 * scale)
    d.rounded_rectangle([x, y, x + s, y + s], radius=int(6 * scale),
                        fill=ACCENT)
    f_word = mono(int(24 * scale), medium=True)
    d.text((x + s + int(14 * scale), y + int(1 * scale)), COPY["wordmark"],
           font=f_word, fill=INK)
    return s


def dateline(d, w, x_right, y, scale=1.0):
    f = mono(int(20 * scale))
    tw = d.textlength(COPY["dateline"], font=f)
    d.text((x_right - tw, y + int(5 * scale)), COPY["dateline"], font=f,
           fill=MUTED)


def wrap(d, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def gen_og(path, w, h, headline_size, kicker_size, sub_size):
    img = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(img)
    inset = int(w * 0.028)
    frame(d, w, h, inset)

    pad = inset + int(w * 0.045)
    scale = w / 1200

    # Masthead
    my = pad
    mh = masthead(d, pad, my, scale)
    dateline(d, w, w - pad, my, scale)

    # Rule under masthead
    ry = my + mh + int(28 * scale)
    d.line([pad, ry, w - pad, ry], fill=INK, width=max(2, int(3 * scale)))

    # Kicker
    ky = ry + int(32 * scale)
    f_kick = mono(kicker_size, medium=True)
    d.text((pad, ky), COPY["kicker"], font=f_kick, fill=ACCENT)
    # small rule flourish after kicker
    kw = d.textlength(COPY["kicker"], font=f_kick)
    d.line([pad + kw + 18, ky + kicker_size // 2 + 2, pad + kw + 18 + 70,
            ky + kicker_size // 2 + 2], fill=ACCENT, width=3)

    # Headline
    hy = ky + kicker_size + int(26 * scale)
    f_head = serif(headline_size)
    for i, line in enumerate(COPY["headline"]):
        d.text((pad - int(headline_size * 0.02),
                hy + i * int(headline_size * 1.06)),
               line, font=f_head, fill=INK)

    # Sub
    sy = hy + len(COPY["headline"]) * int(headline_size * 1.06) + int(24 * scale)
    f_sub = mono(sub_size)
    for i, line in enumerate(wrap(d, COPY["sub"], f_sub, w - 2 * pad)):
        d.text((pad, sy + i * int(sub_size * 1.5)), line, font=f_sub,
               fill=MUTED)

    # Footer
    f_foot = mono(max(14, int(17 * scale)), medium=True)
    fw = d.textlength(COPY["footer"], font=f_foot)
    fy = h - pad - int(4 * scale)
    d.line([pad, fy - int(24 * scale), w - pad, fy - int(24 * scale)],
           fill=INK, width=2)
    d.text(((w - fw) / 2, fy), COPY["footer"], font=f_foot, fill=INK)

    img.save(path, optimize=True)


def gen_x_banner(path, w=1500, h=500):
    img = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(img)
    inset = 30
    frame(d, w, h, inset)
    pad = inset + 56

    # Masthead stays top-left; the main text block shifts right so the
    # circular avatar overlay on X (bottom-left) never covers it.
    my = pad
    masthead(d, pad, my, 0.95)
    dateline(d, w, w - pad, my, 0.9)

    tx = 290
    f_kick = mono(22, medium=True)
    ky = my + 74
    d.text((tx, ky), COPY["kicker"], font=f_kick, fill=ACCENT)

    f_head = serif(72)
    hy = ky + 48
    for i, line in enumerate(COPY["headline"]):
        d.text((tx, hy + i * 78), line, font=f_head, fill=INK)

    f_foot = mono(18, medium=True)
    d.text((tx, h - pad), COPY["footer"], font=f_foot, fill=INK)

    img.save(path, optimize=True)


def gen_profile(path, size=512):
    img = Image.new("RGB", (size, size), PAPER)
    d = ImageDraw.Draw(img)
    frame(d, size, size, 26)

    # Centered ochre mark with serif C
    m = 150
    cx = (size - m) // 2
    cy = int(size * 0.30)
    d.rounded_rectangle([cx, cy, cx + m, cy + m], radius=34, fill=ACCENT)
    f_c = serif(110)
    d.text((size / 2, cy + m / 2 + 2), "C", font=f_c, fill=PAPER, anchor="mm")

    f_word = mono(30, medium=True)
    tw = d.textlength(COPY["wordmark"], font=f_word)
    d.text(((size - tw) / 2, cy + m + 56), COPY["wordmark"], font=f_word,
           fill=INK)

    f_tag = mono(17)
    tag = "THE PUBLIC RECORD, IN THE OPEN"
    tw = d.textlength(tag, font=f_tag)
    d.text(((size - tw) / 2, cy + m + 110), tag, font=f_tag, fill=MUTED)

    img.save(path, optimize=True)


FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="12" fill="#fbf0d9"/>
  <rect x="6" y="6" width="52" height="52" rx="8" fill="none" stroke="#332612" stroke-width="3"/>
  <rect x="16" y="16" width="32" height="32" rx="7" fill="#a8580a"/>
  <text x="32" y="42" font-family="Georgia, 'Times New Roman', serif"
        font-size="30" fill="#fbf0d9" text-anchor="middle">C</text>
</svg>
"""


def main():
    OUT.mkdir(exist_ok=True)
    gen_og(OUT / "og.png", 1200, 630, headline_size=92, kicker_size=24,
           sub_size=22)
    gen_og(OUT / "github-social.png", 1280, 640, headline_size=86,
           kicker_size=25, sub_size=23)
    gen_x_banner(OUT / "x-banner.png")
    gen_profile(OUT / "profile.png")
    (OUT / "favicon.svg").write_text(FAVICON)
    for p in sorted(OUT.iterdir()):
        print(f"{p.name}: {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
