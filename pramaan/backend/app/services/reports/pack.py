"""Assembling the Evidence Pack from stored rows. Nothing is recomputed.

## The governing rule

**A report reads; it never re-derives.** Every number, level, agreement value,
digest and hash in the pack is the value stored in `claims`, `interventions`,
`verdicts`, `evidence`, `adjudications` and `users`. The reconciliation engine is
never called from this package, and neither is any evidence producer.

The alternative — recomputing on export, so the document is "always current" —
was rejected because it produces a document that can disagree with the record it
claims to document. A verdict a named officer signed is the thing under audit; a
freshly computed one is a different verdict wearing the same claim id, and
printing the second under the first's signature is precisely the failure the
adjudication ledger exists to prevent. `POST /verdicts/{id}/recompute` is where
re-running the engine belongs, and it deliberately writes nothing.

The one consequence to be aware of: if the stored lineage is thin, the pack is
thin. It says so, item by item, rather than filling the gap. `PackUnavailable`
covers the case where there is nothing renderable at all, and the API turns it
into a 409 naming the reason — never a fallback to recomputation and never a
placeholder.

## Why `label` is the single derived value

`label` is a presentation of (level, score) and has no column: it is not stored
anywhere. Every other read surface in the API derives it with the engine's own
`label_for` (`v1.claims`, `v1.verdicts`, `v1.alerts`), and the pack does the
same so the exported document cannot show a label the detail screen contradicts.
The report states in its own text that the label is derived and from what.

## Why scenes are looked up in `satellite_scenes`

docs §21.3 requires the pack to carry scene ids *with their dates and cloud
fractions*. The verdict lineage records the granule ids; the acquisition date and
cloud fraction live in the `satellite_scenes` registry. So the ids come from the
lineage and each one is looked up in the registry. An id with no registry row is
reported as `registered=False` rather than dropped or given a plausible date —
"this granule was used and the system cannot tell you when it was taken" is a
real, disclosable state of the record, and the report prints it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.verdicts import (
    StoredVerdict,
    VerdictNotFound,
    latest_verdict_id,
    load_verdict,
)
from app.services.reconcile import EngineConfig, label_for
from app.services.reconcile.types import FAMILIES, Level


class PackUnavailable(Exception):
    """There is no renderable record for this claim, and the reason is in `str`.

    Distinct from "claim does not exist" — which is the jurisdiction layer's job
    and answers 404 — and from "the engine changed", which is the recompute
    endpoint's 409. This one means: the claim is yours, it exists, and the stored
    record cannot produce a document. The message names which part is missing so
    an officer can tell a gap in the data from a bug in the exporter.
    """


# `ST_Y`/`ST_X` on the cast-to-geometry column, matching `v1.claims`: the column
# is GEOMETRY(Point,4326) so the cast is a no-op, and keeping the same spelling
# means one reader learns one idiom.
#
# `projects` is joined for the project code and name. INNER, not LEFT:
# `interventions.project_id` is NOT NULL REFERENCES projects(id), so a LEFT JOIN
# would only hide a broken foreign key behind an empty cell on a government
# document.
_CLAIM = text("""
SELECT c.id                     AS claim_id,
       c.district_lgd           AS district_lgd,
       c.asserted_status::text  AS asserted_status,
       c.asserted_date          AS asserted_date,
       c.uncertainty_m          AS uncertainty_m,
       c.detectability          AS detectability,
       c.created_at             AS created_at,
       ST_Y(c.geom::geometry)   AS lat,
       ST_X(c.geom::geometry)   AS lon,
       i.unique_id              AS unique_id,
       i.type::text             AS intervention_type,
       i.status::text           AS work_status,
       i.completed_date         AS completed_date,
       i.expected_footprint_m2  AS expected_footprint_m2,
       i.village_lgd            AS village_lgd,
       i.survey_no              AS survey_no,
       p.project_code           AS project_code,
       p.name                   AS project_name
FROM claims c
JOIN interventions i ON i.id = c.intervention_id
JOIN projects p ON p.id = i.project_id
WHERE c.id = :claim_id
""")

_EVIDENCE = text("""
SELECT family::text AS family, agreement, available, payload, lineage
FROM evidence WHERE claim_id = :claim_id
""")

# Ordered by `id`, which is the chain order — the same order
# `services.audit.ledger.read_chain` verifies in. Every row is returned, not
# just the newest: `adjudications` carries no uniqueness on `verdict_id`, and a
# verdict signed twice is a fact about the record that a report showing one
# signature would conceal.
#
# `prev_hash` and `row_hash` are BYTEA; hex conversion happens in Python so the
# full 32 bytes reach the document as 64 characters and nothing truncates in SQL.
_SIGNATURES = text("""
SELECT a.id                     AS id,
       a.decision               AS decision,
       a.corrected_level::text  AS corrected_level,
       a.reason                 AS reason,
       a.decided_at             AS decided_at,
       a.prev_hash              AS prev_hash,
       a.row_hash               AS row_hash,
       u.username               AS username,
       u.full_name              AS full_name,
       u.role::text             AS role
FROM adjudications a
JOIN users u ON u.id = a.user_id
WHERE a.verdict_id = :verdict_id
ORDER BY a.id
""")

# docs §21.3 lists the computation timestamp among the things a verdict must
# carry. `StoredVerdict` does not expose `computed_at` — nothing else needs it —
# and a one-column read here is cheaper than widening a dataclass that five
# modules construct and compare.
_COMPUTED_AT = text("SELECT computed_at FROM verdicts WHERE id = :verdict_id")

# `= ANY(:scene_ids)` rather than an `IN` list built by string formatting: the
# ids come from a JSONB document and are therefore data, not code.
_SCENES = text("""
SELECT scene_id, source, sensed_at, cloud_pct, gsd_m
FROM satellite_scenes
WHERE scene_id = ANY(:scene_ids)
""")


@dataclass(frozen=True, slots=True)
class ClaimFacts:
    """The claim and the work it asserts, as stored."""

    claim_id: int
    unique_id: str
    intervention_type: str
    district_lgd: str
    asserted_status: str
    asserted_date: str
    work_status: str
    completed_date: str | None
    lat: float
    lon: float
    #: The uncertainty disk's radius. Every terrain variable was read as a
    #: distribution over this disk, never from the pixel at its centre.
    uncertainty_m: float | None
    detectability: str | None
    expected_footprint_m2: float | None
    village_lgd: str | None
    survey_no: str | None
    project_code: str
    project_name: str
    created_at: str


@dataclass(frozen=True, slots=True)
class FamilyRow:
    """One `evidence` row. `agreement` is None when the family was unavailable.

    Never 0.0 for an unavailable family — that reads as "measured, and neutral",
    which is a different finding from "not measured" and carries a different
    coverage consequence.
    """

    family: str
    agreement: float | None
    available: bool
    reason: str
    cluster_scale: bool
    lineage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Signature:
    """One `adjudications` row joined to its officer.

    `prev_hash` is None only for the genesis row, where the column is NULL. Both
    hashes are full 64-character hex; the report prints them at that length.
    """

    id: int
    decision: str
    corrected_level: str | None
    reason: str | None
    decided_at: str
    officer_username: str
    officer_name: str
    officer_role: str
    prev_hash: str | None
    row_hash: str


@dataclass(frozen=True, slots=True)
class SceneRecord:
    """A granule named in the lineage, and whatever the registry knows about it.

    `registered=False` means the id was used but `satellite_scenes` has no row
    for it, so its acquisition date and cloud fraction are genuinely unknown to
    this system. The report says that rather than omitting the granule.
    """

    scene_id: str
    source: str | None
    sensed_at: str | None
    cloud_pct: float | None
    gsd_m: float | None
    registered: bool


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """Everything the Evidence Pack renders, read from storage.

    Frozen: a renderer that could mutate the pack could make the printed
    document differ from the queried record between two passes over it.
    """

    claim: ClaimFacts
    verdict: StoredVerdict
    #: Derived from the stored level and score by the engine's own `label_for`.
    #: The only value in this pack that is not read from a column; see the module
    #: docstring.
    label: str
    #: When the verdict row was written. docs §21.3.
    computed_at: str
    families: tuple[FamilyRow, ...]
    signatures: tuple[Signature, ...]
    scenes: tuple[SceneRecord, ...]
    #: When this document was produced, and for whom. Not part of the record —
    #: printed in the footer so a forwarded copy carries its own provenance.
    generated_at: str
    generated_for: str

    @property
    def signed(self) -> bool:
        """True when a named officer has signed this verdict.

        The presence of an `adjudications` row, not `verdicts.status`. The two
        agree today, and the former is direct evidence that a human signed
        something while the latter is a flag a future writer could set wrongly.
        A document headed SIGNED RECORD on the strength of a denormalised
        column would be the exact defect this project's pitch rejects.
        """
        return len(self.signatures) > 0

    @property
    def operative_signature(self) -> Signature | None:
        """The last signature in chain order, or None.

        Last, not first: a verdict signed twice is operatively governed by the
        later decision. Both rows are still rendered.
        """
        return self.signatures[-1] if self.signatures else None

    @property
    def status_disagrees(self) -> bool:
        """`verdicts.status` and the ledger do not tell the same story.

        Surfaced rather than reconciled. Either direction is a real integrity
        finding — a status of `adjudicated` with no ledger row, or a ledger row
        with a status that never moved — and the report prints it instead of
        picking whichever answer looks tidier.
        """
        return (self.verdict.status == "adjudicated") != self.signed


def _iso(value: object) -> str | None:
    """Dates and timestamps as ISO strings; None stays None.

    `isoformat()` rather than `str()`: both happen to agree for `date` and
    `datetime` today, but only one of them is a documented format, and these
    strings are printed on a document an auditor reads years later.
    """
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _require_iso(value: object, field: str) -> str:
    rendered = _iso(value)
    if rendered is None:  # pragma: no cover - the columns are NOT NULL
        raise PackUnavailable(f"stored claim is missing {field}, which is NOT NULL in the schema")
    return rendered


def _hex(value: object) -> str | None:
    """A BYTEA column as full-length hex.

    No truncation, ever. A hash shown at 16 characters is not the hash, and a
    document that shortens one has stopped being checkable — which is the entire
    reason the column exists.
    """
    if value is None:
        return None
    return bytes(value).hex()  # type: ignore[call-overload]


def _lineage_bundle(verdict: StoredVerdict) -> dict[str, Any]:
    """The canonical engine input from the stored lineage, or refuse.

    This is the 409 boundary. Three states are separated because they mean
    different things to whoever is holding the empty document:

    * no lineage at all — the row predates migration 0002;
    * lineage without `bundle` — written by something that was not the engine's
      persistence path;
    * `bundle` without `families` — a truncated record.

    None of them falls back to re-running the engine. Recomputing here would
    produce a document that looks complete and describes a verdict nobody
    signed.
    """
    if not verdict.lineage:
        raise PackUnavailable(
            f"verdict {verdict.id} has an empty lineage record, so there is nothing to "
            f"render an Evidence Pack from. The row predates the lineage column "
            f"(migration 0002). Re-running reconciliation would produce a different "
            f"verdict, which this report will not substitute for the stored one."
        )
    bundle = verdict.lineage.get("bundle")
    if not isinstance(bundle, dict):
        raise PackUnavailable(
            f"verdict {verdict.id} has a lineage record with no `bundle` key, so the "
            f"canonical engine input was never stored and the Evidence Pack's data "
            f"lineage section cannot be produced from the record."
        )
    if not bundle.get("families"):
        raise PackUnavailable(
            f"verdict {verdict.id} has a lineage `bundle` with no families, so no "
            f"evidence input was recorded. The pack would show a verdict with no "
            f"basis, which is worse than no pack."
        )
    return bundle


def _scene_ids(verdict: StoredVerdict) -> tuple[str, ...]:
    """Every granule id named anywhere in this verdict's lineage.

    Producers record ids in two places and neither is authoritative on its own:
    `scene_ids` at the family-lineage root (the flat list the satellite and
    temporal producers stamp) and per-season under `observed_series`. Reading
    only one of them silently shortens the list on a document whose whole point
    is that the list is complete.

    Sorted and de-duplicated, so two passes over the same record print the same
    table.
    """
    found: set[str] = set()

    def harvest(node: object) -> None:
        if isinstance(node, dict):
            raw = node.get("scene_ids")
            if isinstance(raw, list):
                found.update(str(item) for item in raw)
            series = node.get("observed_series")
            if isinstance(series, list):
                for entry in series:
                    harvest(entry)

    family_lineage = verdict.lineage.get("family_lineage")
    if isinstance(family_lineage, dict):
        for value in family_lineage.values():
            harvest(value)
    producers = verdict.lineage.get("producers")
    if isinstance(producers, dict):
        families = producers.get("families")
        if isinstance(families, dict):
            for value in families.values():
                if isinstance(value, dict):
                    harvest(value.get("lineage"))
    return tuple(sorted(found))


def _load_scenes(session: Session, scene_ids: tuple[str, ...]) -> tuple[SceneRecord, ...]:
    """Join the lineage's granule ids against the scene registry.

    Every id is returned in either case: registered with its date and cloud
    fraction, or unregistered and marked as such. Dropping the unregistered ones
    would make the pack's scene table look consistent by hiding the gap.
    """
    if not scene_ids:
        return ()
    known = {
        str(row["scene_id"]): row
        for row in session.execute(_SCENES, {"scene_ids": list(scene_ids)}).mappings()
    }
    out: list[SceneRecord] = []
    for scene_id in scene_ids:
        row = known.get(scene_id)
        if row is None:
            out.append(
                SceneRecord(
                    scene_id=scene_id,
                    source=None,
                    sensed_at=None,
                    cloud_pct=None,
                    gsd_m=None,
                    registered=False,
                )
            )
            continue
        out.append(
            SceneRecord(
                scene_id=scene_id,
                source=None if row["source"] is None else str(row["source"]),
                sensed_at=_iso(row["sensed_at"]),
                cloud_pct=None if row["cloud_pct"] is None else float(row["cloud_pct"]),
                gsd_m=None if row["gsd_m"] is None else float(row["gsd_m"]),
                registered=True,
            )
        )
    return tuple(out)


def _families(session: Session, claim_id: int) -> tuple[FamilyRow, ...]:
    """The six evidence rows, in the engine's own family order.

    `FAMILIES` rather than alphabetical or query order: the engine consumes
    independent evidence first and the claim's own photograph last, and an
    alphabetical table would put `photo` second and imply it carries weight it
    is deliberately denied (ADR-001 weights it lowest of the six).
    """
    rows = {
        str(row["family"]): row
        for row in session.execute(_EVIDENCE, {"claim_id": claim_id}).mappings()
    }
    out: list[FamilyRow] = []
    for family in FAMILIES:
        row = rows.get(family)
        if row is None:
            continue
        payload = dict(row["payload"] or {})
        available = bool(row["available"])
        out.append(
            FamilyRow(
                family=family,
                agreement=None if not available else float(row["agreement"]),
                available=available,
                reason=str(payload.get("reason", "")),
                cluster_scale=bool(payload.get("cluster_scale", False)),
                lineage=dict(row["lineage"] or {}),
            )
        )
    return tuple(out)


def _signatures(session: Session, verdict_id: int) -> tuple[Signature, ...]:
    out: list[Signature] = []
    for row in session.execute(_SIGNATURES, {"verdict_id": verdict_id}).mappings():
        row_hash = _hex(row["row_hash"])
        if row_hash is None:  # pragma: no cover - row_hash is NOT NULL
            raise PackUnavailable(
                f"adjudication {row['id']} has no row_hash, so its ledger linkage "
                f"cannot be printed and the pack will not imply one."
            )
        out.append(
            Signature(
                id=int(row["id"]),
                decision=str(row["decision"]),
                corrected_level=None
                if row["corrected_level"] is None
                else str(row["corrected_level"]),
                reason=None if row["reason"] is None else str(row["reason"]),
                decided_at=_require_iso(row["decided_at"], "adjudications.decided_at"),
                officer_username=str(row["username"]),
                officer_name=str(row["full_name"]),
                officer_role=str(row["role"]),
                prev_hash=_hex(row["prev_hash"]),
                row_hash=row_hash,
            )
        )
    return tuple(out)


def load_pack(
    session: Session, claim_id: int, *, generated_for: str, generated_at: str
) -> EvidencePack:
    """Read everything the Evidence Pack prints, or refuse with a reason.

    Raises `PackUnavailable` when the claim has no verdict, or the newest
    verdict has no renderable lineage. Does not check jurisdiction: that belongs
    to `api.scope.require_claim_visible` and is called before this, so no caller
    can reach a pack it may not see by forgetting an argument here.

    `generated_at` is passed in rather than read from the clock so the HTML and
    the PDF of one request carry the same timestamp — two clock reads would make
    the two representations of one export disagree by milliseconds, and a
    document is not the place to explain that.
    """
    row = session.execute(_CLAIM, {"claim_id": claim_id}).mappings().first()
    if row is None:  # pragma: no cover - require_claim_visible raises 404 first
        raise PackUnavailable(f"claim {claim_id} does not exist")

    try:
        verdict = load_verdict(session, latest_verdict_id(session, claim_id))
    except VerdictNotFound as exc:
        # 409, not 404: the claim is real and visible. Reconciliation has not
        # run, so there is no assessment to export — a different answer from
        # "no such claim", and an officer needs to be able to tell them apart.
        raise PackUnavailable(
            f"claim {claim_id} has no verdict, so there is no assessment to export. "
            f"Reconciliation has not run for this claim."
        ) from exc

    # Validated before anything is rendered, so a 409 is decided by the record
    # rather than surfacing halfway down a half-built document.
    _lineage_bundle(verdict)

    computed_at = session.execute(_COMPUTED_AT, {"verdict_id": verdict.id}).scalar_one()

    claim = ClaimFacts(
        claim_id=int(row["claim_id"]),
        unique_id=str(row["unique_id"]),
        intervention_type=str(row["intervention_type"]),
        district_lgd=str(row["district_lgd"]),
        asserted_status=str(row["asserted_status"]),
        asserted_date=_require_iso(row["asserted_date"], "claims.asserted_date"),
        work_status=str(row["work_status"]),
        completed_date=_iso(row["completed_date"]),
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        uncertainty_m=None if row["uncertainty_m"] is None else float(row["uncertainty_m"]),
        detectability=None if row["detectability"] is None else str(row["detectability"]),
        expected_footprint_m2=None
        if row["expected_footprint_m2"] is None
        else float(row["expected_footprint_m2"]),
        village_lgd=None if row["village_lgd"] is None else str(row["village_lgd"]),
        survey_no=None if row["survey_no"] is None else str(row["survey_no"]),
        project_code=str(row["project_code"]),
        project_name=str(row["project_name"]),
        created_at=_require_iso(row["created_at"], "claims.created_at"),
    )

    return EvidencePack(
        claim=claim,
        verdict=verdict,
        label=label_for(Level(verdict.level), verdict.score, EngineConfig()),
        computed_at=_require_iso(computed_at, "verdicts.computed_at"),
        families=_families(session, claim_id),
        signatures=_signatures(session, verdict.id),
        scenes=_load_scenes(session, _scene_ids(verdict)),
        generated_at=generated_at,
        generated_for=generated_for,
    )


def lineage_bundle(pack: EvidencePack) -> dict[str, Any]:
    """The pack's canonical engine input. Validated at load time.

    Re-derived here rather than stored on the dataclass so there is exactly one
    place that knows where in the lineage the bundle lives, and the renderers
    cannot read a stale copy.
    """
    return _lineage_bundle(pack.verdict)
