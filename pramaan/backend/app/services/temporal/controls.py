"""Matched-control selection and the differenced estimator (docs §17.4).

This is the single design element that makes a 30 m signal interpretable despite
rainfall and seasonality, and it is the thing the pitch leads with. It is also
the most misusable: widening the matching criteria until controls appear would
produce a full control set and a meaningless comparison, and nothing in the
output would look different.

So the criteria are explicit constants, every rejection is counted by reason, and
the report states how many candidates each filter removed. A reviewer can see
whether the controls were *found* or *manufactured*.

## Why a percentile, not a p-value

docs §17.4: *"with N <= 12 spatially autocorrelated controls, a p-value would be
misleading precision. Saying 'the site's change exceeds all 12 matched controls'
is both stronger rhetorically and more honest statistically."*

So `ControlComparison` reports the site's percentile rank within the control
delta distribution and whether it falls outside [p10, p90]. No p-value is
computed anywhere in this module, deliberately.

## Why the control design beats rainfall normalisation

Controls in the same sub-watershed physically experienced the same weather. The
differenced estimate therefore removes the common rainfall effect without any
normalisation formula. Rainfall is still reported — as context and for visual
honesty — but the control does the actual work (docs §17.3, point 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

#: Matching tolerances from docs §17.4. Named so a change is visible in a diff.
MIN_DISTANCE_FROM_ANY_INTERVENTION_M = 250.0
MAX_SLOPE_DIFF_DEG = 2.0
MAX_ELEVATION_DIFF_M = 50.0
MAX_DIST_TO_STREAM_DIFF_M = 50.0

#: Selection limits.
MAX_CONTROLS = 12
MIN_CONTROLS = 5
#: Cap per 500 m cell, so a cluster of adjacent pixels cannot masquerade as
#: twelve independent controls. Spatial autocorrelation is the reason the
#: percentile screen replaces a p-value; this limits how bad it gets.
MAX_CONTROLS_PER_CELL = 3
SPATIAL_CELL_M = 500.0

#: Significance screen bounds.
LOWER_PERCENTILE = 10.0
UPPER_PERCENTILE = 90.0

RejectReason = Literal[
    "too_close_to_intervention",
    "inside_command_buffer",
    "slope_mismatch",
    "lulc_mismatch",
    "elevation_mismatch",
    "strahler_mismatch",
    "dist_to_stream_mismatch",
    "insufficient_data",
    "spatial_cell_full",
    "beyond_max_controls",
]


@dataclass(frozen=True, slots=True)
class SiteCovariates:
    """The attributes a control must match. Same shape for site and candidate."""

    slope_deg: float
    aspect_class: str
    lulc_class: str
    soil_class: str
    elevation_m: float
    dist_to_stream_m: float
    strahler_order: int
    #: Projected metres, for the spatial-cell cap.
    easting_m: float = 0.0
    northing_m: float = 0.0


@dataclass(frozen=True, slots=True)
class ControlCandidate:
    """A candidate control pixel with its covariates and its measured delta."""

    control_id: str
    covariates: SiteCovariates
    #: Same-season delta, computed identically to the site's.
    delta: float
    data_sufficiency: float
    #: Distance to the nearest geotagged intervention of any type or year.
    dist_to_nearest_intervention_m: float
    inside_command_buffer: bool = False


@dataclass(frozen=True, slots=True)
class ControlSet:
    """The selected controls, plus a full audit of what was rejected and why."""

    selected: tuple[ControlCandidate, ...]
    rejected: dict[str, int]
    n_candidates: int
    #: True when fewer than MIN_CONTROLS survived. The engine reports the control
    #: family unavailable and caps the epistemic level at L3.
    insufficient: bool
    #: True when the structure sits on a channel, so C6 (Strahler equality)
    #: applies instead of C7 (distance-to-stream similarity).
    channel_structure: bool
    reason: str

    @property
    def n_selected(self) -> int:
        return len(self.selected)

    def deltas(self) -> np.ndarray:
        return np.array([c.delta for c in self.selected], dtype=np.float64)

    def lineage(self) -> dict[str, object]:
        return {
            "control_ids": [c.control_id for c in self.selected],
            "n_selected": self.n_selected,
            "n_candidates": self.n_candidates,
            "rejected_by_reason": dict(self.rejected),
            "insufficient": self.insufficient,
            "channel_structure": self.channel_structure,
            "min_controls": MIN_CONTROLS,
            "max_controls": MAX_CONTROLS,
            "criteria": {
                "min_distance_from_intervention_m": MIN_DISTANCE_FROM_ANY_INTERVENTION_M,
                "max_slope_diff_deg": MAX_SLOPE_DIFF_DEG,
                "max_elevation_diff_m": MAX_ELEVATION_DIFF_M,
                "max_dist_to_stream_diff_m": MAX_DIST_TO_STREAM_DIFF_M,
                "max_per_500m_cell": MAX_CONTROLS_PER_CELL,
            },
        }


def _mahalanobis_like_distance(
    site: SiteCovariates, cand: SiteCovariates, scales: dict[str, float]
) -> float:
    """Standardised Euclidean distance over the continuous covariates.

    Not a true Mahalanobis distance: with N <= 12 candidates the covariance
    matrix is not estimable, and inverting a rank-deficient estimate would
    produce confident nonsense. Standardising each covariate by a fixed,
    published scale is the honest approximation, and the scales are recorded.
    """
    terms = [
        (site.slope_deg - cand.slope_deg) / scales["slope_deg"],
        (site.elevation_m - cand.elevation_m) / scales["elevation_m"],
        (site.dist_to_stream_m - cand.dist_to_stream_m) / scales["dist_to_stream_m"],
    ]
    return float(np.sqrt(sum(t * t for t in terms)))


#: Standardisation scales: the matching tolerances themselves, so a candidate at
#: the edge of every tolerance sits at distance ~sqrt(3).
DISTANCE_SCALES: dict[str, float] = {
    "slope_deg": MAX_SLOPE_DIFF_DEG,
    "elevation_m": MAX_ELEVATION_DIFF_M,
    "dist_to_stream_m": MAX_DIST_TO_STREAM_DIFF_M,
}


def select_controls(
    site: SiteCovariates,
    candidates: list[ControlCandidate],
    *,
    site_data_sufficiency: float,
    channel_structure: bool,
    max_controls: int = MAX_CONTROLS,
    min_controls: int = MIN_CONTROLS,
) -> ControlSet:
    """Apply C1-C8, then rank by covariate distance with a spatial cap.

    `channel_structure` selects between C6 (Strahler order must match, for check
    dams and nala bunds) and C7 (distance-to-stream must be similar, for farm
    ponds and bunds). Applying both would over-constrain: a farm pond has no
    meaningful stream order, and requiring order 0 == order 0 while also
    requiring stream distance within 50 m rejects valid controls for no reason.
    """
    rejected: dict[str, int] = {}

    def reject(reason: RejectReason) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    survivors: list[ControlCandidate] = []
    for c in candidates:
        cv = c.covariates
        if c.dist_to_nearest_intervention_m < MIN_DISTANCE_FROM_ANY_INTERVENTION_M:
            reject("too_close_to_intervention")
            continue
        if c.inside_command_buffer:
            reject("inside_command_buffer")
            continue
        if abs(cv.slope_deg - site.slope_deg) > MAX_SLOPE_DIFF_DEG:
            reject("slope_mismatch")
            continue
        if cv.lulc_class != site.lulc_class:
            reject("lulc_mismatch")
            continue
        if abs(cv.elevation_m - site.elevation_m) > MAX_ELEVATION_DIFF_M:
            reject("elevation_mismatch")
            continue
        if channel_structure:
            if cv.strahler_order != site.strahler_order:
                reject("strahler_mismatch")
                continue
        elif abs(cv.dist_to_stream_m - site.dist_to_stream_m) > MAX_DIST_TO_STREAM_DIFF_M:
            reject("dist_to_stream_mismatch")
            continue
        if c.data_sufficiency < site_data_sufficiency:
            # C8: a control observed less well than the site cannot bound the
            # site's change. Asymmetric on purpose.
            reject("insufficient_data")
            continue
        survivors.append(c)

    survivors.sort(key=lambda c: _mahalanobis_like_distance(site, c.covariates, DISTANCE_SCALES))

    selected: list[ControlCandidate] = []
    cell_counts: dict[tuple[int, int], int] = {}
    for c in survivors:
        if len(selected) >= max_controls:
            reject("beyond_max_controls")
            continue
        cell = (
            int(c.covariates.easting_m // SPATIAL_CELL_M),
            int(c.covariates.northing_m // SPATIAL_CELL_M),
        )
        if cell_counts.get(cell, 0) >= MAX_CONTROLS_PER_CELL:
            reject("spatial_cell_full")
            continue
        cell_counts[cell] = cell_counts.get(cell, 0) + 1
        selected.append(c)

    insufficient = len(selected) < min_controls
    if insufficient:
        reason = (
            f"Only {len(selected)} matched control(s) survived from "
            f"{len(candidates)} candidates, below the minimum of {min_controls}. "
            f"The control family is reported UNAVAILABLE and the epistemic level "
            f"is capped: a control comparison is not attempted rather than "
            f"attempted on too few sites. Rejections by reason: "
            f"{dict(sorted(rejected.items()))}."
        )
    else:
        reason = (
            f"{len(selected)} matched controls selected from {len(candidates)} "
            f"candidates in the same sub-watershed, ranked by standardised "
            f"covariate distance with at most {MAX_CONTROLS_PER_CELL} per "
            f"{SPATIAL_CELL_M:.0f} m cell. Rejections by reason: "
            f"{dict(sorted(rejected.items()))}."
        )

    return ControlSet(
        selected=tuple(selected),
        rejected=rejected,
        n_candidates=len(candidates),
        insufficient=insufficient,
        channel_structure=channel_structure,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class ControlComparison:
    """The differenced estimator and its non-parametric significance screen."""

    site_delta: float
    control_median: float
    #: delta = site - median(controls). The headline number.
    differenced: float
    p10: float
    p90: float
    #: The site's percentile rank within the control delta distribution.
    site_percentile: float
    outside_control_range: bool
    exceeds_all_controls: bool
    n_controls: int
    reason: str
    extras: dict[str, object] = field(default_factory=dict)

    def lineage(self) -> dict[str, object]:
        return {
            "site_delta": self.site_delta,
            "control_median": self.control_median,
            "differenced": self.differenced,
            "control_p10": self.p10,
            "control_p90": self.p90,
            "site_percentile": self.site_percentile,
            "outside_control_range": self.outside_control_range,
            "exceeds_all_controls": self.exceeds_all_controls,
            "n_controls": self.n_controls,
            "screen": f"outside [p{LOWER_PERCENTILE:.0f}, p{UPPER_PERCENTILE:.0f}]",
            "note": "percentile reported instead of a p-value (docs §17.4)",
        }


def compare_to_controls(site_delta: float, controls: ControlSet) -> ControlComparison:
    """Difference the site against its controls and screen by percentile.

    Raises if the control set is insufficient: a comparison against fewer than
    MIN_CONTROLS sites must not be computed at all, because a number would then
    exist and someone would quote it.
    """
    if controls.insufficient:
        raise ValueError(
            f"refusing to compare against {controls.n_selected} control(s): "
            f"minimum is {MIN_CONTROLS}. {controls.reason}"
        )
    d = controls.deltas()
    median = float(np.median(d))
    p10 = float(np.percentile(d, LOWER_PERCENTILE))
    p90 = float(np.percentile(d, UPPER_PERCENTILE))
    differenced = site_delta - median
    percentile = float((d < site_delta).sum() / d.size * 100.0)
    outside = site_delta < p10 or site_delta > p90
    exceeds_all = bool(site_delta > d.max())

    if exceeds_all:
        verdict = (
            f"the site's change ({site_delta:+.4f}) exceeds ALL "
            f"{controls.n_selected} matched controls"
        )
    elif outside:
        verdict = (
            f"the site's change ({site_delta:+.4f}) falls outside the "
            f"[p{LOWER_PERCENTILE:.0f}, p{UPPER_PERCENTILE:.0f}] range of its "
            f"{controls.n_selected} matched controls "
            f"([{p10:+.4f}, {p90:+.4f}])"
        )
    else:
        verdict = (
            f"the site's change ({site_delta:+.4f}) lies within the "
            f"[p{LOWER_PERCENTILE:.0f}, p{UPPER_PERCENTILE:.0f}] range of its "
            f"{controls.n_selected} matched controls "
            f"([{p10:+.4f}, {p90:+.4f}]) — not distinguishable from the "
            f"un-intervened background"
        )

    return ControlComparison(
        site_delta=site_delta,
        control_median=median,
        differenced=differenced,
        p10=p10,
        p90=p90,
        site_percentile=percentile,
        outside_control_range=outside,
        exceeds_all_controls=exceeds_all,
        n_controls=controls.n_selected,
        reason=(
            f"Differenced estimate {differenced:+.4f} = site {site_delta:+.4f} "
            f"minus control median {median:+.4f}. Screen: {verdict}. "
            f"A percentile is reported rather than a p-value because "
            f"{controls.n_selected} spatially autocorrelated controls cannot "
            f"support one honestly (docs §17.4)."
        ),
    )
