"""Mann-Kendall trend test and Theil-Sen slope (docs §17.6).

Both are non-parametric and neither requires a distributional assumption we
cannot justify — which is the whole reason they were chosen over a linear
regression with a t-test. A 5-point seasonal index series is not normal, is
autocorrelated, and may contain an undetected cloud edge; OLS would report a
confident slope anyway.

## The refusal rule

*"For series >= 5 seasonal points: Mann-Kendall with Theil-Sen slope. For
shorter series: no trend claim, only a labelled delta."*

That is enforced, not advised. `mann_kendall` on four points returns
`insufficient=True` and no direction. Reporting a trend from three observations
would be the same class of overclaim as an uncalibrated confidence.

Implemented on the standard library plus numpy rather than pulling in scipy: the
formulas are short, and a dependency whose only use is `norm.sf` is not worth
the image size on a demo VM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

#: Below this many points, no trend claim is made at all.
MIN_POINTS_FOR_TREND = 5

#: Two-sided significance level for the Mann-Kendall Z statistic. 0.05 is
#: conventional; it is exposed so a report can state it rather than imply it.
DEFAULT_ALPHA = 0.05

Direction = Literal["increasing", "decreasing", "no trend", "undetermined"]


def _normal_sf(z: float) -> float:
    """Upper-tail standard normal probability, via erfc. No scipy needed."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


@dataclass(frozen=True, slots=True)
class TrendResult:
    """Outcome of a trend test on a seasonal series."""

    n: int
    insufficient: bool
    direction: Direction
    #: Mann-Kendall S statistic. None when insufficient.
    s: float | None
    #: Normalised test statistic.
    z: float | None
    p_value: float | None
    #: Theil-Sen slope in index units per year.
    slope_per_year: float | None
    #: Number of tied groups, reported because ties reduce the variance and a
    #: series of identical values is a real case (a dry pond every summer).
    n_ties: int
    reason: str

    @property
    def significant(self) -> bool:
        return self.direction in ("increasing", "decreasing")

    def lineage(self) -> dict[str, object]:
        return {
            "n": self.n,
            "insufficient": self.insufficient,
            "direction": self.direction,
            "s": self.s,
            "z": self.z,
            "p_value": self.p_value,
            "slope_per_year": self.slope_per_year,
            "n_ties": self.n_ties,
            "min_points_required": MIN_POINTS_FOR_TREND,
        }


def theil_sen_slope(values: npt.NDArray[np.floating], times: npt.NDArray[np.floating]) -> float:
    """Median of all pairwise slopes. Robust to up to ~29 % outliers.

    Median rather than mean of slopes: one bad composite in a 6-point series
    would drag a mean slope substantially, and one bad composite is the expected
    residual after cloud masking.
    """
    n = len(values)
    slopes: list[float] = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            dt = times[j] - times[i]
            if dt != 0:
                slopes.append(float((values[j] - values[i]) / dt))
    if not slopes:
        return float("nan")
    return float(np.median(slopes))


def mann_kendall(
    values: list[float] | npt.NDArray[np.floating],
    times: list[float] | npt.NDArray[np.floating] | None = None,
    *,
    alpha: float = DEFAULT_ALPHA,
    min_points: int = MIN_POINTS_FOR_TREND,
) -> TrendResult:
    """Two-sided Mann-Kendall test with tie correction, plus Theil-Sen slope.

    `times` are in years (they scale the slope only, not the test). Defaults to
    equally-spaced indices, which is right for a complete seasonal series and
    wrong for one with gaps — so pass real times when seasons are missing.
    """
    raw = np.asarray(values, dtype=np.float64)

    # Length is validated BEFORE the finite mask is applied. Validating after
    # would let a mismatch surface as an IndexError from the boolean index,
    # which tells the caller nothing about what they got wrong.
    if times is not None:
        raw_times = np.asarray(times, dtype=np.float64)
        if raw_times.size != raw.size:
            raise ValueError(f"times length {raw_times.size} != values length {raw.size}")
    else:
        raw_times = np.arange(raw.size, dtype=np.float64)

    finite = np.isfinite(raw)
    arr = raw[finite]
    t = raw_times[finite]
    n = int(arr.size)

    if n < min_points:
        return TrendResult(
            n=n,
            insufficient=True,
            direction="undetermined",
            s=None,
            z=None,
            p_value=None,
            slope_per_year=None,
            n_ties=0,
            reason=(
                f"{n} usable seasonal observation(s), below the {min_points} "
                f"required for a trend claim. A labelled delta is reported "
                f"instead; no direction is asserted (docs §17.6)."
            ),
        )

    # S statistic: sum of signs of all pairwise differences.
    s = 0.0
    for i in range(n - 1):
        s += float(np.sign(arr[i + 1 :] - arr[i]).sum())

    # Variance with correction for tied groups.
    _, counts = np.unique(arr, return_counts=True)
    tied = counts[counts > 1]
    tie_term = float(sum(c * (c - 1) * (2 * c + 5) for c in tied))
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0

    if var_s <= 0:
        # Every value identical: no variance, hence no trend. A dry pond
        # observed as MNDWI -0.45 in five consecutive summers lands here, and
        # "no trend" is the correct and useful answer.
        return TrendResult(
            n=n,
            insufficient=False,
            direction="no trend",
            s=s,
            z=0.0,
            p_value=1.0,
            slope_per_year=0.0,
            n_ties=int(len(tied)),
            reason=(
                "all observations identical, so the series has zero variance: "
                "no trend, reported as such rather than as an undetermined result"
            ),
        )

    # Continuity correction: S is a discrete statistic.
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0

    p = 2.0 * _normal_sf(abs(z))
    slope = theil_sen_slope(arr, t)

    if p <= alpha and z > 0:
        direction: Direction = "increasing"
        reason = (
            f"Mann-Kendall Z={z:+.3f}, p={p:.4f} <= {alpha}: a monotonic "
            f"increase is detected over {n} seasonal observations. Theil-Sen "
            f"slope {slope:+.5f} index units/year."
        )
    elif p <= alpha and z < 0:
        direction = "decreasing"
        reason = (
            f"Mann-Kendall Z={z:+.3f}, p={p:.4f} <= {alpha}: a monotonic "
            f"decrease is detected over {n} seasonal observations. Theil-Sen "
            f"slope {slope:+.5f} index units/year."
        )
    else:
        direction = "no trend"
        reason = (
            f"Mann-Kendall Z={z:+.3f}, p={p:.4f} > {alpha}: no monotonic trend "
            f"is detectable over {n} seasonal observations. This is not evidence "
            f"of no change — it is absence of a detectable trend at this series "
            f"length."
        )

    return TrendResult(
        n=n,
        insufficient=False,
        direction=direction,
        s=s,
        z=z,
        p_value=p,
        slope_per_year=slope,
        n_ties=int(len(tied)),
        reason=reason,
    )
