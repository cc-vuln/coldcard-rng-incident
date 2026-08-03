#!/usr/bin/env python3
"""What may be kept from a response's headers, and how to check it.

Headers are recorded beside every capture because they are evidence: what the
origin said it was serving, when, and how it wanted the artefact cached. Most
of a modern response's headers are not that. They describe the delivery path
between the origin and *this collector* -- which CDN edge answered, which
cache node, which trace id, which session cookie -- and several of those name
the city the collector sat in. A Fastly `x-served-by` carries an airport code.
`cf-ray` ends in a colo. `x-github-edge-region` names a cloud region. Publish
the archive with those intact and the record quietly discloses where the
person keeping it lives, which is not a fact this project set out to publish.

So this is an allowlist, not a blocklist. A blocklist is a promise to have
already heard of every CDN, and the failure mode is silent: a header nobody
has seen before ships, and the leak is only visible to whoever reads the
archive most carefully, who is not on our side. Anything not named here is
dropped at capture time and refused by `just audit` afterwards.

Both `capture.py` (which writes) and `check_publishable.py` (which refuses to
commit) import from this module, so the rule has one home. The same policy
written twice becomes two policies.
"""
from __future__ import annotations

import re

# Kept: headers that describe the *document* -- its type, size, language,
# identity, freshness -- plus the status line. These are what a reader needs
# to re-verify a capture, and none of them varies with where the collector is.
KEEP = frozenset({
    "_status",              # synthesised by capture.py, not from the wire
    "age",
    "cache-control",
    "content-disposition",  # the origin's own filename for the artefact
    "content-language",
    "content-length",
    "content-type",
    "date",
    "etag",
    "expires",
    "last-modified",
    "link",                 # canonical and alternate URLs: evidentiary
    "server",               # the origin's software, not the path to it
    "vary",
})

# Capture-side geolocation echoed into a response *body*. Some origins render
# the visitor's own location into the page (a currency picker, a regional
# banner), so the body carries the leak even when every header is clean. These
# patterns are deliberately narrow: they match the machine-readable echo, not
# any mention of a place, because an article that discusses Singapore is
# ordinary captured content and must not trip the gate.


def safe_headers(headers: dict) -> dict:
    """The subset of `headers` that may be stored beside a capture."""
    return {k: v for k, v in headers.items() if k.lower() in KEEP}


def disallowed(headers: dict) -> list[str]:
    """Header names present that the allowlist does not permit."""
    return sorted(k for k in headers if k.lower() not in KEEP)


def geo_in_body(text: str) -> list[str]:
    """Capture-side geolocation still carrying a value, worst case first.

    Shares `_GEO_PAIR` with `scrub_geo`, so the gate cannot disagree with the
    scrubber about what a leak is. An already-redacted value is not a leak,
    and neither is an empty one.
    """
    hits: list[str] = []
    for m in _GEO_PAIR.finditer(text):
        _, _, _, value = _geo_parts(m)
        if not value or value == GEO_REDACTED:
            continue
        if value.startswith(("{", "%7B", "$")):
            continue
        snippet = m.group(0)
        if snippet not in hits:
            hits.append(snippet)
    for name in ("x-vercel-ip-country", "x-vercel-ip-city", "cf-ipcountry"):
        if re.search(name, text, re.I) and name not in hits:
            hits.append(name)
    return hits


# Some origins render the visitor's own location into the page they serve: a
# currency picker, a regional banner, a signup link carrying geo query
# parameters. That puts the collector's location in the body, where the header
# allowlist cannot reach it, and it survives every encoding the page happens to
# use (plain, percent-encoded, JSON-escaped inside a script).
#
# The rule here is narrow on purpose: only the *value attached to a known geo
# key* is removed. A source writing about Singapore keeps its words, because
# an archive that redacts a publisher's article to protect the archivist has
# broken the thing it exists to do.
GEO_KEYS = (
    "oficialCountryName",   # (sic) the spelling one CDN actually ships
    "officialCountryName",
    "countryRegion",
    "currencyName",
    "currencyCode",
    "currencySymbol",
    "subregion",
    "country",
    "region",
    "city",
    "latitude",
    "longitude",
)

# key, then one of the separators these encodings use, then the value up to
# whatever terminates it in that encoding.
_GEO_PAIR = re.compile(
    # A field name begins after a delimiter, in whichever encoding the page
    # uses. Stated positively so that "%26country" matches while the
    # "country" inside "addressCountry" and the "city" inside "opacity" do
    # not: a letter or digit before the name is not a delimiter.
    r"(?P<pre>^|[?&,{\[\s\"\'\\:>=]|%26|%3F|%253F|u0026)"
    r"(?P<key>" + "|".join(GEO_KEYS) + r")"
    # The separator, then the value, in each encoding these turn up in. A
    # JSON string inside a script lets its value run to the closing quote,
    # spaces included ("South-Eastern Asia"); a query-string value, plain
    # or percent-encoded, never carries a raw space, so whitespace ends it
    # as surely as & does.
    r"(?:(?P<jsep>\\*\"\s*:\s*\\*\")"
    r"(?P<jvalue>(?:(?!\\+\")[^\"\\<>]){0,80})"
    r"|(?P<qsep>=|%3D|%253D)"
    r"(?P<qvalue>(?:(?!%26|%2C|%253D|\\u0026|\\\\u0026)"
    r"[^\"&,}\\\s<>]){0,60}))",
    re.I,
)

GEO_REDACTED = "[redacted-geo]"


def _geo_parts(m: re.Match) -> tuple[str, str, str, str]:
    # The pair pattern has one value branch per encoding; exactly one has
    # matched. The consumed delimiter comes back too: a scrub replacement
    # that drops it would eat the document's own separators.
    sep = m.group("jsep")
    value = m.group("jvalue")
    if sep is None:
        sep = m.group("qsep")
        value = m.group("qvalue")
    return m.group("pre"), m.group("key"), sep, value or ""


def scrub_geo(text: str) -> tuple[str, int]:
    """Blank the values of geolocation fields. Returns (text, replacements)."""
    count = 0

    def replace(m):
        nonlocal count
        # A templating placeholder is not a leak; leave the page's own code be.
        pre, key, sep, value = _geo_parts(m)
        if not value or value.startswith(("{", "%7B", "$")):
            return m.group(0)
        count += 1
        return pre + key + sep + GEO_REDACTED

    return _GEO_PAIR.sub(replace, text), count
