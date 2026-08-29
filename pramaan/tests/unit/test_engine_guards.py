"""Tests for the engine's guard rails and defensive contracts.

These are the paths that fire when a *producer* misbehaves — an out-of-range
agreement, a duplicate family, a weight set that does not sum to 1. They are
tested deliberately and not written off as "can't happen", because every one of
them is the last line of defence between a producer bug and a government
verdict.

Private helpers are exercised directly where their defensive branches are
unreachable through the public API. That is intentional: a defensive branch that
is never verified is indistinguishable from a comment.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bundles import all_agreeing, bundle, fam, gates  # noqa: E402

from app.services.reconcile import (  # noqa: E402
    Alternative,
    EngineConfig,
    EvidenceBundle,
    FamilyEvidence,
    Gates,
    Level,
    Quality,
    Verdict,
    reconcile,
    signature_for,
)
from app.services.reconcile.dissent import build_dissent, verify_shippable  # noqa: E402
from app.services.reconcile.engine import aggregate_evidence, label_for  # noqa: E402
from app.services.reconcile.levels import (  # noqa: E402
    _apply_terrain_contradiction_cap,
    _clamp_to_ceiling,
)
from app.services.reconcile.signatures import SIGNATURES  # noqa: E402
from app.services.reconcile.weights import (  # noqa: E402
    DEFAULT_WEIGHTS,
    _validate_weights,
)

# --- FamilyEvidence --------------------------------------------------------


@pytest.mark.parametrize("bad", [-1.5, 1.5, 2.0, -3.0])
def test_family_evidence_rejects_out_of_range_agreement(bad: float) -> None:
    with pytest.raises(ValueError, match="outside"):
        FamilyEvidence(family="terrain", agreement=bad, available=True, reason="x")


def test_family_evidence_rejects_empty_reason() -> None:
    """A family without a reason cannot be printed in the Evidence Pack."""
    with pytest.raises(ValueError, match="empty reason"):
        FamilyEvidence(family="terrain", agreement=1.0, available=True, reason="")


# --- Quality ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("mi", "ds"),
    [(-0.1, 0.5), (1.1, 0.5), (0.5, -0.2), (0.5, 1.4)],
)
def test_quality_rejects_out_of_unit_range(mi: float, ds: float) -> None:
    with pytest.raises(ValueError, match="outside"):
        Quality(metadata_integrity=mi, data_sufficiency=ds)


def test_quality_product_is_the_multiplier() -> None:
    assert Quality(metadata_integrity=0.5, data_sufficiency=0.5).product == 0.25


# --- Gates -----------------------------------------------------------------


@pytest.mark.parametrize("pixel_area", [0.0, -900.0])
def test_gates_rejects_non_positive_pixel_area(pixel_area: float) -> None:
    g = Gates(
        detectability_passed=True,
        expected_footprint_m2=100.0,
        pixel_area_m2=pixel_area,
        escalated_to_cluster=False,
    )
    with pytest.raises(ValueError, match="must be positive"):
        _ = g.footprint_pixels


# --- EvidenceBundle --------------------------------------------------------


def test_bundle_rejects_duplicate_families() -> None:
    with pytest.raises(ValueError, match="duplicate families"):
        bundle(families=(fam("terrain", 1.0), fam("terrain", -1.0)))


def test_bundle_rejects_unknown_family() -> None:
    """The family set is frozen by ADR-001; a typo must not create a 7th family."""
    rogue = FamilyEvidence.__new__(FamilyEvidence)
    object.__setattr__(rogue, "family", "metadata")
    object.__setattr__(rogue, "agreement", 1.0)
    object.__setattr__(rogue, "available", True)
    object.__setattr__(rogue, "reason", "smuggled in")
    object.__setattr__(rogue, "lineage", {})
    object.__setattr__(rogue, "cluster_scale", False)
    with pytest.raises(ValueError, match="unknown families"):
        bundle(families=(rogue,))


def test_bundle_accessors() -> None:
    b = bundle(families=all_agreeing(1.0))
    assert b.get("terrain") is not None
    assert b.get("photo") is not None
    assert len(b.available()) == 6
    assert set(b.by_family()) == set(DEFAULT_WEIGHTS)


def test_bundle_get_returns_none_for_absent_family() -> None:
    b = bundle(families=(fam("terrain", 1.0),))
    assert b.get("satellite") is None


# --- Verdict ---------------------------------------------------------------


def _minimal_verdict(**overrides: object) -> Verdict:
    base: dict[str, object] = {
        "claim_id": "V-1",
        "label": "CORROBORATED",
        "level": Level.L2_CORROBORATED,
        "score": 0.5,
        "confidence": 0.4,
        "coverage": 1.0,
        "quality": 0.8,
        "data_sufficiency": 0.8,
        "rule_path": ("L2_CORROBORATED",),
        "dissent": ("something",),
        "recommended_action": "no_action",
        "priority": None,
        "engine_version": "engine-test",
        "weights": dict(DEFAULT_WEIGHTS),
        "lineage": {},
    }
    base.update(overrides)
    return Verdict(**base)  # type: ignore[arg-type]


def test_verdict_rejects_empty_dissent() -> None:
    with pytest.raises(ValueError, match="empty dissent panel"):
        _minimal_verdict(dissent=())


def test_verdict_rejects_confidence_exceeding_abs_score() -> None:
    """Invariant I1, enforced at construction.

    This is the exact defect that produced the unreproducible confidence 0.71
    in an earlier draft of the design document's Worked Example B.
    """
    with pytest.raises(ValueError, match="arithmetically impossible"):
        _minimal_verdict(score=-0.59, confidence=0.71)


# --- weights ---------------------------------------------------------------


def test_validate_weights_rejects_missing_family() -> None:
    partial = {k: v for k, v in DEFAULT_WEIGHTS.items() if k != "context"}
    with pytest.raises(ValueError, match="exactly the six frozen families"):
        _validate_weights(partial)  # type: ignore[arg-type]


def test_validate_weights_rejects_sum_not_one() -> None:
    """The exact guard that caught the 0.95 weight set during Stage 1."""
    bad = dict(DEFAULT_WEIGHTS)
    bad["context"] = 0.03
    with pytest.raises(ValueError, match="must sum to 1.0"):
        _validate_weights(bad)


def test_validate_weights_rejects_negative_weight() -> None:
    bad = dict(DEFAULT_WEIGHTS)
    bad["context"] = -0.02
    bad["terrain"] = 0.35
    with pytest.raises(ValueError, match="non-monotonic"):
        _validate_weights(bad)


def test_config_fingerprint_is_stable_and_sensitive() -> None:
    a = EngineConfig()
    b = EngineConfig()
    assert a.fingerprint() == b.fingerprint()
    c = EngineConfig(agreeing_threshold=0.40)
    assert c.fingerprint() != a.fingerprint()


# --- signatures ------------------------------------------------------------


def test_signature_for_unknown_type_fails_loudly() -> None:
    """A silent fallback would score a claim against the wrong expectations."""
    with pytest.raises(KeyError, match="no expected signature"):
        signature_for("moon_base")


def test_every_signature_has_a_positive_footprint_midpoint() -> None:
    for key, sig in SIGNATURES.items():
        assert sig.footprint_min_m2 <= sig.footprint_max_m2, key
        assert sig.typical_footprint_m2 >= 0.0, key


def test_signature_keys_match_type_key_field() -> None:
    for key, sig in SIGNATURES.items():
        assert key == sig.type_key


# --- label mapping ---------------------------------------------------------


def test_label_falls_through_to_inconclusive_below_partial_floor() -> None:
    cfg = EngineConfig()
    assert label_for(Level.L1_OBSERVED, 0.50, cfg) == "CORROBORATED"
    assert label_for(Level.L1_OBSERVED, 0.20, cfg) == "PARTIAL"
    assert label_for(Level.L1_OBSERVED, 0.05, cfg) == "INCONCLUSIVE"


# --- clamp / cap defensive branches ---------------------------------------


def test_clamp_leaves_negative_levels_untouched() -> None:
    """A negative level has no position on the positive ladder to clamp to."""
    rule_path: list[str] = []
    sig = signature_for("farm_pond")
    assert _clamp_to_ceiling(Level.N3_CONTRADICTED, sig, rule_path) is Level.N3_CONTRADICTED
    assert rule_path == []


def test_clamp_leaves_level_alone_when_ceiling_is_not_positive() -> None:
    sig = signature_for("check_dam")
    negative_ceiling = dataclasses.replace(sig, ceiling=Level.N1_INCONCLUSIVE)
    rule_path: list[str] = []
    assert (
        _clamp_to_ceiling(Level.L4_CONTROL_DIFFERENCED, negative_ceiling, rule_path)
        is Level.L4_CONTROL_DIFFERENCED
    )
    assert rule_path == []


def test_terrain_cap_leaves_negative_levels_untouched() -> None:
    rule_path: list[str] = []
    b = bundle(families=(fam("terrain", -1.0),))
    assert (
        _apply_terrain_contradiction_cap(
            Level.N1_INCONCLUSIVE, b, EngineConfig(), rule_path, "reason"
        )
        is Level.N1_INCONCLUSIVE
    )
    assert rule_path == []


def test_terrain_cap_noop_when_terrain_family_absent() -> None:
    rule_path: list[str] = []
    b = bundle(families=(fam("satellite", 1.0),))
    assert (
        _apply_terrain_contradiction_cap(
            Level.L4_CONTROL_DIFFERENCED, b, EngineConfig(), rule_path, "reason"
        )
        is Level.L4_CONTROL_DIFFERENCED
    )
    assert rule_path == []


# --- dissent ---------------------------------------------------------------


def test_dissent_has_a_truthful_fallback_for_a_perfect_bundle() -> None:
    """A flawless bundle still gets a panel — it just says so honestly."""
    b = bundle(
        claim_id="PERFECT",
        families=all_agreeing(1.0),
        metadata_integrity=1.0,
        data_sufficiency=1.0,
        gate=gates(passed=True),
    )
    cfg = EngineConfig()
    agg = aggregate_evidence(b, cfg)
    panel = build_dissent(b, agg, Level.L4_CONTROL_DIFFERENCED, cfg, signature_for("check_dam"))
    assert panel
    assert any("not a causal claim" in entry.lower() for entry in panel)


def test_counter_evidence_inverts_on_a_negative_verdict() -> None:
    """On a negative verdict, AGREEING families are the counter-evidence.

    This is the property that makes the empty-panel fallback in dissent.py
    unreachable, and it is also correct on its own terms: if the system is
    about to report a claim as UNSUPPORTED, the officer's first question is
    "what argues the other way?" — and the answer is every family that agreed.
    """
    b = bundle(
        claim_id="NEGATIVE-INVERTS",
        families=all_agreeing(1.0),
        metadata_integrity=1.0,
        data_sufficiency=1.0,
        gate=gates(passed=True),
    )
    cfg = EngineConfig()
    agg = aggregate_evidence(b, cfg)
    bare = dataclasses.replace(signature_for("check_dam"), note="")
    panel = build_dissent(b, agg, Level.N2_UNSUPPORTED, cfg, bare)

    assert len(panel) == 6, panel
    assert all(entry.startswith("Counter-evidence") for entry in panel)
    for family in DEFAULT_WEIGHTS:
        assert any(family in entry for entry in panel), family
    # No causal-ceiling note: that belongs only on positive verdicts.
    assert not any("causal" in entry.lower() for entry in panel)


def test_verify_shippable_rejects_terrain_path_without_disclosure() -> None:
    """The N3_TERRAIN_PATH disclosure is the sentence a judge looks for."""
    v = _minimal_verdict(
        level=Level.N3_CONTRADICTED,
        label="CONTRADICTED",
        rule_path=("N3_TERRAIN_PATH",),
        dissent=("terrain says no",),
    )
    with pytest.raises(AssertionError, match="does not disclose"):
        verify_shippable(v)


def test_verify_shippable_passes_on_a_real_terrain_path_verdict() -> None:
    b = EvidenceBundle(
        claim_id="TERRAIN-PATH",
        intervention_type="farm_pond",
        families=(
            fam("terrain", -1.0),
            fam("satellite", -1.0, cluster_scale=True),
            fam("temporal", -1.0, cluster_scale=True),
            fam("control", 0.0),
            fam("photo", 0.4),
            fam("context", 0.0),
        ),
        gates=gates(passed=False, footprint_m2=625.0, escalated=True),
        quality=Quality(metadata_integrity=0.7, data_sufficiency=0.8),
        alternatives=(
            Alternative(description="gps error", excluded=True, basis="14 m cannot move 340 m"),
        ),
    )
    v = reconcile(b)
    assert v.level is Level.N3_CONTRADICTED
    assert "N3_TERRAIN_PATH" in v.rule_path
    verify_shippable(v)


def test_terrain_path_requires_an_excluded_alternative() -> None:
    """The final exclusion test on N3_TERRAIN_PATH.

    Identical to the case above except that no alternative has been actively
    excluded. "We didn't think of any" must not read the same as "we ruled them
    out", so the path refuses to fire and the verdict degrades to N2.
    """
    b = EvidenceBundle(
        claim_id="TERRAIN-PATH-NO-ALT",
        intervention_type="farm_pond",
        families=(
            fam("terrain", -1.0),
            fam("satellite", -1.0, cluster_scale=True),
            fam("temporal", -1.0, cluster_scale=True),
            fam("control", 0.0),
            fam("photo", 0.4),
            fam("context", 0.0),
        ),
        gates=gates(passed=False, footprint_m2=625.0, escalated=True),
        quality=Quality(metadata_integrity=0.7, data_sufficiency=0.8),
        alternatives=(
            Alternative(
                description="pond built but never filled",
                excluded=False,
                basis="cannot be excluded from imagery alone",
            ),
        ),
    )
    v = reconcile(b)
    assert v.level is Level.N2_UNSUPPORTED
    assert "N3_TERRAIN_PATH" not in v.rule_path
    assert "n3_terrain_blocked_by=no_alternative_excluded" in v.rule_path
