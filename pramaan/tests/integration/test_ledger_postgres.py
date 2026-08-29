"""The append-only guarantee, proven against a real PostgreSQL server.

`tests/unit/test_ledger.py` proves the hash arithmetic detects tampering. That
is a strictly weaker claim than the one the pitch makes. "Nothing here becomes
government evidence until a named officer signs it, and the signature cannot be
altered afterwards" reduces to three things that only a real server can settle:

1. `append()` writes a row whose stored bytes reproduce the hash that was
   computed before the insert — through `TIMESTAMPTZ` round-tripping, which is
   where a Python-side clock could silently disagree with what Postgres kept.
2. `UPDATE` and `DELETE` on `adjudications` are actually revoked from the
   application role. This is a privilege grant, not arithmetic; a migration that
   forgot the `REVOKE` would pass every unit test.
3. Signing flips the verdict out of PROVISIONAL *in the same transaction*. A
   verdict marked adjudicated without a signature, or a signature without the
   status change, would each be a lie.

The privilege tests use `SET ROLE pramaan_app`. The role is `NOLOGIN`, so it
cannot be connected to directly, but privilege checks do apply after switching
to a non-superuser role — which is the property under test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.audit.ledger import (  # noqa: E402
    GENESIS,
    LedgerError,
    append,
    digest,
    read_chain,
    verify_chain,
)

# Read from the environment rather than imported from `conftest`: two conftest
# modules exist under `tests/`, and a bare import resolves by `sys.path` order.
DSN = os.environ.get("PRAMAAN_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="set PRAMAAN_TEST_DSN to run database integration tests"
)

OFFICER = "r.kumar.ledger"


def sqlalchemy_url(dsn: str) -> str:
    """`host=... port=...` keyword DSN -> SQLAlchemy URL.

    The ledger service takes a SQLAlchemy `Session`; the fixtures take a psycopg
    DSN. Converting here keeps one source of truth for where the database is.
    """
    parts = dict(kv.split("=", 1) for kv in dsn.split())
    return (
        f"postgresql+psycopg://{parts['user']}:{parts.get('password', '')}"
        f"@{parts['host']}:{parts['port']}/{parts['dbname']}"
    )


@pytest.fixture()
def session(con):  # type: ignore[no-untyped-def]
    """A SQLAlchemy session on the same database as `con`.

    `append()` commits, so its rows cannot be rolled back by the `con` fixture.
    They are deleted explicitly as a superuser at teardown — which is only
    possible *because* the test connects as one, and is itself a reminder that
    the application role cannot do this.
    """
    engine = create_engine(sqlalchemy_url(DSN or ""), future=True)
    s = Session(engine, future=True)
    yield s
    s.rollback()
    s.execute(text("DELETE FROM adjudications"))
    s.execute(text("UPDATE verdicts SET status = 'pending'"))
    s.commit()
    s.close()
    engine.dispose()


@pytest.fixture()
def officer(con):  # type: ignore[no-untyped-def]
    """A named officer. Every ledger row must attribute to a real user row:
    `user_id` is a foreign key precisely so an unattributable signature cannot
    be written."""
    cur = con.cursor()
    cur.execute(
        "INSERT INTO users (username,full_name,password_hash,role) "
        "VALUES (%s,'R. Kumar','x','wcdc') RETURNING id::text",
        (f"{OFFICER}-{id(con)}",),
    )
    uid = cur.fetchone()[0]
    con.commit()  # append() runs in its own transaction and must see this user.
    yield uid
    # The signatures must go before the signatory. `user_id` is a foreign key
    # precisely so an unattributable signature cannot exist, which means the
    # ledger rows this officer wrote have to be cleared first - fixture
    # teardown runs in reverse setup order and will not do it for us.
    cur.execute("DELETE FROM adjudications WHERE user_id = %s::uuid", (uid,))
    cur.execute("UPDATE verdicts SET status = 'pending'")
    cur.execute("DELETE FROM users WHERE id = %s::uuid", (uid,))
    con.commit()


@pytest.fixture()
def signable(con, verdict):  # type: ignore[no-untyped-def]
    """The verdict must be committed for `append()`'s own transaction to see
    it."""
    con.commit()
    return verdict


# --- the write path ------------------------------------------------------


def test_append_writes_a_verifiable_first_row(session, officer, signable):  # type: ignore[no-untyped-def]
    row = append(
        session,
        verdict_id=signable,
        user_id=officer,
        decision="accept",
    )
    assert row.id > 0
    assert row.prev_hash == GENESIS.hex()
    assert verify_chain(session).valid


def test_the_stored_row_reproduces_the_hash_that_was_computed(session, officer, signable):  # type: ignore[no-untyped-def]
    """The reason `append()` generates the timestamp itself rather than letting
    the database default it: the row that lands must be byte-for-byte the row
    that was hashed, and `TIMESTAMPTZ` -> `isoformat()` is where that can break.
    """
    written = append(session, verdict_id=signable, user_id=officer, decision="accept")

    stored = read_chain(session)[-1]
    recomputed = digest(
        verdict_id=stored.verdict_id,
        user_id=stored.user_id,
        decision=stored.decision,
        corrected_level=stored.corrected_level,
        reason=stored.reason,
        decided_at=stored.decided_at,
        prev_hash=GENESIS,
    )
    assert recomputed.hex() == stored.row_hash
    assert stored.row_hash == written.row_hash, "in-memory and stored hash diverged"


def test_read_chain_joins_the_officers_name(session, officer, signable):  # type: ignore[no-untyped-def]
    """An Evidence Pack has to print *who* signed. A user id alone would force
    the report to say "signed by 3f8b1c2a-..." which is not accountability."""
    append(session, verdict_id=signable, user_id=officer, decision="accept")

    stored = read_chain(session)[-1]
    assert stored.full_name == "R. Kumar"
    assert stored.username.startswith(OFFICER)


def test_a_chain_of_three_links_verifies(session, officer, signable):  # type: ignore[no-untyped-def]
    append(session, verdict_id=signable, user_id=officer, decision="accept")
    append(
        session,
        verdict_id=signable,
        user_id=officer,
        decision="reject",
        reason="no structure visible at the coordinate",
    )
    append(
        session,
        verdict_id=signable,
        user_id=officer,
        decision="edit",
        reason="terrain family misread the stream order",
        corrected_level="L2_corroborated",
    )

    report = verify_chain(session)
    assert report.valid
    assert report.rows == 3

    rows = read_chain(session)
    assert rows[1].prev_hash == rows[0].row_hash, "links must actually chain"
    assert rows[2].prev_hash == rows[1].row_hash


def test_signing_lifts_the_verdict_out_of_provisional(session, officer, signable):  # type: ignore[no-untyped-def]
    """The single write in the system that changes that word."""
    before = session.execute(
        text("SELECT status FROM verdicts WHERE id = :v"), {"v": signable}
    ).scalar_one()
    assert before == "pending"

    append(session, verdict_id=signable, user_id=officer, decision="accept")

    after = session.execute(
        text("SELECT status FROM verdicts WHERE id = :v"), {"v": signable}
    ).scalar_one()
    assert after == "adjudicated"


def test_a_refused_write_leaves_no_row_and_no_status_change(session, officer, signable):  # type: ignore[no-untyped-def]
    """Validation happens before any statement executes, so a refusal must not
    half-apply — no ledger row, and the verdict still PROVISIONAL."""
    with pytest.raises(LedgerError, match="requires a reason"):
        append(session, verdict_id=signable, user_id=officer, decision="reject")

    session.rollback()
    assert read_chain(session) == []
    status = session.execute(
        text("SELECT status FROM verdicts WHERE id = :v"), {"v": signable}
    ).scalar_one()
    assert status == "pending"


def test_the_database_check_constraint_is_the_real_authority(session, officer, signable):  # type: ignore[no-untyped-def]
    """`append()` mirrors the `reason_required` CHECK for a usable error message.
    The constraint itself must still be live, or a future writer that skips
    `append()` could store an unexplained rejection."""
    with pytest.raises(Exception) as exc:
        session.execute(
            text(
                "INSERT INTO adjudications "
                "(verdict_id,user_id,decision,reason,prev_hash,row_hash) "
                "VALUES (:v, CAST(:u AS uuid), 'reject', NULL, :p, :h)"
            ),
            {"v": signable, "u": officer, "p": GENESIS, "h": b"\x01" * 32},
        )
    session.rollback()
    assert "reason_required" in str(exc.value)


# --- append-only, enforced by the database ------------------------------


@pytest.fixture()
def as_app_role(session):  # type: ignore[no-untyped-def]
    """Run subsequent statements as `pramaan_app`.

    Privilege checks are skipped for superusers, so asserting the REVOKE
    requires actually becoming the application role. `SET LOCAL` scopes it to
    the transaction, so the rollback in each test restores the superuser.
    """
    session.execute(text("SET LOCAL ROLE pramaan_app"))
    return session


def test_the_app_role_can_insert(session, officer, signable):  # type: ignore[no-untyped-def]
    """The control for the two tests below: if the role could not INSERT either,
    they would pass for the wrong reason."""
    append(session, verdict_id=signable, user_id=officer, decision="accept")
    session.execute(text("SET LOCAL ROLE pramaan_app"))
    session.execute(
        text(
            "INSERT INTO adjudications "
            "(verdict_id,user_id,decision,prev_hash,row_hash) "
            "VALUES (:v, CAST(:u AS uuid), 'accept', :p, :h)"
        ),
        {"v": signable, "u": officer, "p": GENESIS, "h": b"\x02" * 32},
    )
    session.rollback()


def test_update_is_revoked_from_the_app_role(session, officer, signable):  # type: ignore[no-untyped-def]
    """This is the whole append-only claim. If UPDATE were permitted, the hash
    chain would be decoration: an attacker with application credentials could
    rewrite a row *and* recompute every downstream link."""
    append(session, verdict_id=signable, user_id=officer, decision="accept")

    session.execute(text("SET LOCAL ROLE pramaan_app"))
    with pytest.raises(ProgrammingError) as exc:
        session.execute(text("UPDATE adjudications SET decision = 'accept'"))
    session.rollback()
    assert "permission denied" in str(exc.value).lower()


def test_delete_is_revoked_from_the_app_role(session, officer, signable):  # type: ignore[no-untyped-def]
    """Deletion is the other half. Without it, an inconvenient signature could
    simply be removed — and a *truncated* chain still verifies, because every
    remaining link is intact. That is precisely why the privilege, not the
    arithmetic, has to carry this."""
    append(session, verdict_id=signable, user_id=officer, decision="accept")

    session.execute(text("SET LOCAL ROLE pramaan_app"))
    with pytest.raises(ProgrammingError) as exc:
        session.execute(text("DELETE FROM adjudications"))
    session.rollback()
    assert "permission denied" in str(exc.value).lower()


def test_tampering_as_a_superuser_is_detected_by_verification(session, officer, signable):  # type: ignore[no-untyped-def]
    """A DBA can still alter the table. The chain's job is to make that visible
    rather than to prevent it — so this proves the detection works against a
    real UPDATE, not just against a hand-built list of rows."""
    append(session, verdict_id=signable, user_id=officer, decision="accept")
    append(
        session,
        verdict_id=signable,
        user_id=officer,
        decision="reject",
        reason="the original stated reason",
    )
    assert verify_chain(session).valid

    session.execute(
        text(
            "UPDATE adjudications SET reason = 'a reason nobody gave' "
            "WHERE id = (SELECT max(id) FROM adjudications)"
        )
    )
    session.commit()

    report = verify_chain(session)
    assert not report.valid
    assert report.reason is not None and "altered" in report.reason


def test_deleting_the_tail_is_not_detectable_and_that_is_documented(session, officer, signable):  # type: ignore[no-untyped-def]
    """An honest negative result.

    A hash chain cannot detect truncation of its own tail: every surviving link
    still verifies. This is exactly why `REVOKE DELETE` is load-bearing rather
    than belt-and-braces, and why an operator must compare the row count
    against an external record. Asserting the limitation keeps anyone from
    later claiming the chain proves more than it does.
    """
    append(session, verdict_id=signable, user_id=officer, decision="accept")
    append(
        session,
        verdict_id=signable,
        user_id=officer,
        decision="reject",
        reason="a rejection somebody would prefer to erase",
    )
    assert verify_chain(session).rows == 2

    session.execute(
        text("DELETE FROM adjudications WHERE id = (SELECT max(id) FROM adjudications)")
    )
    session.commit()

    report = verify_chain(session)
    assert report.valid, "truncation is undetectable by the chain alone"
    assert report.rows == 1, "only the row count reveals it"


# --- the auditor's offline path ------------------------------------------


def run_verifier() -> subprocess.CompletedProcess[str]:
    """Run `scripts/verify_ledger_chain.py` exactly as an auditor would.

    As a subprocess, not an import: the claim is that chain integrity can be
    checked *without the application*, so the test has to exercise the script's
    own entry point, its own database connection and its exit code. Importing
    `verify_chain` and calling it would prove something the tests above already
    prove, and would leave the script itself unexercised — which is how it came
    to be wired into neither CI nor the Makefile in the first place.
    """
    script = Path(__file__).resolve().parents[2] / "scripts" / "verify_ledger_chain.py"
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": sqlalchemy_url(DSN or "")},
        timeout=120,
    )


def test_the_offline_verifier_reports_a_real_chain_valid(session, officer, signable):  # type: ignore[no-untyped-def]
    append(session, verdict_id=signable, user_id=officer, decision="accept")
    append(
        session,
        verdict_id=signable,
        user_id=officer,
        decision="reject",
        reason="no structure visible at the coordinate",
    )

    done = run_verifier()
    assert done.returncode == 0, done.stderr
    assert "VALID" in done.stdout
    assert "2 adjudication(s)" in done.stdout, done.stdout


def test_the_offline_verifier_exits_nonzero_on_a_tampered_chain(session, officer, signable):  # type: ignore[no-untyped-def]
    """The property that makes the script worth shipping: it fails loudly.

    A verifier that printed a warning and exited 0 would be worse than none at
    all, because a cron job would swallow it.
    """
    append(session, verdict_id=signable, user_id=officer, decision="accept")
    append(
        session,
        verdict_id=signable,
        user_id=officer,
        decision="reject",
        reason="the original stated reason",
    )
    assert run_verifier().returncode == 0

    session.execute(
        text(
            "UPDATE adjudications SET reason = 'a reason nobody gave' "
            "WHERE id = (SELECT max(id) FROM adjudications)"
        )
    )
    session.commit()

    done = run_verifier()
    assert done.returncode == 1, done.stdout
    assert "BROKEN" in done.stdout
    assert "row_hash mismatch" in done.stdout, done.stdout


def test_the_status_vocabulary_check_is_live(session, signable):  # type: ignore[no-untyped-def]
    """Migration 0005. `verdicts.status` used to be unconstrained TEXT.

    The hazard was on the write side, not the read side: readers compute
    `provisional = status != 'adjudicated'` and so fail safe, but
    `app/db/verdicts.py` supersedes older versions `WHERE status <>
    'adjudicated'`. A near-miss value satisfies that predicate, so a *signed*
    verdict could have been silently superseded - which is precisely what that
    file's own comment forbids.
    """
    with pytest.raises(Exception) as exc:
        session.execute(
            text("UPDATE verdicts SET status = 'Adjudicated' WHERE id = :v"),
            {"v": signable},
        )
    session.rollback()
    assert "verdict_status_vocabulary" in str(exc.value)


def test_every_real_status_is_accepted(session, signable):  # type: ignore[no-untyped-def]
    """The control for the test above: a constraint that rejected everything
    would pass it for the wrong reason.

    `superseded` is in this list because the constraint's first draft omitted it
    and this suite caught it - the vocabulary was undocumented precisely because
    nothing enforced it.
    """
    for status in ("adjudicated", "superseded", "pending"):
        session.execute(
            text("UPDATE verdicts SET status = :s WHERE id = :v"),
            {"s": status, "v": signable},
        )
    session.rollback()
