"""Refresh-token families and login throttling state.

## Why the refresh store is a table and not Redis

Redis is already in the stack and would be the reflexive choice. It is the wrong
one here: reuse detection is a security control, and a control that forgets its
state on restart is a control an attacker can clear by waiting for a deploy. A
replayed refresh token must revoke its family in an hour, a week, or after a
node reboot.

The table is small — one row per issued refresh token, pruned on expiry — and it
gives the property Redis cannot: the family graph is queryable after the fact,
so "when was this session hijacked" is answerable from the database an auditor
already has.

## Why failed logins live on `users`

Counting attempts per account, in the row the account already occupies, keeps
the check and the increment in the same transaction as the password verify.
A separate table would make the read-modify-write racy, and the race is exactly
what a credential-stuffing run exploits.

Revision ID: 0003_auth_sessions
Revises: 0002_verdict_lineage
"""

from __future__ import annotations

from alembic import op

revision = "0003_auth_sessions"
down_revision = "0002_verdict_lineage"
branch_labels = None
depends_on = None


UPGRADE = """
-- ============ REFRESH TOKEN FAMILIES ============
CREATE TABLE refresh_tokens (
  jti         TEXT PRIMARY KEY,
  family      TEXT NOT NULL,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  -- Set the moment a token is exchanged. A second exchange of the same jti is
  -- the definition of replay, and is what triggers family revocation.
  used_at     TIMESTAMPTZ,
  -- Set on every token in a family when replay is detected anywhere in it.
  revoked_at  TIMESTAMPTZ,
  revoked_reason TEXT
);

CREATE INDEX idx_refresh_family ON refresh_tokens (family);
CREATE INDEX idx_refresh_user   ON refresh_tokens (user_id);
-- Supports the expiry sweep without scanning the table.
CREATE INDEX idx_refresh_expiry ON refresh_tokens (expires_at)
  WHERE revoked_at IS NULL;

-- ============ LOGIN THROTTLING ============
ALTER TABLE users
  ADD COLUMN failed_attempts INT NOT NULL DEFAULT 0,
  ADD COLUMN locked_until    TIMESTAMPTZ,
  ADD COLUMN last_login_at   TIMESTAMPTZ;

-- The application must never rewrite history in the ledger. `adjudications`
-- already has UPDATE/DELETE revoked; refresh tokens are different — they are
-- mutable session state, not evidence — so they are deliberately NOT revoked.
-- Stating that here so the asymmetry reads as a decision rather than an
-- oversight.
"""

DOWNGRADE = """
ALTER TABLE users
  DROP COLUMN IF EXISTS failed_attempts,
  DROP COLUMN IF EXISTS locked_until,
  DROP COLUMN IF EXISTS last_login_at;
DROP TABLE IF EXISTS refresh_tokens CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
