"""Tests for the frozen GT-1 annotation schema.

The schema is frozen before the sprint starts because a mid-sprint change
invalidates every image already labelled (risk R-41). These tests are the freeze:
they fail if the label set, the vocabularies or the split policy drift.

They matter more than when the plan was written. The measured rejection of
Mapillary (docs/10) made team-collected photographs the *primary* GT-1 source
rather than one of four, so the corpus is now expensive to re-label.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ml.annotation.schema import (  # noqa: E402
    ENGINE_LABELS,
    LABELS,
    LABELS_BY_KEY,
    SCHEMA_VERSION,
    Annotation,
    ConstructionStage,
    SceneScale,
    SplitPolicy,
    SprintTarget,
    StructureType,
    Ternary,
    VegetationDensity,
    validate_answers,
)


def complete(**overrides: str) -> dict[str, str]:
    base = {
        "water_present": "yes",
        "structure_present": "yes",
        "structure_type": "masonry_check_dam",
        "vegetation_density": "moderate",
        "exposed_soil": "no",
        "erosion_visible": "no",
        "construction_stage": "completed",
        "scene_scale": "mid",
        "people_present": "no",
        "unusable": "no",
    }
    base.update(overrides)
    return base


# --- the freeze -----------------------------------------------------------


def test_schema_version_is_pinned() -> None:
    assert SCHEMA_VERSION == "gt1-v1"


def test_label_set_is_exactly_as_frozen() -> None:
    """If this fails, either revert or bump SCHEMA_VERSION and re-derive splits."""
    assert tuple(s.key for s in LABELS) == (
        "water_present",
        "structure_present",
        "structure_type",
        "vegetation_density",
        "exposed_soil",
        "erosion_visible",
        "construction_stage",
        "scene_scale",
        "people_present",
        "unusable",
    )


def test_every_label_carries_annotator_guidance() -> None:
    """A label without guidance produces disagreement, which shows up as low kappa."""
    for spec in LABELS:
        assert spec.guidance.strip(), spec.key
        assert len(spec.guidance) > 40, f"{spec.key} guidance is too thin to disambiguate"


#: The three spellings an "I cannot tell" answer takes across the schema.
#: `scene_scale` uses `unknown` rather than `uncertain` because its vocabulary
#: is pinned to the engine's `SceneScale` type (see the parity test below) —
#: one shared vocabulary is worth more than uniform naming inside the schema.
UNCERTAINTY_SPELLINGS = ("uncertain", "unknown", "not_applicable")


def test_every_label_has_an_uncertain_state() -> None:
    """Annotator uncertainty is data, not a gap to be defaulted to 'no'."""
    for spec in LABELS:
        allowed = tuple(t.value for t in Ternary) if spec.kind == "ternary" else spec.values
        assert any(v in UNCERTAINTY_SPELLINGS for v in allowed), (
            f"{spec.key} offers no way for an annotator to say 'I cannot tell'; "
            f"allowed values are {allowed}"
        )


def test_scene_scale_uncertainty_is_spelled_unknown_for_engine_parity() -> None:
    """Pins the one deliberate naming inconsistency so it is not 'fixed' later.

    Renaming `unknown` to `uncertain` here would silently break the vocabulary
    shared with `app.services.reconcile.types.SceneScale`, and the scene-scale
    gate would stop recognising indeterminate frames.
    """
    values = LABELS_BY_KEY["scene_scale"].values
    assert "unknown" in values
    assert "uncertain" not in values


def test_privacy_and_quality_labels_are_not_model_targets() -> None:
    """people_present exists to route to face blurring, never to be predicted."""
    assert not LABELS_BY_KEY["people_present"].feeds_engine
    assert not LABELS_BY_KEY["unusable"].feeds_engine
    assert "people_present" not in ENGINE_LABELS
    assert "unusable" not in ENGINE_LABELS


def test_scene_scale_vocabulary_matches_the_engine_exactly() -> None:
    """One vocabulary from annotator to verdict — no translation table to drift."""
    from app.services.reconcile.types import Gates

    engine_values = {"close_up", "mid", "landscape", "unknown"}
    assert {s.value for s in SceneScale} == engine_values
    # And the engine actually accepts each one.
    for value in engine_values:
        Gates(
            detectability_passed=True,
            expected_footprint_m2=1.0,
            pixel_area_m2=900.0,
            escalated_to_cluster=False,
            scene_scale=value,  # type: ignore[arg-type]
        )


def test_construction_stage_admits_it_cannot_see_not_initiated() -> None:
    """DRISHTI has 4 statuses; only 3 are visually distinguishable."""
    values = {c.value for c in ConstructionStage}
    assert "not_started" in values
    assert "not_applicable" in values
    assert "initiated" not in values, (
        "'initiated' is not distinguishable from 'not started' in a photograph; "
        "claiming otherwise would invite annotator disagreement"
    )


def test_structure_types_are_fewer_than_the_drishti_taxonomy() -> None:
    """The photo model must not pretend to resolve what a photo cannot."""
    from app.services.reconcile.signatures import SIGNATURES

    visual = {s.value for s in StructureType}
    assert len(visual) < len(SIGNATURES), (
        "the visual class list should be strictly coarser than the intervention "
        "taxonomy: farm ponds and percolation tanks are indistinguishable at "
        "close range"
    )
    assert "farm_pond" not in visual
    assert "excavated_pond_or_tank" in visual


# --- validation -----------------------------------------------------------


def test_complete_annotation_validates() -> None:
    validate_answers(complete())


def test_missing_label_is_rejected() -> None:
    answers = complete()
    del answers["scene_scale"]
    with pytest.raises(ValueError, match="missing required labels"):
        validate_answers(answers)


def test_unknown_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown labels"):
        validate_answers(complete(rainfall="heavy"))


def test_out_of_vocabulary_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="not in"):
        validate_answers(complete(water_present="maybe"))


def test_structure_absent_but_typed_is_rejected() -> None:
    """The most common annotator contradiction, checked rather than trusted."""
    with pytest.raises(ValueError, match="set structure_type=none"):
        validate_answers(complete(structure_present="no", structure_type="masonry_check_dam"))


def test_structure_present_but_untyped_is_rejected() -> None:
    with pytest.raises(ValueError, match="choose a type or"):
        validate_answers(complete(structure_present="yes", structure_type="none"))


def test_structure_absent_with_uncertain_type_is_allowed() -> None:
    validate_answers(complete(structure_present="no", structure_type="uncertain"))


def test_annotation_rejects_a_foreign_schema_version() -> None:
    """Corpora labelled under two schemas must never be pooled."""
    with pytest.raises(ValueError, match="must not be pooled"):
        Annotation(
            image_id="IMG-1",
            annotator="a1",
            schema_version="gt1-v0",
            answers=complete(),
        )


def test_annotation_validates_answers_on_construction() -> None:
    with pytest.raises(ValueError, match="missing required labels"):
        Annotation(
            image_id="IMG-1",
            annotator="a1",
            schema_version=SCHEMA_VERSION,
            answers={"water_present": "yes"},
        )


def test_vegetation_density_is_ordinal_not_a_fake_percentage() -> None:
    assert {v.value for v in VegetationDensity} == {
        "none",
        "sparse",
        "moderate",
        "dense",
        "uncertain",
    }


# --- split policy ---------------------------------------------------------


def test_split_fractions_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="must sum to 1.0"):
        SplitPolicy(train_fraction=0.7, val_fraction=0.2, test_fraction=0.2)


def test_split_is_grouped_geographically_not_random() -> None:
    """Random splits leak: field photos come in bursts at one site."""
    assert SplitPolicy().group_by == "micro_watershed_code"


def test_lucas_is_excluded_from_the_test_split() -> None:
    """EU imagery may pre-train, but must never appear in reported Indian metrics."""
    policy = SplitPolicy()
    assert "lucas" in policy.train_sources_allowed
    assert "lucas" not in policy.test_sources_allowed


def test_mapillary_is_confined_to_the_culvert_subset() -> None:
    """Post-measurement (docs/10): only the narrow nala-culvert use survives."""
    policy = SplitPolicy()
    assert "mapillary_culvert" in policy.train_sources_allowed
    assert not any(s.startswith("mapillary") for s in policy.test_sources_allowed)


def test_test_sources_are_a_subset_of_train_vocabulary() -> None:
    with pytest.raises(ValueError, match="not present in train vocabulary"):
        SplitPolicy(
            train_sources_allowed=("team_collected",),
            test_sources_allowed=("team_collected", "commons"),
        )


# --- sprint budget --------------------------------------------------------


def test_sprint_mix_reflects_the_mapillary_rejection() -> None:
    """Team collection is now the primary source, not one of four."""
    mix = SprintTarget().source_mix
    assert mix["team_collected"] >= sum(v for k, v in mix.items() if k != "team_collected"), (
        "team-collected must dominate the corpus after the Mapillary rejection"
    )
    assert mix["mapillary_culvert"] <= 50


def test_sprint_mix_meets_the_minimum() -> None:
    target = SprintTarget()
    assert sum(target.source_mix.values()) >= target.minimum_images


def test_double_annotation_fraction_supports_a_meaningful_kappa() -> None:
    assert SprintTarget().double_annotated_fraction >= 0.10


def test_person_hours_accounts_for_double_annotation() -> None:
    t = SprintTarget()
    naive = t.target_images * t.assumed_seconds_per_image / 3600.0
    assert t.person_hours() > naive
