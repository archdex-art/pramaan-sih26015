"""Verdict and evidence row access.

The pure half of persistence lives in `app.services.audit.persistence`: it turns
a `Verdict` + `EvidenceBundle` into row dicts and rebuilds a bundle from a stored
lineage, with no database anywhere near it. This module is the impure half — it
executes SQL and nothing else.

Keeping the split means the reproducibility guarantee (docs §21.3) is tested as
arithmetic in `tests/unit/test_persistence.py` and as storage behaviour in
`tests/integration/test_recompute_postgres.py`, rather than only end-to-end
where a digest match could come from either half.

## Versioning

`UNIQUE (claim_id, version)` means a re-adjudication appends rather than
overwrites. `next_version` reads the current maximum, so a verdict recomputed
after an engine change is stored beside its predecessor and the ledger keeps a
readable history. Superseding is a status change, never a delete.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.audit import evidence_rows, verdict_row
from app.services.reconcile import EngineConfig, EvidenceBundle, Verdict

_INSERT_VERDICT = text("""
INSERT INTO verdicts (claim_id, version, level, rule_path, score, confidence,
    coverage, quality, data_sufficiency, dissent, recommended_action,
    engine_version, weights, status, lineage, bundle_digest, verdict_digest)
VALUES (:claim_id, :version, :level, :rule_path, :score, :confidence,
    :coverage, :quality, :data_sufficiency, CAST(:dissent AS jsonb),
    CAST(:recommended_action AS jsonb), :engine_version, CAST(:weights AS jsonb),
    :status, CAST(:lineage AS jsonb), :bundle_digest, :verdict_digest)
RETURNING id
""")

# `evidence` carries UNIQUE (claim_id, family, district_lgd), so there is at
# most one row per family per claim — deliberately. Evidence is the **current**
# state, queryable for the UI's evidence tree; the versioned history lives in
# each verdict's immutable, digest-covered `lineage`. Versioning evidence too
# would create a second historical record that could disagree with the lineage,
# and there would be no way to tell which one an auditor should believe.
#
# So a re-run updates in place. Found by an integration test attempting a second
# reconciliation of the same claim: the schema had already made this decision
# and the first draft of this insert violated it.
_INSERT_EVIDENCE = text("""
INSERT INTO evidence (claim_id, district_lgd, family, agreement, available,
    payload, lineage)
VALUES (:claim_id, :district_lgd, CAST(:family AS evidence_family), :agreement,
    :available, CAST(:payload AS jsonb), CAST(:lineage AS jsonb))
ON CONFLICT (claim_id, family, district_lgd) DO UPDATE SET
    agreement = EXCLUDED.agreement,
    available = EXCLUDED.available,
    payload = EXCLUDED.payload,
    lineage = EXCLUDED.lineage,
    computed_at = now()
""")

_SELECT_VERDICT = text("""
SELECT id, claim_id, version, level, rule_path, score, confidence, coverage,
       quality, data_sufficiency, dissent, recommended_action, engine_version,
       weights, status, lineage, bundle_digest, verdict_digest, computed_at
FROM verdicts WHERE id = :id
""")

_SELECT_LATEST = text("""
SELECT id FROM verdicts WHERE claim_id = :claim_id
ORDER BY version DESC LIMIT 1
""")

_NEXT_VERSION = text("""
SELECT COALESCE(MAX(version), 0) + 1 FROM verdicts WHERE claim_id = :claim_id
""")

_CLAIM_DISTRICT = text("SELECT district_lgd FROM claims WHERE id = :id")

_SUPERSEDE = text("""
UPDATE verdicts SET status = 'superseded'
WHERE claim_id = :claim_id AND version < :version AND status <> 'adjudicated'
""")


class ClaimNotFound(LookupError):
    """The claim does not exist, so `district_lgd` cannot be resolved.

    Distinct from an empty evidence set: a missing claim is a caller error,
    while a claim with no evidence is a legitimate L0 verdict.
    """


class VerdictNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class StoredVerdict:
    """A verdict row, with the JSONB columns already decoded."""

    id: int
    claim_id: int
    version: int
    level: str
    rule_path: tuple[str, ...]
    score: float
    confidence: float
    coverage: float
    quality: float | None
    data_sufficiency: float
    dissent: tuple[str, ...]
    recommended_action: dict[str, Any]
    engine_version: str
    weights: dict[str, float]
    status: str
    lineage: dict[str, Any]
    bundle_digest: str | None
    verdict_digest: str | None


def _as_json(value: object) -> str:
    """psycopg adapts dicts to jsonb, but only for a `dict` — a tuple of dissent
    strings is not one. Serialising here keeps every JSONB column on one path."""
    return json.dumps(value)


def district_for_claim(session: Session, claim_id: int) -> str:
    row = session.execute(_CLAIM_DISTRICT, {"id": claim_id}).first()
    if row is None:
        raise ClaimNotFound(f"claim {claim_id} does not exist")
    return str(row[0])


def next_version(session: Session, claim_id: int) -> int:
    return int(session.execute(_NEXT_VERSION, {"claim_id": claim_id}).scalar_one())


def save_verdict(
    session: Session,
    verdict: Verdict,
    bundle: EvidenceBundle,
    *,
    claim_id: int,
    cfg: EngineConfig | None = None,
    extra_lineage: dict[str, Any] | None = None,
    version: int | None = None,
    supersede_earlier: bool = True,
) -> int:
    """Write one verdict and its six evidence rows. Returns the verdict id.

    Does not commit: the caller owns the transaction boundary, so a verdict and
    its evidence rows can never be half-written.
    """
    district_lgd = district_for_claim(session, claim_id)
    resolved = next_version(session, claim_id) if version is None else version

    row = verdict_row(
        verdict,
        bundle,
        claim_id=claim_id,
        version=resolved,
        cfg=cfg,
        extra_lineage=extra_lineage,
    )
    params = dict(row)
    for key in ("dissent", "recommended_action", "weights", "lineage"):
        params[key] = _as_json(params[key])
    verdict_id = int(session.execute(_INSERT_VERDICT, params).scalar_one())

    for evidence in evidence_rows(bundle, claim_id=claim_id, district_lgd=district_lgd):
        payload = dict(evidence)
        payload["payload"] = _as_json(payload["payload"])
        payload["lineage"] = _as_json(payload["lineage"])
        session.execute(_INSERT_EVIDENCE, payload)

    if supersede_earlier and resolved > 1:
        # An adjudicated verdict is never silently superseded: a named officer
        # signed it, and overwriting that status would erase the signature.
        session.execute(_SUPERSEDE, {"claim_id": claim_id, "version": resolved})

    return verdict_id


def load_verdict(session: Session, verdict_id: int) -> StoredVerdict:
    row = session.execute(_SELECT_VERDICT, {"id": verdict_id}).mappings().first()
    if row is None:
        raise VerdictNotFound(f"verdict {verdict_id} does not exist")
    return StoredVerdict(
        id=int(row["id"]),
        claim_id=int(row["claim_id"]),
        version=int(row["version"]),
        level=str(row["level"]),
        rule_path=tuple(row["rule_path"] or ()),
        score=float(row["score"]),
        confidence=float(row["confidence"]),
        coverage=float(row["coverage"]),
        quality=None if row["quality"] is None else float(row["quality"]),
        data_sufficiency=float(row["data_sufficiency"]),
        dissent=tuple(row["dissent"] or ()),
        recommended_action=dict(row["recommended_action"] or {}),
        engine_version=str(row["engine_version"]),
        weights={k: float(v) for k, v in (row["weights"] or {}).items()},
        status=str(row["status"]),
        lineage=dict(row["lineage"] or {}),
        bundle_digest=row["bundle_digest"],
        verdict_digest=row["verdict_digest"],
    )


def latest_verdict_id(session: Session, claim_id: int) -> int:
    row = session.execute(_SELECT_LATEST, {"claim_id": claim_id}).first()
    if row is None:
        raise VerdictNotFound(f"claim {claim_id} has no verdict")
    return int(row[0])
