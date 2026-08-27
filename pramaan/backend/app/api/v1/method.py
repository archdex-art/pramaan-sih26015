"""Method endpoints — the engine explaining itself.

These exist because of a design decision in docs §14.4: the weights and
thresholds are served to the UI's "Method" panel **from the engine at runtime**,
never duplicated into frontend constants or into prose. A judge who clicks
"Method" sees the same numbers the verdict was computed with, because there is
only one copy.

The same principle covers the epistemic ladder and the expected-signature table:
if it drives a verdict, it is queryable.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.services.reconcile import SIGNATURES, Level
from app.services.reconcile.types import FAMILIES, INDEPENDENT_FAMILIES
from app.services.reconcile.weights import ENGINE_VERSION, EngineConfig

router = APIRouter(prefix="/method", tags=["method"])


@router.get("/weights")
def get_weights() -> dict[str, object]:
    """The frozen family weights and the aggregation formula, as used."""
    cfg = EngineConfig()
    return {
        "engine_version": ENGINE_VERSION,
        "config_fingerprint": cfg.fingerprint(),
        "families": list(FAMILIES),
        "independent_families": sorted(INDEPENDENT_FAMILIES),
        "weights": {family: cfg.weight(family) for family in FAMILIES},
        "weight_sum": cfg.weight_sum(),
        "formula": {
            "support": "sum(w_e * s_e * a_e)",
            "weight_total": "sum(w_e * a_e)",
            "score": "support / max(weight_total, eps)",
            "coverage": "weight_total / sum(w_e)",
            "quality": "metadata_integrity * data_sufficiency",
            "confidence": "abs(score) * coverage * quality",
        },
        "notes": {
            "metadata": (
                "Metadata is not an evidence family. It is a trust multiplier and "
                "enters only through quality.metadata_integrity (ADR-001)."
            ),
            "photo_weight": (
                "The photo family carries the lowest substantive weight because the "
                "photograph is the claim's own source and must not outvote "
                "independent evidence. Independent families total "
                f"{cfg.weight_sum() - cfg.weight('photo'):.2f} against photo's "
                f"{cfg.weight('photo'):.2f}."
            ),
            "assumption": (
                "These weights are a documented assumption, not a fitted parameter. "
                "They become an estimate once the adjudication ledger holds enough "
                "decisions to fit them."
            ),
        },
    }


@router.get("/thresholds")
def get_thresholds() -> dict[str, object]:
    """Every decision threshold the engine applies."""
    cfg = EngineConfig()
    return {
        "engine_version": ENGINE_VERSION,
        "agreement": {
            "epsilon": cfg.agreement_epsilon,
            "agreeing_at_or_above": cfg.agreeing_threshold,
            "disagreeing_at_or_below": cfg.disagreeing_threshold,
        },
        "levels": {
            "l4_min_agreeing_families": cfg.l4_min_agreeing_families,
            "l4_min_coverage": cfg.l4_min_coverage,
            "l3_min_agreeing_families": cfg.l3_min_agreeing_families,
            "l2_min_agreeing_families": cfg.l2_min_agreeing_families,
        },
        "negative": {
            "n3_min_data_sufficiency": cfg.n3_min_data_sufficiency,
            "n3_satellite_min_disagreeing": cfg.n3_satellite_min_disagreeing,
            "n3_terrain_max_agreement": cfg.n3_terrain_max_agreement,
            "n2_max_score": cfg.n2_max_score,
        },
        "labels": {
            "corroborated_min_score": cfg.corroborated_min_score,
            "partial_min_score": cfg.partial_min_score,
        },
        "priority": {
            "priority_1_requires_terrain_driver": cfg.priority_1_requires_terrain_driver,
        },
    }


@router.get("/ladder")
def get_ladder() -> dict[str, object]:
    """The epistemic ladder, including what the system refuses to issue."""
    return {
        "levels": [level.value for level in Level],
        "ceiling": Level.L4_CONTROL_DIFFERENCED.value,
        "refused": {
            "L5_causal": (
                "PRAMAAN never issues a causal verdict. L5 is absent from the "
                "engine's Level enum entirely, not merely unreachable, so no code "
                "path can construct it. Attribution requires a designed evaluation "
                "with field measurement."
            )
        },
        "n3_paths": {
            "N3_SATELLITE_PATH": (
                "The structure is large enough to observe, data were sufficient, and "
                "the expected signature is absent in at least two independent "
                "families, with at least one alternative explanation excluded."
            ),
            "N3_TERRAIN_PATH": (
                "The structure is below the sensor detection limit, so absence of a "
                "satellite signature proves nothing. The verdict instead rests on a "
                "deterministic terrain rule that categorically excludes the site, "
                "plus cluster-scale evidence that does not corroborate the claim. "
                "The dissent panel is required to disclose this."
            ),
        },
    }


@router.get("/signatures")
def get_signatures() -> dict[str, object]:
    """The expected-signature table (docs §18.1), including the honest rows."""
    return {
        "signatures": {
            key: {
                "purpose": sig.purpose,
                "expect_increase": list(sig.expect_increase),
                "expect_decrease": list(sig.expect_decrease),
                "aoi": sig.aoi,
                "footprint_min_m2": sig.footprint_min_m2,
                "footprint_max_m2": sig.footprint_max_m2,
                "typical_footprint_m2": sig.typical_footprint_m2,
                "terrain_rule": sig.terrain_rule,
                "confidence_ceiling": sig.ceiling.value,
                "optically_assessable": sig.optically_assessable,
                "note": sig.note,
            }
            for key, sig in SIGNATURES.items()
        },
        "not_optically_assessable": sorted(
            key for key, sig in SIGNATURES.items() if not sig.optically_assessable
        ),
    }
