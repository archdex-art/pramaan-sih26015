"""Jurisdiction enforcement for claim-bound endpoints.

docs §25.1: *"A WCDC user cannot construct a request that returns another
district's data."* That is a statement about every route that takes a
`claim_id`, and it holds only if none of them can forget the check. So there is
exactly one function that decides whether a claim is visible, and every such
route calls it before reading anything else.

## Why out-of-jurisdiction is 404 and not 403

403 says *"this exists and you may not have it"*. Iterating claim ids against a
403/404 boundary then maps the entire national register — how many claims
exist, and in which districts — without ever reading one. 404 says *"there is
no such claim, for you"*, which is the only answer that leaks nothing.

The tradeoff is a worse debugging experience for a misconfigured officer, and
that is the right trade: the audit log records the denial with the principal, so
the information exists where an administrator can see it and an attacker cannot.

## Why the register filters in SQL rather than after fetching

Filtering in Python means the rows crossed a process boundary before being
discarded, so a logging statement, an exception trace, or a future `len()` on
the unfiltered list becomes a leak. `register_clause` pushes the predicate into
the query, and `denies_everything` becomes `FALSE` rather than a missing
`WHERE`.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.authz import ScopeFilter

#: Appended to the register query's WHERE. Parameterised, never interpolated.
_CLAUSES = {
    "unrestricted": "TRUE",
    "district": "c.district_lgd = :scope_district",
    "denied": "FALSE",
}


def register_clause(scope: ScopeFilter) -> tuple[str, dict[str, str]]:
    """SQL fragment and bind parameters restricting a claims query.

    Returns `FALSE` — not an empty string — when the principal has no usable
    jurisdiction. An empty fragment would silently widen the query to every
    district, which is the failure this function exists to prevent.
    """
    if scope.denies_everything:
        return _CLAUSES["denied"], {}
    if scope.unrestricted:
        return _CLAUSES["unrestricted"], {}
    assert scope.district_lgd is not None  # guaranteed by district_predicate
    return _CLAUSES["district"], {"scope_district": scope.district_lgd}


_CLAIM_DISTRICT = text("SELECT district_lgd FROM claims WHERE id = :claim_id")


def require_claim_visible(session: Session, scope: ScopeFilter, claim_id: int) -> str | None:
    """404 unless this claim is inside the principal's jurisdiction.

    Returns the claim's district so a caller that needs it does not repeat the
    query. Raises for both "no such claim" and "not your claim", with the same
    status and the same body — see the module docstring.
    """
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, f"claim {claim_id} does not exist")

    if scope.denies_everything:
        raise not_found

    row = session.execute(_CLAIM_DISTRICT, {"claim_id": claim_id}).first()
    if row is None:
        raise not_found

    district = None if row[0] is None else str(row[0])
    if scope.unrestricted:
        return district

    if district != scope.district_lgd:
        raise not_found
    return district


_VERDICT_CLAIM = text("SELECT claim_id FROM verdicts WHERE id = :verdict_id")


def require_verdict_visible(session: Session, scope: ScopeFilter, verdict_id: int) -> int:
    """404 unless the verdict's claim is in jurisdiction. Returns the claim id.

    Verdicts are addressed by their own id, so the jurisdiction test has to
    traverse to the claim. Doing that traversal here rather than in each route
    is the point: a route that only knows a verdict id has no way to skip it.
    """
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, f"verdict {verdict_id} does not exist")

    if scope.denies_everything:
        raise not_found

    row = session.execute(_VERDICT_CLAIM, {"verdict_id": verdict_id}).first()
    if row is None:
        raise not_found

    claim_id = int(row[0])
    require_claim_visible(session, scope, claim_id)
    return claim_id
