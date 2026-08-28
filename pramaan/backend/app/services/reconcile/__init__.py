"""PRAMAAN reconciliation engine — pure, deterministic, IO-free.

Public API. Nothing outside this package should import submodules directly.
"""

from app.services.reconcile.engine import aggregate_evidence, label_for, reconcile
from app.services.reconcile.signatures import SIGNATURES, Signature, signature_for
from app.services.reconcile.types import (
    FAMILIES,
    INDEPENDENT_FAMILIES,
    Action,
    Aggregate,
    Alternative,
    EvidenceBundle,
    Family,
    FamilyEvidence,
    Gates,
    Label,
    Level,
    Quality,
    Verdict,
)
from app.services.reconcile.weights import DEFAULT_WEIGHTS, ENGINE_VERSION, EngineConfig

__all__ = [
    "DEFAULT_WEIGHTS",
    "ENGINE_VERSION",
    "FAMILIES",
    "INDEPENDENT_FAMILIES",
    "SIGNATURES",
    "Action",
    "Aggregate",
    "Alternative",
    "EngineConfig",
    "EvidenceBundle",
    "Family",
    "FamilyEvidence",
    "Gates",
    "Label",
    "Level",
    "Quality",
    "Signature",
    "Verdict",
    "aggregate_evidence",
    "label_for",
    "reconcile",
    "signature_for",
]
