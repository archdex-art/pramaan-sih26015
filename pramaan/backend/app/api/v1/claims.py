"""Claims register and evidence tree — the data behind S1 and S2.

Two endpoints, both projections of stored rows:

`GET /claims`
    The register. One row per claim with its latest verdict.

`GET /claims/{id}/evidence`
    The evidence tree: one entry per family, with agreement, availability, the
    producer's reason, and its lineage.

## Provenance is a first-class field, not a note

Every row carries `provenance`: `measured` or `golden`. The register is seeded
with the golden-case suite alongside the claims that were actually measured, so
the table demonstrates all eight epistemic levels — and a synthetic case must be
impossible to mistake for a measurement. Returning it as an enum the UI renders
as a badge is the only version of that which survives a screenshot being
forwarded without context.

## Unavailable is not zero

`agreement` is returned as `None` when a family was unavailable. A `0.0` would be
indistinguishable from "measured, and neutral", and the difference between those
two is the difference between low coverage and a genuine neutral reading.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.services.reconcile import EngineConfig, label_for
from app.services.reconcile.types import Level

router = APIRouter(tags=["claims"])

DbSession = Annotated[Session, Depends(db_session)]

Provenance = Literal["measured", "golden"]

# One row per claim, joined to its highest verdict version. `DISTINCT ON` is
# PostgreSQL-specific and is the right tool: a correlated subquery or a window
# filter would both be slower and less readable for exactly this shape.
_REGISTER = text("""
SELECT DISTINCT ON (c.id)
       c.id                AS claim_id,
       i.unique_id         AS unique_id,
       i.type              AS intervention_type,
       c.asserted_date     AS asserted_date,
       c.district_lgd      AS district_lgd,
       c.detectability     AS detectability,
       ST_Y(c.geom::geometry) AS lat,
       ST_X(c.geom::geometry) AS lon,
       c.uncertainty_m     AS uncertainty_m,
       i.expected_footprint_m2 AS expected_footprint_m2,
       v.id                AS verdict_id,
       v.version           AS version,
       v.level             AS level,
       v.score             AS score,
       v.confidence        AS confidence,
       v.coverage          AS coverage,
       v.data_sufficiency  AS data_sufficiency,
       v.status            AS status,
       v.rule_path         AS rule_path,
       jsonb_array_length(v.dissent) AS dissent_count,
       v.lineage -> 'provenance'     AS provenance_note,
       (SELECT count(*) FROM evidence e
         WHERE e.claim_id = c.id AND e.available) AS families_available,
       (SELECT count(*) FROM evidence e WHERE e.claim_id = c.id) AS families_total
FROM claims c
JOIN interventions i ON i.id = c.intervention_id
LEFT JOIN verdicts v ON v.claim_id = c.id
ORDER BY c.id, v.version DESC
""")

_EVIDENCE = text("""
SELECT family, agreement, available, payload, lineage, computed_at
FROM evidence WHERE claim_id = :claim_id
""")

_CLAIM_EXISTS = text("SELECT 1 FROM claims WHERE id = :claim_id")

#: The engine consumes families in this order and the UI must not reorder them:
#: independent evidence first, the claim's own source last. Sorting the tree
#: alphabetically would put `photo` second and quietly imply it carries weight.
FAMILY_ORDER = ("terrain", "satellite", "temporal", "control", "context", "photo")


class RegisterRow(BaseModel):
    claim_id: int
    unique_id: str
    intervention_type: str
    asserted_date: str | None
    district_lgd: str
    lat: float
    lon: float
    uncertainty_m: float | None
    detectability: str | None
    #: Drawn to scale beside the uncertainty disk, which is what makes the
    #: detectability gate visible rather than merely asserted.
    expected_footprint_m2: float | None

    verdict_id: int | None
    version: int | None
    level: str | None
    label: str | None
    score: float | None
    confidence: float | None
    coverage: float | None
    data_sufficiency: float | None
    status: str | None
    rule_path: list[str] = Field(default_factory=list)
    dissent_count: int = 0
    families_available: int = 0
    families_total: int = 0

    provenance: Provenance = Field(
        description="`measured` from real imagery and terrain; `golden` is a "
        "synthetic test bundle with an engine-computed verdict. Rendered as a "
        "badge, never as a footnote."
    )
    provisional: bool = True


class EvidenceEntry(BaseModel):
    family: str
    #: None when the family was unavailable — never 0.0, which would read as
    #: "measured, and neutral".
    agreement: float | None
    available: bool
    reason: str
    cluster_scale: bool = False
    #: Producer provenance: scene ids, DEM product, control ids, analysis grid.
    lineage: dict[str, Any] = Field(default_factory=dict)
    #: `agrees` / `neutral` / `disagrees` / `unavailable` — the UI renders a
    #: glyph, but the semantics are decided here so two screens cannot disagree.
    direction: Literal["agrees", "neutral", "disagrees", "unavailable"]


class EvidenceTree(BaseModel):
    claim_id: int
    entries: list[EvidenceEntry]
    families_available: int
    families_total: int


def _direction(agreement: float | None, available: bool) -> str:
    if not available or agreement is None:
        return "unavailable"
    if agreement >= 0.15:
        return "agrees"
    if agreement <= -0.15:
        return "disagrees"
    return "neutral"


def _provenance(note: Any) -> Provenance:
    """`golden` unless the verdict's lineage names a real data source.

    Defaults to `golden`, deliberately. If provenance cannot be established the
    safe answer is the one that under-claims: a synthetic row mislabelled as
    measured is a far worse failure than the reverse.
    """
    if isinstance(note, str) and "HLS" in note:
        return "measured"
    return "golden"


@router.get("/claims", response_model=list[RegisterRow])
def list_claims(
    session: DbSession,
    level: Annotated[str | None, Query(description="filter by epistemic level")] = None,
    provenance: Annotated[Provenance | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[RegisterRow]:
    rows = session.execute(_REGISTER).mappings().all()
    cfg = EngineConfig()

    out: list[RegisterRow] = []
    for row in rows:
        prov = _provenance(row["provenance_note"])
        lvl = row["level"]
        out.append(
            RegisterRow(
                claim_id=int(row["claim_id"]),
                unique_id=str(row["unique_id"]),
                intervention_type=str(row["intervention_type"]),
                asserted_date=None if row["asserted_date"] is None else str(row["asserted_date"]),
                district_lgd=str(row["district_lgd"]),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                uncertainty_m=None if row["uncertainty_m"] is None else float(row["uncertainty_m"]),
                detectability=None if row["detectability"] is None else str(row["detectability"]),
                expected_footprint_m2=None
                if row["expected_footprint_m2"] is None
                else float(row["expected_footprint_m2"]),
                verdict_id=None if row["verdict_id"] is None else int(row["verdict_id"]),
                version=None if row["version"] is None else int(row["version"]),
                level=lvl,
                label=None if lvl is None else label_for(Level(lvl), float(row["score"]), cfg),
                score=None if row["score"] is None else float(row["score"]),
                confidence=None if row["confidence"] is None else float(row["confidence"]),
                coverage=None if row["coverage"] is None else float(row["coverage"]),
                data_sufficiency=None
                if row["data_sufficiency"] is None
                else float(row["data_sufficiency"]),
                status=row["status"],
                rule_path=list(row["rule_path"] or ()),
                dissent_count=int(row["dissent_count"] or 0),
                families_available=int(row["families_available"] or 0),
                families_total=int(row["families_total"] or 0),
                provenance=prov,
                provisional=row["status"] != "adjudicated",
            )
        )

    if level:
        out = [r for r in out if r.level == level]
    if provenance:
        out = [r for r in out if r.provenance == provenance]
    return out[:limit]


@router.get("/claims/{claim_id}/evidence", response_model=EvidenceTree)
def get_evidence(claim_id: int, session: DbSession) -> EvidenceTree:
    if session.execute(_CLAIM_EXISTS, {"claim_id": claim_id}).first() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"claim {claim_id} does not exist")

    rows = {
        row["family"]: row for row in session.execute(_EVIDENCE, {"claim_id": claim_id}).mappings()
    }
    entries: list[EvidenceEntry] = []
    for family in FAMILY_ORDER:
        row = rows.get(family)
        if row is None:
            continue
        payload = dict(row["payload"] or {})
        available = bool(row["available"])
        agreement = None if not available else float(row["agreement"])
        entries.append(
            EvidenceEntry(
                family=family,
                agreement=agreement,
                available=available,
                reason=str(payload.get("reason", "")),
                cluster_scale=bool(payload.get("cluster_scale", False)),
                lineage=dict(row["lineage"] or {}),
                direction=_direction(agreement, available),  # type: ignore[arg-type]
            )
        )

    return EvidenceTree(
        claim_id=claim_id,
        entries=entries,
        families_available=sum(1 for e in entries if e.available),
        families_total=len(entries),
    )
