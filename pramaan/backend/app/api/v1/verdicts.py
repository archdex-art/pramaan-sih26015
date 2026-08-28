"""Verdict read and recompute endpoints.

`POST /verdicts/{id}/recompute` is the demonstration behind docs §21.3. A judge
clicks it and the API re-runs the frozen engine over the verdict's own stored
lineage, then reports whether the digest matches. The claim "this output is
audit-defensible" stops being a slide and becomes a request.

## Why this is not a "re-verify" button

It recomputes the **decision**, not the **evidence**. No raster is read, no
model runs, nothing is re-measured. It answers exactly one question: *given the
inputs recorded at the time, does the engine still produce this verdict?* A
mismatch means the engine or its configuration changed — which is a finding
about the software, not about the claim.

Re-measuring evidence is a different operation with a different cost, and
conflating them would let a "recompute" quietly change a verdict a named officer
had already signed.

## Why a mismatch is 409 and not 500

A digest mismatch is not a server fault. It is the correct, useful answer when an
engine version has moved on: the stored verdict is no longer reproducible under
today's rules and must be re-adjudicated. 500 would imply the API is broken.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.db.verdicts import (
    StoredVerdict,
    VerdictNotFound,
    latest_verdict_id,
    load_verdict,
)
from app.services.audit import (
    LineageIncomplete,
    bundle_from_lineage,
    config_from_lineage,
    verdict_digest,
)
from app.services.reconcile import EngineConfig, Verdict, label_for, reconcile
from app.services.reconcile.types import Level

router = APIRouter(tags=["verdicts"])

# Annotated form rather than a `Depends` default: FastAPI supports both, but
# a call in a default argument is a real hazard everywhere else in Python, so
# the linter is right to forbid it and this keeps the rule intact.
DbSession = Annotated[Session, Depends(db_session)]

PROVISIONAL_NOTE = "PROVISIONAL until an authorised officer accepts, edits or rejects it."


class VerdictOut(BaseModel):
    """A stored verdict.

    `lineage` is deliberately excluded: it is large and it is an audit artefact,
    served by the recompute endpoint and the Evidence Pack rather than on every
    read.
    """

    id: int
    claim_id: int
    version: int
    level: str
    label: str = Field(
        description="Human-facing label. Derived, not stored: it depends on "
        "level AND score, so it is computed with the engine's own `label_for` "
        "rather than mapped from level here — an L4 verdict is CORROBORATED at "
        "score 1.0 and PARTIAL at 0.3."
    )
    rule_path: list[str]
    score: float
    confidence: float
    coverage: float
    quality: float | None
    data_sufficiency: float
    dissent: list[str] = Field(
        description="Counter-evidence. Never empty: a verdict without stated "
        "counter-evidence is not shippable (docs §16.2 STEP 11)."
    )
    recommended_action: dict[str, Any]
    engine_version: str
    weights: dict[str, float]
    status: str
    provisional: bool = Field(description="True until adjudicated. Reports must print PROVISIONAL.")
    note: str = PROVISIONAL_NOTE


class RecomputeOut(BaseModel):
    """The reproducibility proof, in the shape docs §21.3 specifies."""

    verdict_id: int
    hash_before: str | None
    hash_after: str
    identical: bool
    differences: list[str] = Field(
        description="Named fields that changed. Empty when identical. A digest "
        "mismatch is an alarm; naming the fields is a diagnosis."
    )
    engine_version_stored: str
    engine_version_current: str
    recomputed_level: str
    recomputed_confidence: float


def _to_out(v: StoredVerdict) -> VerdictOut:
    return VerdictOut(
        id=v.id,
        claim_id=v.claim_id,
        version=v.version,
        level=v.level,
        label=label_for(Level(v.level), v.score, EngineConfig()),
        rule_path=list(v.rule_path),
        score=v.score,
        confidence=v.confidence,
        coverage=v.coverage,
        quality=v.quality,
        data_sufficiency=v.data_sufficiency,
        dissent=list(v.dissent),
        recommended_action=v.recommended_action,
        engine_version=v.engine_version,
        weights=v.weights,
        status=v.status,
        provisional=v.status != "adjudicated",
    )


@router.get("/verdicts/{verdict_id}", response_model=VerdictOut)
def get_verdict(verdict_id: int, session: DbSession) -> VerdictOut:
    try:
        return _to_out(load_verdict(session, verdict_id))
    except VerdictNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/claims/{claim_id}/verdict", response_model=VerdictOut)
def get_latest_verdict(claim_id: int, session: DbSession) -> VerdictOut:
    """The current verdict for a claim. Earlier versions stay readable by id."""
    try:
        return _to_out(load_verdict(session, latest_verdict_id(session, claim_id)))
    except VerdictNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/verdicts/{verdict_id}/recompute", response_model=RecomputeOut)
def recompute_verdict(verdict_id: int, session: DbSession) -> RecomputeOut:
    """Re-run the engine over this verdict's stored lineage.

    Read-only. It never writes a new verdict: proving reproducibility must not
    itself mutate the record being proved.
    """
    try:
        stored = load_verdict(session, verdict_id)
    except VerdictNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    try:
        bundle = bundle_from_lineage(stored.lineage)
        cfg = config_from_lineage(stored.lineage)
    except LineageIncomplete as exc:
        # 422: the request is well-formed but this row cannot answer it. The
        # message says which part of the lineage is missing, because "cannot
        # recompute" without a reason is indistinguishable from a bug.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    recomputed = reconcile(bundle, cfg)
    after = verdict_digest(recomputed)
    identical = stored.verdict_digest is not None and after == stored.verdict_digest
    differences = [] if identical else _describe_differences(stored, recomputed)

    payload = RecomputeOut(
        verdict_id=verdict_id,
        hash_before=stored.verdict_digest,
        hash_after=after,
        identical=identical,
        differences=differences,
        engine_version_stored=stored.engine_version,
        engine_version_current=recomputed.engine_version,
        recomputed_level=recomputed.level.value,
        recomputed_confidence=recomputed.confidence,
    )
    if not identical:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=payload.model_dump(),
        )
    return payload


def _describe_differences(stored: StoredVerdict, recomputed: Verdict) -> list[str]:
    """Name what moved, comparing the stored row against the fresh verdict.

    Tolerance is 5e-4 because the stored columns are NUMERIC(5,4): a difference
    below half a stored digit is rounding, not drift.
    """
    out: list[str] = []
    if recomputed.level.value != stored.level:
        out.append(f"level: {stored.level} -> {recomputed.level.value}")
    for field, was in (
        ("score", stored.score),
        ("confidence", stored.confidence),
        ("coverage", stored.coverage),
    ):
        now = float(getattr(recomputed, field))
        if abs(now - was) > 5e-4:
            out.append(f"{field}: {was} -> {now}")
    if tuple(recomputed.rule_path) != stored.rule_path:
        out.append(f"rule_path: {list(stored.rule_path)} -> {list(recomputed.rule_path)}")
    if stored.verdict_digest is None:
        out.append("stored row predates digest storage (migration 0002)")
    return out
