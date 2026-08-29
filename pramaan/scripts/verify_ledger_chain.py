#!/usr/bin/env python3
"""Verify the adjudication ledger hash chain.

This is the offline sibling of ``GET /api/v1/ledger/verify``. It connects
directly to the database (no API token needed) and prints the result. It
exists so a sysadmin or auditor can verify chain integrity outside the
application, which is the definition the design document uses: the chain is
not just checkable from the UI, it is checkable from `psql`.

Usage::

    DATABASE_URL=postgresql+psycopg://... python scripts/verify_ledger_chain.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.audit.ledger import (  # noqa: E402
    GENESIS,
    digest,
)

CHAIN_SQL = text("""
SELECT id, verdict_id, user_id::text AS user_id, decision,
       corrected_level::text AS corrected_level, reason,
       decided_at, prev_hash, row_hash
FROM adjudications
ORDER BY id
""")


def main() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = list(conn.execute(CHAIN_SQL).mappings())

    if not rows:
        print("Ledger is empty — nothing to verify.")
        return

    expected_prev = GENESIS
    for r in rows:
        recomputed = digest(
            verdict_id=r["verdict_id"],
            user_id=r["user_id"],
            decision=r["decision"],
            corrected_level=r["corrected_level"],
            reason=r["reason"],
            decided_at=r["decided_at"].isoformat(),
            prev_hash=expected_prev,
        )
        stored_prev = bytes(r["prev_hash"])
        stored_hash = bytes(r["row_hash"])

        if stored_prev != expected_prev:
            print(f"BROKEN at row {r['id']}: prev_hash mismatch")
            sys.exit(1)
        if stored_hash != recomputed:
            print(f"BROKEN at row {r['id']}: row_hash mismatch (content altered)")
            sys.exit(1)
        expected_prev = recomputed

    print(f"VALID — {len(rows)} adjudication(s), chain intact.")


if __name__ == "__main__":
    main()
