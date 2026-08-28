"""Audit layer: attestation over engine output.

Separate from `reconcile` because the engine's import surface is deliberately
minimal and asserted by a test. The engine decides; this layer attests.
"""

from app.services.audit.persistence import (
    LineageIncomplete,
    bundle_from_lineage,
    config_from_lineage,
    evidence_rows,
    verdict_row,
    wire_payload,
)
from app.services.audit.reproducibility import (
    DIGEST_VERSION,
    RecomputeResult,
    bundle_digest,
    bundle_payload,
    canonical_json,
    compare_verdicts,
    verdict_digest,
    verdict_payload,
)

__all__ = [
    "DIGEST_VERSION",
    "LineageIncomplete",
    "bundle_from_lineage",
    "config_from_lineage",
    "evidence_rows",
    "verdict_row",
    "wire_payload",
    "RecomputeResult",
    "bundle_digest",
    "bundle_payload",
    "canonical_json",
    "compare_verdicts",
    "verdict_digest",
    "verdict_payload",
]
