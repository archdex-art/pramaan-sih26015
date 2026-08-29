"""Constrain the column that decides whether something is government evidence.

`verdicts.status` was `TEXT NOT NULL DEFAULT 'pending'` with no constraint, so
any string at all was storable. Three values are meaningful:

* `pending` — recorded, unsigned. The default, and what the API renders as
  PROVISIONAL.
* `adjudicated` — a named officer signed it. Written only by
  `app.services.audit.ledger.append`, in the same transaction as the ledger row.
* `superseded` — a later version replaced it. Written by
  `app.db.verdicts`, which deliberately excludes signed rows.

## Why an unconstrained column is a real hazard here, not a tidiness complaint

Reads fail safe. `app/api/v1/claims.py` computes
`provisional = status != 'adjudicated'`, so any unexpected value renders as
PROVISIONAL, which is the cautious direction.

Writes do not. `app/db/verdicts.py` supersedes older versions with
`WHERE ... AND status <> 'adjudicated'`, and its own comment states the rule
that matters: *an adjudicated verdict is never silently superseded, because a
named officer signed it.* A status of `'Adjudicated'`, `'adjudicated '` or
`'adjucated'` satisfies `<> 'adjudicated'` and would let exactly that happen —
silently, with no error anywhere, and the signature left pointing at a
superseded row.

A CHECK constraint makes the three words the only three words. It removes a
class of silent failure that no amount of application-side care can rule out,
because the next writer of a status update is not obliged to read either
comment.

The constraint earned its place immediately: it was first written admitting
only `pending` and `adjudicated`, and the integration suite failed with a real
`superseded` row. An unconstrained column had left the actual vocabulary
undocumented and unenforced, which is the whole argument for constraining it.

## Why CHECK and not an ENUM

The other status columns with a fixed vocabulary use ENUM (`work_status`,
`epistemic_level`). This one uses CHECK because adding a value to a Postgres
ENUM cannot be done inside a transaction that also uses it, which makes ENUM
extension awkward in a reversible migration. A verdict lifecycle is likelier to
gain a state (`withdrawn`) than an epistemic ladder is, and CHECK keeps that
change to a one-line constraint swap.

Revision ID: 0005_verdict_status_domain
Revises: 0004_enforce_app_role
"""

from __future__ import annotations

from alembic import op

revision = "0005_verdict_status_domain"
down_revision = "0004_enforce_app_role"
branch_labels = None
depends_on = None


UPGRADE = """
-- Normalise anything stored OUTSIDE the vocabulary before constraining, so this
-- migration cannot fail on a database that predates it.
--
-- The predicate is an explicit NOT IN over all three words, not `<>
-- 'adjudicated'`. The first draft used the latter and would have rewritten
-- every legitimate 'superseded' row to 'pending' - resurrecting superseded
-- verdicts as live ones, which is a far worse outcome than the loose column it
-- was meant to fix.
UPDATE verdicts SET status = 'pending'
WHERE status NOT IN ('pending', 'adjudicated', 'superseded');

ALTER TABLE verdicts
  ADD CONSTRAINT verdict_status_vocabulary
  CHECK (status IN ('pending', 'adjudicated', 'superseded'));
"""

DOWNGRADE = """
ALTER TABLE verdicts DROP CONSTRAINT verdict_status_vocabulary;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    # The UPDATE is deliberately not reversed: the rows it touched held values
    # every reader already treated as "not signed", so restoring them would
    # recreate the hazard without recovering any information.
    op.execute(DOWNGRADE)
