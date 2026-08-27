"""Epistemic level assignment (docs §16.2 STEP 10, §7.3).

The rules here are the product. Two properties are non-negotiable:

1. **Every level is reached by a named rule**, recorded in ``rule_path``. No
   verdict is ever produced by falling through an ``if`` chain into a default
   that nobody can name. When a golden case regresses, the diff shows which
   rule changed.

2. **N3 (Contradicted) has exactly two paths, and both are exclusion tests.**
   This is the D1 fix. An earlier draft required ``detectability_gate PASSED``
   for any N3 verdict, while the design document's flagship worked example
   reached N3 with the gate FAILED (a farm pond at 625 m^2 against a 900 m^2
   pixel). One of the two had to be wrong. The resolution is that there are
   genuinely two different ways to contradict a claim:

   ``N3_SATELLITE_PATH``
       We could have seen the expected signature, we looked, and it is absent in
       multiple independent families.

   ``N3_TERRAIN_PATH``
       We could *not* have seen it — the structure is below the sensor's
       detection limit — but the terrain is categorically incapable of hosting
       or benefiting from this structure type, and the escalated cluster-scale
       evidence does not corroborate the claim either. The verdict rests on a
       deterministic rule, not on absence of satellite evidence, and the dissent
       panel is required to say exactly that.

   Anything that satisfies neither path is N1 (Inconclusive), never N3.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.reconcile.signatures import Signature
from app.services.reconcile.types import (
    INDEPENDENT_FAMILIES,
    Aggregate,
    EvidenceBundle,
    Level,
)
from app.services.reconcile.weights import EngineConfig

#: Ordering used to clamp a level to a type's ceiling. Only the positive ladder
#: is ordered; negative levels are not "lower" than positive ones, they are a
#: different axis, so they are never clamped.
_POSITIVE_ORDER: tuple[Level, ...] = (
    Level.L0_RECORDED,
    Level.L1_OBSERVED,
    Level.L2_CORROBORATED,
    Level.L3_MULTI_INDICATOR,
    Level.L4_CONTROL_DIFFERENCED,
)


@dataclass(frozen=True, slots=True)
class LevelDecision:
    level: Level
    rule_path: tuple[str, ...]


def _agreeing(bundle: EvidenceBundle, cfg: EngineConfig) -> tuple[str, ...]:
    return tuple(f.family for f in bundle.available() if f.agreement >= cfg.agreeing_threshold)


def _disagreeing(bundle: EvidenceBundle, cfg: EngineConfig) -> tuple[str, ...]:
    return tuple(f.family for f in bundle.available() if f.agreement <= cfg.disagreeing_threshold)


def _control_passes(bundle: EvidenceBundle, cfg: EngineConfig) -> bool:
    """A control comparison 'passes' only when controls are available AND the
    site's differential is positive beyond the agreement threshold.

    An unavailable control family (fewer than the minimum matched sites) is not
    a failed control comparison — it is the absence of one, which blocks L4
    without counting against the claim.
    """
    control = bundle.get("control")
    return bool(control and control.available and control.agreement >= cfg.agreeing_threshold)


def _clamp_to_ceiling(level: Level, signature: Signature, rule_path: list[str]) -> Level:
    """Clamp a positive level to the intervention type's ceiling (docs §18.1)."""
    if level not in _POSITIVE_ORDER:
        return level
    ceiling = signature.ceiling
    if ceiling not in _POSITIVE_ORDER:
        return level
    if _POSITIVE_ORDER.index(level) <= _POSITIVE_ORDER.index(ceiling):
        return level
    rule_path.append(f"CEILING_CLAMP[{signature.type_key}->{ceiling.value}]")
    return ceiling


def _apply_terrain_contradiction_cap(
    level: Level,
    bundle: EvidenceBundle,
    cfg: EngineConfig,
    rule_path: list[str],
    n3_terrain_block_reason: str,
) -> Level:
    """Cap the positive ladder at L2 when terrain categorically disagrees.

    Terrain is the deterministic family: it is derived from a DEM by physics,
    not inferred by a model, and it is unaffected by cloud, season or sensor
    resolution. When it says a site is categorically incapable of hosting this
    structure type, the system must not issue a strong positive verdict even if
    every observational family agrees.

    The claim is not thereby contradicted — the work may well exist and be
    functioning, just badly sited, and that case is real (see golden case 16,
    where cluster-scale imagery clearly corroborates an implausibly sited farm
    pond). So the response is a *cap*, not a negative verdict: report L2, put
    the terrain finding in the dissent panel as counter-evidence, and let a
    human weigh siting against outcome.
    """
    if level not in _POSITIVE_ORDER:
        return level
    if _POSITIVE_ORDER.index(level) <= _POSITIVE_ORDER.index(Level.L2_CORROBORATED):
        return level
    terrain = bundle.get("terrain")
    if terrain is None or not terrain.available:
        return level
    if terrain.agreement > cfg.n3_terrain_max_agreement:
        return level
    rule_path.append(f"TERRAIN_CONTRADICTION_CAP[{level.value}->L2_corroborated]")
    # When terrain categorically disagrees but the verdict is still positive,
    # the reader is entitled to know why this was not a contradiction. Without
    # this the rule_path silently drops the most interesting fact about the
    # verdict — that an N3 path was evaluated and deliberately refused.
    rule_path.append(f"n3_terrain_blocked_by={n3_terrain_block_reason}")
    return Level.L2_CORROBORATED


# ---------------------------------------------------------------------------
# The two N3 paths
# ---------------------------------------------------------------------------


def _n3_satellite_path(
    bundle: EvidenceBundle, cfg: EngineConfig, signature: Signature
) -> tuple[bool, tuple[str, ...]]:
    """We could have seen it, we looked, it is absent in >=2 independent families."""
    reasons: list[str] = []
    if not bundle.gates.detectability_passed:
        return False, ("gate_failed",)
    if not signature.optically_assessable:
        # We never expected a signature, so its absence proves nothing.
        return False, ("type_not_optically_assessable",)
    if bundle.quality.data_sufficiency < cfg.n3_min_data_sufficiency:
        return False, ("insufficient_data",)
    independent_disagreeing = [f for f in _disagreeing(bundle, cfg) if f in INDEPENDENT_FAMILIES]
    if len(independent_disagreeing) < cfg.n3_satellite_min_disagreeing:
        return False, ("too_few_independent_disagreeing",)
    if not bundle.excluded_alternatives():
        return False, ("no_alternative_excluded",)
    reasons.append(f"independent_disagreeing={len(independent_disagreeing)}")
    return True, tuple(reasons)


def _n3_terrain_path(
    bundle: EvidenceBundle, cfg: EngineConfig, signature: Signature
) -> tuple[bool, tuple[str, ...]]:
    """We could not have seen it, but the terrain rule categorically excludes it.

    Requires, in addition to the terrain rule firing, that the escalated
    cluster-scale evidence does not corroborate the claim. Terrain
    implausibility alone is a strong signal, but on its own it is a statement
    about the site, not about whether the work happened; pairing it with
    non-corroborating cluster evidence is what makes the verdict about the claim.
    """
    if bundle.gates.detectability_passed:
        # This path exists precisely for the sub-pixel case. If the gate passed,
        # the satellite path is the correct route and must be used.
        return False, ("gate_passed_use_satellite_path",)
    terrain = bundle.get("terrain")
    if terrain is None or not terrain.available:
        return False, ("no_terrain_evidence",)
    if terrain.agreement > cfg.n3_terrain_max_agreement:
        return False, ("terrain_not_categorically_implausible",)
    if not bundle.gates.escalated_to_cluster:
        return False, ("not_escalated_to_cluster",)
    # Cluster-scale evidence must not corroborate. Any agreeing cluster-scale
    # family means the work plausibly exists despite bad siting.
    cluster_families = [f for f in bundle.available() if f.cluster_scale]
    if not cluster_families:
        return False, ("no_cluster_scale_evidence",)
    if any(f.agreement >= cfg.agreeing_threshold for f in cluster_families):
        return False, ("cluster_evidence_corroborates",)
    if not bundle.excluded_alternatives():
        return False, ("no_alternative_excluded",)
    return True, (f"cluster_families={len(cluster_families)}",)


# ---------------------------------------------------------------------------


def assign_level(
    bundle: EvidenceBundle,
    aggregate: Aggregate,
    cfg: EngineConfig,
    signature: Signature,
) -> LevelDecision:
    """Assign an epistemic level. Pure; total; never raises on valid input."""
    rule_path: list[str] = []
    agreeing = _agreeing(bundle, cfg)
    n_agreeing = len(agreeing)
    temporal = bundle.get("temporal")
    temporal_available = bool(temporal and temporal.available)

    # --- Negative ladder is evaluated first: a contradicted claim must never be
    # --- reported as weakly corroborated because some family happened to agree.
    sat_ok, sat_why = _n3_satellite_path(bundle, cfg, signature)
    if sat_ok:
        rule_path.append("N3_SATELLITE_PATH")
        rule_path.extend(sat_why)
        return LevelDecision(Level.N3_CONTRADICTED, tuple(rule_path))

    ter_ok, ter_why = _n3_terrain_path(bundle, cfg, signature)
    if ter_ok:
        rule_path.append("N3_TERRAIN_PATH")
        rule_path.extend(ter_why)
        return LevelDecision(Level.N3_CONTRADICTED, tuple(rule_path))

    # N2 — the expected signature is absent, but an N3 path's exclusion tests
    # were not satisfied. Low-priority queue, never a failure claim.
    if aggregate.score <= cfg.n2_max_score:
        blocked = (
            f"n3_satellite_blocked_by={sat_why[0]}",
            f"n3_terrain_blocked_by={ter_why[0]}",
        )
        if bundle.quality.data_sufficiency < cfg.n3_min_data_sufficiency:
            # Insufficient data cannot even support "unsupported": we do not
            # know enough to say the signature is absent rather than unobserved.
            rule_path.append("N1_DEFAULT")
            rule_path.append("reason=data_sufficiency_below_threshold")
            rule_path.extend(blocked)
            return LevelDecision(Level.N1_INCONCLUSIVE, tuple(rule_path))
        rule_path.append("N2_UNSUPPORTED")
        rule_path.extend(blocked)
        return LevelDecision(Level.N2_UNSUPPORTED, tuple(rule_path))

    # --- L0: a record exists but nothing was computed. Checked before the
    # support floor below, because "no evidence" is a different state from
    # "evidence that cancels out" and the two need different actions.
    if not bundle.available():
        rule_path.append("L0_RECORDED")
        rule_path.append("reason=no_family_available")
        return LevelDecision(Level.L0_RECORDED, tuple(rule_path))

    # --- Net-support floor. A claim cannot be *corroborated* at any level while
    # the weighted evidence is neutral or conflicting.
    #
    # This guard exists because the L2/L3/L4 rules in docs §16.2 STEP 10 count
    # only *agreeing* families and are silent about simultaneous disagreement.
    # Read literally, a bundle of terrain +1, satellite -1, temporal +1,
    # control -1 has two agreeing families including a non-photo one, and would
    # be reported as L2 CORROBORATED with a score of roughly zero. That is the
    # single most embarrassing verdict this engine could produce, so the ladder
    # requires net support, not just a count of votes in favour.
    if aggregate.score < cfg.partial_min_score:
        disagreeing = _disagreeing(bundle, cfg)
        rule_path.append("N1_DEFAULT")
        if disagreeing and agreeing:
            rule_path.append("reason=conflicting_families")
            rule_path.append(f"agreeing={','.join(agreeing)}")
            rule_path.append(f"disagreeing={','.join(disagreeing)}")
        else:
            rule_path.append("reason=net_support_below_floor")
        rule_path.append(f"score={aggregate.score:.4f}")
        return LevelDecision(Level.N1_INCONCLUSIVE, tuple(rule_path))

    # --- Data-sufficiency floor, applied symmetrically. The negative ladder
    # already refuses to conclude below this threshold; so must the positive
    # one. Reaching "corroborated" off 0.18 data sufficiency would be the
    # mirror image of the error the N3 gate exists to prevent — and docs §16.3
    # Example C states the intended outcome explicitly: "INCONCLUSIVE — N1 ·
    # data sufficiency 0.18".
    if bundle.quality.data_sufficiency < cfg.n3_min_data_sufficiency:
        rule_path.append("N1_DEFAULT")
        rule_path.append("reason=data_sufficiency_below_threshold")
        rule_path.append(f"data_sufficiency={bundle.quality.data_sufficiency:.4f}")
        return LevelDecision(Level.N1_INCONCLUSIVE, tuple(rule_path))

    # --- Positive ladder, highest first.
    if (
        n_agreeing >= cfg.l4_min_agreeing_families
        and temporal_available
        and _control_passes(bundle, cfg)
        and aggregate.coverage >= cfg.l4_min_coverage
    ):
        rule_path.append("L4_CONTROL_DIFFERENCED")
        rule_path.append(f"agreeing={n_agreeing}")
        rule_path.append(f"coverage={aggregate.coverage:.4f}")
        level = _clamp_to_ceiling(Level.L4_CONTROL_DIFFERENCED, signature, rule_path)
        level = _apply_terrain_contradiction_cap(level, bundle, cfg, rule_path, ter_why[0])
        return LevelDecision(level, tuple(rule_path))

    if n_agreeing >= cfg.l3_min_agreeing_families and temporal_available:
        rule_path.append("L3_MULTI_INDICATOR")
        rule_path.append(f"agreeing={n_agreeing}")
        level = _clamp_to_ceiling(Level.L3_MULTI_INDICATOR, signature, rule_path)
        level = _apply_terrain_contradiction_cap(level, bundle, cfg, rule_path, ter_why[0])
        return LevelDecision(level, tuple(rule_path))

    if n_agreeing >= cfg.l2_min_agreeing_families and any(
        f in INDEPENDENT_FAMILIES for f in agreeing
    ):
        rule_path.append("L2_CORROBORATED")
        rule_path.append(f"agreeing={n_agreeing}")
        level = _clamp_to_ceiling(Level.L2_CORROBORATED, signature, rule_path)
        return LevelDecision(level, tuple(rule_path))

    if n_agreeing == 1:
        rule_path.append("L1_OBSERVED")
        rule_path.append(f"single_family={agreeing[0]}")
        level = _clamp_to_ceiling(Level.L1_OBSERVED, signature, rule_path)
        return LevelDecision(level, tuple(rule_path))

    rule_path.append("N1_DEFAULT")
    rule_path.append(f"agreeing={n_agreeing}")
    rule_path.append(f"score={aggregate.score:.4f}")
    return LevelDecision(Level.N1_INCONCLUSIVE, tuple(rule_path))
