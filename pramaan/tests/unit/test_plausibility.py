"""Tests for the terrain plausibility rule engine (T10).

This is the family that can send a human to inspect another human's work, so
the tests are weighted towards *refusing* to fire rather than towards firing.
The two properties that matter most:

* an implausible verdict requires the whole uncertainty disk to fail;
* the slope unit is never confused between degrees and percent.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.reconcile import reconcile  # noqa: E402
from app.services.reconcile.types import (  # noqa: E402
    Alternative,
    EvidenceBundle,
    FamilyEvidence,
    Level,
    Quality,
)
from app.services.terrain import detectability, evidence, plausibility  # noqa: E402
from app.services.terrain.plausibility import RULES, SlopeBand, evaluate  # noqa: E402
from app.services.terrain.types import (  # noqa: E402
    DiskStat,
    TerrainSample,
    degrees_to_percent,
    percent_to_degrees,
    uncertainty_disk_radius_m,
)


def flat(value: float) -> DiskStat:
    """A disk where every pixel reads the same — the simplest fixture."""
    return DiskStat(minimum=value, median=value, maximum=value)


def sample(
    *,
    slope_deg: float | DiskStat = 2.0,
    strahler: float | DiskStat = 3.0,
    flow_acc: float | DiskStat = 4180.0,
    dist_stream: float | DiskStat = 8.0,
    upstream_km2: float | DiskStat = 1.9,
    in_depression: bool | None = True,
    radius_m: float = 15.0,
) -> TerrainSample:
    def as_stat(v: float | DiskStat) -> DiskStat:
        return v if isinstance(v, DiskStat) else flat(v)

    return TerrainSample(
        disk_radius_m=radius_m,
        slope_deg=as_stat(slope_deg),
        strahler_order=as_stat(strahler),
        flow_accumulation_px=as_stat(flow_acc),
        dist_to_stream_m=as_stat(dist_stream),
        upstream_area_km2=as_stat(upstream_km2),
        in_depression=in_depression,
        dem_product="NASADEM",
        dem_version="001",
        stream_threshold_px=100.0,
        stream_threshold_agreement=0.79,
    )


# --- unit conversion -------------------------------------------------------


def test_percent_to_degrees_matches_trigonometry() -> None:
    """5% is 2.86 deg, not 5 deg. Confusing them mis-gates half the rule table."""
    assert percent_to_degrees(5.0) == pytest.approx(2.8624, abs=1e-4)
    assert percent_to_degrees(8.0) == pytest.approx(4.5739, abs=1e-4)
    assert percent_to_degrees(15.0) == pytest.approx(8.5308, abs=1e-4)
    assert percent_to_degrees(33.0) == pytest.approx(18.2629, abs=1e-4)
    assert percent_to_degrees(100.0) == pytest.approx(45.0)


def test_slope_conversion_round_trips() -> None:
    for pct in (0.5, 1.0, 5.0, 8.0, 15.0, 33.0, 80.0):
        assert degrees_to_percent(percent_to_degrees(pct)) == pytest.approx(pct)


def test_slope_band_declares_its_unit_and_converts_once() -> None:
    degrees = SlopeBand(None, 5.0, "degrees").as_degrees()
    percent = SlopeBand(None, 5.0, "percent").as_degrees()
    assert degrees == (None, 5.0)
    assert percent[1] == pytest.approx(2.8624, abs=1e-4)
    # Same number, different unit, nearly 2x different threshold.
    assert degrees[1] != pytest.approx(percent[1])


def test_check_dam_uses_degrees_and_percolation_tank_uses_percent() -> None:
    """Pins the docs §18.1 discrepancy so it cannot silently drift."""
    assert RULES["check_dam"].slope is not None
    assert RULES["check_dam"].slope.unit == "degrees"
    assert RULES["percolation_tank"].slope is not None
    assert RULES["percolation_tank"].slope.unit == "percent"


def test_slope_band_describe_covers_every_shape() -> None:
    assert SlopeBand(1.0, 15.0, "percent").describe() == "slope 1-15%"
    assert SlopeBand(None, 5.0, "degrees").describe() == "slope < 5deg"
    assert SlopeBand(3.0, None, "percent").describe() == "slope > 3%"
    assert SlopeBand(None, None, "degrees").describe() == "slope unconstrained"


# --- uncertainty disk ------------------------------------------------------


def test_uncertainty_disk_applies_a_floor() -> None:
    """r = max(gps_accuracy, 15 m) — docs §16.2 STEP 2."""
    assert uncertainty_disk_radius_m(6.0) == 15.0
    assert uncertainty_disk_radius_m(14.9) == 15.0
    assert uncertainty_disk_radius_m(40.0) == 40.0


def test_missing_accuracy_does_not_produce_a_tighter_disk() -> None:
    """Absent metadata must never look more precise than present metadata."""
    assert uncertainty_disk_radius_m(None) == 15.0


def test_negative_accuracy_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        uncertainty_disk_radius_m(-1.0)


def test_disk_stat_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="not ordered"):
        DiskStat(minimum=5.0, median=1.0, maximum=9.0)


# --- plausible -------------------------------------------------------------


def test_ideal_check_dam_is_plausible() -> None:
    r = evaluate("check_dam", sample())
    assert r.verdict == "plausible"
    assert r.agreement == 1.0
    assert r.available
    assert "consistent with a check_dam" in r.reason
    assert r.checks


def test_plausible_reason_names_the_expected_siting_rule() -> None:
    """The Evidence Pack prints this verbatim; it must be self-explanatory."""
    r = evaluate("check_dam", sample())
    assert "Strahler order >= 2" in r.reason
    assert "uncertainty disk" in r.reason


# --- implausible -----------------------------------------------------------


def test_golden_case_21_farm_pond_is_categorically_implausible() -> None:
    """Reproduces the terrain reading behind docs §16.3 Example B.

    The document's prose cites four numbers as evidence of implausibility:
    Strahler order 0, flow accumulation 12 px, slope 6.4 deg, distance to
    stream 340 m. Applying the rule table shows only **two** of them actually
    violate a farm-pond constraint, and it is worth being precise about which:

    * **slope 6.4 deg = 11.2 %** against the 8 % limit -> VIOLATION.
    * **distance to stream 340 m** against the 250 m limit -> VIOLATION.
    * **flow accumulation 12 px** = 12 x 900 m2 = ~1.08 ha of contributing
      area, which is comfortably above the 5 px (~0.45 ha) floor -> PASSES. A
      1 ha catchment can plausibly fill a 625 m2 pond.
    * **Strahler order 0** is not a violation either: a farm pond is fed by
      field runoff, not by a channel, so the rule table deliberately imposes no
      stream-order requirement on this type.

    So the verdict is correct but the doc's reasoning is looser than the rule
    table's. Asserting the precise set here is what stops someone "fixing" the
    rule table later to match the prose and thereby making order-0 siting
    categorically implausible for every farm pond in the country.
    """
    r = evaluate(
        "farm_pond",
        sample(
            slope_deg=6.4,
            strahler=0.0,
            flow_acc=12.0,
            dist_stream=340.0,
            upstream_km2=0.01,
            in_depression=False,
            radius_m=15.0,
        ),
    )
    assert r.verdict == "implausible"
    assert r.agreement == -1.0
    assert r.available
    assert "No part of the 15 m location uncertainty disk" in r.reason

    # The two genuine violations are named in the printed reason.
    assert "slope < 8%" in r.reason
    assert "distance to stream <= 250 m" in r.reason

    # Flow accumulation is evaluated and PASSES, so it must not be cited as a
    # violation — but it must still appear in `checks` for the evidence tree.
    assert "flow accumulation" not in r.reason
    assert any("flow accumulation >= 5 px" in c for c in r.checks)

    # Stream order is not constrained for this type at all.
    assert not any("Strahler" in c for c in r.checks)


def test_implausible_agreement_is_exactly_minus_one() -> None:
    """The engine requires terrain <= -1.0 for N3_TERRAIN_PATH.

    If this drifted to -0.9 the contradicted path would become unreachable and
    the flagship demo case would silently degrade to N2.
    """
    r = evaluate(
        "check_dam",
        sample(strahler=0.0, flow_acc=3.0, dist_stream=900.0),
    )
    assert r.verdict == "implausible"
    assert r.agreement == -1.0


def test_slope_only_violation_is_enough_to_be_implausible() -> None:
    r = evaluate("check_dam", sample(slope_deg=22.0))
    assert r.verdict == "implausible"
    assert "slope" in r.reason


# --- marginal: the precision-protecting middle ----------------------------


def test_median_fails_but_disk_passes_is_marginal_not_implausible() -> None:
    """The benefit-of-the-doubt rule, and the reason it exists.

    The median slope violates the 8% farm-pond limit, but part of the
    uncertainty disk satisfies it. Reporting this as implausible would risk a
    field visit over GPS error.
    """
    limit_deg = percent_to_degrees(8.0)
    r = evaluate(
        "farm_pond",
        sample(
            slope_deg=DiskStat(
                minimum=limit_deg - 1.0,
                median=limit_deg + 1.0,
                maximum=limit_deg + 3.0,
            ),
            dist_stream=100.0,
            flow_acc=50.0,
        ),
    )
    assert r.verdict == "marginal"
    assert r.agreement == pytest.approx(-0.4)
    assert "not a categorical exclusion" in r.reason


def test_marginal_terrain_cannot_reach_n3_in_the_engine() -> None:
    """End-to-end: a marginal rule must not carry a contradicted verdict."""
    marginal = evaluate(
        "farm_pond",
        sample(
            slope_deg=DiskStat(minimum=4.0, median=6.0, maximum=8.0),
            dist_stream=DiskStat(minimum=200.0, median=300.0, maximum=400.0),
            flow_acc=DiskStat(minimum=2.0, median=8.0, maximum=20.0),
            in_depression=False,
        ),
    )
    assert marginal.verdict == "marginal"

    gate = detectability.evaluate("farm_pond", expected_footprint_m2=625.0, cluster_member_count=4)
    bundle = EvidenceBundle(
        claim_id="MARGINAL-NOT-N3",
        intervention_type="farm_pond",
        families=(
            evidence.to_family_evidence(marginal, sample()),
            FamilyEvidence(
                family="satellite",
                agreement=-1.0,
                available=True,
                reason="no cluster MNDWI change",
                cluster_scale=True,
            ),
            FamilyEvidence(
                family="temporal",
                agreement=-1.0,
                available=True,
                reason="no cluster persistence change",
                cluster_scale=True,
            ),
        ),
        gates=evidence.to_gates(gate, marginal),
        quality=Quality(metadata_integrity=0.9, data_sufficiency=0.8),
        alternatives=(Alternative(description="gps error", excluded=True, basis="14 m vs 300 m"),),
    )
    verdict = reconcile(bundle)
    assert verdict.level is not Level.N3_CONTRADICTED
    assert "N3_TERRAIN_PATH" not in verdict.rule_path
    assert "n3_terrain_blocked_by=terrain_not_categorically_implausible" in verdict.rule_path


# --- not applicable --------------------------------------------------------


@pytest.mark.parametrize(
    "type_key", ["dug_well", "borewell", "recharge_shaft", "livestock", "livelihood", "other"]
)
def test_types_without_a_siting_rule_report_unavailable_not_neutral(type_key: str) -> None:
    """Unavailable, not neutral — so coverage reflects the gap honestly."""
    r = evaluate(type_key, sample())
    assert r.verdict == "unknown"
    assert r.agreement == 0.0
    assert not r.available
    assert "reported unavailable rather than neutral" in r.reason


def test_unknown_type_fails_loudly() -> None:
    with pytest.raises(KeyError, match="no terrain rule"):
        evaluate("moon_base", sample())


# --- depression handling ---------------------------------------------------


def test_missing_depression_mask_is_not_a_violation() -> None:
    """An unavailable input must never read as a failed constraint."""
    r = evaluate(
        "waterbody_renovation",
        sample(slope_deg=1.0, in_depression=None),
    )
    assert r.verdict == "plausible"
    assert any("depression mask unavailable" in c for c in r.checks)


def test_absent_depression_is_a_violation_for_a_water_body() -> None:
    r = evaluate("waterbody_renovation", sample(slope_deg=1.0, in_depression=False))
    assert r.verdict == "implausible"
    assert "depression" in r.reason


# --- rule table integrity --------------------------------------------------


def test_every_intervention_type_has_a_rule() -> None:
    """A missing rule would make `evaluate` raise inside a Celery task."""
    from app.services.reconcile.signatures import SIGNATURES

    assert set(RULES) == set(SIGNATURES)


def test_slope_bands_are_ordered_and_sane() -> None:
    for key, rule in RULES.items():
        if rule.slope is None:
            continue
        lo, hi = rule.slope.as_degrees()
        if lo is not None and hi is not None:
            assert lo < hi, key
        for value in (lo, hi):
            if value is not None:
                assert 0.0 <= value < 90.0, key


def test_upstream_area_constraint_is_evaluated_when_present() -> None:
    """Exercises the upstream-area branch via a rule that declares it."""
    import dataclasses

    rule = dataclasses.replace(RULES["check_dam"], min_upstream_area_km2=5.0)
    original = RULES["check_dam"]
    RULES["check_dam"] = rule
    try:
        r = evaluate("check_dam", sample(upstream_km2=0.2))
        assert r.verdict == "implausible"
        assert "upstream area >= 5 km2" in r.reason
    finally:
        RULES["check_dam"] = original


def test_max_strahler_constraint_is_evaluated() -> None:
    """Gully plugs require order 1-2; an order-5 river is the wrong place."""
    r = evaluate("gully_plug", sample(strahler=5.0, slope_deg=5.0, dist_stream=10.0))
    assert r.verdict == "implausible"
    assert "Strahler order <= 2" in r.reason


def test_lower_slope_bound_is_evaluated() -> None:
    """Contour trenches need a slope to conserve moisture on; flat land is wrong."""
    r = evaluate(
        "contour_trench",
        sample(slope_deg=0.2, flow_acc=100.0, dist_stream=200.0),
    )
    assert r.verdict == "implausible"
    assert "lower bound" in r.reason


def test_max_flow_accumulation_constraint_is_evaluated() -> None:
    """A trench belongs in the upper catchment, not on a major channel."""
    r = evaluate(
        "contour_trench",
        sample(slope_deg=10.0, flow_acc=99_000.0, dist_stream=200.0),
    )
    assert r.verdict == "implausible"
    assert "upper catchment" in r.reason


def test_min_dist_to_stream_constraint_is_evaluated() -> None:
    """A plantation in an active channel will be washed out."""
    r = evaluate("plantation", sample(dist_stream=1.0))
    assert r.verdict == "implausible"
    assert "not in an active channel" in r.reason


# --- adapter ---------------------------------------------------------------


def test_adapter_carries_lineage_into_the_evidence_family() -> None:
    """docs §21.3: a verdict must be recomputable from its lineage."""
    s = sample()
    r = evaluate("check_dam", s)
    fam = evidence.to_family_evidence(r, s)
    assert fam.family == "terrain"
    assert fam.agreement == 1.0
    assert fam.available
    assert fam.lineage["dem_product"] == "NASADEM"
    assert fam.lineage["stream_threshold_px"] == 100.0
    # The W3 fix: the calibration score travels with the evidence.
    assert fam.lineage["stream_threshold_agreement"] == 0.79
    assert fam.lineage["rule_id"] == "check_dam:plausible"


def test_adapter_builds_gates_consistently() -> None:
    gate = detectability.evaluate("check_dam")
    plaus = evaluate("check_dam", sample())
    gates = evidence.to_gates(gate, plaus, scene_scale="mid")
    assert gates.detectability_passed
    assert gates.terrain_plausibility == "plausible"
    assert gates.scene_scale == "mid"
    assert gates.footprint_pixels == pytest.approx(gate.footprint_pixels)


def test_full_producer_chain_reaches_n3_terrain_path() -> None:
    """The complete Example B chain, from DEM-shaped numbers to the verdict.

    This is the integration that matters: the terrain producer and the
    detectability gate, wired through the adapter, must independently reproduce
    the flagship demo verdict without any golden-case YAML involved.
    """
    s = sample(
        slope_deg=6.4,
        strahler=0.0,
        flow_acc=12.0,
        dist_stream=340.0,
        upstream_km2=0.01,
        in_depression=False,
        radius_m=uncertainty_disk_radius_m(14.0),
    )
    plaus = evaluate("farm_pond", s)
    gate = detectability.evaluate("farm_pond", expected_footprint_m2=625.0, cluster_member_count=4)

    bundle = EvidenceBundle(
        claim_id="PRODUCER-CHAIN-B",
        intervention_type="farm_pond",
        families=(
            evidence.to_family_evidence(plaus, s),
            FamilyEvidence(
                family="satellite",
                agreement=-1.0,
                available=True,
                reason="cluster of 4 nearby claims: no MNDWI change",
                cluster_scale=True,
            ),
            FamilyEvidence(
                family="temporal",
                agreement=-1.0,
                available=True,
                reason="no cluster persistence change across 2 years",
                cluster_scale=True,
            ),
            FamilyEvidence(
                family="photo",
                agreement=0.4,
                available=True,
                reason="pond_excavation 0.74; water_present abstained",
            ),
        ),
        gates=evidence.to_gates(gate, plaus, scene_scale="mid"),
        quality=Quality(metadata_integrity=0.70, data_sufficiency=0.80),
        alternatives=(
            Alternative(
                description="GPS error placed the point away from the real pond",
                excluded=True,
                basis="14 m accuracy cannot move the point 340 m to a channel",
            ),
        ),
    )

    verdict = reconcile(bundle)
    assert verdict.level is Level.N3_CONTRADICTED
    assert verdict.label == "CONTRADICTED"
    assert "N3_TERRAIN_PATH" in verdict.rule_path
    assert verdict.recommended_action == "physical_verification"
    # Priority 1 because a deterministic terrain rule is the driver, not the AI.
    assert verdict.priority == 1
    assert verdict.confidence <= abs(verdict.score)
    joined = " ".join(verdict.dissent)
    assert "INCONCLUSIVE" in joined
    assert "rests on the terrain rule" in joined


def test_no_module_in_the_terrain_package_reads_a_clock() -> None:
    """The producers must stay deterministic for the same reason the engine does."""
    import ast

    pkg = BACKEND / "app" / "services" / "terrain"
    forbidden = {"now", "today", "utcnow", "monotonic", "perf_counter"}
    offenders: list[str] = []
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders


def test_math_module_is_used_rather_than_a_magic_constant() -> None:
    """Guards against someone replacing the conversion with a hardcoded 0.5729."""
    assert percent_to_degrees(100.0) == pytest.approx(math.degrees(math.atan(1.0)))


def test_a_rule_with_no_evaluable_constraints_degrades_to_unknown() -> None:
    """A malformed rule row must never silently read as `plausible`.

    If someone adds an intervention type and forgets to fill in any constraint,
    the safe failure is "we could not assess siting" with the family reported
    unavailable — not "siting is fine", which would hand the claim a free +1.0
    from the heaviest-weighted family in the system.
    """
    import dataclasses

    empty = dataclasses.replace(
        RULES["plantation"],
        min_strahler=None,
        max_strahler=None,
        slope=None,
        max_dist_to_stream_m=None,
        min_dist_to_stream_m=None,
        min_flow_accumulation_px=None,
        max_flow_accumulation_px=None,
        min_upstream_area_km2=None,
        requires_depression=False,
        not_applicable=False,
    )
    original = RULES["plantation"]
    RULES["plantation"] = empty
    try:
        r = evaluate("plantation", sample())
        assert r.verdict == "unknown"
        assert r.agreement == 0.0
        assert not r.available
        assert r.rule_id == "plantation:no_checks_evaluated"
        assert "required inputs unavailable" in r.reason
    finally:
        RULES["plantation"] = original


def test_no_rule_declares_a_field_that_evaluate_ignores() -> None:
    """Every TerrainRule field must be read by `evaluate()`.

    This is the guard that would have caught `forbid_in_channel`: a config field
    that six rules set and no code path consulted. Asserted structurally so the
    next such field is caught at the point it is added.
    """
    import ast
    import dataclasses

    source = (BACKEND / "app" / "services" / "terrain" / "plausibility.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    evaluate_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate"
    )
    read_attrs = {
        node.attr
        for node in ast.walk(evaluate_fn)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "rule"
    }
    declared = {
        f.name
        for f in dataclasses.fields(plausibility.TerrainRule)
        # `notes` is documentation carried alongside the rule, not a constraint.
        if f.name != "notes"
    }
    unread = declared - read_attrs
    assert not unread, f"TerrainRule fields declared but never read by evaluate(): {unread}"


def test_terrain_path_requires_cluster_scale_evidence_to_exist() -> None:
    """Escalated to cluster, but no family was actually computed at cluster scale.

    Covers the `no_cluster_scale_evidence` guard: the gate said "assess as a
    cluster" and nothing came back at that scale, so there is nothing to weigh
    against the terrain rule and N3_TERRAIN_PATH must refuse to fire.
    """
    from app.services.reconcile.types import Quality as Q

    b = EvidenceBundle(
        claim_id="ESCALATED-BUT-EMPTY",
        intervention_type="farm_pond",
        families=(
            FamilyEvidence(
                family="terrain",
                agreement=-1.0,
                available=True,
                reason="order 0, 310 m from any drainage line",
            ),
            # available, but NOT cluster_scale
            FamilyEvidence(
                family="photo",
                agreement=0.3,
                available=True,
                reason="excavation visible",
            ),
        ),
        gates=evidence.to_gates(
            detectability.evaluate(
                "farm_pond", expected_footprint_m2=625.0, cluster_member_count=4
            ),
            evaluate(
                "farm_pond",
                sample(
                    slope_deg=6.4,
                    strahler=0.0,
                    flow_acc=12.0,
                    dist_stream=340.0,
                    in_depression=False,
                ),
            ),
        ),
        quality=Q(metadata_integrity=0.85, data_sufficiency=0.75),
        alternatives=(Alternative(description="never filled", excluded=True, basis="checked"),),
    )
    v = reconcile(b)
    assert "N3_TERRAIN_PATH" not in v.rule_path
    assert "n3_terrain_blocked_by=no_cluster_scale_evidence" in v.rule_path
