"""Authentication endpoints: login, refresh, logout, whoami.

The response bodies here are what the three workspaces are built from. `/me`
returns the principal *and its capability list*, so the frontend renders
controls from the same policy the server enforces rather than from a duplicated
copy of the rules. A UI that decides for itself what an officer may do will
eventually disagree with the API, and the disagreement always resolves as either
a dead button or a hidden one.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentPrincipal, DbSession
from app.core.authz import CAPABILITIES, WORKSPACE, Capability, Principal, Role
from app.core.security import TokenInvalid, decode
from app.services.audit.trail import Action, client_ip, record
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


def _origin(request: Request) -> tuple[str | None, str | None]:
    """Where the request came from, as the socket and the client report it.

    `request.client` is None for a connection with no peer address (ASGI does
    not guarantee one), so the None is threaded through rather than assumed
    away. See `trail.client_ip` for why `X-Forwarded-For` is ignored.
    """
    return (
        client_ip(request.client.host if request.client else None),
        request.headers.get("user-agent"),
    )


def _refresh_subject(token: str) -> str | None:
    """The `sub` of a refresh token, or None if it will not decode.

    Logout and replay both arrive with only a token, and attributing those
    events to an account is most of their audit value. Decoding here does not
    authorise anything — `logout`/`refresh_tokens` have already made the real
    decision — so a token that fails verification simply yields an
    unattributed row instead of an error.
    """
    try:
        return str(decode(token, "refresh")["sub"])
    except (TokenInvalid, KeyError):
        return None


@router.post("/auth/login", response_model=TokenOut)
def post_login(body: LoginIn, session: DbSession, request: Request) -> TokenOut:
    ip, agent = _origin(request)
    try:
        pair = login(session, body.username, body.password)
    except AccountLocked as exc:
        # A lockout is recorded separately from a rejected password: it is the
        # tail of a burst, and an auditor filtering for it wants the burst, not
        # every attempt in it.
        record(
            session,
            action=Action.LOGIN_LOCKED,
            entity="user",
            payload={"username": body.username, "retry_after_s": exc.retry_after_seconds},
            ip=ip,
            user_agent=agent,
        )
        # 429 with Retry-After, not 401: the credentials may well be correct,
        # and the client needs to know waiting is the remedy.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except AuthFailed as exc:
        # `user_id` stays NULL and the attempted username goes in the payload.
        # `login` deliberately cannot say whether the account exists (that is
        # its enumeration defence), so writing a user_id here would require
        # a second lookup that reintroduces the oracle — and attributing a
        # stranger's guess to a real officer's row is the wrong record anyway.
        # The password is never passed; `trail` redacts it too, belt and braces.
        record(
            session,
            action=Action.LOGIN_FAILED,
            entity="user",
            payload={"username": body.username},
            ip=ip,
            user_agent=agent,
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    record(
        session,
        action=Action.LOGIN_SUCCEEDED,
        user_id=pair.principal.user_id,
        entity="user",
        entity_id=pair.principal.user_id,
        payload={"username": pair.principal.username, "role": str(pair.principal.role)},
        ip=ip,
        user_agent=agent,
    )
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        principal=_principal_out(pair.principal),
    )


@router.post("/auth/refresh", response_model=TokenOut)
def post_refresh(body: RefreshIn, session: DbSession, request: Request) -> TokenOut:
    ip, agent = _origin(request)
    try:
        pair = refresh_tokens(session, body.refresh_token)
    except AuthFailed as exc:
        # Replay detection is the one auth event that is unambiguously a
        # security finding rather than a user mistake, so it gets its own
        # action and carries the revocation message verbatim.
        record(
            session,
            action=Action.TOKEN_REFRESH_REJECTED,
            user_id=_refresh_subject(body.refresh_token),
            entity="refresh_token",
            payload={"reason": str(exc)},
            ip=ip,
            user_agent=agent,
        )
        # The message from a replay carries how many tokens were revoked. That
        # is deliberately surfaced: the honest thing to tell a user whose
        # session just died is that a reuse was detected.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    record(
        session,
        action=Action.TOKEN_REFRESHED,
        user_id=pair.principal.user_id,
        entity="user",
        entity_id=pair.principal.user_id,
        payload={"username": pair.principal.username},
        ip=ip,
        user_agent=agent,
    )
    return TokenOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        principal=_principal_out(pair.principal),
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def post_logout(body: RefreshIn, session: DbSession, request: Request) -> Response:
    ip, agent = _origin(request)
    revoked = logout(session, body.refresh_token)
    subject = _refresh_subject(body.refresh_token)
    record(
        session,
        action=Action.LOGOUT,
        user_id=subject,
        entity="user",
        entity_id=subject,
        # `revoked` distinguishes a real sign-out from a repeat call on a token
        # that was already dead; the response cannot say so (see below) but the
        # trail can, because the trail is not shown to the caller.
        payload={"tokens_revoked": revoked},
        ip=ip,
        user_agent=agent,
    )
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
