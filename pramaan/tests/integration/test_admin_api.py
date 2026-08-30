"""The administration console, through a real database.

Like `test_alerts_api.py`, this suite exists because of a defect of absence:
`app/api/v1/admin.py` was ~400 lines of complete, capability-gated code that
`app.main` never included. `/admin/users`, `/admin/districts` and
`/admin/system` all returned 404 while the front end's rail linked to an
Administration screen — and a 404 is indistinguishable from a feature nobody
wrote, which is why the first test here asserts nothing but mountedness.

What is asserted beyond that is the behaviour an administrator and a reviewer
depend on:

* the gate refuses with 403 rather than 404, so a denial is legible as a denial,
* `/admin/users` reports the role -> workspace mapping the whole UI now routes
  on, for every seeded account,
* `/admin/system` reports the engine's own version and a ledger verdict
  computed by the ledger's own verifier,
* `/admin/data-sources` replays the recorded verification log without smoothing
  its one honest failure into a success.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DSN = os.environ.get("PRAMAAN_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="set PRAMAAN_TEST_DSN to run database integration tests"
)

ADMIN = "admin.dolr"
#: A monitoring officer: holds `ledger:verify` but neither `user:manage` nor
#: `district:manage`, and is not in the administration workspace. The control
#: for every gate assertion below.
OFFICER = "wcdc.nanded"

ROUTES = (
    "/api/v1/admin/users",
    "/api/v1/admin/districts",
    "/api/v1/admin/system",
    "/api/v1/admin/data-sources",
)

#: The same file the endpoint serves. Read independently here so the test
#: compares the response against the artefact on disk rather than against a
#: number copied into the test, which would stop meaning anything the next time
#: a verification pass is run.
DATA_SOURCES_LOG = REPO_ROOT / "docs" / "09-data-sources.log.json"

#: Role -> workspace, restated from `authz.WORKSPACE` on purpose. Importing the
#: map would make this test tautological: it would assert that the endpoint and
#: the map agree, not that either says what the front end was built against.
EXPECTED_WORKSPACE = {
    "wdt": "field",
    "pia": "field",
    "wcdc": "monitoring",
    "slna": "monitoring",
    "readonly": "monitoring",
    "dolr_admin": "administration",
}


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

    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import seed_users

    seed_users.main()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def client(username: str = ADMIN):  # type: ignore[no-untyped-def]
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


# --- Mountedness ------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_the_admin_routes_are_actually_mounted(route: str) -> None:
    """The regression this file exists for.

    Asserted against the OpenAPI schema rather than by calling the route,
    because a 404 from an unmounted router and a 404 from a handler are the same
    status code and only one of them is this bug.
    """
    from app.main import app

    assert route in app.openapi()["paths"], f"{route} is not mounted"


# --- Gating -----------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_the_administrator_reaches_every_route(route: str) -> None:
    r = client(ADMIN).get(route)
    assert r.status_code == 200, f"{route}: {r.status_code} {r.text}"


@pytest.mark.parametrize("route", ROUTES)
def test_a_monitoring_officer_is_refused_not_missed(route: str) -> None:
    """403, never 404.

    The distinction is the whole point: 404 tells a WCDC officer the
    administration console does not exist, which is false and unfalsifiable,
    while 403 tells them it exists and is not theirs. The body must say why —
    an opaque denial is the reason integrators guess.

    The message here names the missing capability rather than the workspace,
    because FastAPI resolves `dependencies` in declaration order and `require`
    is declared first. No seeded role reaches the workspace guard: it fires only
    for a caller who holds `district:manage` outside the administration
    workspace, which `CAPABILITIES` does not currently produce. That guard's own
    message is pinned directly in the test below, so relaxing the capability map
    cannot quietly leave it untested.
    """
    r = client(OFFICER).get(route)
    assert r.status_code == 403, f"{route}: {r.status_code} {r.text}"
    detail = r.json()["detail"]
    assert "wcdc" in detail, f"{route}: denial does not name the caller's role: {detail!r}"
    assert "manage" in detail, f"{route}: denial does not name what is required: {detail!r}"


def test_the_workspace_guard_names_the_callers_workspace() -> None:
    """The second, independent condition on every admin route (see its module
    docstring): it must keep refusing even if `CAPABILITIES` is widened.

    Called directly because no seeded role can reach it through a request.
    """
    from fastapi import HTTPException

    from app.api.v1.admin import administration_only
    from app.core.authz import Principal, Role

    officer = Principal(
        user_id="00000000-0000-0000-0000-000000000000",
        username=OFFICER,
        full_name="R. Kumar (WCDC Nanded)",
        role=Role.WCDC,
        scope_state="Maharashtra",
        scope_district="520",
    )
    with pytest.raises(HTTPException) as raised:
        administration_only(officer)

    assert raised.value.status_code == 403
    assert "monitoring" in raised.value.detail
    assert "administration" in raised.value.detail


@pytest.mark.parametrize("route", ROUTES)
def test_the_console_requires_authentication(route: str) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get(route).status_code == 401


# --- Accounts ---------------------------------------------------------------


def test_every_seeded_account_reports_its_workspace() -> None:
    """The assertion that pins the role -> workspace mapping the UI routes on.

    Every account, not a sample: a mapping that is right for five roles and
    wrong for the sixth lands one set of officers on the wrong workspace, and
    that is exactly the kind of failure a sampled test misses.
    """
    body = client(ADMIN).get("/api/v1/admin/users").json()

    assert len(body) == 7, f"expected the seven seeded accounts, got {len(body)}"
    for user in body:
        assert user["workspace"] == EXPECTED_WORKSPACE[user["role"]], (
            f"{user['username']} ({user['role']}) reported workspace {user['workspace']!r}"
        )

    by_name = {u["username"]: u for u in body}
    assert set(by_name) == {
        "admin.dolr",
        "slna.mh",
        "wcdc.nanded",
        "wcdc.latur",
        "pia.nanded",
        "wdt.nanded",
        "audit.cag",
    }
    assert by_name["admin.dolr"]["scope_district"] is None, (
        "the national administrator must not carry a district scope"
    )
    assert by_name["wcdc.nanded"]["scope_district"] == "520"


def test_no_account_response_carries_a_password_hash() -> None:
    """`_USERS` never selects the column. Asserted anyway, because the cost of
    this test is one line and the cost of the mistake is an offline attack.
    """
    for user in client(ADMIN).get("/api/v1/admin/users").json():
        assert "password_hash" not in user
        assert not any("password" in key for key in user)


# --- System -----------------------------------------------------------------


def test_the_system_summary_reports_the_running_engine() -> None:
    from app.services.reconcile.weights import ENGINE_VERSION

    body = client(ADMIN).get("/api/v1/admin/system").json()

    assert body["engine_version"] == "engine-v1"
    assert body["engine_version"] == ENGINE_VERSION, (
        "the console must report the constant the engine itself imports"
    )
    assert body["users"] == 7


def test_a_clean_ledger_verifies() -> None:
    """An empty or intact hash chain is valid, and the row count comes from the
    verifier rather than a second `COUNT(*)` — so the number and the integrity
    statement cannot be computed from different reads.
    """
    body = client(ADMIN).get("/api/v1/admin/system").json()

    assert body["ledger_valid"] is True
    assert body["ledger_rows"] >= 0
    assert body["ledger_rows"] == body["adjudications"], (
        "one ledger row per signed adjudication; a drift here means the ledger "
        "stopped recording or stopped being the record"
    )


def test_the_empty_subsystems_are_listed_rather_than_omitted() -> None:
    """The console's stated purpose: report zeroes by name. A subsystem missing
    from the list reads as a subsystem nobody thought about.
    """
    from app.api.v1.admin import SUBSYSTEM_TABLES

    body = client(ADMIN).get("/api/v1/admin/system").json()
    subsystems = body["subsystems"]

    assert subsystems, "the subsystem roster must never be empty"
    assert {s["table"] for s in subsystems} == set(SUBSYSTEM_TABLES)
    for entry in subsystems:
        assert entry["row_count"] >= 0
        assert entry["populated"] == (entry["row_count"] > 0), (
            f"{entry['table']}: populated={entry['populated']} contradicts "
            f"row_count={entry['row_count']}"
        )


# --- External data sources --------------------------------------------------


def test_the_data_source_log_round_trips_intact() -> None:
    """Every recorded probe, served as recorded.

    Compared against the file on disk rather than a hardcoded count, because the
    point of this endpoint is that it reproduces the artefact and not a summary
    of it.
    """
    recorded = json.loads(DATA_SOURCES_LOG.read_text(encoding="utf-8"))
    body = client(ADMIN).get("/api/v1/admin/data-sources").json()

    assert len(body) == len(recorded)
    assert [e["key"] for e in body] == [e["key"] for e in recorded], (
        "order carries meaning: the log is a chronological verification pass"
    )
    for entry in body:
        assert entry["status"], f"{entry['key']} has no recorded status"
        assert entry["url"], f"{entry['key']} has no url"
        assert entry["licence"], f"{entry['key']} has no licence"


def test_the_recorded_failure_survives_the_round_trip() -> None:
    """Bhoonidhi could not be probed: no credentials exist for it outside a
    government deployment. An endpoint that reported it as `OK`, or dropped it,
    would turn the one honest entry in the log into the dishonest one.
    """
    body = client(ADMIN).get("/api/v1/admin/data-sources").json()
    bhoonidhi = next((e for e in body if e["key"] == "bhoonidhi"), None)

    assert bhoonidhi is not None, "an unreachable source must still be listed"
    assert bhoonidhi["status"] != "OK"
    assert bhoonidhi["detail"], "a skipped source must say why it was skipped"


def test_the_endpoint_performs_no_network_io(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """It replays a record. Asserted by breaking the socket layer for the
    duration of the call: under §38 offline mode an admin screen that reaches
    out is a demo that fails on a venue network.

    Safe to break here and nowhere else in this file: the handler takes no
    `DbSession`, so the only connection it could open would be the outbound one
    this test forbids. The login happens before the patch is installed.
    """
    import socket

    authenticated = client(ADMIN)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the data-sources endpoint attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert authenticated.get("/api/v1/admin/data-sources").status_code == 200
