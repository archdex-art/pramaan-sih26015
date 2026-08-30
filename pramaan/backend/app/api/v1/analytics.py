"""District-level aggregates over stored verdicts, and the list of what is refused.

Every other read surface answers a question about one claim. This one answers the
question a state or district officer asks before opening any claim: *what does
the register as a whole look like, and how much of it does the engine actually
know?*

## Why the refusal list is a response field and not a footnote

The obvious district dashboard for this programme is "mean NDVI change, rainfall,
share assessed, share escalated" — four numbers that look like measurement and,
for this data, would be four numbers we cannot stand behind. So `refused` is a
first-class field carrying every metric deliberately not computed and the reason.
It is populated from the same code path that computes the rest, so it can never
drift into being aspirational: if a metric becomes derivable, it moves out of
`refused` and into the response because the code that computes it exists.

The alternative — omit what we cannot compute — was rejected because an absent
metric is indistinguishable from a metric nobody thought of, and the whole claim
of this system is that its limits are stated rather than discovered.

## Why the aggregation is in the database and the fold is in Python

A national principal's scope is the full register: 124,000 structures at
programme scale (`scripts/benchmark.py`). Streaming that into Python to count it
would allocate a row per structure to produce roughly twenty integers. So the
database groups by the categorical dimensions — level, action, gate result,
provenance, signed-or-not — and returns one row per *combination*, which is
bounded at a few hundred rows regardless of district size and does not grow with
the register. The numeric means come back as exact `NUMERIC` sums plus counts, so
the Python fold divides once and cannot accumulate float error.

## Why nothing here is COALESCE'd to zero

`means` are `None` when there is nothing to average, and the API says `null`.
Zero is a measurement: a mean confidence of 0.0 means the engine assessed claims
and found no support, which is a finding. An empty scope means it assessed
nothing. Rendering both as `0` is the single dishonesty this product exists to
refuse, so no default is applied at any layer — not in SQL, not in the model.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import CurrentScope, require
from app.api.scope import register_clause
from app.core.authz import Capability
from app.db.session import db_session
from app.services.reconcile.types import FAMILIES, Level
from app.services.reconcile.weights import EngineConfig

router = APIRouter(tags=["analytics"])

DbSession = Annotated[Session, Depends(db_session)]

#: The marker `app.api.v1.claims._provenance` looks for in a verdict's lineage to
#: call a row `measured`. Duplicated here as a bind parameter rather than shared,
#: because that function classifies one row in Python and this endpoint must
#: classify a national register inside the database. The coupling is real and is
#: pinned by `test_provenance_matches_the_register_badge`: if the register's rule
#: moves and this does not, a claim would be badged `measured` in the table and
#: counted `golden` in the summary of the same table.
_MEASURED_MARKER = "HLS"

# `DISTINCT ON (c.id) ... ORDER BY c.id, v.version DESC` is this codebase's
# established way of taking the newest verdict per claim (`v1.claims`,
# `v1.alerts`), kept identical so one plan change fixes all three.
#
# The selection happens in a CTE and *every* aggregate runs outside it. That
# ordering is the correctness of this endpoint, and it is not a style choice: a
# predicate in the same SELECT as `DISTINCT ON` is evaluated before the
# newest-row selection, so for a claim whose v1 was N3 and whose v2 is L3 the
# filter discards the L3 row, `DISTINCT ON` finds only v1 surviving, and the
# superseded N3 is aggregated as if current. Every row returned is a genuine
# row, so nothing looks wrong. That was a live bug in `v1.alerts` (see
# `test_only_the_newest_verdict_version_appears`), and an analytics page is the
# worst place for it: a headline "12% contradicted" built from superseded
# verdicts is a number nobody can trace back to a claim.
#
# There is therefore no WHERE at all outside the CTE. The categorical dimensions
# are grouped, never filtered — `by_level` must carry its zeroes, and a filter
# is how a zero becomes a missing key.
#
# A template, not a `text()`: `{scope}` is filled from `scope.register_clause`,
# which returns one of three fixed fragments and never caller input. The
# district value itself stays a bind parameter, so it never reaches the SQL
# string.
#
# `adjudicated` is `EXISTS` against `adjudications`, not `v.status =
# 'adjudicated'`, following `v1.alerts`: the former is direct evidence that a
# human signed something, the latter is a denormalised flag a future writer
# could forget to set. The failure mode of that staleness here would be a
# "provisional" count that understates how much of the register is unsigned.
#
# `detectability` is selected raw and grouped raw, including NULL. It is a
# nullable TEXT column written as exactly 'passed' or 'failed' by
# `scripts/seed_golden.py` from `bundle.gates.detectability_passed`; a claim
# ingested before the gate ran has neither. Mapping NULL onto 'failed' would
# invent a sensor-physics result, so it is counted as neither and the response's
# two counts deliberately do not have to sum to `totals.claims`.
_NEWEST_SQL = """
WITH newest AS (
    SELECT DISTINCT ON (c.id)
           c.id                              AS claim_id,
           c.detectability                   AS detectability,
           v.level::text                     AS level,
           v.confidence                      AS confidence,
           v.coverage                        AS coverage,
           v.data_sufficiency                AS data_sufficiency,
           v.recommended_action ->> 'action' AS action,
           position(:measured_marker in coalesce(v.lineage ->> 'provenance', ''))
               > 0                           AS measured,
           EXISTS (
               SELECT 1 FROM adjudications a WHERE a.verdict_id = v.id
           )                                 AS adjudicated
    FROM claims c
    JOIN verdicts v ON v.claim_id = c.id
    WHERE {scope}
    ORDER BY c.id, v.version DESC
)
SELECT level,
       action,
       detectability,
       measured,
       adjudicated,
       count(*)                AS n,
       sum(confidence)         AS confidence_sum,
       sum(coverage)           AS coverage_sum,
       sum(data_sufficiency)   AS data_sufficiency_sum
FROM newest
GROUP BY level, action, detectability, measured, adjudicated
"""

# Claims are counted separately because the CTE above joins `verdicts` and a
# claim awaiting reconciliation has none. Folding it in with a LEFT JOIN would
# put a NULL level into `by_level`, i.e. a ninth band that is not on the ladder.
# Two trivial queries, each with one meaning, beat one query with a special case.
_CLAIM_COUNT_SQL = """
SELECT count(*) AS n FROM claims c WHERE {scope}
"""

# Per-family availability and direction, over the claims in scope.
#
# `agreement` is `NUMERIC(4,3)` in `[-1,1]` (CHECK `agreement_range`) and
# `available` is a boolean, so an unavailable family still has an agreement
# value on the row. Every count here is therefore gated on `available`: counting
# the agreement of a family that was never observed would read a stored default
# as a measurement, which is invariant I4 in ADR-001 stated the other way round.
#
# `e.district_lgd = c.district_lgd` is redundant — `evidence` is UNIQUE on
# (claim_id, family, district_lgd) and claim_id functionally determines the
# district — and is present so the planner can propagate a constant district
# into the LIST partition key and prune. Without it a district-scoped query
# scans every district's partition to find rows it will then join away.
_FAMILY_SQL = """
SELECT e.family::text AS family,
       count(*) FILTER (WHERE e.available) AS available,
       count(*) FILTER (
           WHERE e.available AND e.agreement >= :agreeing_min
       ) AS agreeing,
       count(*) FILTER (
           WHERE e.available
             AND e.agreement > :disagreeing_max
             AND e.agreement < :agreeing_min
       ) AS neutral,
       count(*) FILTER (
           WHERE e.available AND e.agreement <= :disagreeing_max
       ) AS disagreeing
FROM evidence e
JOIN claims c ON c.id = e.claim_id AND e.district_lgd = c.district_lgd
WHERE {scope}
GROUP BY e.family
"""


class FamilyBreakdown(BaseModel):
    """One evidence family across the claims in scope.

    `available + neutral` is not the total: `available` is the denominator and
    `agreeing`, `neutral` and `disagreeing` partition it exactly. A family that
    was not observed appears in none of the three, because "we did not look" is
    not a neutral reading — that distinction is the entire reason `coverage`
    exists (`services.reconcile.types`).
    """

    available: int
    agreeing: int
    neutral: int
    disagreeing: int


class Totals(BaseModel):
    #: Every claim in scope, including those with no verdict yet. Deliberately a
    #: larger number than `verdicts`: the gap is the reconciliation backlog, and
    #: hiding it by counting only assessed claims would make the register look
    #: more complete than it is.
    claims: int
    #: Newest verdicts, one per claim that has any. Never the row count of
    #: `verdicts`, which includes superseded versions.
    verdicts: int
    #: Newest verdicts with a signature in `adjudications`.
    adjudicated: int
    #: `verdicts - adjudicated`. Stated rather than left for the client to
    #: subtract, so two screens cannot disagree about what "provisional" means.
    provisional: int


class Means(BaseModel):
    """Arithmetic means over the newest verdicts, or `None` for an empty scope.

    `None`, never `0.0`. See the module docstring: zero is a finding and absence
    is not, and this is the one field where conflating them would be invisible.
    """

    confidence: float | None
    coverage: float | None
    data_sufficiency: float | None


class Detectability(BaseModel):
    """The gate result recorded on the claim, as stored.

    `passed + not_passed` may be less than `totals.claims`. Claims whose gate
    result was never recorded are in neither count, because the only alternatives
    were to invent a pass or invent a failure.
    """

    passed: int
    not_passed: int


class Provenance(BaseModel):
    """Measured versus golden-case, over the newest verdicts.

    Classified by the same rule as the register's per-row badge, and biased the
    same way: anything whose lineage does not name a real data source counts as
    `golden`. A synthetic row miscounted as measured is a far worse failure than
    the reverse.
    """

    measured: int
    golden: int


class Analytics(BaseModel):
    #: The districts actually present in the aggregate, so a national reader can
    #: see the denominator rather than assume it is "all of India".
    district_lgds: list[str]
    totals: Totals
    #: All eight levels, including the zeroes. An omitted band reads as a band
    #: that does not exist, and the ladder's shape is the product.
    by_level: dict[str, int]
    #: Recommended actions present in the aggregate. Not pre-seeded with zeroes:
    #: unlike the ladder, the action vocabulary is the engine's output space and
    #: enumerating it here would assert a closed set this module does not own.
    by_action: dict[str, int]
    means: Means
    detectability: Detectability
    provenance: Provenance
    families: dict[str, FamilyBreakdown]
    #: Share of newest verdicts at `N1_inconclusive`, unrounded. `None` when
    #: there are no verdicts to take a share of.
    #:
    #: Deliberately prominent. A system reporting 30% inconclusive is more
    #: trustworthy than one reporting 100% conclusive, because the second has
    #: either perfect data or a rule that cannot say "I do not know". Rounding it
    #: here would be presentation logic in the API; the client formats it.
    inconclusive_share: float | None
    refused: list[str] = Field(
        description="Metrics deliberately not computed, each with its reason. "
        "Populated by the same code path as the rest of the response, so it "
        "cannot drift into being aspirational."
    )


def _mean(total: Decimal, n: int) -> float | None:
    """`None` for an empty scope, never 0.0."""
    if n == 0:
        return None
    return float(total / n)


def _refusals(*, measured: int, verdicts: int) -> list[str]:
    """What this endpoint will not compute, and why.

    Takes the counts it has just computed rather than being a constant, because
    two of the three refusals are conditional on the data in scope. A hardcoded
    list would keep refusing a metric that had become derivable, which is the
    mirror image of the dishonesty it exists to prevent.
    """
    refused = [
        # No labelled set exists. `tests/golden/cases/` is 42 hand-written
        # bundles asserting engine *behaviour*; none of them is a field-verified
        # outcome, so there is nothing to be right or wrong against. A precision
        # figure computed against the engine's own output would measure
        # self-consistency and be read as accuracy.
        "accuracy / precision / recall — no labelled ground-truth set exists. "
        "The golden suite fixes engine behaviour, not field outcomes, so any "
        "figure would measure self-consistency and be read as correctness.",
        # The ladder's ceiling is L4_control_differenced and there is no L5.
        # Control differencing removes the regional trend; it does not establish
        # that the intervention caused what remains.
        "impact / attribution — the ladder's ceiling is L4_control_differenced. "
        "Differencing against matched controls removes the regional trend; it "
        "does not establish that the intervention caused the residual. No "
        "causal level exists to aggregate.",
    ]

    # The district-mean NDVI delta the reference dashboards lead with. Checked
    # rather than assumed: the per-claim seasonal delta lives in the temporal
    # family's `lineage.site_delta`, written only by the measured pipeline
    # (`scripts/build_temporal_series.py` -> `seed_demo.py`). Golden-case
    # bundles carry `lineage: {}` (`tests/golden/test_golden.py` defaults it),
    # so the value is absent for every synthetic row.
    #
    # Two reasons it stays refused even when present. First, coverage: a mean
    # over the subset that has one is a mean over the measured claims labelled
    # as a district figure. Second, and worse, the index differs by intervention
    # type — NDVI for a plantation, MNDWI for a waterbody — so averaging
    # `site_delta` across types produces a number with no physical
    # interpretation that nonetheless looks exactly like a result. That is the
    # failure `services/temporal/seasons.py` documents for cross-season
    # differencing, and it applies unchanged across families.
    if measured == verdicts and verdicts > 0:
        refused.append(
            "district mean NDVI change — every claim in scope is measured, but "
            "the stored per-claim delta is indexed per intervention type "
            "(NDVI for vegetative work, MNDWI for water bodies). Averaging "
            "across types yields a number with no physical interpretation. "
            "Per-claim deltas are on the claim's temporal record."
        )
    else:
        refused.append(
            f"district mean NDVI change — not derivable. {measured} of "
            f"{verdicts} newest verdicts come from measured imagery; "
            "golden-case bundles store no seasonal delta at all, and a mean "
            "over the measured subset presented as a district figure would "
            "misstate its own coverage."
        )

    # Rainfall is real and stored, but only as the context family's per-claim
    # ratio to a decadal mean. There is no district rainfall series in the
    # database, and a mean of per-claim ratios is not a district rainfall.
    refused.append(
        "district rainfall series — the context family stores a per-claim "
        "ratio against a decadal mean, not a district time series. No table "
        "here holds one, so there is nothing to aggregate."
    )
    return refused


@router.get(
    "/analytics",
    response_model=Analytics,
    dependencies=[Depends(require(Capability.VERDICT_READ))],
)
def analytics(session: DbSession, scope: CurrentScope) -> Analytics:
    clause, params = register_clause(scope)
    cfg = EngineConfig()

    rows = (
        session.execute(
            text(_NEWEST_SQL.format(scope=clause)),
            {**params, "measured_marker": _MEASURED_MARKER},
        )
        .mappings()
        .all()
    )

    by_level = dict.fromkeys((level.value for level in Level), 0)
    by_action: dict[str, int] = {}
    verdicts = 0
    adjudicated = 0
    gate_passed = 0
    gate_not_passed = 0
    measured = 0
    confidence_sum = Decimal(0)
    coverage_sum = Decimal(0)
    sufficiency_sum = Decimal(0)

    for row in rows:
        n = int(row["n"])
        verdicts += n
        # The level is a database enum, so an unknown value here means the enum
        # gained a member without this module learning it. Adding the key rather
        # than dropping the row: an uncounted verdict is a silently wrong total,
        # and a key the client does not recognise is at least visible.
        by_level[row["level"]] = by_level.get(row["level"], 0) + n
        if row["action"] is not None:
            by_action[row["action"]] = by_action.get(row["action"], 0) + n
        if row["adjudicated"]:
            adjudicated += n
        if row["detectability"] == "passed":
            gate_passed += n
        elif row["detectability"] == "failed":
            gate_not_passed += n
        if row["measured"]:
            measured += n
        confidence_sum += row["confidence_sum"]
        coverage_sum += row["coverage_sum"]
        sufficiency_sum += row["data_sufficiency_sum"]

    claims = int(session.execute(text(_CLAIM_COUNT_SQL.format(scope=clause)), params).scalar_one())

    family_rows = (
        session.execute(
            text(_FAMILY_SQL.format(scope=clause)),
            {
                **params,
                # The engine's own published thresholds, read from `EngineConfig`
                # rather than restated. `agreeing_threshold` is what the L2/L3/L4
                # rules mean by "an agreeing family", so these counts are the
                # same counts the level decision used. The neutral band is
                # therefore the open interval (-0.35, +0.35).
                #
                # Not the +/-0.15 band `v1.claims._direction` uses for the
                # per-claim glyph: that one is a reading aid on a single row and
                # is deliberately wider, whereas an aggregate that disagreed
                # with the rule that produced the levels beside it would be
                # unreconcilable by anyone auditing the page.
                "agreeing_min": cfg.agreeing_threshold,
                "disagreeing_max": cfg.disagreeing_threshold,
            },
        )
        .mappings()
        .all()
    )

    # Pre-seeded with all six families at zero, in the engine's order. The six
    # are frozen by ADR-001, so a family missing from the response would mean it
    # was never computed for any claim — which is a finding worth showing, not a
    # key worth omitting.
    # `dict[str, ...]` rather than the inferred `dict[Family, ...]`: the loop
    # below writes keys read back from the database, which is where a new enum
    # member would first appear.
    families: dict[str, FamilyBreakdown] = {
        family: FamilyBreakdown(available=0, agreeing=0, neutral=0, disagreeing=0)
        for family in FAMILIES
    }
    for row in family_rows:
        families[row["family"]] = FamilyBreakdown(
            available=int(row["available"]),
            agreeing=int(row["agreeing"]),
            neutral=int(row["neutral"]),
            disagreeing=int(row["disagreeing"]),
        )

    districts = _districts(session, clause, params)

    return Analytics(
        district_lgds=districts,
        totals=Totals(
            claims=claims,
            verdicts=verdicts,
            adjudicated=adjudicated,
            provisional=verdicts - adjudicated,
        ),
        by_level=by_level,
        by_action=by_action,
        means=Means(
            confidence=_mean(confidence_sum, verdicts),
            coverage=_mean(coverage_sum, verdicts),
            data_sufficiency=_mean(sufficiency_sum, verdicts),
        ),
        detectability=Detectability(passed=gate_passed, not_passed=gate_not_passed),
        provenance=Provenance(measured=measured, golden=verdicts - measured),
        families=families,
        inconclusive_share=(
            None if verdicts == 0 else by_level[Level.N1_INCONCLUSIVE.value] / verdicts
        ),
        refused=_refusals(measured=measured, verdicts=verdicts),
    )


_DISTRICTS_SQL = """
SELECT DISTINCT c.district_lgd AS district_lgd FROM claims c WHERE {scope}
ORDER BY district_lgd
"""


def _districts(session: Session, clause: str, params: dict[str, Any]) -> list[str]:
    """Districts actually represented, not the districts the principal may read.

    The distinction matters for a national reader: "unrestricted" is a
    permission and this is a denominator, and printing the first as the second
    would imply national coverage from one seeded district.
    """
    return [
        str(row[0])
        for row in session.execute(text(_DISTRICTS_SQL.format(scope=clause)), params).all()
    ]
