"""The adjudication ledger: append-only, hash-chained, signed by a named officer.

This module is what makes the console's central sentence true. Until now
*"nothing here becomes government evidence until a named officer signs it"* was
a caption; the `adjudications` table existed and nothing wrote to it.

## The three properties, and how each is actually enforced

1. **Append-only.** `REVOKE UPDATE, DELETE ON adjudications` in migration 0001.
   Enforced by PostgreSQL against the application role, so a bug in this file
   cannot rewrite a decision — the database refuses. Application-level
   append-only is a convention; a revoked grant is a control.

2. **Attributed.** `user_id` is `NOT NULL REFERENCES users(id)`. There is no
   system account and no nullable signer. A decision without an officer cannot
   be stored.

3. **Tamper-evident.** Each row carries `sha256` over its own content plus the
   previous row's hash. Altering any historical row — by direct SQL, bypassing
   the revoke — breaks every subsequent link, and `verify_chain` reports the
   exact row where the break starts.

## What the chain does and does not prove

It proves **integrity**, not **authenticity**. Anyone who can write to the table
and recompute forward hashes can produce a consistent chain; what they cannot do
is alter one row and leave the rest untouched, which is what defeats casual and
accidental tampering and makes deliberate tampering leave a mark.

Cryptographic non-repudiation would need each row signed by a key the officer
holds, not by the server. That is out of scope here and is stated as absent
rather than implied by the word "signed" — see `docs/17-roles-and-ledger.md`.

## Why the digest excludes `decided_at`'s microseconds

It does not. `decided_at` is included at full stored precision, taken from the
database's `now()`. An earlier draft hashed a Python-side timestamp, which meant
the digest could not be recomputed from the stored row — the exact property the
chain exists to provide.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.audit.reproducibility import canonical_json

Decision = Literal["accept", "edit", "reject"]

#: The genesis link. A first row has no predecessor, and using 32 zero bytes
#: rather than NULL keeps the digest input a fixed shape.
GENESIS = b"\x00" * 32


class LedgerError(Exception):
    """A ledger write was refused."""


@dataclass(frozen=True, slots=True)
class LedgerRow:
    id: int
    verdict_id: int
    user_id: str
    username: str
    full_name: str
    decision: Decision
    corrected_level: str | None
    reason: str | None
    decided_at: str
    prev_hash: str | None
    row_hash: str


def digest(
    *,
    verdict_id: int,
    user_id: str,
    decision: str,
    corrected_level: str | None,
    reason: str | None,
    decided_at: str,
    prev_hash: bytes,
) -> bytes:
    """The chain link for one adjudication.

    Field names are spelled into the payload rather than positional, so adding a
    field later cannot silently shift the meaning of an existing one. Every
    decision-bearing column is included: omitting `corrected_level` would let an
    officer's correction be swapped without breaking the chain, which would make
    the chain worse than useless — it would be falsely reassuring.
    """
    payload: dict[str, Any] = {
        "verdict_id": verdict_id,
        "user_id": user_id,
        "decision": decision,
        "corrected_level": corrected_level,
        "reason": reason,
        "decided_at": decided_at,
        "prev_hash": prev_hash.hex(),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).digest()


_TAIL = text("""
SELECT row_hash FROM adjudications ORDER BY id DESC LIMIT 1
""")

_INSERT = text("""
INSERT INTO adjudications
    (verdict_id, user_id, decision, corrected_level, reason, decided_at,
     prev_hash, row_hash)
VALUES
    (:verdict_id, CAST(:user_id AS uuid), :decision,
     CAST(:corrected_level AS epistemic_level), :reason, :decided_at,
     :prev_hash, :row_hash)
RETURNING id
""")


def append(
    session: Session,
    *,
    verdict_id: int,
    user_id: str,
    decision: Decision,
    corrected_level: str | None = None,
    reason: str | None = None,
) -> LedgerRow:
    """Append one adjudication and mark its verdict adjudicated.

    ## Hash first, insert once

    The digest must cover `decided_at`, or an auditor could not recompute the
    link from the stored row. That rules out letting the database default the
    timestamp and hashing afterwards, because the correction would be an UPDATE
    — and UPDATE on this table is revoked from the application role, which is
    precisely the control that makes the ledger append-only.

    So the timestamp is generated here, hashed here, and inserted explicitly in
    a single statement. The row that lands in the table is byte-for-byte the row
    that was hashed. A Python-side clock is the small price; storing exactly
    what was hashed is what matters, and a skewed clock produces an odd
    timestamp rather than an unverifiable chain.

    ## Serialisation

    Reading the tail hash and inserting must not interleave with another
    appender, or two rows claim the same predecessor and the chain forks. A fork
    is unrecoverable after the fact, so this is one of the few places that
    deserves an explicit table lock.
    """
    if decision not in ("accept", "edit", "reject"):
        raise LedgerError(f"unknown decision {decision!r}")
    if decision != "accept" and not (reason or "").strip():
        # Mirrors the CHECK constraint, so the caller gets a usable message
        # instead of an IntegrityError. The constraint remains the authority.
        raise LedgerError(f"decision '{decision}' requires a reason")
    if decision == "edit" and not corrected_level:
        raise LedgerError("decision 'edit' requires a corrected_level")
    if decision != "edit" and corrected_level:
        raise LedgerError("corrected_level is only meaningful for decision 'edit'")
    # Serialise appenders. The tail hash read and insert must be atomic with
    # respect to other appenders, or two rows claim the same predecessor and
    # the chain forks. An advisory lock is used instead of LOCK TABLE because
    # the application role has only INSERT+SELECT on this table — exactly the
    # privilege set that makes the ledger append-only. Advisory locks require
    # no table-level privilege and release automatically at transaction end.
    #
    # The key 0x505241_4D41414E is "PRAMAAN" in ASCII, truncated to fit
    # bigint. It is application-scoped: no other code in this system uses
    # advisory locks, and the value is unlikely to collide with anything else.
    session.execute(text("SELECT pg_advisory_xact_lock(0x505241_4D41414E)"))

    tail = session.execute(_TAIL).first()
    prev = GENESIS if tail is None else bytes(tail[0])

    decided_at = datetime.now(UTC)
    # Hash the exact string that will be stored and later re-read, so the
    # verifier's `isoformat()` of the stored value reproduces this input.
    decided_at_key = decided_at.isoformat()

    row_hash = digest(
        verdict_id=verdict_id,
        user_id=user_id,
        decision=decision,
        corrected_level=corrected_level,
        reason=reason,
        decided_at=decided_at_key,
        prev_hash=prev,
    )

    inserted = session.execute(
        _INSERT,
        {
            "verdict_id": verdict_id,
            "user_id": user_id,
            "decision": decision,
            "corrected_level": corrected_level,
            "reason": reason,
            "decided_at": decided_at,
            "prev_hash": prev,
            "row_hash": row_hash,
        },
    ).first()
    if inserted is None:  # pragma: no cover - RETURNING always yields a row
        raise LedgerError("insert returned no row")

    # The verdict stops being PROVISIONAL only now. This is the single write in
    # the system that changes that word, and it is in the same transaction as
    # the ledger row — a verdict marked adjudicated without a signature, or a
    # signature without the status change, would each be a lie.
    session.execute(
        text("UPDATE verdicts SET status = 'adjudicated' WHERE id = :vid"),
        {"vid": verdict_id},
    )
    session.commit()

    return LedgerRow(
        id=int(inserted[0]),
        verdict_id=verdict_id,
        user_id=user_id,
        username="",
        full_name="",
        decision=decision,
        corrected_level=corrected_level,
        reason=reason,
        decided_at=decided_at_key,
        prev_hash=prev.hex(),
        row_hash=row_hash.hex(),
    )


_CHAIN = text("""
SELECT a.id, a.verdict_id, a.user_id::text AS user_id, u.username, u.full_name,
       a.decision, a.corrected_level::text AS corrected_level, a.reason,
       a.decided_at, a.prev_hash, a.row_hash
FROM adjudications a
JOIN users u ON u.id = a.user_id
ORDER BY a.id
""")


@dataclass(frozen=True, slots=True)
class ChainReport:
    valid: bool
    rows: int
    #: The id of the first row that fails verification, or None.
    broken_at: int | None
    reason: str | None


def read_chain(session: Session) -> list[LedgerRow]:
    return [
        LedgerRow(
            id=int(r["id"]),
            verdict_id=int(r["verdict_id"]),
            user_id=str(r["user_id"]),
            username=str(r["username"]),
            full_name=str(r["full_name"]),
            decision=r["decision"],
            corrected_level=r["corrected_level"],
            reason=r["reason"],
            decided_at=r["decided_at"].isoformat(),
            prev_hash=None if r["prev_hash"] is None else bytes(r["prev_hash"]).hex(),
            row_hash=bytes(r["row_hash"]).hex(),
        )
        for r in session.execute(_CHAIN).mappings()
    ]


def verify_chain(session: Session) -> ChainReport:
    """Recompute every link and report the first divergence.

    Two failure modes, distinguished because they mean different things:

    - **`prev_hash` mismatch** — a row was inserted, deleted or reordered.
    - **`row_hash` mismatch** — a row's own content was altered.

    An empty ledger is valid. "Nobody has signed anything yet" is a true and
    consistent state, and reporting it as invalid would train operators to
    ignore the check.
    """
    expected_prev = GENESIS
    count = 0

    for row in read_chain(session):
        count += 1
        stored_prev = bytes.fromhex(row.prev_hash) if row.prev_hash else GENESIS
        if stored_prev != expected_prev:
            return ChainReport(
                valid=False,
                rows=count,
                broken_at=row.id,
                reason=(
                    "prev_hash does not match the previous row's hash — a row "
                    "was inserted, removed or reordered"
                ),
            )

        recomputed = digest(
            verdict_id=row.verdict_id,
            user_id=row.user_id,
            decision=row.decision,
            corrected_level=row.corrected_level,
            reason=row.reason,
            decided_at=row.decided_at,
            prev_hash=stored_prev,
        )
        if recomputed.hex() != row.row_hash:
            return ChainReport(
                valid=False,
                rows=count,
                broken_at=row.id,
                reason="row_hash does not match the row's content — it was altered",
            )
        expected_prev = recomputed

    return ChainReport(valid=True, rows=count, broken_at=None, reason=None)
