#!/usr/bin/env python3
"""Assemble an agent prompt from a trusted template and untrusted evidence.

Every unattended agent here is given two kinds of text: standing instructions
this project wrote, and material somebody else wrote. The whole risk is that
the second kind gets read as the first, so this is the one place the two are
joined, and it keeps them apart in the only ways a text channel allows.

Untrusted content is wrapped in a per-run marker the template names, and any
occurrence of that marker inside the content is mangled before wrapping. The
marker is unpredictable, so content cannot close the fence by guessing it, and
the mangling means it cannot close the fence by echoing it either.

None of that is a security boundary and it is not offered as one. It is a
convention that makes the boundary legible; the boundary itself is that the
agent runs as an account with no secrets and no write access outside its
remit, and that a gate reads everything it produced. See
docs/design/agent-sandbox.md.

This replaces the earlier `awk -v` and template-specific renderers. awk
expands backslash escapes inside a -v value, and the value was a candidate
line, which is text an attacker writes.

    render_agent_prompt.py --template T --nonce HEX \\
        [--value KEY=STRING] [--file KEY=PATH] [--untrusted KEY=PATH]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def split_pair(raw: str) -> tuple[str, str]:
    key, sep, value = raw.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {raw!r}")
    return key, value


PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]{2,})\}")


def neutralise(text: str, nonce: str) -> str:
    """Take the two things away that let content act on the prompt.

    The marker, so a body cannot close the fence it is inside. And anything
    shaped like one of this renderer's own placeholders, so a body cannot
    have text substituted into it on the second pass.
    """
    text = text.replace(nonce, f"{nonce[:4]}-REMOVED-BY-RENDERER")
    return PLACEHOLDER.sub(lambda m: f"(placeholder {m.group(1)} removed)", text)


def fence(text: str, nonce: str) -> str:
    return (f"<<<UNTRUSTED-{nonce}\n"
            f"{neutralise(text, nonce).rstrip()}\n"
            f"UNTRUSTED-{nonce}>>>")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--value", type=split_pair, action="append", default=[],
                        help="substitute {KEY} with a literal string")
    parser.add_argument("--file", type=split_pair, action="append", default=[],
                        help="substitute {KEY} with a file this project wrote")
    parser.add_argument("--untrusted", type=split_pair, action="append",
                        default=[],
                        help="substitute {KEY} with fenced untrusted content")
    args = parser.parse_args()

    prompt = args.template.read_text()
    substitutions: dict[str, str] = {"NONCE": args.nonce}

    for key, value in args.value:
        substitutions[key] = value
    for key, path in args.file:
        substitutions[key] = Path(path).read_text().rstrip()
    for key, path in args.untrusted:
        substitutions[key] = fence(Path(path).read_text(), args.nonce)

    # Two passes, because an injected trusted file names the nonce itself: the
    # standing rules describe the fence, and they arrive as a substitution.
    # Untrusted content cannot exploit the second pass, because it is fenced
    # by the first and had the marker stripped out of it before that.
    for _ in range(2):
        for key, value in substitutions.items():
            prompt = prompt.replace("{" + key + "}", value)

    # An unfilled placeholder means the driver and the template disagree, and
    # the agent would be told to assess a batch it cannot see. Fail rather
    # than send it. The check compares the TEMPLATE against the declared keys;
    # it must not scan the substituted output. Trusted --file content is ours
    # and can legitimately carry brace tokens (site pages hydrate with Astro
    # expressions like {VENDOR_RANGE_AS_OF}), and untrusted content had
    # anything placeholder-shaped neutralised before substitution.
    declared = set(substitutions)
    missing = sorted(set(PLACEHOLDER.findall(args.template.read_text()))
                     - declared)
    if missing:
        print(f"render-agent-prompt: template still contains unfilled "
              f"placeholder(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    sys.stdout.write(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
