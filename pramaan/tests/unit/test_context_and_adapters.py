"""Tests for the temporal/control/context adapters, and a full six-family assembly.

The adapters are where a producer's numbers become evidence, so the properties
under test are mostly about *refusing* to score things:

* an index the intervention type does not expect is not scored at all;
* an unpaired season is unavailable, never neutral;
* a control set below the minimum is unavailable, never neutral;
* rainfall in the same direction as the change ARGUES AGAINST the claim.

The last one is the counter-intuitive core of the context family: a good monsoon
must not let the system claim credit.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.context import RainfallContext, to_context_evidence  # noqa: E402
from app.services.context.rainfall import (  # noqa: E402
    ANOMALY_THRESHOLD,
    STRONG_ANOMALY_THRESHOLD,
)
from app.services.temporal import controls as ct  # noqa: E402
from app.services.temporal import seasons as sn  # noqa: E402
from app.services.temporal import trend as tr  # noqa: E402
from app.services.temporal.evidence import (  # noqa: E402
    SEASON_WEIGHT,
    assess,
    to_control_evidence,
    to_temporal_evidence,
)


def delta(
    index: str,
    season: sn.Season,
    change: float,
    *,
    pre_year: int = 2022,
    post_year: int = 2025,
    suff: float = 0.9,
) -> sn.SeasonalDelta:
    pre = sn.SeasonalObservation(
        index_name=index,
        season=season,
        year=pre_year,
        value=0.30,
        data_sufficiency=suff,
        n_scenes=4,
    )
    post = sn.SeasonalObservation(
        index_name=index,
        season=season,
        year=post_year,
        value=0.30 + change,
        data_sufficiency=suff,
        n_scenes=4,
    )
    return sn.seasonal_delta(pre, post)


def rain(anom: float, season: sn.Season = sn.Season.RABI) -> RainfallContext:
    return RainfallContext(
        season=season, year=2024, rainfall_mm=100.0 * anom, decadal_mean_mm=100.0
    )


# --- temporal adapter: scoring against the expected signature ------------


def test_index_in_the_signature_is_scored() -> None:
    """A check dam expects MNDWI to rise."""
    a = assess([delta("MNDWI", sn.Season.RABI, +0.12)], "check_dam")
    assert a.scored == 1
    assert a.agreement > 0.9
    ev = to_temporal_evidence(a)
    assert ev.available
    assert ev.agreement > 0.9


def test_index_absent_from_the_signature_is_not_scored() -> None:
    """A contour trench's signature has no water term, so MNDWI means nothing."""
    a = assess([delta("MNDWI", sn.Season.RABI, +0.30)], "contour_trench")
    assert a.scored == 0
    assert any("not part of the expected signature" in r for r in a.reasons)
    ev = to_temporal_evidence(a)
    assert not ev.available, "must be unavailable, not neutral (invariant I4)"


def test_change_against_expectation_scores_negative() -> None:
    """A check dam whose MNDWI FELL is evidence against the claim."""
    a = assess([delta("MNDWI", sn.Season.RABI, -0.12)], "check_dam")
    assert a.agreement < -0.9
    assert any("against expectation" in r for r in a.reasons)


def test_expected_decrease_is_honoured() -> None:
    """A contour trench expects BSI to FALL, so a fall corroborates."""
    fell = assess([delta("BSI", sn.Season.RABI, -0.12)], "contour_trench")
    rose = assess([delta("BSI", sn.Season.RABI, +0.12)], "contour_trench")
    assert fell.agreement > 0.9
    assert rose.agreement < -0.9


def test_sub_noise_delta_scores_zero_and_says_so() -> None:
    a = assess([delta("MNDWI", sn.Season.RABI, +0.005)], "check_dam")
    assert a.scored == 1
    assert a.agreement == 0.0
    assert any("noise floor" in r for r in a.reasons)


def test_rabi_outweighs_kharif() -> None:
    """docs §17.2: rabi is the diagnostic season, kharif the weakest.

    Measured justification in docs/11 §8: 75% of rabi scenes are usable against
    10.6% of kharif, and monsoon water bodies are at maximum extent regardless
    of any intervention.
    """
    assert SEASON_WEIGHT[sn.Season.RABI] > SEASON_WEIGHT[sn.Season.SUMMER]
    assert SEASON_WEIGHT[sn.Season.SUMMER] > SEASON_WEIGHT[sn.Season.KHARIF]

    # A rabi rise against a kharif fall of equal size must net positive.
    mixed = assess(
        [
            delta("MNDWI", sn.Season.RABI, +0.12),
            delta("MNDWI", sn.Season.KHARIF, -0.12),
        ],
        "check_dam",
    )
    assert mixed.agreement > 0


def test_skipped_seasons_are_reported_in_the_reason() -> None:
    a = assess(
        [delta("MNDWI", sn.Season.RABI, +0.12)],
        "check_dam",
        skipped={sn.Season.KHARIF: "0 usable scenes after cloud masking"},
    )
    ev = to_temporal_evidence(a)
    assert "kharif not assessed" in ev.reason
    assert "cloud masking" in ev.reason


def test_trend_results_are_carried_into_the_reason_and_lineage() -> None:
    t = tr.mann_kendall([0.10, 0.16, 0.23, 0.29, 0.36, 0.42])
    a = assess([delta("NDVI", sn.Season.RABI, +0.12)], "plantation", trends={"NDVI": t})
    ev = to_temporal_evidence(a)
    assert "NDVI trend" in ev.reason
    assert "trends" in ev.lineage
    assert ev.lineage["trends"]["NDVI"]["direction"] == "increasing"  # type: ignore[index]


def test_temporal_lineage_publishes_the_season_weights() -> None:
    a = assess([delta("MNDWI", sn.Season.RABI, +0.12)], "check_dam")
    lin = to_temporal_evidence(a).lineage
    assert lin["season_weights"]["rabi"] == 1.0  # type: ignore[index]
    assert lin["min_meaningful_delta"] == 0.02


def test_cluster_scale_flag_propagates() -> None:
    """A cluster-scale temporal reading must be labelled as such for the engine."""
    a = assess([delta("MNDWI", sn.Season.RABI, +0.12)], "farm_pond")
    ev = to_temporal_evidence(a, cluster_scale=True)
    assert ev.cluster_scale


# --- control adapter -----------------------------------------------------


def cov() -> ct.SiteCovariates:
    return ct.SiteCovariates(
        slope_deg=3.0,
        aspect_class="S",
        lulc_class="cropland",
        soil_class="clay",
        elevation_m=400.0,
        dist_to_stream_m=100.0,
        strahler_order=2,
    )


def control_set(deltas: list[float]) -> ct.ControlSet:
    cands = [
        ct.ControlCandidate(
            control_id=f"C{i}",
            covariates=ct.SiteCovariates(
                slope_deg=3.0,
                aspect_class="S",
                lulc_class="cropland",
                soil_class="clay",
                elevation_m=400.0,
                dist_to_stream_m=100.0,
                strahler_order=2,
                easting_m=i * 600.0,
                northing_m=i * 600.0,
            ),
            delta=d,
            data_sufficiency=0.9,
            dist_to_nearest_intervention_m=900.0,
        )
        for i, d in enumerate(deltas)
    ]
    return ct.select_controls(cov(), cands, site_data_sufficiency=0.8, channel_structure=True)


def test_insufficient_controls_yield_unavailable_evidence() -> None:
    cs = control_set([0.01, 0.02, 0.01])
    ev = to_control_evidence(cs, None, "check_dam", "MNDWI")
    assert not ev.available
    assert ev.agreement == 0.0
    assert "below the minimum of 5" in ev.reason


def test_site_exceeding_all_controls_scores_strongly() -> None:
    cs = control_set([0.01, 0.02, 0.01, 0.02, 0.01, 0.02])
    cmp = ct.compare_to_controls(0.20, cs)
    ev = to_control_evidence(cs, cmp, "check_dam", "MNDWI")
    assert ev.available
    assert ev.agreement > 0.9
    assert "exceeds every control" in ev.reason


def test_change_inside_the_control_range_is_halved() -> None:
    """A rise every control also shows is not differential evidence.

    The fixture needs a differenced estimate that is MEANINGFUL (above the noise
    floor) while the site still sits inside [p10, p90] — so the controls are
    spread widely and the site sits high but not beyond them. A tight control
    spread would instead make the difference sub-noise and exercise a different
    branch.
    """
    cs = control_set([0.00, 0.05, 0.10, 0.30, 0.35, 0.40])  # median 0.20
    inside = ct.compare_to_controls(0.32, cs)  # differenced +0.12, inside range
    assert abs(inside.differenced) > 0.02
    assert not inside.outside_control_range
    ev_inside = to_control_evidence(cs, inside, "check_dam", "MNDWI")

    cs2 = control_set([0.01, 0.01, 0.02, 0.02, 0.01, 0.02])
    outside = ct.compare_to_controls(0.18, cs2)
    ev_outside = to_control_evidence(cs2, outside, "check_dam", "MNDWI")

    assert ev_inside.agreement < ev_outside.agreement
    assert "agreement is halved" in ev_inside.reason


def test_site_underperforming_its_controls_scores_negative() -> None:
    """The surroundings improved and the site did not — a real finding."""
    cs = control_set([0.10, 0.12, 0.14, 0.16, 0.18, 0.20])
    cmp = ct.compare_to_controls(-0.05, cs)
    ev = to_control_evidence(cs, cmp, "check_dam", "MNDWI")
    assert ev.agreement < 0


def test_control_on_an_unexpected_index_is_unavailable() -> None:
    """Differencing MNDWI for a contour trench cannot corroborate anything."""
    cs = control_set([0.01, 0.02, 0.01, 0.02, 0.01, 0.02])
    cmp = ct.compare_to_controls(0.20, cs)
    ev = to_control_evidence(cs, cmp, "contour_trench", "MNDWI")
    assert not ev.available
    assert "not part of the expected signature" in ev.reason


def test_sub_noise_differenced_estimate_scores_zero() -> None:
    cs = control_set([0.10, 0.10, 0.10, 0.10, 0.10, 0.10])
    cmp = ct.compare_to_controls(0.105, cs)
    ev = to_control_evidence(cs, cmp, "check_dam", "MNDWI")
    assert ev.agreement == 0.0
    assert "noise floor" in ev.reason


# --- context: rainfall as a confounder ----------------------------------


def test_rainfall_validates_its_inputs() -> None:
    with pytest.raises(ValueError, match="rainfall_mm cannot be negative"):
        RainfallContext(season=sn.Season.RABI, year=2024, rainfall_mm=-1, decadal_mean_mm=100)
    with pytest.raises(ValueError, match="decadal_mean_mm must be positive"):
        RainfallContext(season=sn.Season.RABI, year=2024, rainfall_mm=100, decadal_mean_mm=0)


@pytest.mark.parametrize(
    ("anom", "descriptor"),
    [
        (1.00, "near normal"),
        (0.96, "near normal"),
        (1.31, "wetter than normal"),
        (1.70, "exceptionally wet"),
        (0.70, "drier than normal"),
        (0.40, "drought"),
    ],
)
def test_descriptors_match_the_thresholds(anom: float, descriptor: str) -> None:
    assert rain(anom).descriptor == descriptor


def test_normal_rainfall_corroborates_a_rise() -> None:
    """The change happened without help from the weather."""
    ev = to_context_evidence([rain(0.96)], observed_index_delta=+0.09)
    assert ev.available
    assert ev.agreement > 0
    assert "not attributable to unusual weather" in ev.reason


def test_a_wet_year_ARGUES_AGAINST_the_claim() -> None:
    """The counter-intuitive core: a good monsoon must not earn credit.

    Rainfall moving in the same direction as the observed change is a sufficient
    alternative explanation, so the context family DISAGREES.
    """
    ev = to_context_evidence([rain(1.70)], observed_index_delta=+0.09)
    assert ev.agreement < 0
    assert "sufficient alternative explanation" in ev.reason
    assert "cannot be attributed to the intervention" in ev.reason


def test_a_rise_during_drought_is_stronger_evidence() -> None:
    """Improving while the weather worsened is the best case available."""
    drought = to_context_evidence([rain(0.45)], observed_index_delta=+0.09)
    normal = to_context_evidence([rain(0.98)], observed_index_delta=+0.09)
    assert drought.agreement > normal.agreement
    assert "despite the weather" in drought.reason


def test_a_fall_during_drought_does_not_read_as_failure() -> None:
    """Drought is a sufficient explanation for a decline, so context disagrees
    with treating the decline as evidence against the structure."""
    ev = to_context_evidence([rain(0.45)], observed_index_delta=-0.09)
    assert ev.agreement < 0
    assert "sufficient alternative explanation" in ev.reason


def test_wetter_years_disagree_more_strongly() -> None:
    mild = to_context_evidence([rain(1.30)], observed_index_delta=+0.09)
    extreme = to_context_evidence([rain(1.90)], observed_index_delta=+0.09)
    assert extreme.agreement < mild.agreement


def test_no_rainfall_record_is_unavailable_not_neutral() -> None:
    ev = to_context_evidence([], observed_index_delta=+0.09)
    assert not ev.available
    assert "matched-control design already removes" in ev.reason


def test_rainfall_without_a_measured_change_is_unavailable() -> None:
    """Rainfall alone says nothing about a claim."""
    ev = to_context_evidence([rain(1.70)], observed_index_delta=None)
    assert not ev.available
    assert "nothing for rainfall to explain" in ev.reason


def test_sub_noise_change_scores_zero_context() -> None:
    ev = to_context_evidence([rain(1.70)], observed_index_delta=+0.005)
    assert ev.agreement == 0.0
    assert "nothing for rainfall to explain either way" in ev.reason


def test_context_lineage_notes_that_controls_do_the_real_work() -> None:
    """docs §17.3 point 3, carried into the Evidence Pack."""
    ev = to_context_evidence([rain(0.96)], observed_index_delta=+0.09)
    assert "matched-control design is the primary rainfall control" in str(ev.lineage["note"])
    assert ev.lineage["contexts"][0]["anomaly"] == pytest.approx(0.96)  # type: ignore[index]


def test_thresholds_are_ordered() -> None:
    assert ANOMALY_THRESHOLD < STRONG_ANOMALY_THRESHOLD


# --- the full six-family assembly ---------------------------------------


def test_all_six_families_assemble_into_a_verdict() -> None:
    """Every family built by its own producer, then reconciled.

    This is the M8 integration gate in miniature: no hand-written
    FamilyEvidence anywhere, every family produced by the module that owns it.
    """
    from app.services.photo import LabelPrediction, PhotoLabels
    from app.services.photo import to_family_evidence as photo_evidence
    from app.services.reconcile import Alternative, EvidenceBundle, Quality, reconcile
    from app.services.reconcile.types import Level
    from app.services.terrain import detectability, plausibility
    from app.services.terrain import evidence as terrain_evidence
    from app.services.terrain.types import DiskStat, TerrainSample

    # --- terrain + gates (ideal check dam siting)
    sample = TerrainSample(
        disk_radius_m=15.0,
        slope_deg=DiskStat(1.8, 2.1, 2.6),
        strahler_order=DiskStat(3, 3, 3),
        flow_accumulation_px=DiskStat(3900, 4180, 4400),
        dist_to_stream_m=DiskStat(5, 8, 12),
        upstream_area_km2=DiskStat(1.7, 1.9, 2.1),
        in_depression=True,
        dem_product="NASADEM",
        dem_version="001",
        stream_threshold_px=100.0,
        stream_threshold_agreement=0.79,
    )
    plaus = plausibility.evaluate("check_dam", sample)
    gate = detectability.evaluate("check_dam", expected_footprint_m2=3200.0)
    assert plaus.verdict == "plausible"
    assert gate.passed

    # --- photo
    labels = PhotoLabels(
        image_id="IMG-END-TO-END",
        labels={
            "structure_present": LabelPrediction(
                key="structure_present",
                raw=0.88,
                calibrated=0.88,
                decision="yes",
                abstain_band=(0.4, 0.6),
            ),
            "water_present": LabelPrediction(
                key="water_present",
                raw=0.91,
                calibrated=0.91,
                decision="yes",
                abstain_band=(0.4, 0.6),
            ),
        },
        scene_scale="mid",
        scene_scale_confidence=0.93,
        model_name="siglip2-zeroshot",
        model_version="v1",
        extra={"structure_type_value": "masonry_check_dam"},
    )
    photo = photo_evidence(labels, "check_dam")

    # --- temporal
    deltas = [
        delta("MNDWI", sn.Season.RABI, +0.14),
        delta("MNDWI", sn.Season.SUMMER, +0.11),
    ]
    trend = tr.mann_kendall([0.10, 0.15, 0.21, 0.27, 0.33, 0.39])
    temporal = to_temporal_evidence(assess(deltas, "check_dam", trends={"MNDWI": trend}))

    # --- control
    cs = control_set([0.01, 0.02, 0.01, 0.03, 0.02, 0.01])
    cmp = ct.compare_to_controls(0.14, cs)
    control = to_control_evidence(cs, cmp, "check_dam", "MNDWI")

    # --- context
    context = to_context_evidence([rain(0.96, sn.Season.RABI)], +0.14)

    # --- satellite (site-disk MNDWI observation, same producer family)
    from app.services.reconcile.types import FamilyEvidence

    satellite = FamilyEvidence(
        family="satellite",
        agreement=1.0,
        available=True,
        reason="post-monsoon MNDWI at the site disk rose from -0.08 to +0.31",
        lineage={"index_formula_version": "idx-v1"},
    )

    bundle = EvidenceBundle(
        claim_id="END-TO-END-CHECKDAM",
        intervention_type="check_dam",
        families=(
            terrain_evidence.to_family_evidence(plaus, sample),
            satellite,
            temporal,
            control,
            photo,
            context,
        ),
        gates=terrain_evidence.to_gates(gate, plaus, scene_scale="mid"),
        quality=Quality(metadata_integrity=0.95, data_sufficiency=0.88),
        alternatives=(
            Alternative(
                description="a wet year explains the change",
                excluded=True,
                basis="rainfall anomaly 0.96x of the decadal mean",
            ),
        ),
    )

    verdict = reconcile(bundle)

    # All six families available and agreeing -> the ceiling.
    assert verdict.coverage == pytest.approx(1.0)
    assert verdict.level is Level.L4_CONTROL_DIFFERENCED
    assert verdict.label == "CORROBORATED"
    assert verdict.recommended_action == "no_action"
    assert verdict.confidence <= abs(verdict.score)
    assert verdict.dissent
    assert any("not a causal claim" in d.lower() for d in verdict.dissent)
    # Lineage must carry every producer's provenance.
    fams = verdict.lineage["families"]  # type: ignore[index]
    assert set(fams) == {
        "terrain",
        "satellite",
        "temporal",
        "control",
        "photo",
        "context",
    }
    assert fams["terrain"]["lineage"]["dem_product"] == "NASADEM"  # type: ignore[index]
    assert fams["control"]["lineage"]["n_selected"] == 6  # type: ignore[index]


def test_windows_and_pairings_feed_the_temporal_adapter() -> None:
    """The other half of the chain: dates in, scored evidence out."""
    from app.services.temporal import build_pairings, build_windows

    w = build_windows(date(2023, 11, 20))
    observations = [
        sn.SeasonalObservation("MNDWI", sn.Season.RABI, 2022, -0.08, 0.9, 5),
        sn.SeasonalObservation("MNDWI", sn.Season.RABI, 2025, +0.24, 0.9, 6),
        sn.SeasonalObservation("MNDWI", sn.Season.SUMMER, 2022, -0.20, 0.8, 4),
        sn.SeasonalObservation("MNDWI", sn.Season.SUMMER, 2025, -0.02, 0.8, 4),
    ]
    res = build_pairings(observations, w)
    deltas = [sn.seasonal_delta(p.pre, p.post) for p in res.pairings]
    ev = to_temporal_evidence(assess(deltas, "check_dam", skipped=res.skipped))
    assert ev.available
    assert ev.agreement > 0.9, "both seasons rose as a working check dam should"
    assert "kharif not assessed" in ev.reason


def test_anomaly_predicates_expose_the_thresholds() -> None:
    """Used by the UI to badge a year and by reports to caveat a comparison."""
    normal = rain(1.05)
    assert not normal.is_anomalous
    assert not normal.is_strongly_anomalous

    wet = rain(1.35)
    assert wet.is_anomalous
    assert not wet.is_strongly_anomalous

    extreme = rain(1.80)
    assert extreme.is_anomalous
    assert extreme.is_strongly_anomalous

    drought = rain(0.35)
    assert drought.is_anomalous
    assert drought.is_strongly_anomalous
