"""The activity trail (design doc S2) — the administration console's chronology.

One read over `audit_log`, newest first. Everything on it was written by
`app/services/audit/trail.py` at the moment the thing happened; nothing here
derives, summarises or backfills. An empty response means nothing has happened
since the table started being written to, and the screen must say that rather
than fill itself in.

## What this is *not*

It is not the ledger. `GET /api/v1/ledger` and `GET /api/v1/ledger/verify` serve
a hash-chained, tamper-evident record of adjudications; this serves a plain log
of activity with no integrity claim. The distinction matters because they will
sit near each other in the same workspace, and a reader who mistakes the trail
for the chain will over-trust it. The trail carries `row_hash` in the payload of
an `adjudication.signed` entry precisely so that anyone who wants the strong
guarantee can follow it to the record that offers one.

## Gating: `ledger:verify`, and why not `user:manage`

`ledger:verify` is the "may inspect the integrity of the record" capability, and
its holders are exactly the people whose job is oversight: `wcdc`, `slna`,
`readonly` (the CAG auditor account) and `dolr_admin`. The field roles `wdt` and
`pia` do not hold it, which is the intended exclusion — an officer who submits
evidence should not be able to watch who has been reviewing it.

`user:manage` was considered and rejected: only `dolr_admin` holds it, and a
trail that only the administrator can read is not oversight, it is a private
log. The external auditor is the reader this screen exists for.

## No jurisdiction scoping, deliberately

`audit_log` has no district column and most of its rows — logins, refreshes,
lockouts — have no district to scope by. Attaching one would mean inventing an
attribution for events that genuinely have none. Scoping is therefore not
attempted rather than half-applied, and the capability gate above is the whole
access control. A district officer holding `ledger:verify` can see that an
officer elsewhere signed something; they cannot see what it said, because the
verdict itself is still scoped by `api/scope.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DbSession, require
from app.core.authz import Capability

router = APIRouter(tags=["audit"])

# LEFT JOIN, not JOIN, for two independent reasons and both are load-bearing:
#
#  1. A system-generated event has no `user_id` at all. A failed login is the
#     common one — `services/auth/session.login` cannot say whether the account
#     exists without becoming a username oracle, so the attempted name lives in
#     the payload and the actor column is NULL. An inner join would silently
#     delete exactly the rows a security reviewer opens this screen to find.
#  2. If a user row is ever removed, an inner join would erase their history.
#     Deleting a person must not delete what they did.
#
# So `username`/`full_name`/`role` are nullable all the way to the wire, and the
# client renders a null triple as a system event. It must not substitute
# "unknown user": that reads as a lookup failure — a bug — when in fact the
# absence is the recorded truth.
#
# `COALESCE(payload, '{}')` because the column is nullable in migration 0001 and
# the S2 contract promises an object. An empty object is not a fabricated value:
# it is the accurate statement that this action carried no detail.
#
# `ORDER BY at DESC, id DESC`: `at` alone is not a total order. Two rows written
# in the same transaction can share a timestamp to the microsecond, and an
# unstable order across pages would make a trail appear to reshuffle itself. The
# identity column breaks the tie in insertion order.
#
# The `:action IS NULL OR` form keeps one statement and one plan for all four
# filter combinations instead of concatenating predicates in Python, which is
# how a filter argument eventually reaches the SQL string.
#
# The `CAST(... AS text)` is not decoration. psycopg sends parameters untyped,
# and `$1 IS NULL` on its own gives Postgres nothing to infer from, so the
# statement fails to prepare with `AmbiguousParameter` — at request time, in
# the branch where the filter is used. The cast tells the planner what the
# parameter is.
_TRAIL = text("""
SELECT a.id,
       a.at,
       a.action,
       a.entity,
       a.entity_id,
       COALESCE(a.payload, '{}'::jsonb) AS payload,
       u.username,
       u.full_name,
       u.role::text AS role
  FROM audit_log a
  LEFT JOIN users u ON u.id = a.user_id
 WHERE (CAST(:action AS text) IS NULL OR a.action = CAST(:action AS text))
   AND (CAST(:entity AS text) IS NULL OR a.entity = CAST(:entity AS text))
 ORDER BY a.at DESC, a.id DESC
 LIMIT :limit
""")


class AuditEvent(BaseModel):
    """One row of the trail.

    The actor fields are a triple that is either wholly present or wholly
    absent. They are joined at read time rather than denormalised into the row
    at write time so that a renamed officer's past entries show the name the
    directory holds now — the trail records *who*, and `users` is the authority
    on what that person is called.
    """

    id: int
    at: str
    username: str | None
    full_name: str | None
    role: str | None
    action: str
    entity: str | None
    entity_id: str | None
    payload: dict[str, Any]


@router.get(
    "/audit",
    response_model=list[AuditEvent],
    dependencies=[Depends(require(Capability.LEDGER_VERIFY))],
)
def list_audit_events(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=500, description="newest N events")] = 100,
    action: Annotated[str | None, Query(description="exact action, e.g. auth.login.failed")] = None,
    entity: Annotated[str | None, Query(description="exact entity, e.g. verdict")] = None,
) -> list[AuditEvent]:
    """The newest `limit` trail entries, most recent first.

    `limit` is capped at 500 by the query constraint. The cap is not politeness:
    `audit_log` is the one table designed to grow without bound, and an
    unbounded read of it is a way to take the API down from a browser.

    Unknown `action` or `entity` values are not rejected. The vocabulary is
    closed on the *write* side (`trail.Action`), so a filter naming something
    outside it can only match nothing, and returning an empty list is the
    truthful answer to "show me events of a kind that never occurred". A 422
    would instead claim the value is invalid, which is a different statement.
    """
    rows = session.execute(_TRAIL, {"limit": limit, "action": action, "entity": entity}).mappings()
    return [
        AuditEvent(
            id=int(r["id"]),
            # Serialised here rather than left to Pydantic: the column is
            # TIMESTAMPTZ and the S2 contract says `at` is a string, so the
            # offset is spelled out in the payload instead of depending on the
            # client's parser to preserve it.
            at=_iso(r["at"]),
            username=r["username"],
            full_name=r["full_name"],
            role=r["role"],
            action=r["action"],
            entity=r["entity"],
            entity_id=r["entity_id"],
            payload=dict(r["payload"]),
        )
        for r in rows
    ]


def _iso(value: datetime) -> str:
    """`at` is NOT NULL in migration 0001, so no None branch exists to handle."""
    return value.isoformat()
