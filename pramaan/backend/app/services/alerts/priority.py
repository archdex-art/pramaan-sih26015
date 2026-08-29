"""Derives a monitoring officer's work queue from verdicts that already exist.

The `alerts` table in the schema is a *notification log* — a record of things
that were pushed at someone. This module deliberately does not read or write it,
because a queue built from stored alert rows answers the wrong question: it tells
you what was once dispatched, not what is currently wrong. Re-deriving the
ranking from the latest verdict per claim means a re-run of the engine changes
the queue immediately, and a claim that got adjudicated stops shouting without
anyone having to remember to delete a row.

## Why ranking lives here and not in SQL

`ORDER BY` could express most of this. It was rejected for two reasons. First,
the tiebreak is level-dependent — a contradiction is ranked by *confidence* and a
data gap by *scarcity of data*, which are opposite directions of the same
instinct — and encoding that in SQL produces a `CASE` expression nobody can
review. Second, the ranking is the part a district officer will argue with, so it
has to be testable with a list of plain dicts and no database at all. Everything
in this module is pure: no session, no IO, no clock.

## Why L2/L3/L4 are absent rather than ranked last

A corroborated verdict is not a low-priority alert, it is not an alert. Giving it
priority 47 invites a UI to render it in the same list as a contradiction and
invites an officer to work down to it. `rank` drops those levels entirely, and
`ALERT_LEVELS` exists so the query can drop them before they cross the process
boundary too.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Epistemic level -> severity band, 1 = most urgent. Only levels appearing here
#: are alerts; L2_corroborated, L3_multi_indicator and L4_control_differenced are
#: absent because the evidence agrees with the claim and there is nothing to act
#: on.
#:
#: The ordering choice that matters is N2 above N1. N1_inconclusive is a *data*
#: failure — the engine could not see enough to decide — whereas N2_unsupported
#: is an *evidential* failure: the engine looked and found nothing backing the
#: claim. Ranking N1 above N2 because "inconclusive sounds worse than
#: unsupported" would push officers towards sites where the only defect is a
#: cloudy scene, ahead of sites where independent evidence is genuinely missing.
SEVERITY: dict[str, int] = {
    "N3_contradicted": 1,
    "N2_unsupported": 2,
    "N1_inconclusive": 3,
    "L0_recorded": 4,
    "L1_observed": 5,
}

#: Levels the register query may keep. Derived from `SEVERITY` so the definition
#: of "is an alert" cannot drift between the SQL and the ranking.
ALERT_LEVELS: tuple[str, ...] = tuple(SEVERITY)

#: Levels whose tiebreak is confidence, descending. For an adversarial finding
#: the officer wants the case the engine is *surest* about first, because that is
#: the one that survives a challenge. For the data-limited levels the tiebreak is
#: data_sufficiency ascending instead: there the scarcest evidence is the most
#: urgent, and confidence is low for all of them by construction.
_RANK_BY_CONFIDENCE: frozenset[str] = frozenset({"N3_contradicted", "N2_unsupported"})


@dataclass(frozen=True, slots=True)
class Alert:
    """One queue entry. Frozen because `priority` is only meaningful relative to
    the rest of the list it was computed with — mutating one entry silently
    invalidates the ordering it belongs to.
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
    #: Position in this queue, 1 = work on first. Distinct from the engine's own
    #: `recommended_action.priority` (1..5), which grades a single verdict in
    #: isolation and says nothing about what else is waiting in the district.
    priority: int
    reason: str
    recommended_action: str
    adjudicated: bool


def reason_for(level: str, data_sufficiency: float, intervention_type: str) -> str:
    """One sentence a district officer can act on without opening the evidence tree.

    The wording is load-bearing. An `N1_inconclusive` claim must never read as an
    accusation: the engine did not find the structure missing, it failed to get a
    usable look, and an officer who reads "inconclusive" as "suspect" will burn a
    field trip that returns the same non-answer. Conversely `N3_contradicted` is
    the only band allowed to demand physical verification, matching the
    vocabulary lock in `reconcile.types.Action`.

    `data_sufficiency` is quoted only in the bands where it is the operative
    fact. Printing it next to a contradiction would suggest the contradiction is
    provisional on more data, which is exactly the opposite of the finding.
    """
    asset = intervention_type.replace("_", " ")
    if level == "N3_contradicted":
        return (
            f"Independent evidence disputes this {asset}: the site requires physical "
            f"verification before the asset is treated as built."
        )
    if level == "N2_unsupported":
        return (
            f"No independent evidence supports this {asset}. Schedule a field visit to "
            f"establish whether the asset exists on the ground."
        )
    if level == "N1_inconclusive":
        return (
            f"This is a data problem, not a negative finding: only "
            f"{data_sufficiency:.0%} of the evidence needed to judge this {asset} was "
            f"usable, so dispatching a field team now may waste the trip — get a clean "
            f"image or geotag first."
        )
    if level == "L0_recorded":
        return (
            f"Nothing but the claim's own paperwork exists for this {asset} "
            f"({data_sufficiency:.0%} evidence coverage). It is recorded, not observed."
        )
    if level == "L1_observed":
        return (
            f"This {asset} is visible in its own submission but nothing independent has "
            f"confirmed it yet. Re-check next monitoring cycle."
        )
    raise ValueError(f"{level} is not an alert level; expected one of {ALERT_LEVELS}")


def _sort_key(row: Mapping[str, Any]) -> tuple[int, int, float, int]:
    """`(severity, adjudicated, band tiebreak, claim_id)`.

    Read left to right, this is the sentence "worst finding first; within a
    finding, work nobody has signed off yet; within that, the entry this band
    considers most urgent; and ties broken by claim id so the queue is stable
    across requests rather than shuffling on every page load".

    `adjudicated` sits *above* the tiebreak on purpose. A verdict a human has
    already signed is finished work as far as the queue is concerned, even when
    it is a high-confidence contradiction, and leaving it interleaved makes an
    officer re-open decisions instead of clearing the backlog.
    """
    level = str(row["level"])
    tiebreak = (
        -float(row["confidence"])
        if level in _RANK_BY_CONFIDENCE
        else float(row["data_sufficiency"])
    )
    return (SEVERITY[level], int(bool(row["adjudicated"])), tiebreak, int(row["claim_id"]))


def rank(rows: Sequence[Mapping[str, Any]]) -> list[Alert]:
    """Order the alert-worthy rows and stamp each with its queue position.

    Rows are the joined claim/verdict/intervention shape produced by
    `api.v1.alerts`; anything with the right keys works, which is the point of
    accepting mappings rather than a bespoke input dataclass.
    """
    ordered = sorted((row for row in rows if str(row["level"]) in SEVERITY), key=_sort_key)
    return [
        Alert(
            claim_id=int(row["claim_id"]),
            verdict_id=int(row["verdict_id"]),
            unique_id=str(row["unique_id"]),
            intervention_type=str(row["intervention_type"]),
            district_lgd=str(row["district_lgd"]),
            level=str(row["level"]),
            label=str(row["label"]),
            confidence=float(row["confidence"]),
            data_sufficiency=float(row["data_sufficiency"]),
            priority=position,
            reason=reason_for(
                str(row["level"]),
                float(row["data_sufficiency"]),
                str(row["intervention_type"]),
            ),
            # The engine's own recommendation, carried through verbatim. This
            # module ranks; it does not get to re-decide what should be done to a
            # site, because that decision is recorded in the verdict's lineage
            # and has to stay reproducible from it.
            recommended_action=str(row["recommended_action"]),
            adjudicated=bool(row["adjudicated"]),
        )
        for position, row in enumerate(ordered, start=1)
    ]
