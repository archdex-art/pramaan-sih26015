#!/usr/bin/env python3
"""Fail if any stylesheet uses a `var(--token)` that is never defined.

## Why this exists

An undefined custom property does not raise, warn, or log. The declaration is
simply dropped and the property falls back — usually to `inherit` or
`currentColor`. That failure mode is silent and can be invisible in the most
literal sense: `.login-submit` was written with `color: var(--paper-1)` when the
real token is `--paper`, so the label inherited the button's own dark ink and
rendered as black-on-black. A 1.00:1 contrast ratio, shipped, with a green
build — `tsc` and `vite build` cannot see inside a CSS string.

Six invented tokens across 31 declarations got through review this way
(`--paper-1`, `--t-note`, `--t-subhead`, `--level-L4`, `--level-N3`, plus
`--error-bg`/`--error-edge` which had been dead since the chart styles were
written). A typo in a token name is not a style question, it is a broken
reference, and broken references belong in CI.

Exit 1 on any undefined reference, listing file and line.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

STYLES = Path(__file__).resolve().parent.parent / "frontend" / "src" / "styles"

DEFINE = re.compile(r"^\s*(--[A-Za-z0-9-]+)\s*:")
USE = re.compile(r"var\(\s*(--[A-Za-z0-9-]+)")


def main() -> int:
    files = sorted(STYLES.glob("*.css"))
    if not files:
        print(f"no stylesheets found under {STYLES}", file=sys.stderr)
        return 1

    defined: set[str] = set()
    for path in files:
        for line in path.read_text().splitlines():
            m = DEFINE.match(line)
            if m:
                defined.add(m.group(1))

    problems: list[str] = []
    for path in files:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for token in USE.findall(line):
                # A `var()` fallback (`var(--x, 12px)`) is a deliberate default,
                # not a broken reference, so only the bare form is an error.
                if token in defined:
                    continue
                if re.search(rf"var\(\s*{re.escape(token)}\s*,", line):
                    continue
                problems.append(f"{path.name}:{lineno}: undefined {token}")

    if problems:
        print(
            f"{len(problems)} undefined CSS custom propert"
            f"{'y' if len(problems) == 1 else 'ies'} referenced:\n"
        )
        for p in problems:
            print(f"  {p}")
        print(
            "\nAn undefined var() is silently dropped and the property falls back — "
            "\nthis is how black-on-black text ships with a green build."
        )
        return 1

    print(f"CSS tokens OK — {len(defined)} defined, all references resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
