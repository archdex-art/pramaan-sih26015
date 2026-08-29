"""Login, refresh rotation and reuse detection.

FR-1.1's acceptance criterion is precise: *"Valid login returns tokens; expired
access token is rejected; **reused refresh token revokes the family**."* The
third clause is the one that needs real machinery, and it is the reason this
module exists rather than a twenty-line login handler.

## The rotation invariant

Each refresh exchange consumes one token and issues exactly one successor in the
same family. Therefore:

    a jti presented twice  <=>  someone holds a token they should not

There is no benign second use. A client that legitimately refreshed already has
the successor; a client replaying the old token either lost the successor to a
crash or never had it because an attacker stole the token first. Both cases are
resolved the same way — kill the family and make everyone log in again — because
the server cannot distinguish them and the safe branch is the strict one.

## Why the whole family dies, not just the replayed token

Revoking only the replayed jti leaves the attacker's successor valid: they
replayed the *old* token, so they plainly also hold whatever came after it, or
they are racing the real client for it. Revoking the family is the only action
that reliably ends the session for both parties.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import CursorResult, RowMapping, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from app.core.authz import Principal, Role
from app.core.security import (
    TokenInvalid,
    decode,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    lockout_seconds,
    needs_rehash,
    verify_password,
)


class AuthFailed(Exception):
    """Login or refresh was refused.

    One exception type, and callers map it to a single 401 with one message.
    Distinguishing "no such user" from "wrong password" turns the login form
    into a username oracle, which is how attackers build target lists.
    """


class AccountLocked(Exception):
    """Too many failed attempts. Carries the retry delay, which is not secret."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"account locked; retry in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    principal: Principal


_USER_BY_NAME = text("""
SELECT id, username, full_name, password_hash, role::text AS role,
       scope_state, scope_district, is_active, failed_attempts, locked_until
FROM users WHERE username = :username
""")

_USER_BY_ID = text("""
SELECT id, username, full_name, role::text AS role,
       scope_state, scope_district, is_active
FROM users WHERE id = CAST(:user_id AS uuid)
""")


def _now() -> datetime:
    return datetime.now(UTC)


def _principal(row: RowMapping) -> Principal:
    return Principal(
        user_id=str(row["id"]),
        username=str(row["username"]),
        full_name=str(row["full_name"]),
        role=Role(str(row["role"])),
        scope_state=None if row["scope_state"] is None else str(row["scope_state"]),
        scope_district=(None if row["scope_district"] is None else str(row["scope_district"])),
    )


def _affected(session: Session, statement: TextClause, params: dict[str, Any]) -> int:
    """Run DML and return the row count.

    `Session.execute` is typed as returning `Result`, which has no `rowcount`;
    the object actually returned for DML is a `CursorResult`, which does. The
    narrowing lives here once rather than as an ignore comment at each call
    site.
    """
    result = session.execute(statement, params)
    assert isinstance(result, CursorResult)
    return int(result.rowcount or 0)


def _record_refresh(
    session: Session, jti: str, family: str, user_id: str, expires: datetime
) -> None:
    session.execute(
        text("""
        INSERT INTO refresh_tokens (jti, family, user_id, expires_at)
        VALUES (:jti, :family, CAST(:user_id AS uuid), :expires)
        """),
        {"jti": jti, "family": family, "user_id": user_id, "expires": expires},
    )


def _revoke_family(session: Session, family: str, reason: str) -> int:
    """Revoke every unrevoked token in a family. Returns how many were killed."""
    return _affected(
        session,
        text("""
        UPDATE refresh_tokens
           SET revoked_at = now(), revoked_reason = :reason
         WHERE family = :family AND revoked_at IS NULL
        """),
        {"family": family, "reason": reason},
    )


@lru_cache(maxsize=1)
def _absent_user_hash() -> str:
    """One Argon2id hash, computed once per process, for the absent-user path.

    It must be produced by `hash_password` rather than hard-coded so it always
    carries the same cost parameters as real stored hashes — a constant pasted
    here would stop matching the moment the parameters were tuned, and the
    verify against it would then take visibly less time than a real one.
    """
    return hash_password("absent-user-placeholder")


def login(session: Session, username: str, password: str) -> TokenPair:
    """Authenticate and start a new refresh family.

    ## Constant-time behaviour for unknown usernames

    A missing or deactivated account still costs one password verification, so
    that "no such user" and "wrong password" are indistinguishable by response
    *timing* as well as by message. Skipping the verify on the unknown-user path
    is a textbook enumeration oracle.

    The dummy hash is **precomputed once** (`_absent_user_hash`). An earlier
    version called `hash_password(...)` inline on every absent-user request,
    which made that path do two Argon2id operations against the real path's one
    — measured at a +23.7 ms median differential with non-overlapping ranges,
    i.e. the mitigation was itself the oracle, just inverted. One cached hash
    plus one verify matches the real path's single verify.
    """
    row = session.execute(_USER_BY_NAME, {"username": username}).mappings().first()

    if row is None or not row["is_active"]:
        # Exactly one verify, same parameters as a real one, then fail
        # identically to a bad password.
        verify_password(password, _absent_user_hash())
        raise AuthFailed("invalid username or password")

    locked_until = row["locked_until"]
    if locked_until is not None and locked_until > _now():
        raise AccountLocked(int((locked_until - _now()).total_seconds()) + 1)

    if not verify_password(password, str(row["password_hash"])):
        attempts = int(row["failed_attempts"]) + 1
        delay = lockout_seconds(attempts)
        session.execute(
            text("""
            UPDATE users
               SET failed_attempts = :attempts,
                   locked_until = CASE WHEN :delay > 0
                       THEN now() + make_interval(secs => :delay) ELSE NULL END
             WHERE id = :uid
            """),
            {"attempts": attempts, "delay": delay, "uid": row["id"]},
        )
        session.commit()
        if delay > 0:
            raise AccountLocked(delay)
        raise AuthFailed("invalid username or password")

    # Success: clear the counter, and upgrade the stored hash if the cost
    # parameters have moved since it was written.
    updates = {"uid": row["id"]}
    rehash_sql = ""
    if needs_rehash(str(row["password_hash"])):
        rehash_sql = ", password_hash = :new_hash"
        updates["new_hash"] = hash_password(password)
    session.execute(
        text(f"""
        UPDATE users
           SET failed_attempts = 0, locked_until = NULL, last_login_at = now()
               {rehash_sql}
         WHERE id = :uid
        """),
        updates,
    )

    principal = _principal(row)
    access, ttl = issue_access_token(principal)
    refresh, jti, family, expires = issue_refresh_token(principal.user_id)
    _record_refresh(session, jti, family, principal.user_id, expires)
    session.commit()
    return TokenPair(access, refresh, ttl, principal)


def refresh_tokens(session: Session, refresh_token: str) -> TokenPair:
    """Exchange a refresh token for a new pair, or detect replay and revoke.

    Order matters. The token's signature is checked first, then its presence in
    the store, then whether it has already been used. Checking the store before
    the signature would let an attacker probe for valid jti values with
    unsigned tokens.
    """
    try:
        claims = decode(refresh_token, "refresh")
    except TokenInvalid as exc:
        raise AuthFailed("invalid refresh token") from exc

    jti = str(claims["jti"])
    family = str(claims.get("family", ""))

    stored = (
        session.execute(
            text("""
        SELECT jti, family, user_id, used_at, revoked_at, expires_at
        FROM refresh_tokens WHERE jti = :jti
        FOR UPDATE
        """),
            {"jti": jti},
        )
        .mappings()
        .first()
    )

    # A correctly signed token with no row is a token issued by a keypair we
    # still trust against a store that has been reset, or a forgery with a
    # leaked key. Neither is refreshable.
    if stored is None:
        if family:
            _revoke_family(session, family, "unknown jti presented")
            session.commit()
        raise AuthFailed("invalid refresh token")

    if stored["revoked_at"] is not None:
        raise AuthFailed("invalid refresh token")

    if stored["used_at"] is not None:
        # Replay. Kill the family — see the module docstring for why the whole
        # family and not just this token.
        killed = _revoke_family(session, str(stored["family"]), "refresh token reuse detected")
        session.commit()
        raise AuthFailed(f"refresh token reuse detected; {killed} session token(s) revoked")

    if stored["expires_at"] <= _now():
        raise AuthFailed("invalid refresh token")

    row = session.execute(_USER_BY_ID, {"user_id": str(stored["user_id"])}).mappings().first()
    if row is None or not row["is_active"]:
        # Deactivating a user must end their sessions, not merely stop new
        # logins.
        _revoke_family(session, str(stored["family"]), "user deactivated")
        session.commit()
        raise AuthFailed("invalid refresh token")

    session.execute(
        text("UPDATE refresh_tokens SET used_at = now() WHERE jti = :jti"),
        {"jti": jti},
    )

    principal = _principal(row)
    access, ttl = issue_access_token(principal)
    new_refresh, new_jti, _, expires = issue_refresh_token(
        principal.user_id, family=str(stored["family"])
    )
    _record_refresh(session, new_jti, str(stored["family"]), principal.user_id, expires)
    session.commit()
    return TokenPair(access, new_refresh, ttl, principal)


def logout(session: Session, refresh_token: str) -> int:
    """Revoke the presented token's family. Returns tokens revoked.

    Signature-checked but tolerant of an already-invalid token: logging out is
    the one operation that must never fail in a way that leaves a session
    alive. An unparseable token has no family to revoke, and reporting zero is
    the honest answer.
    """
    try:
        claims = decode(refresh_token, "refresh")
    except TokenInvalid:
        return 0
    family = str(claims.get("family", ""))
    if not family:
        return 0
    killed = _revoke_family(session, family, "logout")
    session.commit()
    return killed


def prune_expired(session: Session) -> int:
    """Delete refresh rows that can no longer authorise anything.

    Revoked rows are kept: they are the audit trail of a detected replay, and
    deleting them would erase the evidence of the incident they record.
    """
    killed = _affected(
        session,
        text("DELETE FROM refresh_tokens WHERE expires_at < now() AND revoked_at IS NULL"),
        {},
    )
    session.commit()
    return killed
