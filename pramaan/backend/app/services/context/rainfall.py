"""Rainfall context and the `context` evidence family (docs §17.3).

Without rainfall context, a good monsoon makes every intervention look successful
and a drought makes every one look failed.

## What this family actually scores

Not "was there rain" — whether **rainfall can account for the observed change**.
The logic is deliberately counter-intuitive:

* Index rose, rainfall was normal or below normal -> context **corroborates**.
  The change happened without help from the weather.
* Index rose, rainfall was well above normal -> context **disagrees**. A wet
  year is a sufficient alternative explanation, so the rise is not attributable
  to the structure.
* Index fell, rainfall was well below normal -> context **corroborates the
  alternative explanation**, i.e. it argues against reading the fall as failure.

So a high-rainfall year *reduces* the evidential value of a positive change. This
is the family that stops the system claiming credit for a good monsoon.

## Why this is the lightest-weighted family (w = 0.08)

docs §17.3 point 3: *"the matched-control design is a better rainfall control
than any normalisation formula, because the controls physically experienced the
same weather."* Controls do the real work. Rainfall is carried for transparency
and as a visible confounder check, and weighted accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.reconcile.types import FamilyEvidence
from app.services.temporal.seasons import Season

#: |anomaly - 1| beyond this is "anomalous" (docs §17.3).
ANOMALY_THRESHOLD = 0.25

#: Beyond this, the year is extreme enough that it is a sufficient alternative
#: explanation on its own.
STRONG_ANOMALY_THRESHOLD = 0.50

#: An index change smaller than this is not attributed to anything.
MIN_MEANINGFUL_DELTA = 0.02


@dataclass(frozen=True, slots=True)
class RainfallContext:
    """Seasonal rainfall against the decadal mean for one sub-watershed."""

    season: Season
    year: int
    #: Observed seasonal rainfall, mm.
    rainfall_mm: float
    #: Decadal mean for the same season and sub-watershed, mm.
    decadal_mean_mm: float
    source: str = "CHIRPS"
    #: Years contributing to the decadal mean. Below ~8 the mean is thin and the
    #: anomaly should be read cautiously; recorded rather than hidden.
    n_years_in_mean: int = 10

    def __post_init__(self) -> None:
        if self.rainfall_mm < 0:
            raise ValueError(f"rainfall_mm cannot be negative: {self.rainfall_mm}")
        if self.decadal_mean_mm <= 0:
            raise ValueError(
                f"decadal_mean_mm must be positive, got {self.decadal_mean_mm}: "
                "an anomaly ratio is undefined without a baseline"
            )

    @property
    def anomaly(self) -> float:
        """Observed / decadal mean. 1.0 is exactly normal."""
        return self.rainfall_mm / self.decadal_mean_mm

    @property
    def deviation(self) -> float:
        """Signed distance from normal, e.g. +0.31 for a 31 % wet year."""
        return self.anomaly - 1.0

    @property
    def is_anomalous(self) -> bool:
        return abs(self.deviation) > ANOMALY_THRESHOLD

    @property
    def is_strongly_anomalous(self) -> bool:
        return abs(self.deviation) > STRONG_ANOMALY_THRESHOLD

    @property
    def descriptor(self) -> str:
        d = self.deviation
        if d > STRONG_ANOMALY_THRESHOLD:
            return "exceptionally wet"
        if d > ANOMALY_THRESHOLD:
            return "wetter than normal"
        if d < -STRONG_ANOMALY_THRESHOLD:
            return "drought"
        if d < -ANOMALY_THRESHOLD:
            return "drier than normal"
        return "near normal"

    def lineage(self) -> dict[str, object]:
        return {
            "season": self.season.value,
            "year": self.year,
            "rainfall_mm": self.rainfall_mm,
            "decadal_mean_mm": self.decadal_mean_mm,
            "anomaly": round(self.anomaly, 4),
            "deviation": round(self.deviation, 4),
            "descriptor": self.descriptor,
            "source": self.source,
            "n_years_in_mean": self.n_years_in_mean,
            "anomaly_threshold": ANOMALY_THRESHOLD,
        }


def to_context_evidence(
    contexts: list[RainfallContext],
    observed_index_delta: float | None,
) -> FamilyEvidence:
    """Build the `context` family.

    `observed_index_delta` is the change the temporal family measured. Without
    it there is nothing to explain, so the family is unavailable rather than
    neutral: rainfall alone says nothing about a claim.
    """
    if not contexts:
        return FamilyEvidence(
            family="context",
            agreement=0.0,
            available=False,
            reason=(
                "No rainfall record available for this sub-watershed and season, "
                "so no confounder check could be performed. Note that the matched-"
                "control design already removes the common rainfall effect "
                "(docs §17.3): this family is transparency, not the primary control."
            ),
            lineage={},
        )

    if observed_index_delta is None:
        return FamilyEvidence(
            family="context",
            agreement=0.0,
            available=False,
            reason=(
                "Rainfall context is available but no index change was measured, "
                "so there is nothing for rainfall to explain. Rainfall on its own "
                "is not evidence about a claim."
            ),
            lineage={"contexts": [c.lineage() for c in contexts]},
        )

    # Mean deviation across the assessed seasons, weighted by nothing: each
    # assessed season contributes equally, because a wet rabi and a wet summer
    # are separately capable of explaining their own season's change.
    mean_dev = sum(c.deviation for c in contexts) / len(contexts)
    worst = max(contexts, key=lambda c: abs(c.deviation))

    parts = [
        f"{c.season.value} {c.year}: {c.rainfall_mm:.0f} mm against a "
        f"{c.decadal_mean_mm:.0f} mm decadal mean "
        f"(anomaly {c.anomaly:.2f}x, {c.descriptor})"
        for c in contexts
    ]

    if abs(observed_index_delta) < MIN_MEANINGFUL_DELTA:
        agreement = 0.0
        verdict = (
            f"the measured change ({observed_index_delta:+.4f}) is below the "
            f"noise floor, so there is nothing for rainfall to explain either way"
        )
    else:
        # Does rainfall move in the same direction as the observed change?
        # If so, it is a competing explanation and the family disagrees.
        same_direction = (observed_index_delta > 0) == (mean_dev > 0)
        strength = min(1.0, abs(mean_dev) / STRONG_ANOMALY_THRESHOLD)
        if abs(mean_dev) <= ANOMALY_THRESHOLD:
            # Near-normal rainfall: the change happened without help. This is
            # the corroborating case, and it is capped because "normal weather"
            # is weak positive evidence, not proof.
            agreement = 0.6
            verdict = (
                f"rainfall was {worst.descriptor} (anomaly {worst.anomaly:.2f}x), "
                f"so the observed change of {observed_index_delta:+.4f} is not "
                f"attributable to unusual weather"
            )
        elif same_direction:
            agreement = -strength
            verdict = (
                f"rainfall was {worst.descriptor} (anomaly {worst.anomaly:.2f}x) "
                f"in the same direction as the observed change "
                f"({observed_index_delta:+.4f}), so the weather is a sufficient "
                f"alternative explanation and this change cannot be attributed "
                f"to the intervention"
            )
        else:
            agreement = strength * 0.8
            verdict = (
                f"rainfall was {worst.descriptor} (anomaly {worst.anomaly:.2f}x) "
                f"AGAINST the direction of the observed change "
                f"({observed_index_delta:+.4f}), so the change occurred despite "
                f"the weather rather than because of it"
            )

    return FamilyEvidence(
        family="context",
        agreement=round(max(-1.0, min(1.0, agreement)), 4),
        available=True,
        reason=f"{verdict}. Rainfall record: {'; '.join(parts)}.",
        lineage={
            "contexts": [c.lineage() for c in contexts],
            "mean_deviation": round(mean_dev, 4),
            "observed_index_delta": observed_index_delta,
            "note": (
                "The matched-control design is the primary rainfall control "
                "(docs §17.3 point 3); this family is a visible confounder check."
            ),
        },
    )
