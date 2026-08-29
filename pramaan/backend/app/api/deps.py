"""Request-time authentication and authorisation dependencies.

One place converts a bearer token into a `Principal`, and one place converts a
required `Capability` into a 403. Routers declare what they need and never
inspect a role, so adding a seventh role changes `authz.CAPABILITIES` and
nothing else.

## Why `require` returns a dependency rather than taking the principal

    @router.post(..., dependencies=[Depends(require(Capability.ADJUDICATION_CREATE))])

reads as a statement about the endpoint and is enforced before the handler body
runs. A check inside the handler is enforced only if the author remembers to
write it, and the failure mode of forgetting is a silently unprotected route.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.authz import (
    Capability,
    Principal,
    ScopeFilter,
    district_predicate,
)
from app.core.security import TokenInvalid, decode, principal_from_claims
from app.db.session import db_session

DbSession = Annotated[Session, Depends(db_session)]

#: WWW-Authenticate on every 401, so a client can tell "authenticate" from
#: "you are authenticated and still not allowed".
_UNAUTHENTICATED = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    "authentication required",
    headers={"WWW-Authenticate": "Bearer"},
)


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _UNAUTHENTICATED
    return token.strip()


def current_principal(request: Request) -> Principal:
    """Decode the access token, or 401.

    Any token problem — absent, malformed, expired, wrong type, unknown role —
    produces the same 401 with the same body. The distinctions are useful to an
    attacker and to nobody else.
    """
    try:
        return principal_from_claims(decode(_bearer(request), "access"))
    except TokenInvalid as exc:
        raise _UNAUTHENTICATED from exc


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require(*capabilities: Capability) -> Callable[[Principal], Principal]:
    """Build a dependency asserting the principal holds every capability given.

    Conjunctive on purpose. An endpoint that would be satisfied by any one of
    several capabilities is an endpoint doing more than one thing, and should be
    two endpoints.
    """

    def guard(principal: CurrentPrincipal) -> Principal:
        missing = [c for c in capabilities if not principal.can(c)]
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                # Naming the capability is safe and saves an integrator an hour.
                # It reveals the policy, which is published in the API docs
                # anyway, not the data.
                f"role '{principal.role}' lacks {', '.join(sorted(missing))}",
            )
        return principal

    return guard


def current_scope(principal: CurrentPrincipal) -> ScopeFilter:
    """The caller's jurisdiction, resolved once per request.

    Handlers take this instead of deriving it, so no handler can accidentally
    skip it. See `authz.district_predicate` for why the no-jurisdiction case
    denies everything rather than allowing everything.
    """
    return district_predicate(principal)


CurrentScope = Annotated[ScopeFilter, Depends(current_scope)]
