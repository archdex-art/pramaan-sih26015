"""The monitoring officer's work queue — what to look at, in what order, and why.

Every other read surface in the API answers "what does the system think about
this claim". This one answers the question an officer actually opens the app
with: "of the several hundred claims in my district, which one do I touch
first". The answer has to be a single ordered list, because a dashboard of
counts is a thing to interpret rather than a thing to act on.

## Why nothing is read from the `alerts` table

The queue is derived at query time from the latest verdict per claim. The
alternative — materialising rows into `alerts` and serving those — was rejected
because it introduces a second truth: a re-run of the reconciliation engine, or a
human adjudication, would change the verdict without changing the stored alert,
and the officer's queue would keep pointing at a finding that no longer exists.
Deriving costs one join over a district's claims and can never be stale.

## Why the ranking is not in this file

`app.services.alerts.priority` is pure and has no session. That boundary exists
so the ordering officers will dispute can be reproduced in a test from a list of
dicts, and so no future endpoint can invent a second ordering by writing its own
`ORDER BY`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import CurrentScope, require
from app.api.scope import register_clause
from app.core.authz import Capability
from app.db.session import db_session
from app.services.alerts.priority import ALERT_LEVELS, SEVERITY, Alert, rank
from app.services.reconcile import EngineConfig, label_for
from app.services.reconcile.types import Level

router = APIRouter(tags=["alerts"])

DbSession = Annotated[Session, Depends(db_session)]

# `DISTINCT ON (c.id) ... ORDER BY c.id, v.version DESC` is the codebase's
# established way of taking the newest verdict per claim (see `v1.claims`), and
# it is kept here rather than swapped for `row_number()` so both register queries
# read the same and one plan change fixes both. An older version must never
# appear in the queue: a claim adjudicated up to L3 would otherwise keep
# surfacing under whatever it used to be.
#
# A template, not a `text()`: `{scope}` is filled from `scope.register_clause`,
# which returns one of three fixed fragments and never caller input. The district
# value itself stays a bind parameter, so it never reaches the SQL string.
#
# `:alert_levels` is bound from `priority.ALERT_LEVELS`, so corroborated verdicts
# are discarded in the database instead of being fetched and then filtered in
# Python. `rank` drops them again; that is not redundancy for its own sake, it is
# the pure function refusing to trust its caller's WHERE clause.
#
# `adjudicated` is `EXISTS` against `adjudications` rather than
# `v.status = 'adjudicated'`. The two agree today, but the former is direct
# evidence that a human signed something and the latter is a denormalised flag
# that a future writer could forget to set — and the failure mode of that
# staleness is unsigned work hidden at the bottom of the queue.
#
# No `COALESCE` on the numeric columns: `confidence`, `data_sufficiency`, `score`
# and `recommended_action` are all NOT NULL in the migration, so defaulting them
# here would only mask a schema change behind a plausible-looking zero.
_QUEUE_SQL = """
SELECT DISTINCT ON (c.id)
       c.id                 AS claim_id,
       v.id                 AS verdict_id,
       i.unique_id          AS unique_id,
       i.type::text         AS intervention_type,
       c.district_lgd       AS district_lgd,
       v.level::text        AS level,
       v.score              AS score,
       v.confidence         AS confidence,
       v.data_sufficiency   AS data_sufficiency,
       v.recommended_action ->> 'action' AS recommended_action,
       EXISTS (SELECT 1 FROM adjudications a WHERE a.verdict_id = v.id) AS adjudicated
FROM claims c
JOIN interventions i ON i.id = c.intervention_id
JOIN verdicts v ON v.claim_id = c.id
WHERE {scope}
  AND v.level::text = ANY(:alert_levels)
ORDER BY c.id, v.version DESC
"""


class AlertOut(BaseModel):
    """Wire form of `priority.Alert`. Mirrored field for field rather than
    serialising the dataclass directly, so a rename inside the service is a
    compile-time problem here instead of a silent API break.
    """

    claim_id: int
    verdict_id: int
    unique_id: str
    intervention_type: str
    district_lgd: str
    level: str
    label: str
    confidence: float
    data_sufficiency: float
    priority: int
    reason: str
    recommended_action: str
    adjudicated: bool


class AlertSummary(BaseModel):
    """Counts for the district header strip.

    `by_level` carries every alert level, including the ones at zero. An empty
    dict would be ambiguous between "no contradicted claims" and "contradicted
    claims were not counted", and a header that omits a band is read as the band
    not existing.
    """

    by_level: dict[str, int]
    total: int
    unadjudicated: int
    #: `None` — not an empty string and not a reassuring sentence — when the queue
    #: is empty. The client decides how to say "nothing to do"; inventing that
    #: text here would put a claim in the API that the data does not support.
    highest_priority_reason: str | None


def _queue(session: Session, scope_clause: str, params: dict[str, str]) -> list[Alert]:
    """The full ranked queue for the caller's jurisdiction.

    Shared by both endpoints so the summary can never disagree with the list it
    is summarising — the alternative, a second aggregate query, would drift the
    moment either ranking or scope changed.
    """
    rows = session.execute(
        text(_QUEUE_SQL.format(scope=scope_clause)),
        {**params, "alert_levels": list(ALERT_LEVELS)},
    ).mappings()

    cfg = EngineConfig()
    # `label` is not stored: it is a presentation of (level, score) and is derived
    # by the engine's own `label_for` so the queue cannot show a label the verdict
    # detail page would contradict.
    enriched: list[dict[str, Any]] = [
        {**row, "label": label_for(Level(row["level"]), float(row["score"]), cfg)} for row in rows
    ]
    return rank(enriched)


@router.get(
    "/alerts",
    response_model=list[AlertOut],
    dependencies=[Depends(require(Capability.VERDICT_READ))],
)
def list_alerts(
    session: DbSession,
    scope: CurrentScope,
    limit: Annotated[int, Query(ge=1, le=500, description="top N by priority")] = 100,
) -> list[AlertOut]:
    clause, params = register_clause(scope)
    # Truncation happens *after* ranking, so a limit returns the N most urgent
    # entries rather than an arbitrary N that the database happened to emit first.
    # `asdict`, not `vars`: `Alert` uses `slots=True` and therefore has no
    # `__dict__` to read.
    return [AlertOut(**asdict(alert)) for alert in _queue(session, clause, params)[:limit]]


@router.get(
    "/alerts/summary",
    response_model=AlertSummary,
    dependencies=[Depends(require(Capability.VERDICT_READ))],
)
def alert_summary(session: DbSession, scope: CurrentScope) -> AlertSummary:
    clause, params = register_clause(scope)
    queue = _queue(session, clause, params)

    by_level = dict.fromkeys(SEVERITY, 0)
    for alert in queue:
        by_level[alert.level] += 1

    return AlertSummary(
        by_level=by_level,
        total=len(queue),
        unadjudicated=sum(1 for alert in queue if not alert.adjudicated),
        # `queue` is already ordered, so element 0 *is* priority 1. Re-deriving
        # the maximum here would be a second ranking rule waiting to disagree.
        highest_priority_reason=queue[0].reason if queue else None,
    )
