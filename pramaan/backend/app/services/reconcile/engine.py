"""The reconciliation engine (docs §16.2, §20.2).

``reconcile(bundle, cfg) -> Verdict`` is a **pure function**: no IO, no network,
no clock, no randomness, no global mutable state. Given the same bundle and
config it returns a byte-identical verdict forever. That single property is what
makes machine output admissible as government evidence (docs §21.3), and it is
enforced mechanically by ``tests/unit/test_engine_purity.py``, which walks this
package's AST and asserts it imports nothing impure.

The arithmetic is exactly the published formula (docs §14.4) and nothing else:

    support      = sum(w_e * s_e * a_e)
    weight_total = sum(w_e * a_e)
    score        = support / max(weight_total, eps)
    coverage     = weight_total / sum(w_e)
    quality      = metadata_integrity * data_sufficiency
    confidence   = |score| * coverage * quality

There is deliberately no extra fudge term, no post-hoc rescaling and no clamp
that would make a published example unreproducible.
"""

from __future__ import annotations

from app.services.reconcile.dissent import build_dissent
from app.services.reconcile.levels import assign_level
from app.services.reconcile.signatures import Signature, signature_for
from app.services.reconcile.types import (
    Action,
    Aggregate,
    EvidenceBundle,
    Label,
    Level,
    Verdict,
)
from app.services.reconcile.weights import ENGINE_VERSION, EngineConfig

#: Guards a division by zero when every family is unavailable. Chosen small
#: enough never to affect a real computation and named so it is not a mystery.
_EPS = 1e-9


def aggregate_evidence(bundle: EvidenceBundle, cfg: EngineConfig) -> Aggregate:
    """The published aggregation formula. No hidden terms."""
    support = 0.0
    weight_total = 0.0
    for fam in bundle.families:
        if not fam.available:
            continue
        weight = cfg.weight(fam.family)
        agreement = fam.agreement
        # Sub-epsilon agreement is noise, not support. Reported as neutral so a
        # 0.02 wobble is never presented to an officer as "this family agrees".
        if abs(agreement) < cfg.agreement_epsilon:
            agreement = 0.0
        support += weight * agreement
        weight_total += weight

    score = support / max(weight_total, _EPS)
    coverage = weight_total / cfg.weight_sum()
    quality = bundle.quality.product
    confidence = abs(score) * coverage * quality
    return Aggregate(
        support=support,
        weight_total=weight_total,
        score=score,
        coverage=coverage,
        quality=quality,
        confidence=confidence,
    )


def label_for(level: Level, score: float, cfg: EngineConfig) -> Label:
    """Map level plus score to the human-facing label.

    Level leads, not score: a contradicted verdict is CONTRADICTED regardless of
    how the arithmetic came out, because the level encodes the exclusion tests
    that the score alone cannot express.

    Public because the API needs it. `label` is not a stored column - it is
    derived - and the read model must derive it the same way the engine did.
    An earlier draft of `api/v1/verdicts.py` reimplemented the mapping from
    `level` alone and got it wrong; that is impossible here, because the label
    genuinely depends on score as well.
    """
    if level is Level.N3_CONTRADICTED:
        return "CONTRADICTED"
    if level is Level.N2_UNSUPPORTED:
        return "UNSUPPORTED"
    if level in {Level.N1_INCONCLUSIVE, Level.L0_RECORDED}:
        return "INCONCLUSIVE"
    if score >= cfg.corroborated_min_score:
        return "CORROBORATED"
    if score >= cfg.partial_min_score:
        return "PARTIAL"
    return "INCONCLUSIVE"


def _terrain_is_driver(bundle: EvidenceBundle, cfg: EngineConfig) -> bool:
    terrain = bundle.get("terrain")
    return bool(terrain and terrain.available and terrain.agreement <= cfg.disagreeing_threshold)


def _recommend(
    bundle: EvidenceBundle,
    level: Level,
    aggregate: Aggregate,
    cfg: EngineConfig,
    signature: Signature,
) -> tuple[Action, int | None]:
    """Turn a verdict into a work instruction (docs §16.2 STEP 12).

    Priority 1 is reserved for verdicts driven by a deterministic terrain rule.
    An AI-driven flag never reaches the top of a human's queue on its own — that
    is the structural answer to the false-accusation risk (W6, docs §37).
    """
    if level is Level.N3_CONTRADICTED:
        if cfg.priority_1_requires_terrain_driver and _terrain_is_driver(bundle, cfg):
            return "physical_verification", 1
        return "physical_verification", 2

    if level is Level.N2_UNSUPPORTED:
        return "physical_verification", 3

    if level in {Level.N1_INCONCLUSIVE, Level.L0_RECORDED}:
        # Distinguish "we could not see" from "the record is broken". Only the
        # latter is something a field officer can act on today.
        if bundle.quality.metadata_integrity < 0.5:
            return "recapture_geotag", 4
        if not signature.optically_assessable:
            # Re-observing a borewell or a livestock asset next season will
            # never produce a signature, because the type has none. Telling an
            # officer to "confirm next cycle" would be busywork the system knows
            # is futile — so it asks for nothing instead.
            return "no_action", None
        if bundle.quality.data_sufficiency < cfg.n3_min_data_sufficiency:
            return "confirm_next_cycle", None
        if not bundle.available():
            return "data_quality_fix", 4
        return "confirm_next_cycle", None

    # Positive ladder.
    if level in {Level.L3_MULTI_INDICATOR, Level.L4_CONTROL_DIFFERENCED}:
        return "no_action", None
    if not signature.optically_assessable:
        # Existence-only types can never be confirmed further by re-observation.
        return "no_action", None
    return "confirm_next_cycle", None


def _build_lineage(
    bundle: EvidenceBundle, cfg: EngineConfig, aggregate: Aggregate
) -> dict[str, object]:
    """The record that makes a verdict recomputable (docs §21.3).

    Everything needed to reproduce this verdict byte-identically, and nothing
    that would make it irreproducible — notably no timestamp. The computation
    time belongs on the database row, not in the engine's output identity.
    """
    return {
        "engine_version": ENGINE_VERSION,
        "config_fingerprint": cfg.fingerprint(),
        "intervention_type": bundle.intervention_type,
        "gates": {
            "detectability_passed": bundle.gates.detectability_passed,
            "expected_footprint_m2": bundle.gates.expected_footprint_m2,
            "pixel_area_m2": bundle.gates.pixel_area_m2,
            "footprint_pixels": bundle.gates.footprint_pixels,
            "escalated_to_cluster": bundle.gates.escalated_to_cluster,
            "scene_scale": bundle.gates.scene_scale,
            "terrain_plausibility": bundle.gates.terrain_plausibility,
        },
        "quality": {
            "metadata_integrity": bundle.quality.metadata_integrity,
            "data_sufficiency": bundle.quality.data_sufficiency,
        },
        "aggregate": {
            "support": aggregate.support,
            "weight_total": aggregate.weight_total,
            "score": aggregate.score,
            "coverage": aggregate.coverage,
            "quality": aggregate.quality,
            "confidence": aggregate.confidence,
        },
        "families": {
            fam.family: {
                "agreement": fam.agreement,
                "available": fam.available,
                "cluster_scale": fam.cluster_scale,
                "lineage": fam.lineage,
            }
            for fam in bundle.families
        },
        "alternatives": [
            {"description": a.description, "excluded": a.excluded, "basis": a.basis}
            for a in bundle.alternatives
        ],
    }


def reconcile(bundle: EvidenceBundle, cfg: EngineConfig | None = None) -> Verdict:
    """Reconcile an evidence bundle into an adjudicable verdict.

    Pure. Deterministic. Total on any bundle that constructed successfully.
    """
    cfg = cfg if cfg is not None else EngineConfig()
    signature = signature_for(bundle.intervention_type)

    aggregate = aggregate_evidence(bundle, cfg)
    decision = assign_level(bundle, aggregate, cfg, signature)
    label = label_for(decision.level, aggregate.score, cfg)
    dissent = build_dissent(bundle, aggregate, decision.level, cfg, signature)
    action, priority = _recommend(bundle, decision.level, aggregate, cfg, signature)

    return Verdict(
        claim_id=bundle.claim_id,
        label=label,
        level=decision.level,
        score=round(aggregate.score, 4),
        confidence=round(aggregate.confidence, 4),
        coverage=round(aggregate.coverage, 4),
        quality=round(aggregate.quality, 4),
        data_sufficiency=round(bundle.quality.data_sufficiency, 4),
        rule_path=decision.rule_path,
        dissent=dissent,
        recommended_action=action,
        priority=priority,
        engine_version=ENGINE_VERSION,
        # Widened from dict[Family, float] to dict[str, float]: the verdict is a
        # serialisation boundary (DB JSONB, API response, PDF), and the Literal
        # key type is invariant so it cannot be passed through directly.
        weights={str(family): weight for family, weight in cfg.weights.items()},
        lineage=_build_lineage(bundle, cfg, aggregate),
    )
