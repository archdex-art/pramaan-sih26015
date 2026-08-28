"""Add the columns the reproducibility guarantee needs.

Revision ID: 0002_verdict_lineage
Revises: 0001_baseline

docs §21.3 promises that "a verdict can be recomputed byte-identically from its
lineage record" and adds "it costs one JSONB column". The DDL in §22.2 does not
contain that column — `verdicts` stores `dissent`, `recommended_action`,
`engine_version` and `weights`, but nothing from which the engine's *input* could
be rebuilt. So the guarantee the document calls "the single property that makes
the output usable as government evidence" had nowhere to live.

This migration adds it, plus three related omissions:

`lineage JSONB`
    The complete canonical engine input: families with their agreements and
    availability, gates, quality terms, alternatives, the analysis grid, and
    every producer's provenance. `/verdicts/{id}/recompute` rebuilds an
    EvidenceBundle from this and re-runs the engine.

`quality NUMERIC(5,4)`
    The engine computes `quality = metadata_integrity * data_sufficiency` and
    uses it directly in `confidence`. Only `data_sufficiency` was stored, so the
    published formula could not be verified against a stored row — an auditor
    recomputing `confidence` by hand would find a term missing.

`bundle_digest` / `verdict_digest CHAR(64)`
    SHA-256 over the canonical input and the decision fields. Storing them makes
    a recompute a comparison rather than a re-derivation, and makes an
    unchanged-input/changed-output situation detectable at query time rather
    than only when somebody happens to re-run one verdict.

A partial index on `bundle_digest` supports the useful query "has this exact
evidence been adjudicated before", which is how a re-ingested duplicate geotag
gets caught.
"""

from __future__ import annotations

from alembic import op

revision = "0002_verdict_lineage"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

DDL_UP = """
ALTER TABLE verdicts
  ADD COLUMN lineage        JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN quality        NUMERIC(5,4),
  ADD COLUMN bundle_digest  CHAR(64),
  ADD COLUMN verdict_digest CHAR(64);

COMMENT ON COLUMN verdicts.lineage IS
  'Complete canonical engine input: families, gates, quality, alternatives, '
  'analysis grid, producer provenance. A verdict is recomputed from this '
  '(docs §21.3). Never overwritten — a new verdict version is appended.';

COMMENT ON COLUMN verdicts.quality IS
  'metadata_integrity * data_sufficiency, as used in confidence. Stored so the '
  'published formula can be verified against the row.';

COMMENT ON COLUMN verdicts.bundle_digest IS
  'SHA-256 over the canonical engine input. Equal digests must yield equal '
  'verdicts; a mismatch means the engine or its config changed.';

-- Answers "has this exact evidence already been adjudicated?", which is how a
-- re-ingested duplicate geotag is caught. Partial: rows predating this
-- migration have no digest and should not bloat the index.
CREATE INDEX idx_v_bundle_digest ON verdicts (bundle_digest)
  WHERE bundle_digest IS NOT NULL;

-- The invariant the engine guarantees, enforced at the storage boundary too:
-- quality is a product of two [0,1] terms.
ALTER TABLE verdicts
  ADD CONSTRAINT quality_is_unit_range
  CHECK (quality IS NULL OR (quality >= 0 AND quality <= 1));
"""

DDL_DOWN = """
DROP INDEX IF EXISTS idx_v_bundle_digest;
ALTER TABLE verdicts DROP CONSTRAINT IF EXISTS quality_is_unit_range;
ALTER TABLE verdicts
  DROP COLUMN IF EXISTS verdict_digest,
  DROP COLUMN IF EXISTS bundle_digest,
  DROP COLUMN IF EXISTS quality,
  DROP COLUMN IF EXISTS lineage;
"""


def upgrade() -> None:
    op.execute(DDL_UP)


def downgrade() -> None:
    op.execute(DDL_DOWN)
