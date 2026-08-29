"""Authentication endpoints: login, refresh, logout, whoami.

The response bodies here are what the three workspaces are built from. `/me`
returns the principal *and its capability list*, so the frontend renders
controls from the same policy the server enforces rather than from a duplicated
copy of the rules. A UI that decides for itself what an officer may do will
eventually disagree with the API, and the disagreement always resolves as either
a dead button or a hidden one.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentPrincipal, DbSession
from app.core.authz import CAPABILITIES, WORKSPACE, Capability, Principal, Role
from app.services.auth.session import (
    AccountLocked,
    AuthFailed,
    login,
    logout,
    refresh_tokens,
)

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1)


class PrincipalOut(BaseModel):
    user_id: str
    username: str
    full_name: str
    role: str
    scope_state: str | None
    scope_district: str | None
    workspace: str
    #: The authorisation policy, served to the client. Single source of truth.
    capabilities: list[str]


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    principal: PrincipalOut


def _principal_out(principal: Principal) -> PrincipalOut:
    return PrincipalOut(
        user_id=principal.user_id,
        username=principal.username,
        full_name=principal.full_name,
        role=str(principal.role),
        scope_state=principal.scope_state,
        scope_district=principal.scope_district,
        workspace=str(principal.workspace),
        capabilities=sorted(str(c) for c in principal.capabilities),
    )


@router.post("/auth/login", response_model=TokenOut)
def post_login(body: LoginIn, session: DbSession) -> TokenOut:
    try:
        pair = login(session, body.username, body.password)
    except AccountLocked as exc:
        # 429 with Retry-After, not 401: the credentials may well be correct,
        # and the client needs to know waiting is the remedy.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except AuthFailed as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        principal=_principal_out(pair.principal),
    )


@router.post("/auth/refresh", response_model=TokenOut)
def post_refresh(body: RefreshIn, session: DbSession) -> TokenOut:
    try:
        pair = refresh_tokens(session, body.refresh_token)
    except AuthFailed as exc:
        # The message from a replay carries how many tokens were revoked. That
        # is deliberately surfaced: the honest thing to tell a user whose
        # session just died is that a reuse was detected.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        principal=_principal_out(pair.principal),
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def post_logout(body: RefreshIn, session: DbSession) -> Response:
    logout(session, body.refresh_token)
    # 204 whether or not anything was revoked. Reporting "that token was
    # already dead" tells a caller which tokens exist.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me", response_model=PrincipalOut)
def get_me(principal: CurrentPrincipal) -> PrincipalOut:
    return _principal_out(principal)


class RoleInfo(BaseModel):
    role: str
    workspace: str
    capabilities: list[str]


@router.get("/auth/roles", response_model=list[RoleInfo])
def get_roles() -> list[RoleInfo]:
    """The whole authorisation policy, unauthenticated.

    Publishing it is a deliberate choice. The policy is not a secret — the
    separation of duties it encodes is the *argument*, and a reviewer should be
    able to read it off the running system rather than take a slide's word for
    it. Notably: no role holds both `claim:create` and `adjudication:create`.
    """
    return [
        RoleInfo(
            role=str(role),
            workspace=str(WORKSPACE[role]),
            capabilities=sorted(str(c) for c in caps),
        )
        for role, caps in ((r, CAPABILITIES[r]) for r in Role)
    ]


class SeparationOut(BaseModel):
    """Machine-checkable statement of the separation-of-duties invariant."""

    holds: bool
    conflicting_roles: list[str]
    statement: str


@router.get("/auth/separation-of-duties", response_model=SeparationOut)
def get_separation() -> SeparationOut:
    """Assert, at runtime, that no role can both submit and adjudicate.

    This exists because the claim is load-bearing and a reviewer should not have
    to trust a document for it. It recomputes from `CAPABILITIES`, so it cannot
    drift from the policy the guards actually use.
    """
    conflicting = sorted(
        str(role)
        for role, caps in CAPABILITIES.items()
        if Capability.CLAIM_CREATE in caps and Capability.ADJUDICATION_CREATE in caps
    )
    return SeparationOut(
        holds=not conflicting,
        conflicting_roles=conflicting,
        statement=(
            "No role holds both claim:create and adjudication:create. The role "
            "that submits evidence cannot be the role that signs it, and "
            "dolr_admin holds neither."
        ),
    )
