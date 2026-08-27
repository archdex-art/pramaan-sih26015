#!/usr/bin/env python3
"""Regenerate the design document's worked examples FROM the engine.

This script is the permanent fix for defect D2. An earlier draft of the master
design document hand-wrote §16.3 Example B with `confidence 0.71` against a
score of -0.59 — arithmetically impossible, since confidence = |score| x
coverage x quality and both multipliers are at most 1. Nobody caught it because
the number lived in prose and the formula lived in code.

The fix is structural: the examples are now *output*, not prose. The same
golden-case YAML files that gate CI are rendered into the document, so a
published number that the engine cannot reproduce is impossible by construction.

Usage:
    python scripts/render_worked_examples.py            # print
    python scripts/render_worked_examples.py --write    # patch the design doc
    python scripts/render_worked_examples.py --check    # CI: fail if stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "golden"))

from app.services.reconcile import EngineConfig, reconcile  # noqa: E402
from app.services.reconcile.weights import DEFAULT_WEIGHTS  # noqa: E402

CASES_DIR = REPO_ROOT / "tests" / "golden" / "cases"

#: The three cases the design document publishes as §16.3 Examples A, B and C,
#: mapped to their golden-case files. Same inputs as CI: no separate fixtures
#: that could drift from the tested ones.
PUBLISHED = [
    ("A", "Corroborated (the good news case)", "01_l4_check_dam_clean"),
    ("B", "Contradicted (the case that pays for the product)", "21_n3_terrain_path_farm_pond"),
    ("C", "Inconclusive (and why that's a feature)", "11_n1_cloud_blocked_trenches"),
]

START_MARKER = "<!-- BEGIN GENERATED WORKED EXAMPLES -->"
END_MARKER = "<!-- END GENERATED WORKED EXAMPLES -->"


def load_case(stem: str) -> dict[str, Any]:
    return yaml.safe_load((CASES_DIR / f"{stem}.yaml").read_text(encoding="utf-8"))


def build_bundle(spec: dict[str, Any]):  # type: ignore[no-untyped-def]
    # Reuse the golden suite's own builder so the document and CI cannot
    # construct bundles differently.
    from test_golden import build_bundle as _build  # noqa: PLC0415

    return _build(spec)


def render_case(letter: str, title: str, stem: str) -> str:
    spec = load_case(stem)
    bundle = build_bundle(spec)
    cfg = EngineConfig()
    v = reconcile(bundle, cfg)
    agg = v.lineage["aggregate"]  # type: ignore[index]

    lines: list[str] = []
    lines.append(f"### Example {letter} — {title}")
    lines.append("")
    lines.append(
        f"*Generated from `tests/golden/cases/{stem}.yaml` by "
        f"`scripts/render_worked_examples.py`. Engine `{v.engine_version}`.*"
    )
    lines.append("")
    lines.append("```")
    lines.append(f"CLAIM      {spec['claim_id']}  ·  type {spec['intervention_type']}")
    lines.append("")

    g = spec["gates"]
    gate_word = "PASSED" if g["detectability_passed"] else "FAILED"
    px = float(g["expected_footprint_m2"]) / float(g.get("pixel_area_m2", 900.0))
    lines.append(
        f"DETECT     expected footprint {float(g['expected_footprint_m2']):.0f} m2 "
        f"= {px:.2f} px  ->  GATE {gate_word}"
    )
    if g.get("escalated_to_cluster"):
        lines.append("           per-structure satellite claim DISABLED, escalated to CLUSTER")
    lines.append("")

    for f in spec.get("families", []):
        name = f["family"]
        w = DEFAULT_WEIGHTS[name]
        avail = f.get("available", True)
        s = float(f["agreement"])
        marker = "" if avail else "  [UNAVAILABLE, a=0]"
        scale = "  [cluster scale]" if f.get("cluster_scale") else ""
        lines.append(f"{name.upper():<10} s={s:+.2f}  w={w:.2f}{marker}{scale}")
        lines.append(f"           {f['reason']}")
    lines.append("")

    q = spec["quality"]
    lines.append(
        f"AGGREGATE  support = {agg['support']:+.4f}   "  # type: ignore[index]
        f"weight_total = {agg['weight_total']:.4f}"  # type: ignore[index]
    )
    lines.append(f"           score = support / weight_total = {v.score:+.4f}")
    lines.append(f"           coverage = {v.coverage:.4f}")
    lines.append(
        f"           quality  = metadata_integrity {float(q['metadata_integrity']):.2f}"
        f" x data_sufficiency {float(q['data_sufficiency']):.2f} = {v.quality:.4f}"
    )
    lines.append(f"           confidence = |score| x coverage x quality = {v.confidence:.4f}")
    lines.append("")
    lines.append(f"VERDICT    {v.label} — {v.level.value} · confidence {v.confidence:.2f}")
    lines.append(f"RULE_PATH  {' -> '.join(v.rule_path)}")
    lines.append(
        f"ACTION     {v.recommended_action}"
        + (f", priority {v.priority}" if v.priority is not None else "")
    )
    lines.append("")
    lines.append("DISSENT")
    for entry in v.dissent:
        wrapped = _wrap(entry, width=76, indent=11)
        lines.append(f"         - {wrapped.lstrip()}")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _wrap(text: str, width: int, indent: int) -> str:
    words = text.split()
    out: list[str] = []
    line = ""
    pad = " " * indent
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return f"\n{pad}".join(out)


def render_all() -> str:
    blocks = [
        START_MARKER,
        "",
        "> **Generated, not hand-written.** Every block below is the literal "
        "output of `scripts/render_worked_examples.py`, which loads the same "
        "golden-case YAML that gates CI, calls `engine.reconcile()`, and renders "
        "the result. A number here that the engine cannot reproduce is therefore "
        "impossible. Regenerate with `make examples`; never hand-edit.",
        "",
    ]
    for letter, title, stem in PUBLISHED:
        blocks.append(render_case(letter, title, stem))
    blocks.append(END_MARKER)
    return "\n".join(blocks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument(
        "--doc",
        default=str(REPO_ROOT.parent / "docs" / "PRAMAAN_SIH26015_Master_Research_and_Design.md"),
    )
    args = ap.parse_args()

    rendered = render_all()

    if not (args.write or args.check):
        print(rendered)
        return 0

    doc = Path(args.doc)
    if not doc.exists():
        print(f"design doc not found: {doc}", file=sys.stderr)
        return 2
    text = doc.read_text(encoding="utf-8")

    if START_MARKER not in text or END_MARKER not in text:
        print(
            f"markers not found in {doc.name}. Insert this pair around §16.3's "
            f"worked examples:\n  {START_MARKER}\n  {END_MARKER}",
            file=sys.stderr,
        )
        return 2

    head, _, rest = text.partition(START_MARKER)
    _, _, tail = rest.partition(END_MARKER)
    updated = head + rendered + tail

    if args.check:
        if updated != text:
            print(
                "docs/§16.3 worked examples are STALE — the engine no longer "
                "reproduces the published numbers. Run: make examples",
                file=sys.stderr,
            )
            return 1
        print("worked examples are up to date with the engine")
        return 0

    doc.write_text(updated, encoding="utf-8")
    print(f"regenerated §16.3 worked examples in {doc.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
