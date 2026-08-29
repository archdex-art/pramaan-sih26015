"""Bundle builders for the engine test suites.

Everything here is synthetic. The engine is a pure function, so its tests need
no database, no rasters and no network - which is exactly why it could be built
and frozen in Stage 1 before any data pipeline existed (docs §28.2 Phase 1).
"""

from __future__ import annotations

import sys
from pathlib import Path

# The backend package lives in backend/; make it importable without installing.
BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.reconcile import (  # noqa: E402
    Alternative,
    EngineConfig,
    EvidenceBundle,
    FamilyEvidence,
    Gates,
    Quality,
)
from app.services.reconcile.types import Family  # noqa: E402

#: Re-exported so `conftest.py` can build the `cfg` fixture without
#: repeating the `sys.path` setup above.
__all__ = [
    "Alternative",
    "EngineConfig",
    "EvidenceBundle",
    "FamilyEvidence",
    "Family",
    "Gates",
    "Quality",
    "all_agreeing",
    "bundle",
    "fam",
    "gates",
]


def fam(
    family: Family,
    agreement: float,
    *,
    available: bool = True,
    reason: str | None = None,
    cluster_scale: bool = False,
    lineage: dict[str, object] | None = None,
) -> FamilyEvidence:
    return FamilyEvidence(
        family=family,
        agreement=agreement,
        available=available,
        reason=reason or f"synthetic {family} evidence at agreement {agreement:+.2f}",
        lineage=lineage or {},
        cluster_scale=cluster_scale,
    )


def gates(
    *,
    passed: bool = True,
    footprint_m2: float = 3_200.0,
    pixel_area_m2: float = 900.0,
    escalated: bool = False,
    scene_scale: str = "mid",
    terrain_plausibility: str = "plausible",
) -> Gates:
    return Gates(
        detectability_passed=passed,
        expected_footprint_m2=footprint_m2,
        pixel_area_m2=pixel_area_m2,
        escalated_to_cluster=escalated,
        scene_scale=scene_scale,  # type: ignore[arg-type]
        terrain_plausibility=terrain_plausibility,  # type: ignore[arg-type]
    )


def bundle(
    *,
    claim_id: str = "TEST-0001",
    intervention_type: str = "check_dam",
    families: tuple[FamilyEvidence, ...] = (),
    gate: Gates | None = None,
    metadata_integrity: float = 0.95,
    data_sufficiency: float = 0.88,
    alternatives: tuple[Alternative, ...] = (),
    limitations: tuple[str, ...] = (),
) -> EvidenceBundle:
    return EvidenceBundle(
        claim_id=claim_id,
        intervention_type=intervention_type,
        families=families,
        gates=gate or gates(),
        quality=Quality(
            metadata_integrity=metadata_integrity,
            data_sufficiency=data_sufficiency,
        ),
        alternatives=alternatives,
        limitations=limitations,
    )


def all_agreeing(agreement: float = 1.0) -> tuple[FamilyEvidence, ...]:
    return (
        fam("terrain", agreement),
        fam("satellite", agreement),
        fam("temporal", agreement),
        fam("photo", agreement),
        fam("control", agreement),
        fam("context", agreement),
    )


# Why this is not in `conftest.py`
#
# These are plain functions, not fixtures, so callers must import them by name.
# Two `conftest.py` files now exist under `tests/` (this suite and the database
# suite), and a bare `from conftest import ...` resolves by `sys.path` order
# rather than by proximity - so it silently bound to whichever pytest had
# imported first. Giving the builders their own module name removes the
# ambiguity instead of depending on collection order.
