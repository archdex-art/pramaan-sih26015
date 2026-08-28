"""Satellite index observations -> engine evidence.

This is the adapter the satellite family was missing. `indices.py` and
`fmask.py` compute the numbers; this module turns them into a `FamilyEvidence`
the engine can consume, in the same shape as the terrain, temporal, control,
photo and context adapters.

## What this family answers, and what it must not

It answers *"is the observed surface state at 30 m consistent with this
intervention type's expected signature?"* — a statement about **state at
observed dates**.

It must not answer "did it change". That is the temporal family, and it is a
different claim with a different failure mode: state can be consistent while
nothing changed (a pond that was always there), and change can be real while
state is inconsistent (a rise in a wholly wrong index). Collapsing them would
double-count one observation across two supposedly independent families, and the
independence of families is what the weighting in docs §14.4 buys.

## Availability

Unavailable, never neutral, when:

* no index in the type's expected signature was observed — the imagery said
  nothing about this claim, which is not the same as saying "no";
* the usable fraction is below the floor — a composite over mostly-cloud pixels
  has a value, and that value is noise.

Both are invariant I4: an absent family lowers coverage rather than reading as
agreement of zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.reconcile import FamilyEvidence
from app.services.reconcile.signatures import Signature, signature_for

#: Below this usable fraction the composite is not evidence. Chosen to match the
#: temporal module's MIN_OBSERVATION_SUFFICIENCY so a scene good enough for one
#: family is good enough for the other; a split floor would let the satellite
#: family speak about a composite the temporal family had already refused.
MIN_USABLE_FRACTION = 0.35

#: An index this far from its neutral point counts as clearly consistent.
#: Below the noise floor nothing is asserted at all.
STRONG_SIGNAL = 0.20
NOISE_FLOOR = 0.02

#: Neutral points differ by index family: normalised-difference water and
#: vegetation indices cross zero, so zero is the natural pivot. Anything whose
#: neutral point is not zero must be registered here rather than assumed.
NEUTRAL_POINT: dict[str, float] = {
    "NDVI": 0.0,
    "SAVI": 0.0,
    "NDWI": 0.0,
    "MNDWI": 0.0,
    "NDMI": 0.0,
    "BSI": 0.0,
}


@dataclass(frozen=True, slots=True)
class IndexObservation:
    """One index value over the claim's AOI, with the quality that produced it."""

    index_name: str
    value: float
    #: Fmask-derived usable fraction over the AOI. Scene metadata is optimistic
    #: by ~10 pp (docs/11 §9), so this must be the mask-derived figure.
    usable_fraction: float
    n_scenes: int
    #: `site disk`, `command buffer`, or `cluster` — recorded because a
    #: cluster-scale reading must never be presented as a per-structure one.
    aoi: str = "site disk"

    def __post_init__(self) -> None:
        if not 0.0 <= self.usable_fraction <= 1.0:
            raise ValueError(
                f"{self.index_name}: usable_fraction {self.usable_fraction} outside [0, 1]"
            )
        if self.n_scenes < 0:
            raise ValueError(f"{self.index_name}: n_scenes cannot be negative")


def expected_direction(index_name: str, signature: Signature) -> int:
    """+1 if this index should read high, -1 low, 0 if it is not in the signature.

    Matching is by prefix so a qualified name like `NDVI_rabi_command` resolves
    against the `NDVI` measurement. The signature table names indices in domain
    terms; the producers name them in band terms.
    """
    for expected in signature.expect_increase:
        if expected == index_name or expected.startswith(f"{index_name}_"):
            return 1
    for expected in signature.expect_decrease:
        if expected == index_name or expected.startswith(f"{index_name}_"):
            return -1
    return 0


def _score(observation: IndexObservation, direction: int) -> float:
    """Signed agreement in [-1, 1] for one observation.

    Scaled linearly to STRONG_SIGNAL rather than stepped, so a marginal reading
    produces marginal agreement instead of falling off a threshold.
    """
    neutral = NEUTRAL_POINT.get(observation.index_name, 0.0)
    offset = (observation.value - neutral) * direction
    if abs(offset) < NOISE_FLOOR:
        return 0.0
    return max(-1.0, min(1.0, offset / STRONG_SIGNAL))


@dataclass(frozen=True, slots=True)
class SatelliteAssessment:
    """What the satellite producer concluded, before it becomes evidence."""

    observations: tuple[IndexObservation, ...]
    agreement: float
    scored: int
    unscored: tuple[str, ...]
    rejected: dict[str, str]
    reasons: tuple[str, ...]


def assess(
    observations: list[IndexObservation],
    intervention_type: str,
) -> SatelliteAssessment:
    """Score observed index values against the type's expected signature."""
    signature = signature_for(intervention_type)
    reasons: list[str] = []
    unscored: list[str] = []
    rejected: dict[str, str] = {}
    weighted = 0.0
    total = 0.0
    scored = 0

    for obs in observations:
        if obs.usable_fraction < MIN_USABLE_FRACTION:
            rejected[obs.index_name] = (
                f"usable fraction {obs.usable_fraction:.0%} below the "
                f"{MIN_USABLE_FRACTION:.0%} floor"
            )
            reasons.append(
                f"{obs.index_name} not scored: only {obs.usable_fraction:.0%} of "
                f"the {obs.aoi} was cloud-free across {obs.n_scenes} scene(s)"
            )
            continue

        direction = expected_direction(obs.index_name, signature)
        if direction == 0:
            unscored.append(obs.index_name)
            reasons.append(
                f"{obs.index_name} is not part of the expected signature for a "
                f"{intervention_type}, so its value ({obs.value:+.4f}) is "
                "recorded but not scored"
            )
            continue

        score = _score(obs, direction)
        # Weight by usable fraction: a composite over 40 % of the AOI is real
        # evidence, but it is not worth as much as one over 95 %.
        weighted += score * obs.usable_fraction
        total += obs.usable_fraction
        scored += 1
        wanted = "high" if direction > 0 else "low"
        if score == 0.0:
            reasons.append(
                f"{obs.index_name} {obs.value:+.4f} is within {NOISE_FLOOR} of "
                f"neutral — no state asserted (expected {wanted})"
            )
        else:
            moved = "as expected" if score > 0 else "against expectation"
            reasons.append(
                f"{obs.index_name} {obs.value:+.4f} over the {obs.aoi} "
                f"(expected {wanted}) — reads {moved}, agreement {score:+.2f} "
                f"at usable fraction {obs.usable_fraction:.0%}"
            )

    agreement = weighted / total if total > 0 else 0.0
    return SatelliteAssessment(
        observations=tuple(observations),
        agreement=max(-1.0, min(1.0, agreement)),
        scored=scored,
        unscored=tuple(unscored),
        rejected=dict(rejected),
        reasons=tuple(reasons),
    )


def to_family_evidence(
    assessment: SatelliteAssessment,
    *,
    cluster_scale: bool = False,
    lineage_extra: dict[str, object] | None = None,
) -> FamilyEvidence:
    """Build the `satellite` family.

    Unavailable when nothing could be scored. That covers both "the type has no
    optical signature" and "every composite was too cloudy", and in neither case
    did the imagery say no — it said nothing.
    """
    lineage: dict[str, object] = {
        "observations": [
            {
                "index": o.index_name,
                "value": round(o.value, 4),
                "usable_fraction": round(o.usable_fraction, 4),
                "n_scenes": o.n_scenes,
                "aoi": o.aoi,
            }
            for o in assessment.observations
        ],
        "scored": assessment.scored,
        "unscored_indices": list(assessment.unscored),
        "rejected": dict(assessment.rejected),
        "min_usable_fraction": MIN_USABLE_FRACTION,
        "strong_signal": STRONG_SIGNAL,
        "noise_floor": NOISE_FLOOR,
    }
    if lineage_extra:
        lineage.update(lineage_extra)

    if assessment.scored == 0:
        return FamilyEvidence(
            family="satellite",
            agreement=0.0,
            available=False,
            reason=(
                "No index in this intervention type's expected signature could "
                "be scored. "
                + (
                    "; ".join(assessment.reasons)
                    if assessment.reasons
                    else "No usable composites over the claim's AOI."
                )
            ),
            lineage=lineage,
            cluster_scale=cluster_scale,
        )

    return FamilyEvidence(
        family="satellite",
        agreement=round(assessment.agreement, 4),
        available=True,
        reason="; ".join(assessment.reasons),
        lineage=lineage,
        cluster_scale=cluster_scale,
    )
