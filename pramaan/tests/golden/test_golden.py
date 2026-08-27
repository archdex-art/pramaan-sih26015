"""GT-3 golden case suite (docs §13.1).

Every case is a declarative YAML evidence bundle plus the expected level, label
and rule path. This suite is the demo's insurance policy: it is what guarantees
the system cannot produce an embarrassing verdict on stage, and nothing merges
to main without it green (docs §29 working agreements).

Two coverage guarantees are asserted by the suite itself, not left to reviewer
diligence:

* every one of the 8 epistemic levels is reachable by at least one case;
* both named N3 paths (satellite and terrain) are exercised.

A case file is the whole specification of a case. If a reviewer cannot tell from
the YAML why the expected verdict is correct, the case is under-specified.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

TESTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_ROOT))

from app.services.reconcile import (  # noqa: E402
    Alternative,
    EngineConfig,
    EvidenceBundle,
    FamilyEvidence,
    Gates,
    Level,
    Quality,
    reconcile,
)
from app.services.reconcile.dissent import verify_shippable  # noqa: E402

CASES_DIR = Path(__file__).parent / "cases"


def load_cases() -> list[tuple[str, dict[str, Any]]]:
    files = sorted(CASES_DIR.glob("*.yaml"))
    assert files, f"no golden cases found in {CASES_DIR}"
    out: list[tuple[str, dict[str, Any]]] = []
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        out.append((path.stem, data))
    return out


CASES = load_cases()


def build_bundle(spec: dict[str, Any]) -> EvidenceBundle:
    families = tuple(
        FamilyEvidence(
            family=f["family"],
            agreement=float(f["agreement"]),
            available=bool(f.get("available", True)),
            reason=f["reason"],
            lineage=f.get("lineage", {}) or {},
            cluster_scale=bool(f.get("cluster_scale", False)),
        )
        for f in spec.get("families", [])
    )
    g = spec["gates"]
    gates = Gates(
        detectability_passed=bool(g["detectability_passed"]),
        expected_footprint_m2=float(g["expected_footprint_m2"]),
        pixel_area_m2=float(g.get("pixel_area_m2", 900.0)),
        escalated_to_cluster=bool(g.get("escalated_to_cluster", False)),
        scene_scale=g.get("scene_scale", "unknown"),
        terrain_plausibility=g.get("terrain_plausibility", "unknown"),
    )
    q = spec["quality"]
    alternatives = tuple(
        Alternative(
            description=a["description"],
            excluded=bool(a["excluded"]),
            basis=a["basis"],
        )
        for a in spec.get("alternatives", [])
    )
    return EvidenceBundle(
        claim_id=spec["claim_id"],
        intervention_type=spec["intervention_type"],
        families=families,
        gates=gates,
        quality=Quality(
            metadata_integrity=float(q["metadata_integrity"]),
            data_sufficiency=float(q["data_sufficiency"]),
        ),
        alternatives=alternatives,
        limitations=tuple(spec.get("limitations", [])),
    )


@pytest.mark.parametrize(("name", "spec"), CASES, ids=[c[0] for c in CASES])
def test_golden_case(name: str, spec: dict[str, Any]) -> None:
    bundle = build_bundle(spec)
    verdict = reconcile(bundle, EngineConfig())
    expect = spec["expect"]

    assert verdict.level.value == expect["level"], (
        f"{name}: expected level {expect['level']}, got {verdict.level.value}\n"
        f"  rule_path: {verdict.rule_path}\n"
        f"  score={verdict.score} confidence={verdict.confidence} "
        f"coverage={verdict.coverage}"
    )
    assert verdict.label == expect["label"], (
        f"{name}: expected label {expect['label']}, got {verdict.label} "
        f"(rule_path={verdict.rule_path})"
    )

    for required in expect.get("rule_path_contains", []):
        assert required in verdict.rule_path, (
            f"{name}: rule_path {verdict.rule_path} is missing {required!r}"
        )
    for forbidden in expect.get("rule_path_excludes", []):
        assert forbidden not in verdict.rule_path, (
            f"{name}: rule_path {verdict.rule_path} must not contain {forbidden!r}"
        )

    if "action" in expect:
        assert verdict.recommended_action == expect["action"], (
            f"{name}: expected action {expect['action']}, got {verdict.recommended_action}"
        )
    if "priority" in expect:
        assert verdict.priority == expect["priority"]

    for tol_field in ("score", "confidence", "coverage"):
        if tol_field in expect:
            actual = getattr(verdict, tol_field)
            assert abs(actual - float(expect[tol_field])) < 5e-4, (
                f"{name}: {tol_field} expected {expect[tol_field]}, got {actual}"
            )

    for needle in expect.get("dissent_contains", []):
        joined = " ".join(verdict.dissent)
        assert needle.lower() in joined.lower(), (
            f"{name}: dissent panel missing required disclosure {needle!r}\n"
            f"  panel: {verdict.dissent}"
        )

    # Structural guarantees that apply to every case, always.
    assert verdict.dissent, f"{name}: empty dissent panel"
    assert verdict.confidence <= abs(verdict.score) + 1e-9, f"{name}: violates I1"
    verify_shippable(verdict)


def test_every_epistemic_level_is_reachable() -> None:
    """All 8 levels must be covered — docs §28.2 Phase 1 exit criterion."""
    covered = set()
    for _name, spec in CASES:
        covered.add(spec["expect"]["level"])
    expected = {level.value for level in Level}
    missing = expected - covered
    assert not missing, (
        f"golden suite does not reach these epistemic levels: {sorted(missing)}. "
        "The Phase 1 exit criterion requires all 8 to be reachable."
    )


def test_both_n3_paths_are_exercised() -> None:
    """The D1 fix is only real if both named paths have a case."""
    paths = set()
    for _name, spec in CASES:
        for entry in spec["expect"].get("rule_path_contains", []):
            if entry.startswith("N3_"):
                paths.add(entry)
    assert paths == {"N3_SATELLITE_PATH", "N3_TERRAIN_PATH"}, (
        f"expected both N3 paths to be exercised, found {sorted(paths)}"
    )


def test_case_count_meets_gt3_floor() -> None:
    """docs §13.1 GT-3 targets 60-100 cases. Stage 1 ships the structural core;
    this floor rises as producers land and contribute their own edge cases.
    """
    assert len(CASES) >= 20, (
        f"only {len(CASES)} golden cases; the Stage 1 floor is 20 covering every "
        "level and both N3 paths. Producer teams add theirs in Stage 3."
    )
