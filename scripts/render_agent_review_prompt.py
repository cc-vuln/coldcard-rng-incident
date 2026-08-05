#!/usr/bin/env python3
"""Combine the standing review instructions with one bounded batch."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("candidates", nargs="+")
    args = parser.parse_args()
    prompt = args.template.read_text()
    candidate_list = "".join(f"- {path}\n" for path in args.candidates)
    prompt = prompt.replace("{CANDIDATES}", candidate_list.rstrip())
    prompt = prompt.replace("{PACKETS}", args.packets.read_text().rstrip())
    print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
