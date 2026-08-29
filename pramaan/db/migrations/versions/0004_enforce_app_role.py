"""Make the append-only ledger control actually take effect.

## The defect this fixes

Migration 0001 does the right thing on paper:

    GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO pramaan_app;
    REVOKE UPDATE, DELETE ON adjudications FROM pramaan_app;

and the deck says the ledger is append-only. Measured against the running
system, it was not:

1. The application connects as `pramaan`, which **owns** the tables and
   therefore holds UPDATE and DELETE on `adjudications` regardless of what is
   revoked from `pramaan_app`. The revoke applied to a role nothing used.
2. `pramaan_app` held no privilege on **any sequence** — 0 of 34 — so it could
   not have executed a single `BIGSERIAL` insert had it been used.
3. `refresh_tokens`, added in 0003, arrived after 0001's one-shot
   `ON ALL TABLES` grant and so had no grants at all.

Point 1 is the serious one: a control that is defined but not in force is worse
than an absent one, because it gets quoted as if it were real.

## What this migration does

- Grants sequence usage, without which the app role is decorative.
- Grants `refresh_tokens` fully, including DELETE: session state is not
  evidence, and expired rows must be prunable. The asymmetry with
  `adjudications` is the whole point.
- Re-asserts the `adjudications` revoke after the blanket grant, so ordering
  cannot silently re-open it.
- Sets **default privileges**, so a future table cannot repeat defect 3. This
  is the durable half of the fix.

Privilege *dropping* happens at connect time in `app/db/session.py`; see the
note there for why `SET ROLE` rather than a second set of credentials.

Revision ID: 0004_enforce_app_role
Revises: 0003_auth_sessions
"""

from __future__ import annotations

from alembic import op

revision = "0004_enforce_app_role"
down_revision = "0003_auth_sessions"
branch_labels = None
depends_on = None


UPGRADE = """
-- Sequences: required for every BIGSERIAL insert.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pramaan_app;

-- Tables added after 0001's one-shot grant.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO pramaan_app;

-- Session state, not evidence: prunable on purpose.
GRANT DELETE ON refresh_tokens TO pramaan_app;

-- Re-assert after the blanket grant above, which would otherwise restore
-- UPDATE on the ledger. Order matters and this line is why.
REVOKE UPDATE, DELETE ON adjudications FROM pramaan_app;

-- The durable fix: any table or sequence created later is granted
-- automatically, so "new table has no grants" cannot recur.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE ON TABLES TO pramaan_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO pramaan_app;
"""

DOWNGRADE = """
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE SELECT, INSERT, UPDATE ON TABLES FROM pramaan_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE USAGE, SELECT ON SEQUENCES FROM pramaan_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM pramaan_app;
REVOKE DELETE ON refresh_tokens FROM pramaan_app;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
