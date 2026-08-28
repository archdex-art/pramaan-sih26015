"""Persisting verdicts, and rebuilding an EvidenceBundle from a stored one.

This module closes the loop docs §21.3 opens. Two operations matter:

`persist_verdict`
    Writes the normalised `evidence` rows (for querying and the UI's evidence
    tree) AND the canonical bundle payload into `verdicts.lineage` (for
    reproducibility). Both, deliberately: the normalised rows are what a
    jurisdiction-scoped query filters on, and the lineage is what a recompute
    rebuilds from. Deriving one from the other at read time would make either
    the queries slow or the guarantee fragile.

`bundle_from_lineage`
    Rebuilds an `EvidenceBundle` from a stored lineage record so the engine can
    be re-run against it. This is the operation that makes "recomputable" a fact
    rather than a claim.

## Verdicts are append-only

docs §21.2: *"old verdicts are never overwritten — a new verdict version is
appended and the UI shows the history."* `persist_verdict` therefore computes
the next `version` for a claim rather than updating in place. A verdict that an
officer has already seen must remain readable exactly as they saw it, or the
adjudication ledger's hash chain attests to something that no longer exists.

## Why the payload is stored rather than reconstructed from evidence rows

The normalised rows lose things the engine needs: the `Gates`, the `Quality`
terms, the alternatives, and the exact engine config. Reconstructing a bundle
from rows alone would silently substitute today's defaults for whatever was in
force when the verdict was computed — which is precisely the failure the
guarantee exists to prevent.
"""

from __future__ import annotations

from typing import Any

from app.services.audit.reproducibility import (
    bundle_digest,
    bundle_payload,
    verdict_digest,
)
from app.services.reconcile.types import (
    Alternative,
    EvidenceBundle,
    FamilyEvidence,
    Gates,
    Quality,
    Verdict,
)
from app.services.reconcile.weights import EngineConfig


class LineageIncomplete(ValueError):
    """Raised when a stored lineage cannot rebuild a bundle.

    Distinct from a digest mismatch: this means the record itself is unusable,
    not that the verdict changed. An officer needs to know which of the two they
    are looking at.
    """


def verdict_row(
    verdict: Verdict,
    bundle: EvidenceBundle,
    *,
    claim_id: int,
    version: int,
    cfg: EngineConfig | None = None,
    extra_lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the `verdicts` row for a computed verdict.

    Returns a plain dict rather than an ORM instance so this module stays
    testable without a database session and usable from both the Celery task and
    a seed script.
    """
    cfg = cfg or EngineConfig()
    lineage: dict[str, Any] = {
        # The canonical engine input — what a recompute rebuilds from.
        "bundle": bundle_payload(bundle, cfg),
        # Producer provenance: scene ids, DEM version, model tags, control ids,
        # analysis grid. Prose reasons live here too, for the Evidence Pack.
        "producers": verdict.lineage,
        "family_reasons": {f.family: f.reason for f in bundle.families},
        "dissent": list(verdict.dissent),
    }
    if extra_lineage:
        lineage.update(extra_lineage)

    return {
        "claim_id": claim_id,
        "version": version,
        "level": verdict.level.value,
        "rule_path": list(verdict.rule_path),
        "score": verdict.score,
        "confidence": verdict.confidence,
        "coverage": verdict.coverage,
        "quality": verdict.quality,
        "data_sufficiency": verdict.data_sufficiency,
        "dissent": list(verdict.dissent),
        "recommended_action": {
            "action": verdict.recommended_action,
            "priority": verdict.priority,
        },
        "engine_version": verdict.engine_version,
        "weights": dict(verdict.weights),
        "status": "pending",
        "lineage": lineage,
        "bundle_digest": bundle_digest(bundle, cfg),
        "verdict_digest": verdict_digest(verdict),
    }


def evidence_rows(
    bundle: EvidenceBundle, *, claim_id: int, district_lgd: str
) -> list[dict[str, Any]]:
    """Build the normalised `evidence` rows.

    `district_lgd` is required because `evidence` is LIST-partitioned by it
    (migration 0001). Omitting it would route every row to the DEFAULT
    partition and quietly defeat the partitioning plan — so it is a positional
    requirement, not an optional field.
    """
    return [
        {
            "claim_id": claim_id,
            "district_lgd": district_lgd,
            "family": f.family,
            "agreement": f.agreement,
            "available": f.available,
            "payload": {
                "reason": f.reason,
                "cluster_scale": f.cluster_scale,
            },
            "lineage": f.lineage,
        }
        for f in bundle.families
    ]


def _require(payload: dict[str, Any], key: str, context: str) -> Any:
    if key not in payload:
        raise LineageIncomplete(
            f"stored lineage is missing {context}.{key}. The verdict cannot be "
            f"recomputed from this record; it predates the lineage column "
            f"(migration 0002) or was written by a different engine version."
        )
    return payload[key]


def bundle_from_lineage(lineage: dict[str, Any]) -> EvidenceBundle:
    """Rebuild the exact engine input from a stored lineage record.

    Reasons are restored from `family_reasons` where available. They do not
    affect the verdict — they are excluded from the digest for exactly that
    reason — but `FamilyEvidence` requires a non-empty reason, and a recompute
    that had to invent placeholder prose would produce an Evidence Pack that
    reads differently from the original.
    """
    stored = _require(lineage, "bundle", "lineage")
    reasons: dict[str, str] = lineage.get("family_reasons", {}) or {}

    families = tuple(
        FamilyEvidence(
            family=_require(f, "family", "bundle.families[]"),
            agreement=float(_require(f, "agreement", "bundle.families[]")),
            available=bool(_require(f, "available", "bundle.families[]")),
            reason=reasons.get(f["family"], "reason not retained in lineage"),
            lineage={},
            cluster_scale=bool(f.get("cluster_scale", False)),
        )
        for f in _require(stored, "families", "bundle")
    )

    g = _require(stored, "gates", "bundle")
    q = _require(stored, "quality", "bundle")

    return EvidenceBundle(
        claim_id=str(_require(stored, "claim_id", "bundle")),
        intervention_type=str(_require(stored, "intervention_type", "bundle")),
        families=families,
        gates=Gates(
            detectability_passed=bool(g["detectability_passed"]),
            expected_footprint_m2=float(g["expected_footprint_m2"]),
            pixel_area_m2=float(g["pixel_area_m2"]),
            escalated_to_cluster=bool(g["escalated_to_cluster"]),
            scene_scale=g.get("scene_scale", "unknown"),
            terrain_plausibility=g.get("terrain_plausibility", "unknown"),
        ),
        quality=Quality(
            metadata_integrity=float(q["metadata_integrity"]),
            data_sufficiency=float(q["data_sufficiency"]),
        ),
        alternatives=tuple(
            Alternative(
                description=a["description"],
                excluded=bool(a["excluded"]),
                basis=a.get("basis", "basis not retained in lineage"),
            )
            for a in stored.get("alternatives", [])
        ),
        limitations=tuple(stored.get("limitations", [])),
    )


def config_from_lineage(lineage: dict[str, Any]) -> EngineConfig:
    """Rebuild the engine config, and refuse if it has drifted.

    The stored fingerprint is compared against the current default config. A
    mismatch is raised rather than silently accepted, because recomputing under
    today's weights and reporting "identical" would be the exact false
    reassurance the guarantee is supposed to rule out.
    """
    stored = lineage.get("bundle", {})
    fingerprint = stored.get("config_fingerprint")
    current = EngineConfig()
    if fingerprint is not None and fingerprint != current.fingerprint():
        raise LineageIncomplete(
            "the stored engine config fingerprint does not match the current "
            "default configuration. Recomputing under today's weights would "
            "compare a verdict against different maths and could report "
            "'identical' for the wrong reason. Pin the historical config "
            "explicitly to recompute this verdict.\n"
            f"  stored : {fingerprint}\n"
            f"  current: {current.fingerprint()}"
        )
    return current
