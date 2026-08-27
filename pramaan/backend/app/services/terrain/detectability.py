"""The detectability gate (T11, docs §16.2 STEP 3).

The single most important gate in the system, and the cheapest. It answers one
question before any imagery is touched:

    *Could this structure possibly be visible at this sensor's resolution?*

If the answer is no, then "we looked and saw nothing" carries no information,
and the engine must not be allowed to treat that silence as evidence against
the claim. Absence of evidence is not evidence of absence (docs §16.4) — and
here it is enforced as a hard gate rather than as guidance, because the failure
mode is a false accusation against a named field officer.

The gate runs first for a second, practical reason: computing seasonal index
composites for a structure that cannot be resolved is pure wasted compute.

## Why this is not simply `footprint < pixel_area`

A structure one pixel in area is not reliably detectable. It must be large
enough that a change in it moves the pixel's aggregate reflectance beyond noise,
and it will almost never be pixel-aligned — a 900 m² square footprint spread
across four 900 m² pixels contributes 25 % to each. So the gate requires a
configurable multiple of the pixel area, defaulting to 1.5 pixels, and reports
the ratio so the UI can explain itself in the structure's own numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.reconcile.signatures import signature_for

#: Nominal pixel area for the 30 m tier the problem statement names
#: (HLS L30/S30, Landsat OLI, Resourcesat LISS-III at 23.5 m resampled).
PIXEL_AREA_30M_M2 = 900.0

#: A footprint must exceed this many pixels to be individually assessable.
#: 1.5 rather than 1.0 because a structure is not pixel-aligned: a footprint of
#: exactly one pixel area typically spreads across four pixels at ~25% each,
#: which is below any defensible change-detection threshold.
DEFAULT_MIN_PIXELS = 1.5

#: Radius within which sibling claims are pooled when the gate fails. 500 m is
#: the figure in docs §16.2 STEP 3.
DEFAULT_CLUSTER_RADIUS_M = 500.0

#: A cluster needs at least this many members to be worth assessing. A "cluster"
#: of one is just the original undetectable structure with extra words.
DEFAULT_MIN_CLUSTER_MEMBERS = 3


@dataclass(frozen=True, slots=True)
class DetectabilityResult:
    """Whether per-structure satellite assessment is permitted, and why."""

    passed: bool
    expected_footprint_m2: float
    pixel_area_m2: float
    footprint_pixels: float
    min_pixels_required: float
    escalated_to_cluster: bool
    cluster_radius_m: float | None
    cluster_member_count: int | None
    #: Printed verbatim in the UI banner and the Evidence Pack.
    reason: str
    #: Type-level fact: some interventions have no optical signature at any
    #: resolution, so the gate is not the binding constraint for them.
    optically_assessable: bool

    def lineage(self) -> dict[str, object]:
        return {
            "expected_footprint_m2": self.expected_footprint_m2,
            "pixel_area_m2": self.pixel_area_m2,
            "footprint_pixels": self.footprint_pixels,
            "min_pixels_required": self.min_pixels_required,
            "escalated_to_cluster": self.escalated_to_cluster,
            "cluster_member_count": self.cluster_member_count,
            "optically_assessable": self.optically_assessable,
        }


def evaluate(
    intervention_type: str,
    *,
    expected_footprint_m2: float | None = None,
    pixel_area_m2: float = PIXEL_AREA_30M_M2,
    min_pixels: float = DEFAULT_MIN_PIXELS,
    cluster_member_count: int | None = None,
    cluster_radius_m: float = DEFAULT_CLUSTER_RADIUS_M,
    min_cluster_members: int = DEFAULT_MIN_CLUSTER_MEMBERS,
) -> DetectabilityResult:
    """Run the gate. Pure; no IO.

    `expected_footprint_m2` overrides the type default when the MIS records an
    actual dimension for this specific work — a 3 ha percolation tank and a
    0.2 ha one are both `percolation_tank`, and using the type midpoint for both
    would gate the small one wrongly.
    """
    if pixel_area_m2 <= 0:
        raise ValueError(f"pixel_area_m2 must be positive, got {pixel_area_m2}")
    if min_pixels <= 0:
        raise ValueError(f"min_pixels must be positive, got {min_pixels}")

    signature = signature_for(intervention_type)
    footprint = (
        expected_footprint_m2
        if expected_footprint_m2 is not None
        else signature.typical_footprint_m2
    )
    if footprint < 0:
        raise ValueError(f"expected_footprint_m2 cannot be negative, got {footprint}")

    pixels = footprint / pixel_area_m2
    passed = pixels >= min_pixels

    # A type with no optical signature is a separate, stronger constraint than
    # the gate. Reporting "gate passed" for a borewell because its casing
    # happens to exceed 1.5 pixels would be technically true and practically
    # misleading, so the type fact is surfaced alongside.
    if not signature.optically_assessable:
        return DetectabilityResult(
            passed=False,
            expected_footprint_m2=footprint,
            pixel_area_m2=pixel_area_m2,
            footprint_pixels=pixels,
            min_pixels_required=min_pixels,
            escalated_to_cluster=False,
            cluster_radius_m=None,
            cluster_member_count=None,
            reason=(
                f"Intervention type '{intervention_type}' has no reliable optical "
                f"signature at any resolution, so satellite assessment is not "
                f"applicable regardless of footprint. {signature.note}"
            ),
            optically_assessable=False,
        )

    if passed:
        return DetectabilityResult(
            passed=True,
            expected_footprint_m2=footprint,
            pixel_area_m2=pixel_area_m2,
            footprint_pixels=pixels,
            min_pixels_required=min_pixels,
            escalated_to_cluster=False,
            cluster_radius_m=None,
            cluster_member_count=None,
            reason=(
                f"Expected footprint {footprint:.0f} m2 is {pixels:.2f} pixels at "
                f"{pixel_area_m2:.0f} m2 per pixel, at or above the {min_pixels:.1f} "
                f"pixel minimum. Per-structure satellite assessment is enabled."
            ),
            optically_assessable=True,
        )

    # Gate failed. Try to escalate to a neighbourhood claim.
    can_escalate = cluster_member_count is not None and cluster_member_count >= min_cluster_members
    if can_escalate:
        reason = (
            f"Expected footprint {footprint:.0f} m2 is only {pixels:.2f} pixels at "
            f"{pixel_area_m2:.0f} m2 per pixel — below the sensor detection limit. "
            f"Per-structure satellite assessment is disabled; assessed as a cluster "
            f"of {cluster_member_count} works within {cluster_radius_m:.0f} m instead."
        )
    else:
        found = 1 if cluster_member_count is None else cluster_member_count
        reason = (
            f"Expected footprint {footprint:.0f} m2 is only {pixels:.2f} pixels at "
            f"{pixel_area_m2:.0f} m2 per pixel — below the sensor detection limit. "
            f"Per-structure satellite assessment is disabled, and cluster "
            f"escalation was not possible: {found} work(s) within "
            f"{cluster_radius_m:.0f} m, minimum is {min_cluster_members}."
        )

    return DetectabilityResult(
        passed=False,
        expected_footprint_m2=footprint,
        pixel_area_m2=pixel_area_m2,
        footprint_pixels=pixels,
        min_pixels_required=min_pixels,
        escalated_to_cluster=can_escalate,
        cluster_radius_m=cluster_radius_m,
        cluster_member_count=cluster_member_count,
        reason=reason,
        optically_assessable=True,
    )
