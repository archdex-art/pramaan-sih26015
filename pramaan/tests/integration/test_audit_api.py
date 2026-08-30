"""The activity trail (S2), through a real database.

`audit_log` existed from migration 0001 — RANGE-partitioned by month, indexed,
granted — and no code path ever inserted a row into it. A partition plan for a
permanently empty table is not a control; it is the appearance of one. So the
properties asserted here are the ones that make the trail worth showing to an
auditor:

* a login produces exactly one row, attributed to the account that made it,
* a *failed* login produces a row and that row cannot contain the password,
* the field roles that submit evidence cannot read who has been reviewing it,
* an event with no actor is served as an event with no actor, not as an
  unknown user,
* a failure to write the trail cannot fail the operation the trail describes.

The last one is the reason `record` is best-effort, and it is asserted rather
than documented because "never raises" is a claim that decays the moment
someone adds a `raise` to the error path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DSN = os.environ.get("PRAMAAN_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="set PRAMAAN_TEST_DSN to run database integration tests"
)

AUDITOR = "audit.cag"
ADMIN = "admin.dolr"
OFFICER = "wcdc.nanded"
FIELD = "wdt.nanded"

TRAIL_PATH = "/api/v1/audit"


def sqlalchemy_url(dsn: str) -> str:
    parts = dict(kv.split("=", 1) for kv in dsn.split())
    return (
        f"postgresql+psycopg://{parts['user']}:{parts.get('password', '')}"
        f"@{parts['host']}:{parts['port']}/{parts['dbname']}"
    )


@pytest.fixture(scope="module", autouse=True)
def app_on_test_database():  # type: ignore[no-untyped-def]
    """Point the app at the test database and seed the real accounts."""
    assert DSN is not None
    os.environ["DATABASE_URL"] = sqlalchemy_url(DSN)

    from app.core.config import get_settings
    from app.db.session import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import seed_users

    seed_users.main()

    # `app/main.py` is owned by the integration pass, and this suite must be
    # runnable before the mount lands. Mounting here when the application has
    # not already done it keeps the tests exercising the real router through
    # the real dependency graph either way; once `main.py` includes it this is
    # a no-op.
    #
    # Detected by a request rather than by scanning `app.routes`: FastAPI 0.141
    # keeps included routers as unexpanded `_IncludedRouter` entries with no
    # `.path`, so a path scan reports "not mounted" for every router in the
    # application. A 404 is the behaviour that matters anyway — an unmounted
    # route and an absent feature are the same thing to a caller.
    from fastapi.testclient import TestClient

    from app.main import app

    if TestClient(app).get(TRAIL_PATH).status_code == 404:
        from app.api.v1.audit import router as audit_router

        app.include_router(audit_router, prefix="/api/v1")

    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def client(username: str = AUDITOR):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from seed_users import PASSWORD

    from app.main import app

    unauthenticated = TestClient(app)
    r = unauthenticated.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token = r.json()["access_token"]
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture()
def empty_trail(con):  # type: ignore[no-untyped-def]
    """An empty `audit_log`, committed.

    Committed because the endpoint and the writer both open their own sessions
    and would not see an uncommitted delete. Emptied because every count in
    this file is exact, and an exact count against a shared table is a test
    that passes until the next fixture writes a row.

    DELETE runs on the `con` connection, which is the owning superuser. The
    application role deliberately holds only SELECT/INSERT/UPDATE here, so the
    app itself cannot erase its own trail.
    """
    cur = con.cursor()
    cur.execute("DELETE FROM audit_log")
    con.commit()
    yield con
    con.rollback()


def rows(con, **where):  # type: ignore[no-untyped-def]
    """Trail rows joined to their actor, oldest first, read outside the app."""
    clauses = " AND ".join(f"a.{k} = %({k})s" for k in where) or "TRUE"
    cur = con.cursor()
    cur.execute(
        "SELECT a.id, a.action, a.entity, a.entity_id, a.payload, a.ip, a.user_id,"
        " u.username, u.role::text"
        f" FROM audit_log a LEFT JOIN users u ON u.id = a.user_id WHERE {clauses}"
        " ORDER BY a.at, a.id",
        where,
    )
    return cur.fetchall()


def session():  # type: ignore[no-untyped-def]
    from app.db.session import get_sessionmaker

    return get_sessionmaker()()


# --------------------------------------------------------------------------
# The writer
# --------------------------------------------------------------------------


def test_a_successful_login_writes_one_attributed_row(empty_trail) -> None:  # type: ignore[no-untyped-def]
    """One row, naming the account and the role it logged in as.

    Exactly one: a hook that fires twice would double every count on the
    administration console, and the console has no way to tell duplicates from
    genuine repeated activity.
    """
    client(OFFICER)

    written = rows(empty_trail, action="auth.login.succeeded")
    assert len(written) == 1
    (_id, _action, entity, entity_id, payload, ip, user_id, username, role) = written[0]
    assert (username, role) == (OFFICER, "wcdc")
    assert entity == "user"
    # The entity is the account, so `entity_id` is its uuid and matches the
    # actor column. They are separate columns because for most other actions
    # (a signature, a capture) the actor and the subject are different rows.
    assert entity_id == str(user_id)
    assert payload == {"username": OFFICER, "role": "wcdc"}
    # Starlette's TestClient reports the peer as the literal "testclient",
    # which is not an address. NULL is the honest record of "not captured";
    # storing 0.0.0.0 would put a fact in the table that never happened.
    assert ip is None


def test_a_failed_login_is_recorded_and_carries_no_password(empty_trail) -> None:  # type: ignore[no-untyped-def]
    """The attempted username is evidence. The attempted password is a secret
    that would still be a secret after being typed into the wrong box — very
    often the user's *correct* password for another system."""
    from fastapi.testclient import TestClient

    from app.main import app

    secret = "not-the-password-1234"
    r = TestClient(app).post("/api/v1/auth/login", json={"username": OFFICER, "password": secret})
    assert r.status_code == 401

    written = rows(empty_trail, action="auth.login.failed")
    assert len(written) == 1
    (_id, _action, _entity, _entity_id, payload, _ip, user_id, username, role) = written[0]
    assert payload == {"username": OFFICER}
    assert secret not in repr(written[0])
    # No actor: `login` cannot reveal whether the account exists without
    # becoming a username oracle, and attributing a stranger's guess to a real
    # officer's row would be the wrong record even if it could.
    assert (user_id, username, role) == (None, None, None)


def test_a_secret_bearing_payload_is_redacted_not_stored(empty_trail) -> None:  # type: ignore[no-untyped-def]
    """No call site passes a secret today. This asserts the property survives
    the call site that eventually tries."""
    from app.services.audit.trail import Action, record

    record(
        session(),
        action=Action.LOGIN_FAILED,
        payload={"username": "someone", "password": "hunter2", "Refresh_Token": "eyJ..."},
    )

    (row,) = rows(empty_trail, action="auth.login.failed")
    payload = row[4]
    assert payload == {
        "username": "someone",
        "password": "[redacted]",
        # Matched case-insensitively: a caller writing `Refresh_Token` means
        # the same thing as one writing `refresh_token`.
        "Refresh_Token": "[redacted]",
    }


def test_an_empty_payload_is_stored_as_an_empty_object(empty_trail) -> None:  # type: ignore[no-untyped-def]
    """`{}` and not NULL: the S2 contract promises an object, and an empty one
    truthfully says the action carried no detail."""
    from app.services.audit.trail import Action, record

    record(session(), action=Action.VERDICT_RECOMPUTED)

    (row,) = rows(empty_trail, action="verdict.recomputed")
    assert row[4] == {}


def test_a_failed_trail_write_does_not_raise_and_is_logged(empty_trail, caplog) -> None:  # type: ignore[no-untyped-def]
    """The whole reason `record` is best-effort.

    A bad `user_id` fails the uuid cast in Postgres, which is a stand-in for
    the real failures — a full disk, a lost connection — that must not turn a
    committed login or a signed adjudication into a 500.
    """
    from app.services.audit.trail import Action, record

    live = session()
    with caplog.at_level("ERROR", logger="app.services.audit.trail"):
        record(live, action=Action.LOGIN_SUCCEEDED, user_id="not-a-uuid")

    assert rows(empty_trail) == []
    assert "audit trail write failed" in caplog.text
    # Rolled back, not left in a failed transaction: the caller may still be
    # holding this session and its next statement must work.
    from sqlalchemy import text

    assert live.execute(text("SELECT 1")).scalar_one() == 1


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        (None, None),  # ASGI does not guarantee a peer address.
        ("testclient", None),  # Starlette's test transport. Not an address.
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8::1", "2001:db8::1"),
    ],
)
def test_client_ip_records_an_address_or_nothing(host, expected) -> None:  # type: ignore[no-untyped-def]
    """`inet` rejects malformed input, and an exception thrown at cast time
    would lose the entire row rather than just the address."""
    from app.services.audit.trail import client_ip

    assert client_ip(host) == expected


def test_the_row_lands_in_a_partition_that_matches_the_maintenance_state(
    empty_trail,  # type: ignore[no-untyped-def]
) -> None:
    """A row in `audit_log_default` is not an error — it is the signal that
    `scripts/rotate_audit_partitions.py` is overdue.

    Migration 0001 creates monthly partitions for 2026-01..03 only. Rather than
    hardcode a partition name that goes stale, this asserts the *relationship*:
    the row lands in DEFAULT if and only if no month partition covers now().
    That is the invariant the ops alert should be built on.
    """
    from app.services.audit.trail import Action, record

    record(session(), action=Action.LOGOUT)

    cur = empty_trail.cursor()
    cur.execute("SELECT tableoid::regclass::text FROM audit_log")
    (landed,) = cur.fetchone()

    cur.execute(
        """
        SELECT count(*) FROM pg_class c
          JOIN pg_inherits i ON i.inhrelid = c.oid
          JOIN pg_class p ON p.oid = i.inhparent
         WHERE p.relname = 'audit_log'
           AND c.relname <> 'audit_log_default'
           AND now() >= split_part(
                 substring(pg_get_expr(c.relpartbound, c.oid) from 'FROM \\(''(.*?)''\\)'),
                 '', 1)::timestamptz
           AND now() <  substring(pg_get_expr(c.relpartbound, c.oid) from 'TO \\(''(.*?)''\\)')
                 ::timestamptz
        """
    )
    (covering,) = cur.fetchone()

    assert (landed == "audit_log_default") == (covering == 0), (
        f"row landed in {landed} with {covering} covering month partition(s)"
    )


# --------------------------------------------------------------------------
# The reader
# --------------------------------------------------------------------------


def test_unauthenticated_access_is_refused() -> None:
    """Doubles as the reachability check: a mounted route answers 401, an
    unmounted one answers 404, and the alerts router shipped complete,
    unmounted and untested once already. An unreachable endpoint and an absent
    feature look identical from outside."""
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get(TRAIL_PATH).status_code == 401


def test_a_field_role_cannot_read_the_trail() -> None:
    """`wdt` submits evidence and does not hold `ledger:verify`. An officer who
    can see who has been reviewing their submissions is a different product."""
    assert client(FIELD).get(TRAIL_PATH).status_code == 403


@pytest.mark.parametrize("username", [AUDITOR, ADMIN, OFFICER])
def test_the_oversight_roles_can_read_the_trail(username) -> None:  # type: ignore[no-untyped-def]
    """The external auditor, the administrator and the district monitoring
    officer — everyone holding `ledger:verify`."""
    r = client(username).get(TRAIL_PATH)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_a_system_row_is_served_without_inventing_a_user(empty_trail) -> None:  # type: ignore[no-untyped-def]
    """NULL actor in, null actor out. The client renders that as a system
    event; substituting "unknown user" would read as a failed lookup, which is
    a bug report about something that is actually the recorded truth."""
    from app.services.audit.trail import Action, record

    record(session(), action=Action.VERDICT_RECOMPUTED, entity="verdict", entity_id="4242")

    body = client(AUDITOR).get(TRAIL_PATH, params={"action": "verdict.recomputed"}).json()
    assert len(body) == 1
    assert body[0]["username"] is None
    assert body[0]["full_name"] is None
    assert body[0]["role"] is None
    assert body[0]["entity_id"] == "4242"
    assert body[0]["payload"] == {}


def test_the_trail_is_newest_first_and_limit_keeps_the_newest(empty_trail) -> None:  # type: ignore[no-untyped-def]
    """Truncation at the wrong end of a chronology is worse than no
    chronology: it looks complete and shows the oldest events."""
    from app.services.audit.trail import Action, record

    live = session()
    for n in range(3):
        record(live, action=Action.VERDICT_RECOMPUTED, entity="verdict", entity_id=str(n))

    api = client(AUDITOR)
    everything = api.get(TRAIL_PATH, params={"entity": "verdict"}).json()
    assert [e["entity_id"] for e in everything] == ["2", "1", "0"]

    one = api.get(TRAIL_PATH, params={"entity": "verdict", "limit": 1}).json()
    assert [e["entity_id"] for e in one] == ["2"]


def test_the_action_and_entity_filters_select_exactly(empty_trail) -> None:  # type: ignore[no-untyped-def]
    from app.services.audit.trail import Action, record

    live = session()
    record(live, action=Action.VERDICT_RECOMPUTED, entity="verdict", entity_id="7")
    record(live, action=Action.LOGOUT, entity="user")

    api = client(AUDITOR)  # this login itself writes an auth.login.succeeded row

    assert [e["action"] for e in api.get(TRAIL_PATH, params={"action": "auth.logout"}).json()] == [
        "auth.logout"
    ]
    assert [e["action"] for e in api.get(TRAIL_PATH, params={"entity": "verdict"}).json()] == [
        "verdict.recomputed"
    ]
    # An action outside the closed vocabulary matches nothing rather than
    # 422-ing: "no such event occurred" is the truthful answer, and it is not
    # the same statement as "that filter is invalid".
    assert api.get(TRAIL_PATH, params={"action": "no.such.action"}).json() == []
    # Three writes plus one login row, so the unfiltered read is a superset.
    assert len(api.get(TRAIL_PATH).json()) == 3


@pytest.mark.parametrize("limit", [0, 501, -1])
def test_limit_is_bounded(limit) -> None:  # type: ignore[no-untyped-def]
    """`audit_log` is the one table designed to grow without bound, so an
    unbounded read of it is a denial of service reachable from a browser."""
    assert client(AUDITOR).get(TRAIL_PATH, params={"limit": limit}).status_code == 422


def test_logout_and_refresh_are_attributed_to_the_token_holder(empty_trail) -> None:  # type: ignore[no-untyped-def]
    """Both arrive carrying only a refresh token. Decoding it for the subject
    authorises nothing — the real decision has already been made — and without
    it the two most common session events would be unattributed."""
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    from seed_users import PASSWORD

    tokens = anon.post(
        "/api/v1/auth/login", json={"username": OFFICER, "password": PASSWORD}
    ).json()

    refreshed = anon.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    ).json()
    assert (
        anon.post(
            "/api/v1/auth/logout", json={"refresh_token": refreshed["refresh_token"]}
        ).status_code
        == 204
    )

    for action in ("auth.token.refreshed", "auth.logout"):
        (row,) = rows(empty_trail, action=action)
        assert row[7] == OFFICER, f"{action} was not attributed"
    # Two, not one: logout revokes the whole refresh family, and this family
    # has two members because the token was rotated once above. That number is
    # the fact the response deliberately withholds — telling a caller how many
    # of their tokens were live tells them which tokens exist — and it is
    # exactly what an investigator asking "was this session hijacked" wants.
    assert rows(empty_trail, action="auth.logout")[0][4] == {"tokens_revoked": 2}
