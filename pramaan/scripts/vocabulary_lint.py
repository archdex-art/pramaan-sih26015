#!/usr/bin/env python3
"""Vocabulary lock (W6 fix, docs §37).

PRAMAAN never emits the words *fraud*, *fake*, *false* or *failed* in anything a
user, an officer or a citizen can read. The strongest phrase available anywhere
in the UI, API or Evidence Pack PDF is **"requires physical verification"**.

Why this is a CI rule and not a style guideline: the system's output can end up
in a file that affects a named person's reputation — a WDT member whose geotag
the system flagged. A word choice that turns "the satellite record does not
corroborate this claim" into "this claim is false" is a substantive harm, and
substantive harms get enforced by the build, not by review discipline.

Scope: user-facing strings only. Internal identifiers may legitimately contain
these words (`detectability_passed`, `gate_failed`, a boolean named `is_false`),
and banning them in code would be cargo-culting the rule instead of applying it.

Usage:
    python scripts/vocabulary_lint.py           # lint, exit 1 on violation
    python scripts/vocabulary_lint.py --list    # show what is scanned
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Word-boundary patterns. `\b` matters: "failed" must be caught but
#: "gate_failed" (an identifier) is excluded by the scope rules below, and
#: "falsify" is a legitimate scientific term we do not want flagged.
BANNED = {
    "fraud": re.compile(r"\bfraud(ulent|ulently)?\b", re.IGNORECASE),
    "fake": re.compile(r"\bfake[ds]?\b", re.IGNORECASE),
    "false": re.compile(r"\bfalse(ly)?\b", re.IGNORECASE),
    "failed": re.compile(r"\bfail(ed|ure|ures|s|ing)?\b", re.IGNORECASE),
}

#: Files whose *entire* content is user-facing text.
USER_FACING_GLOBS = (
    "frontend/src/lib/i18n/**/*.json",
    "backend/app/services/reports/templates/**/*.html",
)

#: Python modules whose string literals reach the user. Only string literals are
#: scanned in these, never identifiers or comments.
USER_FACING_PYTHON = (
    "backend/app/services/reconcile/dissent.py",
    "backend/app/services/reconcile/signatures.py",
)

#: Substrings that make a match legitimate. Each one is a deliberate, documented
#: exemption rather than a blanket escape hatch.
ALLOWED_CONTEXTS = (
    # The engine's own vocabulary-lock test asserts on the banned list.
    "banned vocabulary",
    # Naming the rule in a docstring is how the rule stays discoverable.
    "vocabulary lock",
    "vocabulary_lint",
    # Describing a *data* failure, not a person: "0 of 4 scenes passed cloud
    # masking" style messages talk about scenes, and the detectability gate's
    # pass/fail is a sensor-physics fact, not an accusation.
    "detectability gate",
    "gate failed",
)


class Violation:
    def __init__(self, path: Path, line_no: int, word: str, text: str) -> None:
        self.path = path
        self.line_no = line_no
        self.word = word
        self.text = text

    def __str__(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        return (
            f"{rel}:{self.line_no}: banned word {self.word!r} in user-facing "
            f"text\n    {self.text.strip()[:140]}"
        )


def is_allowed(line: str) -> bool:
    lowered = line.lower()
    return any(ctx in lowered for ctx in ALLOWED_CONTEXTS)


def scan_text_file(path: Path) -> list[Violation]:
    out: list[Violation] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if is_allowed(line):
            continue
        for word, pattern in BANNED.items():
            if pattern.search(line):
                out.append(Violation(path, i, word, line))
    return out


def scan_python_string_literals(path: Path) -> list[Violation]:
    """Scan only string literals, so identifiers and comments are exempt.

    Docstrings are excluded too: they are developer-facing. They are identified
    structurally — the first statement of a module, class or function body —
    rather than by a heuristic on indentation or newline count. An earlier
    version guessed with `col_offset == 0`, which silently missed every indented
    function docstring and produced a false positive on the first run.
    """
    import ast

    out: list[Violation] = []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()

    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstring_nodes:
            continue
        line_no = node.lineno
        context = lines[line_no - 1] if line_no - 1 < len(lines) else ""
        if is_allowed(node.value) or is_allowed(context):
            continue
        for word, pattern in BANNED.items():
            if pattern.search(node.value):
                out.append(Violation(path, line_no, word, node.value))
    return out


def collect_targets() -> tuple[list[Path], list[Path]]:
    text_files: list[Path] = []
    for glob in USER_FACING_GLOBS:
        text_files.extend(sorted(REPO_ROOT.glob(glob)))
    py_files = [REPO_ROOT / rel for rel in USER_FACING_PYTHON]
    py_files = [p for p in py_files if p.exists()]
    return text_files, py_files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show scanned files and exit")
    args = ap.parse_args()

    text_files, py_files = collect_targets()

    if args.list:
        print("User-facing text files:")
        for p in text_files or []:
            print(f"  {p.relative_to(REPO_ROOT)}")
        if not text_files:
            print("  (none yet — i18n bundles and report templates land in Stage 4/5)")
        print("Python modules (string literals only):")
        for p in py_files:
            print(f"  {p.relative_to(REPO_ROOT)}")
        return 0

    violations: list[Violation] = []
    for path in text_files:
        violations.extend(scan_text_file(path))
    for path in py_files:
        violations.extend(scan_python_string_literals(path))

    if violations:
        print(
            f"VOCABULARY LOCK VIOLATED ({len(violations)}). PRAMAAN never accuses; "
            "the strongest available phrase is 'requires physical verification'.\n",
            file=sys.stderr,
        )
        for v in violations:
            print(v, file=sys.stderr)
        return 1

    scanned = len(text_files) + len(py_files)
    print(f"vocabulary lock holds across {scanned} user-facing file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
