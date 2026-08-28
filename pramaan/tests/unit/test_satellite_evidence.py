"""Tests for the satellite evidence adapter.

The adapter's job is mostly *refusing* to score, so that is what these weight
towards. Three refusals carry the design:

1. an index outside the type's expected signature is recorded, never scored;
2. a composite below the usable-fraction floor is rejected, not down-weighted;
3. nothing scoreable means the family is **unavailable**, not neutral (I4).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.reconcile.signatures import signature_for  # noqa: E402
from app.services.satellite.evidence import (  # noqa: E402
    MIN_USABLE_FRACTION,
    NOISE_FLOOR,
    STRONG_SIGNAL,
    IndexObservation,
    assess,
    expected_direction,
    to_family_evidence,
)


def obs(name: str, value: float, usable: float = 0.95, n: int = 4) -> IndexObservation:
    return IndexObservation(index_name=name, value=value, usable_fraction=usable, n_scenes=n)


# --- validation ----------------------------------------------------------


@pytest.mark.parametrize("usable", [-0.01, 1.01])
def test_usable_fraction_outside_unit_range_is_rejected(usable: float) -> None:
    with pytest.raises(ValueError, match="usable_fraction"):
        obs("MNDWI", 0.3, usable=usable)


def test_negative_scene_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="n_scenes"):
        IndexObservation(index_name="MNDWI", value=0.3, usable_fraction=0.9, n_scenes=-1)


# --- signature matching --------------------------------------------------


def test_direction_matches_a_qualified_signature_name() -> None:
    """A check dam's signature names `NDVI_rabi_command`; the producer measures
    `NDVI`. Without prefix matching the vegetation signal is silently unscored.
    """
    sig = signature_for("check_dam")
    assert "NDVI_rabi_command" in sig.expect_increase
    assert expected_direction("NDVI", sig) == 1
    assert expected_direction("MNDWI", sig) == 1


def test_an_index_outside_the_signature_scores_zero_direction() -> None:
    """A rising MNDWI corroborates a check dam and means nothing for a contour
    trench, whose signature has no water term."""
    assert expected_direction("MNDWI", signature_for("contour_trench")) == 0


def test_unscored_indices_are_recorded_not_dropped() -> None:
    a = assess([obs("MNDWI", 0.4)], "contour_trench")
    assert a.scored == 0
    assert "MNDWI" in a.unscored
    assert any("not part of the expected signature" in r for r in a.reasons)


# --- scoring -------------------------------------------------------------


def test_a_strong_expected_reading_agrees() -> None:
    a = assess([obs("MNDWI", STRONG_SIGNAL)], "check_dam")
    assert a.agreement == pytest.approx(1.0)
    assert a.scored == 1


def test_a_strong_contrary_reading_disagrees() -> None:
    a = assess([obs("MNDWI", -STRONG_SIGNAL)], "check_dam")
    assert a.agreement == pytest.approx(-1.0)


def test_a_marginal_reading_produces_marginal_agreement() -> None:
    """Scaled, not stepped: a threshold here would turn a 0.001 difference in
    reflectance into a categorical change of verdict."""
    a = assess([obs("MNDWI", STRONG_SIGNAL / 2)], "check_dam")
    assert 0.4 < a.agreement < 0.6


def test_a_reading_inside_the_noise_floor_asserts_nothing() -> None:
    a = assess([obs("MNDWI", NOISE_FLOOR / 2)], "check_dam")
    assert a.agreement == 0.0
    assert a.scored == 1, "it was scoreable; it just carries no signal"
    assert any("no state asserted" in r for r in a.reasons)


def test_agreement_is_clamped_to_the_unit_interval() -> None:
    """MNDWI can reach +1.0, five times STRONG_SIGNAL. Without the clamp the
    engine would receive an out-of-range agreement and raise."""
    a = assess([obs("MNDWI", 1.0)], "check_dam")
    assert a.agreement == pytest.approx(1.0)
    ev = to_family_evidence(a)
    assert -1.0 <= ev.agreement <= 1.0


def test_a_cloudier_composite_counts_for_less() -> None:
    """Two observations disagreeing, the confident one over more clear pixels."""
    clear_agrees = assess(
        [obs("MNDWI", STRONG_SIGNAL, usable=0.95), obs("NDVI", -STRONG_SIGNAL, usable=0.40)],
        "check_dam",
    )
    clear_disagrees = assess(
        [obs("MNDWI", STRONG_SIGNAL, usable=0.40), obs("NDVI", -STRONG_SIGNAL, usable=0.95)],
        "check_dam",
    )
    assert clear_agrees.agreement > 0 > clear_disagrees.agreement


# --- refusals ------------------------------------------------------------


def test_a_composite_below_the_usable_floor_is_rejected() -> None:
    """Rejected outright, not down-weighted: a value over mostly-cloud pixels is
    noise wearing a number."""
    a = assess([obs("MNDWI", 0.4, usable=MIN_USABLE_FRACTION - 0.01)], "check_dam")
    assert a.scored == 0
    assert "MNDWI" in a.rejected
    assert "below the" in a.rejected["MNDWI"]


def test_the_floor_matches_the_temporal_modules_floor() -> None:
    """A split floor would let this family speak about a composite the temporal
    family had already refused."""
    from app.services.temporal.windows import MIN_OBSERVATION_SUFFICIENCY

    assert MIN_USABLE_FRACTION == MIN_OBSERVATION_SUFFICIENCY


def test_nothing_scoreable_means_unavailable_not_neutral() -> None:
    """Invariant I4. Neutral would raise coverage on evidence never gathered."""
    ev = to_family_evidence(assess([obs("MNDWI", 0.4, usable=0.1)], "check_dam"))
    assert ev.available is False
    assert ev.agreement == 0.0
    assert "could" in ev.reason


def test_no_observations_at_all_is_unavailable_with_a_stated_reason() -> None:
    ev = to_family_evidence(assess([], "check_dam"))
    assert ev.available is False
    assert "No usable composites" in ev.reason


def test_a_type_with_no_optical_signature_is_unavailable() -> None:
    """A dug well cannot be assessed optically at 30 m. The family must say so
    rather than score zero and let coverage imply it was looked at."""
    ev = to_family_evidence(assess([obs("NDVI", 0.5)], "dug_well"))
    assert ev.available is False


# --- lineage -------------------------------------------------------------


def test_lineage_records_every_observation_including_rejected_ones() -> None:
    """An auditor must be able to see what was looked at and why it was
    discarded, not only what survived."""
    a = assess(
        [obs("MNDWI", 0.4), obs("NDVI", 0.3, usable=0.1), obs("BSI", 0.2)],
        "check_dam",
    )
    lineage = to_family_evidence(a).lineage
    observations = lineage["observations"]
    assert isinstance(observations, list)
    assert len(observations) == 3, "rejected observations stay in the lineage"
    assert "NDVI" in lineage["rejected"]  # type: ignore[operator]
    assert lineage["min_usable_fraction"] == MIN_USABLE_FRACTION


def test_lineage_extra_is_merged() -> None:
    ev = to_family_evidence(
        assess([obs("MNDWI", 0.4)], "check_dam"),
        lineage_extra={"scene_ids": ["HLS.S30.T43QGB.2024311T052011.v2.0"]},
    )
    assert ev.lineage["scene_ids"] == ["HLS.S30.T43QGB.2024311T052011.v2.0"]


def test_cluster_scale_is_carried_into_the_evidence() -> None:
    """A cluster-scale reading must never be presented as per-structure."""
    ev = to_family_evidence(assess([obs("MNDWI", 0.4)], "check_dam"), cluster_scale=True)
    assert ev.cluster_scale is True
    ev2 = to_family_evidence(
        assess([obs("MNDWI", 0.4, usable=0.1)], "check_dam"), cluster_scale=True
    )
    assert ev2.cluster_scale is True, "also on the unavailable path"


def test_the_reason_names_the_aoi_it_measured() -> None:
    """ "MNDWI is high" is not auditable without knowing over what."""
    a = assess(
        [
            IndexObservation(
                index_name="MNDWI",
                value=0.4,
                usable_fraction=0.9,
                n_scenes=3,
                aoi="site disk + 300 m command buffer",
            )
        ],
        "check_dam",
    )
    assert "300 m command buffer" in to_family_evidence(a).reason


def test_an_unregistered_index_uses_zero_as_its_neutral_point() -> None:
    """Documented fallback. An index whose neutral point is not zero must be
    registered in NEUTRAL_POINT; this pins the default so adding one is a
    deliberate act rather than an accident."""
    from app.services.satellite.evidence import NEUTRAL_POINT

    assert "MNDWI" in NEUTRAL_POINT
    a = assess([obs("MNDWI", STRONG_SIGNAL)], "check_dam")
    assert a.agreement == pytest.approx(1.0)


# --- indices a signature expects to FALL ---------------------------------


def test_an_index_expected_to_decrease_resolves_to_minus_one() -> None:
    """Six types expect BSI (bare-soil) to fall — a contour trench works by
    getting soil covered. Without this branch every one of them would read as
    "not in the signature" and go unscored, silently losing the only optical
    signal those types have."""
    sig = signature_for("contour_trench")
    assert "BSI" in sig.expect_decrease
    assert expected_direction("BSI", sig) == -1


def test_a_falling_bsi_agrees_with_a_contour_trench() -> None:
    """Soil got covered, which is exactly what the intervention should do."""
    a = assess([obs("BSI", -STRONG_SIGNAL)], "contour_trench")
    assert a.scored == 1
    assert a.agreement == pytest.approx(1.0)


def test_a_rising_bsi_disagrees_with_a_contour_trench() -> None:
    """More bare soil after a soil-conservation work is contrary evidence, and
    the sign must not be swallowed by the direction flip."""
    a = assess([obs("BSI", STRONG_SIGNAL)], "contour_trench")
    assert a.agreement == pytest.approx(-1.0)
    assert any("against expectation" in r for r in a.reasons)


def test_the_reason_states_which_way_the_index_was_expected_to_read() -> None:
    """An auditor cannot check a sign convention that is not written down."""
    down = assess([obs("BSI", -STRONG_SIGNAL)], "contour_trench")
    up = assess([obs("MNDWI", STRONG_SIGNAL)], "check_dam")
    assert any("expected low" in r for r in down.reasons)
    assert any("expected high" in r for r in up.reasons)
