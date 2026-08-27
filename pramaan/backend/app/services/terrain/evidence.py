"""Adapter: terrain producer outputs -> engine inputs.

This is the seam between the impure world (a DEM on disk, a database row) and
the pure engine. Keeping the conversion in one named place means the engine's
contract has exactly one producer-side implementation to audit, instead of the
mapping being reinvented in a Celery task, an API handler and a seed script.
"""

from __future__ import annotations

from app.services.reconcile.types import FamilyEvidence, Gates, SceneScale
from app.services.terrain.detectability import DetectabilityResult
from app.services.terrain.plausibility import PlausibilityResult
from app.services.terrain.types import TerrainSample


def to_family_evidence(
    plausibility: PlausibilityResult,
    sample: TerrainSample,
) -> FamilyEvidence:
    """Build the `terrain` evidence family.

    Note the `available` flag comes from the plausibility result, not from
    whether a DEM existed. A sampled DEM with no applicable rule (a borewell)
    still yields `available=False`, because "we sampled the terrain but the type
    has no siting rule" is an absence of evidence, not neutral evidence.
    """
    return FamilyEvidence(
        family="terrain",
        agreement=plausibility.agreement,
        available=plausibility.available,
        reason=plausibility.reason,
        lineage={**sample.lineage(), **plausibility.lineage()},
    )


def to_gates(
    detectability: DetectabilityResult,
    plausibility: PlausibilityResult,
    *,
    scene_scale: SceneScale = "unknown",
) -> Gates:
    """Build the engine's `Gates` from the detectability and plausibility results."""
    return Gates(
        detectability_passed=detectability.passed,
        expected_footprint_m2=detectability.expected_footprint_m2,
        pixel_area_m2=detectability.pixel_area_m2,
        escalated_to_cluster=detectability.escalated_to_cluster,
        scene_scale=scene_scale,
        terrain_plausibility=plausibility.verdict,
    )
