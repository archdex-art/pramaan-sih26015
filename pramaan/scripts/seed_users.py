#!/usr/bin/env python3
"""Seed one account per government role, across two districts.

## Why two districts and not one

A single district cannot demonstrate jurisdiction scoping. The whole claim —
*"a WCDC user cannot construct a request that returns another district's data"*
(docs §25.1) — is unfalsifiable with one district in the database. So there are
two WCDC officers in different districts, and the negative test asks each for
the other's claims.

## Passwords

Deterministic and printed, because these are demo accounts on a prototype and a
reviewer needs to log in. They are still real Argon2id hashes against the real
login path — nothing here bypasses authentication.

The one thing this script will not do is create an account whose password is
weaker than policy: `hash_password` raises, and that is the correct behaviour
even for a seed.

    make seed-users
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.authz import CAPABILITIES, WORKSPACE, Capability, Role  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402

#: The district this build has real data for. `terrain.json` and the demo claim
#: are both in 520 (Nanded); 522 exists to make cross-district denial testable.
HOME_DISTRICT = "520"
OTHER_DISTRICT = "522"

#: (username, full name, role, scope_state, scope_district)
ACCOUNTS: tuple[tuple[str, str, Role, str | None, str | None], ...] = (
    ("admin.dolr", "A. Sharma (DoLR)", Role.DOLR_ADMIN, None, None),
    ("slna.mh", "S. Patil (SLNA Maharashtra)", Role.SLNA, "Maharashtra", None),
    ("wcdc.nanded", "R. Kumar (WCDC Nanded)", Role.WCDC, "Maharashtra", HOME_DISTRICT),
    ("wcdc.latur", "M. Joshi (WCDC Latur)", Role.WCDC, "Maharashtra", OTHER_DISTRICT),
    ("pia.nanded", "P. Deshmukh (PIA Nanded)", Role.PIA, "Maharashtra", HOME_DISTRICT),
    ("wdt.nanded", "V. Rathod (WDT Nanded)", Role.WDT, "Maharashtra", HOME_DISTRICT),
    ("audit.cag", "Observer (Audit)", Role.READONLY, "Maharashtra", HOME_DISTRICT),
)

#: Long enough to satisfy MIN_PASSWORD_LENGTH without being memorable-only.
PASSWORD = "pramaan-demo-2026"


def main() -> int:
    engine = create_engine(get_settings().database_url, future=True)
    # One hash for every account: Argon2id at 64 MiB is ~100 ms per call, and
    # hashing the same password seven times buys nothing.
    shared_hash = hash_password(PASSWORD)

    with engine.begin() as conn:
        for username, full_name, role, state, district in ACCOUNTS:
            conn.execute(
                text("""
                INSERT INTO users
                    (username, full_name, password_hash, role, scope_state,
                     scope_district)
                VALUES
                    (:username, :full_name, :password_hash, CAST(:role AS user_role),
                     :state, :district)
                ON CONFLICT (username) DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    password_hash = EXCLUDED.password_hash,
                    role = EXCLUDED.role,
                    scope_state = EXCLUDED.scope_state,
                    scope_district = EXCLUDED.scope_district,
                    is_active = TRUE,
                    failed_attempts = 0,
                    locked_until = NULL
                """),
                {
                    "username": username,
                    "full_name": full_name,
                    "password_hash": shared_hash,
                    "role": str(role),
                    "state": state,
                    "district": district,
                },
            )

    print(f"seeded {len(ACCOUNTS)} accounts · password for all: {PASSWORD}\n")
    width = max(len(u) for u, *_ in ACCOUNTS)
    for username, _, role, _, district in ACCOUNTS:
        caps = CAPABILITIES[role]
        marks = "".join(
            (
                "S" if Capability.CLAIM_CREATE in caps else "-",
                "A" if Capability.ADJUDICATION_CREATE in caps else "-",
                "U" if Capability.USER_MANAGE in caps else "-",
            )
        )
        scope = district or ("national" if role is Role.DOLR_ADMIN else "state-only")
        print(
            f"  {username:<{width}}  {str(role):<11}  {WORKSPACE[role]:<14} {scope:<11} [{marks}]"
        )
    print("\n  [S]ubmit claims · [A]djudicate · manage [U]sers")
    print("  No account has both S and A — that is the separation of duties.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
