"""Tests for the photo producer and the scene-scale gate (docs §16.2 STEP 5).

The gate was specified in the design document and implemented nowhere: the
engine recorded `scene_scale` in a verdict's lineage and then ignored it. These
tests pin the behaviour now that it acts, because it is the mechanism that stops
a close-up of a wet pipe from being read as evidence about a 900 m² pixel.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.photo import LabelPrediction, PhotoLabels, to_family_evidence  # noqa: E402
from app.services.photo.evidence import (  # noqa: E402
    CLOSE_UP_ATTENUATION,
    MAX_NEGATIVE_AGREEMENT,
    UNKNOWN_SCALE_ATTENUATION,
)


def pred(key: str, calibrated: float, band: tuple[float, float] = (0.4, 0.6)) -> LabelPrediction:
    low, high = band
    decision = "abstain" if low <= calibrated <= high else ("yes" if calibrated > high else "no")
    return LabelPrediction(
        key=key, raw=calibrated, calibrated=calibrated, decision=decision, abstain_band=band
    )


def labels(
    *,
    scale: str = "mid",
    scale_conf: float = 0.95,
    structure: float = 0.9,
    water: float = 0.9,
    erosion: float = 0.1,
    struct_class: str | None = "masonry_check_dam",
    veg: str | None = None,
    image_id: str = "IMG-1",
) -> PhotoLabels:
    extra: dict[str, object] = {}
    if struct_class is not None:
        extra["structure_type_value"] = struct_class
    if veg is not None:
        extra["vegetation_density_value"] = veg
    return PhotoLabels(
        image_id=image_id,
        labels={
            "structure_present": pred("structure_present", structure),
            "water_present": pred("water_present", water),
            "erosion_visible": pred("erosion_visible", erosion),
        },
        scene_scale=scale,  # type: ignore[arg-type]
        scene_scale_confidence=scale_conf,
        model_name="siglip2-zeroshot",
        model_version="v1",
        calibration_date="2026-08-20",
        extra=extra,
    )


# --- LabelPrediction contract ---------------------------------------------


def test_decision_must_be_derivable_from_the_calibrated_score() -> None:
    """A hand-set decision that contradicts the score breaks reproducibility."""
    with pytest.raises(ValueError, match="contradicts calibrated"):
        LabelPrediction(
            key="water_present",
            raw=0.9,
            calibrated=0.9,
            decision="no",
            abstain_band=(0.4, 0.6),
        )


def test_abstain_band_is_validated() -> None:
    with pytest.raises(ValueError, match="invalid abstain band"):
        LabelPrediction(
            key="x", raw=0.5, calibrated=0.5, decision="abstain", abstain_band=(0.8, 0.2)
        )


def test_calibrated_score_must_be_a_probability() -> None:
    with pytest.raises(ValueError, match="outside"):
        LabelPrediction(key="x", raw=1.4, calibrated=1.4, decision="yes", abstain_band=(0.4, 0.6))


def test_label_dict_key_must_match_prediction_key() -> None:
    with pytest.raises(ValueError, match="!="):
        PhotoLabels(
            image_id="X",
            labels={"water_present": pred("structure_present", 0.9)},
            scene_scale="mid",
        )


# --- the scene-scale gate -------------------------------------------------


def test_mid_scale_photo_is_not_attenuated() -> None:
    ev = to_family_evidence(labels(scale="mid"), "check_dam")
    assert ev.available
    assert ev.agreement == pytest.approx(1.0, abs=1e-6)
    assert ev.lineage["scene_scale_factor"] == 1.0
    assert "Scene-scale gate applied" not in ev.reason


def test_close_up_is_attenuated_but_not_nulled() -> None:
    """A close-up still evidences existence — it just cannot speak to extent."""
    mid = to_family_evidence(labels(scale="mid"), "check_dam")
    close = to_family_evidence(labels(scale="close_up"), "check_dam")

    assert close.available, "a close-up is still evidence, not an absence of it"
    assert close.agreement == pytest.approx(mid.agreement * CLOSE_UP_ATTENUATION)
    assert 0 < close.agreement < mid.agreement
    assert "cannot be cross-checked against a 30 m pixel" in close.reason


def test_unknown_scale_is_attenuated() -> None:
    ev = to_family_evidence(labels(scale="unknown"), "check_dam")
    assert ev.lineage["scene_scale_factor"] == UNKNOWN_SCALE_ATTENUATION
    assert "scale-indeterminate" in ev.reason


def test_low_confidence_scale_call_cannot_unlock_full_weight() -> None:
    """Guessing 'mid' at low confidence must not buy full-weight evidence.

    Otherwise the cheapest way for a model to maximise its influence is to
    always claim site-scale framing.
    """
    confident = to_family_evidence(labels(scale="mid", scale_conf=0.95), "check_dam")
    unsure = to_family_evidence(labels(scale="mid", scale_conf=0.30), "check_dam")
    assert unsure.agreement < confident.agreement
    assert unsure.lineage["scene_scale_factor"] == UNKNOWN_SCALE_ATTENUATION
    assert "below the" in unsure.reason


def test_attenuation_is_recorded_in_lineage_for_the_evidence_pack() -> None:
    ev = to_family_evidence(labels(scale="close_up"), "check_dam")
    assert ev.lineage["scene_scale"] == "close_up"
    assert ev.lineage["scene_scale_factor"] == CLOSE_UP_ATTENUATION
    assert "raw_agreement" in ev.lineage


# --- the negative cap -----------------------------------------------------


def test_negative_photo_agreement_is_capped() -> None:
    """`photo` is the claim's own source, so it withholds support, never contradicts.

    Without this cap the photo family alone could push a claim towards a
    contradicted verdict, which would defeat the whole point of weighting
    independent evidence above self-report (ADR-001).
    """
    ev = to_family_evidence(
        labels(scale="mid", structure=0.05, water=0.05, struct_class="none"),
        "check_dam",
    )
    assert ev.agreement >= MAX_NEGATIVE_AGREEMENT
    assert ev.agreement == pytest.approx(MAX_NEGATIVE_AGREEMENT)
    assert "may withhold support" in ev.reason


# --- abstention -----------------------------------------------------------


def test_all_abstained_yields_unavailable_not_neutral() -> None:
    """Invariant I4: abstention contributes exactly zero, via unavailability."""
    ev = to_family_evidence(labels(scale="mid", structure=0.5, water=0.5, erosion=0.5), "check_dam")
    assert not ev.available
    assert ev.agreement == 0.0
    assert "told us nothing" in ev.reason


def test_partial_abstention_still_scores_the_decided_labels() -> None:
    ev = to_family_evidence(labels(scale="mid", water=0.5), "check_dam")
    assert ev.available
    assert ev.lineage["labels_decided"] == 1
    assert "water presence abstained" in ev.reason


# --- expected-signature matching ------------------------------------------


def test_visual_class_matching_the_type_corroborates() -> None:
    ev = to_family_evidence(labels(struct_class="masonry_check_dam"), "check_dam")
    assert ev.agreement > 0
    assert "matches what a check_dam looks like" in ev.reason


def test_visual_class_inconsistent_with_the_type_reduces_agreement() -> None:
    """A well photographed where a check dam is claimed is a real signal."""
    match = to_family_evidence(labels(struct_class="masonry_check_dam"), "check_dam")
    mismatch = to_family_evidence(labels(struct_class="well_or_borewell"), "check_dam")
    assert mismatch.agreement < match.agreement
    assert "not consistent with a check_dam" in mismatch.reason


def test_uncertain_visual_class_is_neither_credited_nor_penalised() -> None:
    ev = to_family_evidence(labels(struct_class="uncertain"), "check_dam")
    assert "class could not be determined" in ev.reason


def test_dry_structure_is_only_weak_negative_evidence() -> None:
    """A check dam photographed in summer is legitimately dry.

    Treating 'no water' as strong disagreement would flag every pre-monsoon
    geotag in the country.
    """
    wet = to_family_evidence(labels(water=0.9), "check_dam")
    dry = to_family_evidence(labels(water=0.05), "check_dam")
    assert dry.agreement < wet.agreement
    assert dry.agreement > MAX_NEGATIVE_AGREEMENT, "dry must not saturate the cap"
    assert "legitimately dry" in dry.reason


def test_water_is_ignored_for_types_whose_signature_does_not_expect_it() -> None:
    """A plantation's signature has no water term, so water must not score it."""
    with_water = to_family_evidence(labels(struct_class="plantation", water=0.9), "plantation")
    no_water = to_family_evidence(labels(struct_class="plantation", water=0.05), "plantation")
    assert with_water.agreement == no_water.agreement
    assert "water" not in with_water.reason.lower()


def test_vegetation_scores_only_types_expecting_an_ndvi_rise() -> None:
    dense = to_family_evidence(labels(struct_class="plantation", veg="dense"), "plantation")
    bare = to_family_evidence(labels(struct_class="plantation", veg="none"), "plantation")
    assert dense.agreement > bare.agreement
    assert "vegetation cover is dense" in dense.reason


def test_active_erosion_counts_against_a_soil_conservation_work() -> None:
    calm = to_family_evidence(
        labels(struct_class="trench_or_contour_work", erosion=0.05, water=0.5),
        "contour_trench",
    )
    eroding = to_family_evidence(
        labels(struct_class="trench_or_contour_work", erosion=0.95, water=0.5),
        "contour_trench",
    )
    assert eroding.agreement < calm.agreement
    assert "intended to arrest it" in eroding.reason


def test_types_with_no_visual_signature_report_unavailable() -> None:
    for type_key in ("livestock", "livelihood"):
        ev = to_family_evidence(labels(struct_class="other_structure"), type_key)
        assert not ev.available, type_key
        assert ev.agreement == 0.0
        assert "no visual signature" in ev.reason


def test_unknown_intervention_type_fails_loudly() -> None:
    with pytest.raises(KeyError, match="no expected signature"):
        to_family_evidence(labels(), "moon_base")


# --- end to end into the engine ------------------------------------------


def test_close_up_photo_cannot_lift_a_claim_to_a_high_level_alone() -> None:
    """The gate's whole purpose, verified through the engine.

    A confident close-up plus nothing else must not produce a strong verdict.
    """
    from app.services.reconcile import EvidenceBundle, Gates, Quality, reconcile

    close_up = to_family_evidence(labels(scale="close_up"), "check_dam")
    bundle = EvidenceBundle(
        claim_id="CLOSEUP-ONLY",
        intervention_type="check_dam",
        families=(close_up,),
        gates=Gates(
            detectability_passed=True,
            expected_footprint_m2=3200.0,
            pixel_area_m2=900.0,
            escalated_to_cluster=False,
            scene_scale="close_up",
        ),
        quality=Quality(metadata_integrity=0.9, data_sufficiency=0.8),
    )
    v = reconcile(bundle)
    # Coverage is the photo weight only, so confidence is structurally small.
    assert v.coverage == pytest.approx(0.12)
    assert v.confidence < 0.1
    assert v.level.value.startswith("L1") or v.level.value.startswith("N1")


# --- remaining branches, exercised deliberately ---------------------------


def test_structure_visible_but_type_unmappable_still_credits_existence() -> None:
    """A type with no visual-class mapping (e.g. 'other') falls back to existence.

    The frame genuinely shows a built structure; we simply cannot check it
    against an expected visual class, so it earns partial rather than full
    credit.
    """
    ev = to_family_evidence(labels(struct_class="other_structure"), "other")
    assert ev.available
    assert ev.agreement > 0
    assert "a built structure is visible" in ev.reason


def test_sparse_vegetation_is_reported_without_credit_or_penalty() -> None:
    """The middle vegetation classes are informative but not evidential."""
    ev = to_family_evidence(
        labels(struct_class="plantation", veg="sparse", water=0.5), "plantation"
    )
    assert "vegetation cover is sparse" in ev.reason
    assert ev.available


def test_uncertain_vegetation_is_not_scored_at_all() -> None:
    scored = to_family_evidence(
        labels(struct_class="plantation", veg="dense", water=0.5), "plantation"
    )
    unscored = to_family_evidence(
        labels(struct_class="plantation", veg="uncertain", water=0.5), "plantation"
    )
    assert "vegetation" not in unscored.reason.lower()
    assert unscored.agreement < scored.agreement


def test_scene_scale_confidence_is_validated() -> None:
    with pytest.raises(ValueError, match="scene_scale_confidence"):
        PhotoLabels(
            image_id="X",
            labels={"water_present": pred("water_present", 0.9)},
            scene_scale="mid",
            scene_scale_confidence=1.5,
        )


def test_accessor_helpers_behave_on_missing_and_present_labels() -> None:
    p = labels()
    assert p.get("water_present") is not None
    assert p.get("nonexistent") is None
    assert p.says_yes("water_present")
    assert not p.says_no("water_present")
    assert p.says_no("erosion_visible")
    assert not p.decided("nonexistent")
    assert p.abstained_keys() == ()
