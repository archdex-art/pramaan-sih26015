"""Tests for the adjudication ledger's hash chain.

The ledger is the single most security-critical component in the system: the
claim "this output is government evidence" reduces to "a named officer signed it
and the signature cannot be altered afterwards". It was, until this file,
**untested** — 53 % covered, and `make check` red at 98.01 % against a 100 %
gate.

These tests are adversarial on purpose. A hash chain that only gets tested with
well-formed input is falsely reassuring, because its entire job is to detect
input that is *not* well-formed. Every tampering mode the chain claims to detect
is performed here and asserted to be detected:

* a row's content altered in place
* a row deleted from the middle
* two rows swapped
* a row inserted with a forged predecessor
* `corrected_level` swapped without touching anything else

The database half — that `UPDATE` and `DELETE` are actually revoked — is in
`tests/integration/`, because it is a privilege grant rather than arithmetic and
can only be proven against a real server.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.audit.ledger import (  # noqa: E402
    GENESIS,
    ChainReport,
    LedgerError,
    LedgerRow,
    digest,
    verify_chain,
)

NOW = datetime(2026, 8, 29, 9, 14, tzinfo=UTC).isoformat()
USER = "3f8b1c2a-0000-4000-8000-000000000001"


def link(
    *,
    verdict_id: int = 1,
    user_id: str = USER,
    decision: str = "accept",
    corrected_level: str | None = None,
    reason: str | None = None,
    decided_at: str = NOW,
    prev_hash: bytes = GENESIS,
) -> bytes:
    return digest(
        verdict_id=verdict_id,
        user_id=user_id,
        decision=decision,
        corrected_level=corrected_level,
        reason=reason,
        decided_at=decided_at,
        prev_hash=prev_hash,
    )


# --- the digest ----------------------------------------------------------


def test_digest_is_deterministic() -> None:
    assert link() == link()


def test_digest_is_a_full_sha256() -> None:
    assert len(link()) == 32


@pytest.mark.parametrize(
    "field,value",
    [
        ("verdict_id", 2),
        ("user_id", "3f8b1c2a-0000-4000-8000-000000000002"),
        ("decision", "reject"),
        ("corrected_level", "L2_corroborated"),
        ("reason", "not the same reason"),
        ("decided_at", datetime(2026, 8, 29, 9, 15, tzinfo=UTC).isoformat()),
        ("prev_hash", b"\x01" * 32),
    ],
)
def test_every_decision_bearing_field_changes_the_digest(field: str, value: object) -> None:
    """Each field must be covered, or it can be swapped without breaking the
    chain — which would make the chain worse than useless, because it would be
    falsely reassuring.

    `corrected_level` is the one that matters most here: it is the officer's
    actual correction. If it were omitted from the payload, an L2 correction
    could be rewritten as N3 with the chain still verifying.
    """
    assert link() != link(**{field: value})  # type: ignore[arg-type]


def test_a_none_reason_and_an_empty_reason_are_different_links() -> None:
    """`None` means "no reason was required"; "" would mean one was given and
    was blank. Collapsing them would let one be rewritten as the other."""
    assert link(reason=None) != link(reason="")


def test_the_genesis_link_is_thirty_two_zero_bytes() -> None:
    """A fixed shape rather than NULL, so the first row's digest input has the
    same arity as every other row's."""
    assert GENESIS == b"\x00" * 32
    assert len(GENESIS) == 32


# --- write-time refusals -------------------------------------------------


class FakeSession:
    """Records what would have been executed, and never touches a database.

    `append()` refuses malformed input *before* it reads the tail hash, so these
    refusals are provable without a server. If a refusal ever regressed to
    happening after the first `execute`, `calls` would be non-empty and the
    assertions below would catch it.
    """

    def __init__(self) -> None:
        self.calls: list[object] = []

    def execute(self, statement: object, params: object = None) -> object:
        self.calls.append(statement)
        raise AssertionError("append() must refuse before touching the session")

    def commit(self) -> None:  # pragma: no cover - never reached in these tests
        raise AssertionError("append() must refuse before committing")


def append_with(**kwargs: object) -> None:
    from app.services.audit.ledger import append

    session = FakeSession()
    try:
        append(session, **kwargs)  # type: ignore[arg-type]
    finally:
        assert session.calls == [], "refused too late — the session was touched"


def test_an_unknown_decision_is_refused() -> None:
    with pytest.raises(LedgerError, match="unknown decision"):
        append_with(verdict_id=1, user_id=USER, decision="approve")


@pytest.mark.parametrize("decision", ["edit", "reject"])
def test_edit_and_reject_require_a_reason(decision: str) -> None:
    """An unexplained rejection is the failure this system exists to prevent:
    it implicates a beneficiary with no stated basis."""
    with pytest.raises(LedgerError, match="requires a reason"):
        append_with(
            verdict_id=1, user_id=USER, decision=decision, corrected_level="L2_corroborated"
        )


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_whitespace_is_not_a_reason(blank: str) -> None:
    """The database CHECK only tests NOT NULL, so a single space would satisfy
    it. Mirroring the intent rather than the letter is the point."""
    with pytest.raises(LedgerError, match="requires a reason"):
        append_with(verdict_id=1, user_id=USER, decision="reject", reason=blank)


def test_accept_needs_no_reason() -> None:
    """Accepting the engine's own verdict adds no new assertion, so demanding
    prose for it would train officers to type "ok" — which is worse than
    nothing, because it looks like a justification."""
    from app.services.audit.ledger import append

    session = FakeSession()
    with pytest.raises(AssertionError, match="must refuse before touching"):
        append(session, verdict_id=1, user_id=USER, decision="accept")  # type: ignore[arg-type]
    # It got as far as the session, which means validation passed.
    assert session.calls, "accept without a reason should pass validation"


def test_edit_requires_a_corrected_level() -> None:
    with pytest.raises(LedgerError, match="requires a corrected_level"):
        append_with(verdict_id=1, user_id=USER, decision="edit", reason="terrain misread")


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_corrected_level_is_only_meaningful_for_edit(decision: str) -> None:
    """A corrected level on an accept or a reject is incoherent — and it would
    be hashed, so it would become a permanent, signed contradiction."""
    with pytest.raises(LedgerError, match="only meaningful"):
        append_with(
            verdict_id=1,
            user_id=USER,
            decision=decision,
            reason="a reason",
            corrected_level="L2_corroborated",
        )


# --- chain verification --------------------------------------------------


class ChainSession:
    """A session whose `read_chain` result is a fixed list of rows."""

    def __init__(self, rows: list[LedgerRow]) -> None:
        self._rows = rows

    def execute(self, *_: object, **__: object) -> object:  # pragma: no cover
        raise AssertionError("verify_chain must read through read_chain")


def chain(*specs: dict[str, object]) -> list[LedgerRow]:
    """Build a correctly linked chain from partial row specs."""
    rows: list[LedgerRow] = []
    prev = GENESIS
    for i, spec in enumerate(specs, start=1):
        decision = str(spec.get("decision", "accept"))
        reason = spec.get("reason")
        corrected = spec.get("corrected_level")
        verdict_id = int(spec.get("verdict_id", i))
        decided_at = str(spec.get("decided_at", NOW))
        h = digest(
            verdict_id=verdict_id,
            user_id=USER,
            decision=decision,
            corrected_level=None if corrected is None else str(corrected),
            reason=None if reason is None else str(reason),
            decided_at=decided_at,
            prev_hash=prev,
        )
        rows.append(
            LedgerRow(
                id=i,
                verdict_id=verdict_id,
                user_id=USER,
                username="r.kumar",
                full_name="R. Kumar",
                decision=decision,
                corrected_level=None if corrected is None else str(corrected),
                reason=None if reason is None else str(reason),
                decided_at=decided_at,
                prev_hash=prev.hex(),
                row_hash=h.hex(),
            )
        )
        prev = h
    return rows


def report(rows: list[LedgerRow], monkeypatch: pytest.MonkeyPatch) -> ChainReport:
    import app.services.audit.ledger as mod

    monkeypatch.setattr(mod, "read_chain", lambda _session: rows)
    return verify_chain(ChainSession(rows))  # type: ignore[arg-type]


def test_an_empty_ledger_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Nobody has signed anything yet" is a true and consistent state.
    Reporting it as invalid would train operators to ignore the check."""
    r = report([], monkeypatch)
    assert r == ChainReport(valid=True, rows=0, broken_at=None, reason=None)


def test_a_well_formed_chain_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = chain(
        {"decision": "accept"},
        {"decision": "reject", "reason": "no structure visible on the ground"},
        {"decision": "edit", "reason": "terrain misread", "corrected_level": "L2_corroborated"},
    )
    r = report(rows, monkeypatch)
    assert r.valid
    assert r.rows == 3
    assert r.broken_at is None


def test_altering_a_row_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row_hash failure mode: content changed in place."""
    rows = chain({"decision": "accept"}, {"decision": "reject", "reason": "original"})
    tampered = rows[1]
    rows[1] = replace(tampered, reason="rewritten after the fact")

    r = report(rows, monkeypatch)
    assert not r.valid
    assert r.broken_at == 2
    assert r.reason is not None and "altered" in r.reason


def test_swapping_a_corrected_level_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The specific attack `digest()` includes `corrected_level` to prevent:
    rewrite the officer's correction and change nothing else."""
    rows = chain(
        {"decision": "edit", "reason": "terrain misread", "corrected_level": "L2_corroborated"}
    )
    original = rows[0]
    rows[0] = replace(original, corrected_level="N3_contradicted")

    r = report(rows, monkeypatch)
    assert not r.valid
    assert r.broken_at == 1


def test_deleting_a_row_from_the_middle_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prev_hash failure mode: row 3 still names row 2 as its predecessor."""
    rows = chain(
        {"decision": "accept"},
        {"decision": "reject", "reason": "second"},
        {"decision": "reject", "reason": "third"},
    )
    del rows[1]

    r = report(rows, monkeypatch)
    assert not r.valid
    assert r.broken_at == 3
    assert r.reason is not None and "removed or reordered" in r.reason


def test_reordering_rows_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = chain(
        {"decision": "accept"},
        {"decision": "reject", "reason": "second"},
        {"decision": "reject", "reason": "third"},
    )
    rows[1], rows[2] = rows[2], rows[1]

    r = report(rows, monkeypatch)
    assert not r.valid


def test_a_forged_row_appended_to_a_valid_chain_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An attacker who can INSERT but cannot recompute the tail: the forged row
    claims GENESIS as its predecessor."""
    rows = chain({"decision": "accept"})
    forged_hash = digest(
        verdict_id=99,
        user_id=USER,
        decision="accept",
        corrected_level=None,
        reason=None,
        decided_at=NOW,
        prev_hash=GENESIS,
    )
    rows.append(
        LedgerRow(
            id=2,
            verdict_id=99,
            user_id=USER,
            username="attacker",
            full_name="A. Ttacker",
            decision="accept",
            corrected_level=None,
            reason=None,
            decided_at=NOW,
            prev_hash=GENESIS.hex(),
            row_hash=forged_hash.hex(),
        )
    )

    r = report(rows, monkeypatch)
    assert not r.valid
    assert r.broken_at == 2


def test_verification_reports_the_first_break_not_the_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An auditor needs to know where the chain diverged, not merely that it
    did — everything after the first break is downstream of it."""
    rows = chain(
        {"decision": "accept"},
        {"decision": "reject", "reason": "second"},
        {"decision": "reject", "reason": "third"},
        {"decision": "reject", "reason": "fourth"},
    )
    rows[1] = replace(rows[1], reason="tampered")
    rows[3] = replace(rows[3], reason="also tampered")

    r = report(rows, monkeypatch)
    assert not r.valid
    assert r.broken_at == 2, "must name the earliest divergence"


def test_a_missing_prev_hash_reads_as_genesis(monkeypatch: pytest.MonkeyPatch) -> None:
    """`prev_hash` is nullable in the schema. A first row written with NULL must
    verify identically to one written with the genesis bytes, or rows created by
    an older writer would read as tampered."""
    rows = chain({"decision": "accept"})
    rows[0] = replace(rows[0], prev_hash=None)

    assert report(rows, monkeypatch).valid
