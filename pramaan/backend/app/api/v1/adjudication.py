"""Adjudication and ledger endpoints — where a verdict stops being provisional.

`POST /api/v1/verdicts/{id}/adjudicate` is the only write in the system that
clears the word PROVISIONAL, and it is gated on `adjudication:create`, which no
field role and no administrator holds.

`GET /api/v1/ledger/verify` recomputes the whole hash chain on demand. It exists
so the integrity claim is checkable from the running system by anyone with the
capability, rather than asserted on a slide.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentPrincipal, CurrentScope, DbSession, require
from app.api.scope import require_verdict_visible
from app.core.authz import Capability
from app.services.audit.ledger import (
    LedgerError,
    append,
    read_chain,
    verify_chain,
)
from app.services.reconcile.types import Level

router = APIRouter(tags=["adjudication"])


class AdjudicateIn(BaseModel):
    decision: Literal["accept", "edit", "reject"]
    #: Required for `edit`, forbidden otherwise — the service enforces both.
    corrected_level: str | None = None
    #: Required for `edit` and `reject` by a database CHECK, not just by us.
    reason: str | None = Field(default=None, max_length=4000)


class AdjudicationOut(BaseModel):
    id: int
    verdict_id: int
    decision: str
    corrected_level: str | None
    reason: str | None
    decided_at: str
    #: Who signed. The whole point.
    signed_by_username: str
    signed_by_name: str
    prev_hash: str
    row_hash: str


class ChainOut(BaseModel):
    valid: bool
    rows: int
    broken_at: int | None
    reason: str | None
    statement: str


@router.post(
    "/verdicts/{verdict_id}/adjudicate",
    response_model=AdjudicationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Capability.ADJUDICATION_CREATE))],
)
def post_adjudicate(
    verdict_id: int,
    body: AdjudicateIn,
    session: DbSession,
    scope: CurrentScope,
    principal: CurrentPrincipal,
) -> AdjudicationOut:
    """Sign a verdict. Jurisdiction is checked before anything is written.

    The signer is taken from the token, never from the request body. An endpoint
    that accepts `user_id` from the caller lets any officer sign as any other,
    which would make the ledger's attribution decorative.
    """
    require_verdict_visible(session, scope, verdict_id)

    if body.corrected_level is not None:
        # Validate against the engine's own Level enum rather than trusting the
        # database cast to fail. A bad value here should be a 422 naming the
        # allowed set, not a 500 from a failed enum cast.
        try:
            Level(body.corrected_level)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"corrected_level must be one of: "
                f"{', '.join(sorted(str(lv.value) for lv in Level))}",
            ) from exc

    try:
        row = append(
            session,
            verdict_id=verdict_id,
            user_id=principal.user_id,
            decision=body.decision,
            corrected_level=body.corrected_level,
            reason=body.reason,
        )
    except LedgerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    return AdjudicationOut(
        id=row.id,
        verdict_id=row.verdict_id,
        decision=row.decision,
        corrected_level=row.corrected_level,
        reason=row.reason,
        decided_at=row.decided_at,
        signed_by_username=principal.username,
        signed_by_name=principal.full_name,
        prev_hash=row.prev_hash or "",
        row_hash=row.row_hash,
    )


@router.get(
    "/ledger/verify",
    response_model=ChainOut,
    dependencies=[Depends(require(Capability.LEDGER_VERIFY))],
)
def get_ledger_verify(session: DbSession) -> ChainOut:
    """Recompute every link in the adjudication chain.

    Deliberately not scoped by jurisdiction. The chain is global — a district
    filter would produce a subset whose links legitimately do not join, and the
    check would report tampering that had not happened. Integrity of the whole
    ledger is a different question from visibility of individual claims, and
    conflating them would break the one check an auditor most needs.
    """
    report = verify_chain(session)
    return ChainOut(
        valid=report.valid,
        rows=report.rows,
        broken_at=report.broken_at,
        reason=report.reason,
        statement=(
            "Each row is sha256 over its own content plus the previous row's "
            "hash. This proves integrity, not authenticity: it detects "
            "alteration of any historical row, and does not prove which officer "
            "physically pressed the key. Per-officer signing keys are not "
            "implemented."
        ),
    )


class LedgerEntryOut(BaseModel):
    id: int
    verdict_id: int
    decision: str
    corrected_level: str | None
    reason: str | None
    decided_at: str
    signed_by_username: str
    signed_by_name: str
    row_hash: str


@router.get(
    "/ledger",
    response_model=list[LedgerEntryOut],
    dependencies=[Depends(require(Capability.LEDGER_VERIFY))],
)
def get_ledger(session: DbSession) -> list[LedgerEntryOut]:
    """The signed record, in chain order."""
    return [
        LedgerEntryOut(
            id=r.id,
            verdict_id=r.verdict_id,
            decision=r.decision,
            corrected_level=r.corrected_level,
            reason=r.reason,
            decided_at=r.decided_at,
            signed_by_username=r.username,
            signed_by_name=r.full_name,
            row_hash=r.row_hash,
        )
        for r in read_chain(session)
    ]
