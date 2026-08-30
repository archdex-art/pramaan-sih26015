"""The activity trail — who did what, when, from where.

This is the *second* audit record in the system and it is deliberately weaker
than the first. `ledger.py` hash-chains adjudications: it is tamper-evident,
append-only at the grant level (migration 0004 revokes UPDATE and DELETE on
`adjudications` from `pramaan_app`), and it is the authoritative record of a
signature. This table is a plain chronological log of *everything else* —
logins, failed logins, refresh replays, recomputes — with no chaining and no
integrity claim beyond "the database row says so".

Keeping them separate is the point. Folding logins into the hash chain would
mean a failed login could break, or be used to grow, the chain that attests to
government evidence. Copying the chain's guarantees onto this table would be an
overclaim: nothing here is verifiable offline and this module does not pretend
otherwise.

## `audit_log` was dead until this module

Migration 0001 created the table, RANGE-partitioned by month, with an ops note
about a rotation cron. Nothing in the codebase ever inserted a row. A partition
plan for an empty table is not a feature, and an "audit trail" screen reading an
empty table would have been the exact kind of decoration this project refuses.

## The partition key is `at`, and DEFAULT is a signal

Live partitions are `audit_log_2026_01..03` plus `audit_log_default`. Any write
dated outside January–March 2026 — which is every write made after 2026-03-31,
i.e. all of them today — routes to `audit_log_default`. That is **not an error**
and must never be treated as one: the DEFAULT partition exists precisely so a
late maintenance job cannot take logins down. It *is* a signal that
`scripts/rotate_audit_partitions.py` is overdue, and the honest place to see it
is a non-empty `audit_log_default`, which is why this module does not paper over
it by widening the range.

This module never issues DDL. Creating a partition from a request handler means
an ACCESS EXCLUSIVE lock on `audit_log` taken during a login, serialising every
concurrent request behind it, and doing it from a role that should not hold DDL
rights in the first place. Partition maintenance is an ops job.

## Best-effort, not transactional

`record` commits its own row and swallows — after logging — any failure. The
alternative, enlisting in the caller's transaction, was rejected:

* `login`, `refresh_tokens` and `ledger.append` all commit *before* the router
  gets control, so by the time a hook runs there is nothing left to join. An
  audit row could only be written in a fresh transaction anyway.
* Given that, the only question is whether an audit failure should surface as a
  500. It must not. A full disk in the audit partition would then log an officer
  out of a system whose actual work — the signature, already committed and
  hash-chained — succeeded. The operation the trail describes is more important
  than the trail.
* For adjudication specifically this is unambiguously right: `adjudications` is
  the authoritative record and it is chained. A missing `audit_log` row loses a
  convenience index over an event that is already provable.

The cost is admitted: an attacker who can reliably make this insert fail can
suppress trail rows. They cannot suppress the ledger, and they cannot suppress
the log line this module emits on the way past.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

_log = logging.getLogger(__name__)


class Action(StrEnum):
    """The closed vocabulary of trail actions.

    Frozen and dotted on purpose. `action` is a free-text column, and free text
    in a filterable column drifts — `login`, `LOGIN`, `user_login` and
    `auth.login` all appear within a month of the second developer, and then the
    admin console's `?action=` filter quietly returns a subset of the truth
    while looking like it returned all of it. An enum makes the drift a mypy
    error instead.

    Failure and rejection get their own values rather than a `success: false`
    field in the payload, because "show me every failed login" must be an index
    scan on one column, not a JSONB predicate over the whole table.

    The values are deliberately **not** the `Capability` strings
    (`claim:create`, `verdict:recompute`, ...). A capability is a permission
    someone holds; an action is a thing that happened. Spelling them the same
    way makes a grep for one return the other, and invites the assumption that
    every capability has a matching action, which is false in both directions:
    reads are gated and not logged, and a failed login is logged and gated by
    nothing.

    `VERDICT_RECOMPUTED` currently has no producer — the recompute endpoint
    lives in `api/v1/verdicts.py` and has not been hooked. The value is defined
    so the hook cannot invent a spelling; until it exists, filtering on it
    honestly returns nothing.
    """

    LOGIN_SUCCEEDED = "auth.login.succeeded"
    LOGIN_FAILED = "auth.login.failed"
    LOGIN_LOCKED = "auth.login.locked"
    LOGOUT = "auth.logout"
    TOKEN_REFRESHED = "auth.token.refreshed"
    TOKEN_REFRESH_REJECTED = "auth.token.rejected"
    CLAIM_CAPTURED = "claim.captured"
    CLAIM_REJECTED = "claim.rejected"
    ADJUDICATION_SIGNED = "adjudication.signed"
    VERDICT_RECOMPUTED = "verdict.recomputed"


#: Payload keys whose values are never written, whatever a caller passes.
#:
#: The call sites in this repository do not pass secrets — the auth hooks record
#: the attempted *username* and nothing else. This exists because that is a
#: property of five call sites today and of an unknown number later, and the
#: failure mode is a password sitting in a table an auditor is encouraged to
#: read. Redaction rather than rejection: refusing the write would turn a
#: careless payload into a lost audit row.
_REDACTED_KEYS = frozenset(
    {
        "password",
        "passwd",
        "password_hash",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
    }
)

_REDACTED = "[redacted]"

_INSERT = text("""
INSERT INTO audit_log (user_id, action, entity, entity_id, ip, user_agent, payload)
VALUES (CAST(:user_id AS uuid), :action, :entity, :entity_id,
        CAST(:ip AS inet), :user_agent, CAST(:payload AS jsonb))
""")


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Blank the values of known-secret keys. Shallow by design: a nested secret
    would need a recursive walk over caller-controlled data on the login path,
    and every payload built in this repository is flat."""
    return {k: (_REDACTED if k.lower() in _REDACTED_KEYS else v) for k, v in payload.items()}


def client_ip(host: str | None) -> str | None:
    """Validate a client address for the `inet` column, or give up honestly.

    Postgres rejects a malformed `inet`, and an exception raised while casting
    would be caught by `record` and lose the whole row. Anything unparseable —
    Starlette's `TestClient` reports the literal `"testclient"`, a Unix-socket
    connection reports no host at all — is stored as NULL rather than coerced
    into a plausible address. A NULL says "not recorded"; `0.0.0.0` would be a
    fabricated fact in an audit table.

    The caller is responsible for passing `request.client.host`. Behind a
    reverse proxy that is the proxy's address, not the user's: `X-Forwarded-For`
    is trivially spoofable by anyone who can reach the app directly, so it is
    not consulted, and this column means "the peer we actually accepted a socket
    from" until a trusted-proxy configuration exists to make it mean more.
    """
    if host is None:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def record(
    session: Session,
    *,
    action: Action,
    user_id: str | None = None,
    entity: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Write one trail row. Never raises; see the module docstring.

    `user_id` is NULL for events with no authenticated actor — a failed login
    has an attempted username, not a user — and readers must render those as
    system/anonymous events rather than inventing an actor for them.
    """
    try:
        session.execute(
            _INSERT,
            {
                "user_id": user_id,
                "action": str(action),
                "entity": entity,
                "entity_id": entity_id,
                "ip": ip,
                "user_agent": user_agent,
                # `json.dumps` + an explicit cast, matching `app/db/verdicts.py`:
                # one code path for every JSONB column in the codebase.
                "payload": json.dumps(_redact(payload or {})),
            },
        )
        session.commit()
    except SQLAlchemyError:
        # Roll back so the session is usable again — the caller's own work is
        # already committed, so this discards nothing but the failed insert.
        # `exception` and not `warning`: a broken audit trail is an operational
        # defect that must reach the log with a traceback, even though it is
        # deliberately not reaching the user.
        session.rollback()
        _log.exception("audit trail write failed: action=%s entity=%s", action, entity)
