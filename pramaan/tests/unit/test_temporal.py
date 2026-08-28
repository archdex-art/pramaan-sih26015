"""Tests for temporal windows, seasons, trend and matched controls.

Three properties carry the weight here, and each corresponds to a way the
analysis could produce a confident, meaningless number:

1. **Cross-season deltas are unconstructable** (docs §17.2 calls them a category
   error). Enforced by the type system, so it is tested as an exception.
2. **No trend claim below 5 seasonal points** (docs §17.6).
3. **No control comparison below 5 matched controls** (docs §17.4) — and the
   screen is a percentile, never a p-value.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.temporal import controls as ct  # noqa: E402
from app.services.temporal import seasons as sn  # noqa: E402
from app.services.temporal import trend as tr  # noqa: E402
from app.services.temporal import windows as wd  # noqa: E402


def obs(
    season: sn.Season,
    year: int,
    value: float,
    *,
    suff: float = 0.9,
    n: int = 4,
    index: str = "NDVI",
    family: str = "HLS",
) -> sn.SeasonalObservation:
    return sn.SeasonalObservation(
        index_name=index,
        season=season,
        year=year,
        value=value,
        data_sufficiency=suff,
        n_scenes=n,
        sensor_family=family,
    )


# --- seasons: the category-error ban ------------------------------------


def test_month_to_season_covers_all_twelve_months() -> None:
    got = {sn.season_of(date(2024, m, 15)) for m in range(1, 13)}
    assert got == {sn.Season.KHARIF, sn.Season.RABI, sn.Season.SUMMER}
    assert sn.season_of(date(2024, 8, 1)) is sn.Season.KHARIF
    assert sn.season_of(date(2024, 12, 1)) is sn.Season.RABI
    assert sn.season_of(date(2024, 4, 1)) is sn.Season.SUMMER


def test_rabi_spanning_the_calendar_year_is_one_season() -> None:
    """Nov-Dec 2023 and Jan-Feb 2024 are the SAME rabi season.

    Getting this wrong splits one season into two half-seasons and makes a
    "year-over-year" comparison compare a season against itself.
    """
    assert sn.season_year(date(2023, 11, 20)) == 2024
    assert sn.season_year(date(2023, 12, 31)) == 2024
    assert sn.season_year(date(2024, 1, 10)) == 2024
    assert sn.season_year(date(2024, 2, 28)) == 2024
    # Non-rabi months are unaffected.
    assert sn.season_year(date(2024, 8, 1)) == 2024


def test_cross_season_delta_is_refused() -> None:
    """docs §17.2: not a comparison, a category error."""
    with pytest.raises(sn.CrossSeasonComparison, match="category error"):
        sn.seasonal_delta(obs(sn.Season.KHARIF, 2022, 0.4), obs(sn.Season.RABI, 2024, 0.6))


def test_same_season_delta_is_the_difference() -> None:
    d = sn.seasonal_delta(obs(sn.Season.RABI, 2022, 0.42), obs(sn.Season.RABI, 2024, 0.58))
    assert d.delta == pytest.approx(0.16)
    assert d.season is sn.Season.RABI
    assert d.label == "NDVI rabi 2022->2024"


def test_delta_sufficiency_is_the_weaker_end() -> None:
    """A delta is only as trustworthy as its thinner window."""
    d = sn.seasonal_delta(
        obs(sn.Season.RABI, 2022, 0.4, suff=0.95),
        obs(sn.Season.RABI, 2024, 0.6, suff=0.42),
    )
    assert d.data_sufficiency == pytest.approx(0.42)


def test_cross_index_and_cross_sensor_deltas_are_refused() -> None:
    with pytest.raises(ValueError, match="NDVI against MNDWI"):
        sn.seasonal_delta(
            obs(sn.Season.RABI, 2022, 0.4),
            obs(sn.Season.RABI, 2024, 0.6, index="MNDWI"),
        )
    # W4 fix: Resourcesat is a parallel series, never concatenated into HLS.
    with pytest.raises(ValueError, match="sensor families"):
        sn.seasonal_delta(
            obs(sn.Season.RABI, 2022, 0.4),
            obs(sn.Season.RABI, 2024, 0.6, family="RS2-LISS3"),
        )


def test_degenerate_and_reversed_pairs_are_refused() -> None:
    with pytest.raises(ValueError, match="same season-year"):
        sn.seasonal_delta(obs(sn.Season.RABI, 2024, 0.4), obs(sn.Season.RABI, 2024, 0.6))
    with pytest.raises(ValueError, match="precedes"):
        sn.seasonal_delta(obs(sn.Season.RABI, 2024, 0.4), obs(sn.Season.RABI, 2022, 0.6))


def test_observation_validates_its_own_fields() -> None:
    with pytest.raises(ValueError, match="data_sufficiency"):
        obs(sn.Season.RABI, 2024, 0.5, suff=1.4)
    with pytest.raises(ValueError, match="n_scenes"):
        obs(sn.Season.RABI, 2024, 0.5, n=-1)


def test_every_season_has_a_published_rationale() -> None:
    """Printed in the Evidence Pack so weighting differences are explicable."""
    for season in sn.Season:
        assert season in sn.SEASON_RATIONALE
        assert len(sn.SEASON_RATIONALE[season]) > 60


# --- windows -------------------------------------------------------------


def test_window_layout_matches_the_published_diagram() -> None:
    w = wd.build_windows(date(2023, 11, 20))
    assert w.pre_end == date(2023, 8, 20)
    assert w.post_start == date(2024, 2, 20)
    assert w.pre_start == date(2021, 8, 20)
    assert w.post_end == date(2026, 2, 20)


def test_construction_band_belongs_to_neither_window() -> None:
    """Including it manufactures a fake degradation-then-recovery signal."""
    w = wd.build_windows(date(2023, 11, 20))
    mid = date(2023, 11, 20)
    assert w.is_excluded(mid)
    assert not w.contains_pre(mid)
    assert not w.contains_post(mid)


def test_month_shift_clamps_to_month_length() -> None:
    """31 Jan minus 3 months must not raise; 31 Nov does not exist."""
    w = wd.build_windows(date(2024, 5, 31))
    assert w.pre_end == date(2024, 2, 29), "leap-year February, clamped from 31"


def test_window_construction_validates_parameters() -> None:
    with pytest.raises(ValueError, match="buffer_months"):
        wd.build_windows(date(2024, 1, 1), buffer_months=-1)
    with pytest.raises(ValueError, match="window_months"):
        wd.build_windows(date(2024, 1, 1), window_months=0)


def test_pairing_matches_within_season_only() -> None:
    w = wd.build_windows(date(2023, 11, 20))
    observations = [
        obs(sn.Season.RABI, 2022, 0.40),  # PRE  (mid 2022-01-01)
        obs(sn.Season.RABI, 2025, 0.58),  # POST (mid 2025-01-01)
        obs(sn.Season.SUMMER, 2022, 0.20),  # PRE
        obs(sn.Season.SUMMER, 2025, 0.31),  # POST
    ]
    res = wd.build_pairings(observations, w)
    assert set(res.seasons_available()) == {sn.Season.RABI, sn.Season.SUMMER}
    assert not res.insufficient_history
    for p in res.pairings:
        # Every pairing must survive seasonal_delta, i.e. be same-season.
        sn.seasonal_delta(p.pre, p.post)


def test_season_with_no_baseline_is_skipped_with_a_reason() -> None:
    w = wd.build_windows(date(2023, 11, 20))
    res = wd.build_pairings([obs(sn.Season.RABI, 2025, 0.58)], w)
    assert res.insufficient_history
    assert sn.Season.RABI in res.skipped
    assert "none in PRE" in res.skipped[sn.Season.RABI]


def test_season_with_no_post_is_skipped_with_a_reason() -> None:
    w = wd.build_windows(date(2023, 11, 20))
    res = wd.build_pairings([obs(sn.Season.RABI, 2022, 0.40)], w)
    assert "none in POST" in res.skipped[sn.Season.RABI]


def test_observations_below_the_sufficiency_floor_are_dropped() -> None:
    """Mirrors the engine's floor so an unusable delta is never built."""
    w = wd.build_windows(date(2023, 11, 20))
    res = wd.build_pairings(
        [obs(sn.Season.RABI, 2022, 0.4, suff=0.10), obs(sn.Season.RABI, 2025, 0.6, suff=0.10)],
        w,
    )
    assert res.insufficient_history
    assert "data-sufficiency floor" in res.skipped[sn.Season.RABI]


def test_best_candidate_is_chosen_by_sufficiency_not_recency() -> None:
    """Choosing the closest in time would prefer a cloud-wrecked composite."""
    w = wd.build_windows(date(2023, 11, 20))
    res = wd.build_pairings(
        [
            obs(sn.Season.RABI, 2022, 0.40, suff=0.95),  # further, clean
            obs(sn.Season.RABI, 2023, 0.44, suff=0.40),  # nearer, murky
            obs(sn.Season.RABI, 2025, 0.58, suff=0.90),
        ],
        w,
    )
    pre = next(p.pre for p in res.pairings if p.season is sn.Season.RABI)
    assert pre.year == 2022, "the cleaner PRE must win over the nearer one"


def test_excluded_and_out_of_range_observations_are_not_paired() -> None:
    w = wd.build_windows(date(2023, 11, 20))
    # rabi 2024 midpoint (2024-01-01) sits inside the construction band.
    assert wd.observation_window(obs(sn.Season.RABI, 2024, 0.5), w) is None
    # rabi 2019 is before the PRE window opens.
    assert wd.observation_window(obs(sn.Season.RABI, 2019, 0.5), w) is None
    assert wd.observation_window(obs(sn.Season.RABI, 2022, 0.5), w) == "pre"
    assert wd.observation_window(obs(sn.Season.RABI, 2025, 0.5), w) == "post"


def test_kharif_and_summer_midpoints_fall_in_their_months() -> None:
    w = wd.build_windows(date(2023, 11, 20))
    assert wd.observation_window(obs(sn.Season.KHARIF, 2022, 0.5), w) == "pre"
    assert wd.observation_window(obs(sn.Season.SUMMER, 2025, 0.5), w) == "post"


def test_windows_lineage_records_the_parameters_used() -> None:
    lin = wd.build_windows(date(2023, 11, 20)).lineage()
    assert lin["construction_buffer_months"] == 3
    assert lin["window_months"] == 24


# --- trend ---------------------------------------------------------------


def test_no_trend_claim_below_five_points() -> None:
    """docs §17.6: shorter series get a labelled delta, never a direction."""
    for n in (0, 1, 2, 3, 4):
        r = tr.mann_kendall([0.1 * i for i in range(n)])
        assert r.insufficient, n
        assert r.direction == "undetermined"
        assert r.slope_per_year is None
        assert r.p_value is None
        assert "below the 5" in r.reason or "below the 5 required" in r.reason


def test_monotonic_increase_is_detected() -> None:
    r = tr.mann_kendall([0.10, 0.16, 0.23, 0.29, 0.36, 0.42])
    assert not r.insufficient
    assert r.direction == "increasing"
    assert r.significant
    assert r.slope_per_year is not None and r.slope_per_year > 0
    assert r.p_value is not None and r.p_value <= 0.05


def test_monotonic_decrease_is_detected() -> None:
    r = tr.mann_kendall([0.42, 0.36, 0.29, 0.23, 0.16, 0.10])
    assert r.direction == "decreasing"
    assert r.slope_per_year is not None and r.slope_per_year < 0


def test_noise_yields_no_trend_and_says_so_carefully() -> None:
    r = tr.mann_kendall([0.30, 0.28, 0.31, 0.29, 0.30, 0.29])
    assert r.direction == "no trend"
    assert not r.significant
    # The wording must not claim evidence of absence.
    assert "not evidence of no change" in r.reason


def test_constant_series_is_no_trend_not_undetermined() -> None:
    """A dry pond at MNDWI -0.45 for five summers is a real, useful answer."""
    r = tr.mann_kendall([-0.45] * 5)
    assert not r.insufficient
    assert r.direction == "no trend"
    assert r.slope_per_year == 0.0
    assert r.n_ties == 1
    assert "zero variance" in r.reason


def test_theil_sen_is_robust_to_a_single_outlier() -> None:
    """One undetected cloud edge is the expected residual after Fmask."""
    clean = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]
    spiked = [0.10, 0.20, 0.30, 0.40, 0.50, 5.00]
    t = np.arange(6, dtype=float)
    ts_clean = tr.theil_sen_slope(np.array(clean), t)
    ts_spiked = tr.theil_sen_slope(np.array(spiked), t)
    ols_shift = abs(np.polyfit(t, spiked, 1)[0] - np.polyfit(t, clean, 1)[0])
    ts_shift = abs(ts_spiked - ts_clean)
    assert ts_shift < ols_shift, "Theil-Sen must be less disturbed than OLS"


def test_nan_values_are_dropped_before_testing() -> None:
    r = tr.mann_kendall([0.1, float("nan"), 0.2, 0.3, 0.4, 0.5, 0.6])
    assert r.n == 6
    assert r.direction == "increasing"


def test_times_length_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="times length"):
        tr.mann_kendall([0.1, 0.2, 0.3, 0.4, 0.5], times=[0, 1, 2])


def test_real_times_scale_the_slope_not_the_test() -> None:
    vals = [0.10, 0.20, 0.30, 0.40, 0.50]
    dense = tr.mann_kendall(vals, times=[0, 1, 2, 3, 4])
    sparse = tr.mann_kendall(vals, times=[0, 2, 4, 6, 8])
    assert dense.direction == sparse.direction
    assert dense.p_value == pytest.approx(sparse.p_value)
    assert sparse.slope_per_year == pytest.approx(dense.slope_per_year / 2)


def test_trend_lineage_records_the_threshold() -> None:
    lin = tr.mann_kendall([0.1, 0.2, 0.3]).lineage()
    assert lin["min_points_required"] == 5
    assert lin["insufficient"] is True


# --- controls ------------------------------------------------------------


def cov(
    slope: float = 3.0,
    elev: float = 400.0,
    dist: float = 100.0,
    order: int = 0,
    lulc: str = "cropland",
    e: float = 0.0,
    n: float = 0.0,
) -> ct.SiteCovariates:
    return ct.SiteCovariates(
        slope_deg=slope,
        aspect_class="S",
        lulc_class=lulc,
        soil_class="clay",
        elevation_m=elev,
        dist_to_stream_m=dist,
        strahler_order=order,
        easting_m=e,
        northing_m=n,
    )


def cand(
    cid: str,
    *,
    delta: float = 0.02,
    suff: float = 0.9,
    dist_int: float = 900.0,
    buffer: bool = False,
    **kw: float,
) -> ct.ControlCandidate:
    return ct.ControlCandidate(
        control_id=cid,
        covariates=cov(**kw),  # type: ignore[arg-type]
        delta=delta,
        data_sufficiency=suff,
        dist_to_nearest_intervention_m=dist_int,
        inside_command_buffer=buffer,
    )


def spread_controls(n: int, **kw: float) -> list[ct.ControlCandidate]:
    """n candidates spread across distinct 500 m cells to dodge the cap."""
    return [
        cand(f"C{i}", e=i * 600.0, n=i * 600.0, **kw)  # type: ignore[arg-type]
        for i in range(n)
    ]


def test_sufficient_controls_are_selected() -> None:
    cs = ct.select_controls(
        cov(), spread_controls(8), site_data_sufficiency=0.8, channel_structure=False
    )
    assert not cs.insufficient
    assert cs.n_selected == 8
    assert "8 matched controls selected" in cs.reason


def test_max_controls_is_capped_at_twelve() -> None:
    cs = ct.select_controls(
        cov(), spread_controls(20), site_data_sufficiency=0.8, channel_structure=False
    )
    assert cs.n_selected == ct.MAX_CONTROLS
    assert cs.rejected["beyond_max_controls"] == 8


def test_below_five_controls_is_insufficient_and_blocks_comparison() -> None:
    """A comparison must not be computed at all, or the number gets quoted."""
    cs = ct.select_controls(
        cov(), spread_controls(4), site_data_sufficiency=0.8, channel_structure=False
    )
    assert cs.insufficient
    assert "below the minimum of 5" in cs.reason
    with pytest.raises(ValueError, match="refusing to compare"):
        ct.compare_to_controls(0.2, cs)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"dist_int": 100.0}, "too_close_to_intervention"),
        ({"buffer": True}, "inside_command_buffer"),
        ({"slope": 9.0}, "slope_mismatch"),
        ({"lulc": "forest"}, "lulc_mismatch"),
        ({"elev": 600.0}, "elevation_mismatch"),
        ({"suff": 0.2}, "insufficient_data"),
    ],
)
def test_each_criterion_rejects_and_is_counted(kwargs: dict, reason: str) -> None:
    """Every rejection is attributed, so a reader can tell found from manufactured."""
    bad = [cand(f"B{i}", e=i * 600.0, n=i * 600.0, **kwargs) for i in range(6)]
    cs = ct.select_controls(cov(), bad, site_data_sufficiency=0.8, channel_structure=False)
    assert cs.rejected.get(reason) == 6
    assert cs.insufficient


def test_channel_structures_match_on_strahler_order() -> None:
    """C6 for check dams and nala bunds."""
    site = cov(order=3)
    wrong = [cand(f"W{i}", e=i * 600.0, n=i * 600.0, order=1) for i in range(6)]
    cs = ct.select_controls(site, wrong, site_data_sufficiency=0.8, channel_structure=True)
    assert cs.rejected["strahler_mismatch"] == 6


def test_non_channel_structures_match_on_distance_to_stream() -> None:
    """C7 for farm ponds and bunds — applying C6 too would over-constrain."""
    site = cov(dist=100.0, order=0)
    far = [cand(f"F{i}", e=i * 600.0, n=i * 600.0, dist=400.0) for i in range(6)]
    cs = ct.select_controls(site, far, site_data_sufficiency=0.8, channel_structure=False)
    assert cs.rejected["dist_to_stream_mismatch"] == 6
    assert "strahler_mismatch" not in cs.rejected


def test_spatial_cell_cap_prevents_a_cluster_masquerading_as_independence() -> None:
    """Twelve adjacent pixels are not twelve independent controls."""
    clustered = [cand(f"X{i}", e=10.0, n=10.0) for i in range(10)]
    cs = ct.select_controls(cov(), clustered, site_data_sufficiency=0.8, channel_structure=False)
    assert cs.n_selected == ct.MAX_CONTROLS_PER_CELL
    assert cs.rejected["spatial_cell_full"] == 7
    assert cs.insufficient, "3 controls in one cell must not pass the minimum"


def test_controls_are_ranked_by_covariate_closeness() -> None:
    site = cov(slope=3.0, elev=400.0, dist=100.0)
    pool = [
        cand("far", slope=4.9, elev=445.0, dist=140.0, e=0, n=0),
        cand("near", slope=3.05, elev=401.0, dist=101.0, e=600, n=600),
        cand("mid", slope=3.8, elev=420.0, dist=120.0, e=1200, n=1200),
    ]
    cs = ct.select_controls(site, pool, site_data_sufficiency=0.8, channel_structure=False)
    assert [c.control_id for c in cs.selected] == ["near", "mid", "far"]


def test_control_set_lineage_publishes_the_criteria() -> None:
    cs = ct.select_controls(
        cov(), spread_controls(6), site_data_sufficiency=0.8, channel_structure=False
    )
    lin = cs.lineage()
    assert lin["n_selected"] == 6
    assert lin["criteria"]["max_slope_diff_deg"] == 2.0  # type: ignore[index]
    assert len(lin["control_ids"]) == 6  # type: ignore[arg-type]


# --- the differenced estimator ------------------------------------------


def test_differenced_estimate_subtracts_the_control_median() -> None:
    cs = ct.select_controls(
        cov(),
        [
            cand(f"C{i}", delta=d, e=i * 600.0, n=i * 600.0)
            for i, d in enumerate([0.01, 0.02, 0.03, 0.02, 0.01, 0.02])
        ],
        site_data_sufficiency=0.8,
        channel_structure=False,
    )
    cmp = ct.compare_to_controls(0.18, cs)
    assert cmp.control_median == pytest.approx(0.02)
    assert cmp.differenced == pytest.approx(0.16)


def test_site_exceeding_all_controls_is_reported_as_such() -> None:
    """The phrasing docs §17.4 prefers to a p-value."""
    cs = ct.select_controls(
        cov(),
        [cand(f"C{i}", delta=0.02, e=i * 600.0, n=i * 600.0) for i in range(6)],
        site_data_sufficiency=0.8,
        channel_structure=False,
    )
    cmp = ct.compare_to_controls(0.5, cs)
    assert cmp.exceeds_all_controls
    assert cmp.outside_control_range
    assert "exceeds ALL 6 matched controls" in cmp.reason


def test_site_inside_the_control_range_is_not_distinguishable() -> None:
    cs = ct.select_controls(
        cov(),
        [
            cand(f"C{i}", delta=d, e=i * 600.0, n=i * 600.0)
            for i, d in enumerate([0.00, 0.05, 0.10, 0.15, 0.20, 0.25])
        ],
        site_data_sufficiency=0.8,
        channel_structure=False,
    )
    cmp = ct.compare_to_controls(0.12, cs)
    assert not cmp.outside_control_range
    assert "not distinguishable from the un-intervened background" in cmp.reason


def test_no_p_value_is_ever_produced() -> None:
    """docs §17.4: a p-value on <=12 autocorrelated controls is false precision."""
    cs = ct.select_controls(
        cov(), spread_controls(8), site_data_sufficiency=0.8, channel_structure=False
    )
    cmp = ct.compare_to_controls(0.3, cs)
    assert not hasattr(cmp, "p_value")
    assert "percentile is reported rather than a p-value" in cmp.reason
    assert "percentile" in str(cmp.lineage()["note"])


def test_comparison_lineage_carries_the_screen_bounds() -> None:
    cs = ct.select_controls(
        cov(), spread_controls(8), site_data_sufficiency=0.8, channel_structure=False
    )
    lin = ct.compare_to_controls(0.3, cs).lineage()
    assert "control_p10" in lin
    assert "control_p90" in lin
    assert lin["n_controls"] == 8


def test_site_percentile_is_reported() -> None:
    cs = ct.select_controls(
        cov(),
        [
            cand(f"C{i}", delta=d, e=i * 600.0, n=i * 600.0)
            for i, d in enumerate([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
        ],
        site_data_sufficiency=0.8,
        channel_structure=False,
    )
    cmp = ct.compare_to_controls(0.25, cs)
    assert cmp.site_percentile == pytest.approx(50.0)


# --- remaining branches, exercised deliberately -------------------------


def test_s_statistic_of_exactly_zero_gives_z_zero_and_no_trend() -> None:
    """Equal numbers of increases and decreases, with real variance present.

    Distinct from the constant-series case: here the series genuinely varies,
    the Mann-Kendall S cancels to exactly zero, and the continuity correction
    must not be applied in either direction.
    """
    r = tr.mann_kendall([0.1, 0.1, 0.4, 0.3, 0.2, 0.1])
    assert r.s == 0.0
    assert r.z == 0.0
    assert r.direction == "no trend"
    assert r.p_value == pytest.approx(1.0)
    assert r.n_ties == 1


def test_theil_sen_with_no_distinct_times_is_nan_not_a_crash() -> None:
    """All observations at one instant: no slope is definable."""
    vals = np.array([0.1, 0.2, 0.3], dtype=float)
    times = np.zeros(3, dtype=float)
    assert np.isnan(tr.theil_sen_slope(vals, times))


def test_window_boundary_properties_expose_the_excluded_band() -> None:
    """The UI shades this band on the hero chart, so it must be queryable."""
    w = wd.build_windows(date(2023, 11, 20))
    assert w.excluded_start == w.pre_end
    assert w.excluded_end == w.post_start
    assert w.excluded_start < w.excluded_end


def test_window_describe_names_the_excluded_band() -> None:
    text = wd.build_windows(date(2023, 11, 20)).describe()
    assert "EXCLUDED" in text
    assert "PRE" in text and "POST" in text
    assert "2023-08-20" in text and "2024-02-20" in text


def test_site_below_the_control_range_is_flagged_outside_but_not_exceeding() -> None:
    """A site that UNDERPERFORMS its controls is also a finding.

    This is the branch that matters for a contradicted verdict: the structure's
    surroundings improved and the site did not.
    """
    cs = ct.select_controls(
        cov(),
        [
            cand(f"C{i}", delta=d, e=i * 600.0, n=i * 600.0)
            for i, d in enumerate([0.10, 0.12, 0.14, 0.16, 0.18, 0.20])
        ],
        site_data_sufficiency=0.8,
        channel_structure=False,
    )
    cmp = ct.compare_to_controls(-0.05, cs)
    assert cmp.outside_control_range
    assert not cmp.exceeds_all_controls
    assert cmp.differenced < 0
    assert "falls outside the" in cmp.reason
    assert cmp.site_percentile == pytest.approx(0.0)


def test_pairings_skip_observations_inside_the_construction_band() -> None:
    """An in-band observation must be dropped by build_pairings, not just by
    observation_window: it is the path a real ingest takes."""
    w = wd.build_windows(date(2023, 11, 20))
    res = wd.build_pairings(
        [
            obs(sn.Season.RABI, 2022, 0.40),  # PRE
            obs(sn.Season.RABI, 2024, 0.99),  # inside the excluded band
            obs(sn.Season.RABI, 2025, 0.58),  # POST
        ],
        w,
    )
    pairing = next(p for p in res.pairings if p.season is sn.Season.RABI)
    assert pairing.pre.year == 2022
    assert pairing.post.year == 2025
    assert 0.99 not in (pairing.pre.value, pairing.post.value), (
        "the construction-band observation must never enter a delta"
    )


def test_channel_structure_with_matching_order_is_accepted() -> None:
    """The C6 pass-through: order matches, so selection proceeds to C8."""
    site = cov(order=3, dist=20.0)
    pool = [cand(f"M{i}", e=i * 600.0, n=i * 600.0, order=3, dist=25.0) for i in range(6)]
    cs = ct.select_controls(site, pool, site_data_sufficiency=0.8, channel_structure=True)
    assert cs.n_selected == 6
    assert not cs.insufficient
    assert "strahler_mismatch" not in cs.rejected
    assert cs.channel_structure is True
