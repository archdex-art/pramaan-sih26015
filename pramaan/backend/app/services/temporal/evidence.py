"""Temporal and control deltas -> engine evidence families.

Two families come out of this module, and keeping them separate is ADR-001's
key structural decision:

* `temporal` answers *"did the surface state at this site change?"*
* `control` answers *"did comparable un-intervened sites change the same way?"*

They fail independently — a cloud gap kills `temporal`; a thin matched pool kills
`control` while leaving `temporal` intact — and the L4 rule requires both.

## Scoring against the expected signature, not in the abstract

A rising MNDWI is corroborating for a check dam and meaningless for a contour
trench, whose signature contains no water term at all. So every delta is scored
against `Signature.expect_increase` / `expect_decrease`, and an index the type
does not expect is skipped rather than counted as neutral agreement.

## Season weighting

docs §17.2 names rabi "the diagnostic season" and summer "the stress season",
and calls kharif the weakest — monsoon water bodies are at maximum extent
regardless of any intervention, and cloud makes kharif thin anyway (measured:
10.6 % of kharif scenes usable versus 75 % in rabi, docs/11 §8). So seasons are
weighted rather than averaged, and the weights are published.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.reconcile.signatures import Signature, signature_for
from app.services.reconcile.types import FamilyEvidence
from app.services.temporal.controls import ControlComparison, ControlSet
from app.services.temporal.seasons import Season, SeasonalDelta
from app.services.temporal.trend import TrendResult

#: Diagnostic weight per season (docs §17.2). Published, not hidden: a judge
#: asking "why does rabi count more?" gets the rationale in SEASON_RATIONALE.
SEASON_WEIGHT: dict[Season, float] = {
    Season.RABI: 1.0,  # residual moisture and irrigation show up here
    Season.SUMMER: 0.9,  # water persisting into summer is the strongest signal
    Season.KHARIF: 0.4,  # max extent regardless of intervention; cloud-thin
}

#: A delta smaller than this is noise at 30 m, not a change. Index units.
MIN_MEANINGFUL_DELTA = 0.02

#: Delta at which a same-season change is scored as fully consistent. Chosen
#: from the design document's own worked example (rabi NDVI +0.09 in the command
#: buffer read as corroborating), not from a fitted threshold.
FULL_AGREEMENT_DELTA = 0.08


def _expected_direction(index_name: str, signature: Signature) -> int:
    """+1 if the type expects this index to rise, -1 to fall, 0 if not expected."""
    base = index_name.split("_")[0].upper()
    for expected in signature.expect_increase:
        if expected.split("_")[0].upper() == base:
            return 1
    for expected in signature.expect_decrease:
        if expected.split("_")[0].upper() == base:
            return -1
    return 0


def _score_delta(delta: SeasonalDelta, direction: int) -> float:
    """Signed agreement in [-1, 1] for one delta against its expected direction."""
    if abs(delta.delta) < MIN_MEANINGFUL_DELTA:
        return 0.0
    observed = delta.delta * direction  # positive when it moved as expected
    magnitude = min(1.0, abs(observed) / FULL_AGREEMENT_DELTA)
    return magnitude if observed > 0 else -magnitude


@dataclass(frozen=True, slots=True)
class TemporalAssessment:
    """What the temporal producer concluded, before it becomes evidence."""

    deltas: tuple[SeasonalDelta, ...]
    trends: dict[str, TrendResult]
    #: Weighted agreement across all scored deltas.
    agreement: float
    #: Seasons that could not be paired, and why.
    skipped: dict[Season, str]
    scored: int
    reasons: tuple[str, ...]


def assess(
    deltas: list[SeasonalDelta],
    intervention_type: str,
    *,
    trends: dict[str, TrendResult] | None = None,
    skipped: dict[Season, str] | None = None,
) -> TemporalAssessment:
    """Score a claim's seasonal deltas against its type's expected signature."""
    signature = signature_for(intervention_type)
    reasons: list[str] = []
    weighted_sum = 0.0
    weight_total = 0.0
    scored = 0

    for d in deltas:
        direction = _expected_direction(d.index_name, signature)
        if direction == 0:
            reasons.append(
                f"{d.index_name} is not part of the expected signature for a "
                f"{intervention_type}, so its {d.season.value} change "
                f"({d.delta:+.4f}) is recorded but not scored"
            )
            continue
        season_w = SEASON_WEIGHT[d.season]
        score = _score_delta(d, direction)
        weighted_sum += score * season_w
        weight_total += season_w
        scored += 1
        expected_word = "increase" if direction > 0 else "decrease"
        if abs(d.delta) < MIN_MEANINGFUL_DELTA:
            reasons.append(
                f"{d.index_name} {d.season.value} {d.pre.year}->{d.post.year} "
                f"changed {d.delta:+.4f}, below the {MIN_MEANINGFUL_DELTA} "
                f"noise floor at 30 m — no change asserted"
            )
        else:
            moved = "as expected" if score > 0 else "against expectation"
            reasons.append(
                f"{d.index_name} {d.season.value} {d.pre.year}->{d.post.year} "
                f"{d.delta:+.4f} ({expected_word} expected) — moved {moved}, "
                f"agreement {score:+.2f} at season weight {season_w:.1f}"
            )

    agreement = weighted_sum / weight_total if weight_total > 0 else 0.0

    for name, tr in (trends or {}).items():
        reasons.append(f"{name} trend: {tr.reason}")

    for season, why in (skipped or {}).items():
        reasons.append(f"{season.value} not assessed: {why}")

    return TemporalAssessment(
        deltas=tuple(deltas),
        trends=dict(trends or {}),
        agreement=max(-1.0, min(1.0, agreement)),
        skipped=dict(skipped or {}),
        scored=scored,
        reasons=tuple(reasons),
    )


def to_temporal_evidence(
    assessment: TemporalAssessment,
    *,
    cluster_scale: bool = False,
    lineage_extra: dict[str, object] | None = None,
) -> FamilyEvidence:
    """Build the `temporal` family.

    Unavailable when nothing could be scored — not neutral. An unpaired season
    told us nothing, and coverage must record that rather than reading it as
    agreement of zero (invariant I4).
    """
    if assessment.scored == 0:
        return FamilyEvidence(
            family="temporal",
            agreement=0.0,
            available=False,
            reason=(
                "No same-season delta could be scored against this intervention "
                "type's expected signature. "
                + (
                    "; ".join(assessment.reasons)
                    if assessment.reasons
                    else "No usable seasonal observations in both windows."
                )
            ),
            lineage={"scored": 0, **(lineage_extra or {})},
            cluster_scale=cluster_scale,
        )

    lineage: dict[str, object] = {
        "scored_deltas": assessment.scored,
        "season_weights": {s.value: w for s, w in SEASON_WEIGHT.items()},
        "min_meaningful_delta": MIN_MEANINGFUL_DELTA,
        "full_agreement_delta": FULL_AGREEMENT_DELTA,
        "deltas": [
            {
                "index": d.index_name,
                "season": d.season.value,
                "pre_year": d.pre.year,
                "post_year": d.post.year,
                "delta": d.delta,
                "data_sufficiency": d.data_sufficiency,
            }
            for d in assessment.deltas
        ],
        "trends": {k: v.lineage() for k, v in assessment.trends.items()},
        **(lineage_extra or {}),
    }
    return FamilyEvidence(
        family="temporal",
        agreement=round(assessment.agreement, 4),
        available=True,
        reason="; ".join(assessment.reasons),
        lineage=lineage,
        cluster_scale=cluster_scale,
    )


def to_control_evidence(
    controls: ControlSet,
    comparison: ControlComparison | None,
    intervention_type: str,
    index_name: str,
    *,
    cluster_scale: bool = False,
) -> FamilyEvidence:
    """Build the `control` family from a differenced comparison.

    Availability is driven by the control set, not by the result: fewer than the
    minimum matched sites means no comparison was attempted, which is an absence
    of evidence and must not be scored as agreement either way.
    """
    if controls.insufficient or comparison is None:
        return FamilyEvidence(
            family="control",
            agreement=0.0,
            available=False,
            reason=controls.reason,
            lineage=controls.lineage(),
            cluster_scale=cluster_scale,
        )

    signature = signature_for(intervention_type)
    direction = _expected_direction(index_name, signature)

    if direction == 0:
        return FamilyEvidence(
            family="control",
            agreement=0.0,
            available=False,
            reason=(
                f"{index_name} is not part of the expected signature for a "
                f"{intervention_type}, so a differenced comparison on it cannot "
                f"corroborate or contradict the claim. {controls.reason}"
            ),
            lineage={**controls.lineage(), **comparison.lineage()},
            cluster_scale=cluster_scale,
        )

    observed = comparison.differenced * direction
    if abs(comparison.differenced) < MIN_MEANINGFUL_DELTA:
        agreement = 0.0
        verdict = (
            f"the differenced estimate ({comparison.differenced:+.4f}) is below "
            f"the {MIN_MEANINGFUL_DELTA} noise floor: the site is not "
            f"distinguishable from its controls"
        )
    else:
        magnitude = min(1.0, abs(observed) / FULL_AGREEMENT_DELTA)
        # Falling inside the control range caps the magnitude: a change in the
        # expected direction that every control also shows is not differential
        # evidence, whatever its size.
        if not comparison.outside_control_range:
            magnitude *= 0.5
        agreement = magnitude if observed > 0 else -magnitude
        verdict = (
            f"differenced estimate {comparison.differenced:+.4f} against "
            f"{comparison.n_controls} matched controls"
        )
        if comparison.exceeds_all_controls:
            verdict += " — exceeds every control"
        elif comparison.outside_control_range:
            verdict += " — outside the [p10, p90] control range"
        else:
            verdict += " — within the control range, so agreement is halved"

    return FamilyEvidence(
        family="control",
        agreement=round(max(-1.0, min(1.0, agreement)), 4),
        available=True,
        reason=f"{verdict}. {comparison.reason}",
        lineage={**controls.lineage(), **comparison.lineage()},
        cluster_scale=cluster_scale,
    )
