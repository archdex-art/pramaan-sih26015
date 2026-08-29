"""The priority alert queue (FR-10), through a real database.

This router was 194 lines of complete, careful, capability-gated code that was
never mounted in `app.main` and had no tests. It was therefore unreachable: the
P0 requirement it implements — "raise a priority-ranked alert for every N3
verdict" — was not actually met by the running system, and nothing failed to say
so. Mounting it without tests would only move the problem from "unreachable" to
"unverified", which is worse: unreachable code cannot mislead an officer.

What is asserted here is the behaviour a district officer depends on:

* corroborated verdicts stay out of the queue,
* the queue is ordered by urgency and truncation keeps the urgent end,
* the summary can never disagree with the list it summarises,
* jurisdiction scoping actually excludes another district's claims,
* `adjudicated` reflects a real signature rather than a denormalised flag.
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

OFFICER = "wcdc.nanded"
HOME_DISTRICT = "520"


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
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def client(username: str = OFFICER):  # type: ignore[no-untyped-def]
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


def alert_claim(  # type: ignore[no-untyped-def]
    con,
    *,
    level: str,
    score: float,
    district: str,
    action: str = "field_verification",
):
    """A claim whose newest verdict sits at `level`, committed.

    Committed because the endpoint opens its own session. Built through the
    shared `claim` machinery would be neater, but that fixture is function
    scoped and hardcodes district 520 — and cross-district exclusion is one of
    the properties under test.
    """
    from conftest import POLY, _insert_verdict

    cur = con.cursor()
    tag = os.urandom(5).hex()
    cur.execute(f"INSERT INTO watersheds (ws_code,geom) VALUES ('WS-{tag}',{POLY}) RETURNING id")
    ws = cur.fetchone()[0]
    cur.execute(
        f"INSERT INTO sub_watersheds (sws_code,watershed_id,geom) VALUES ('SWS-{tag}',%s,{POLY})"
        " RETURNING id",
        (ws,),
    )
    sws = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO micro_watersheds (mws_code,sub_ws_id,state_lgd,district_lgd,geom,"
        f"analysis_srid) VALUES ('MWS-{tag}',%s,'27',%s,{POLY},32643) RETURNING id",
        (sws, district),
    )
    mws = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO projects (project_code,name,mws_id,state_lgd,district_lgd) "
        f"VALUES ('WDC-{tag}','Alerts',%s,'27',%s) RETURNING id",
        (mws, district),
    )
    proj = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO interventions (unique_id,project_id,mws_id,district_lgd,type,status,"
        "completed_date,geom,expected_footprint_m2) VALUES "
        f"('ALERT-{tag}',%s,%s,%s,'check_dam','completed','2023-11-20',"
        "ST_SetSRID(ST_MakePoint(77.05,19.05),4326),3200) RETURNING id",
        (proj, mws, district),
    )
    iv = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO claims (intervention_id,district_lgd,asserted_status,asserted_date,geom,"
        "uncertainty_m,detectability) VALUES (%s,%s,'completed','2023-11-20',"
        "ST_SetSRID(ST_MakePoint(77.05,19.05),4326),15,'passed') RETURNING id",
        (iv, district),
    )
    claim_id = cur.fetchone()[0]

    vid = _insert_verdict(
        cur,
        {
            "claim_id": claim_id,
            "version": 1,
            "level": level,
            "rule_path": ["TEST"],
            "score": score,
            "confidence": min(0.4, abs(score)),
            "coverage": 1.0,
            "quality": 1.0,
            "data_sufficiency": 0.9,
            "dissent": ["synthetic alert fixture"],
            "recommended_action": {"action": action},
            "engine_version": "engine-v1",
            "weights": {"terrain": 0.25},
            "status": "pending",
            "lineage": {"engine_version": "engine-v1"},
            "bundle_digest": os.urandom(32).hex(),
            "verdict_digest": os.urandom(32).hex(),
        },
    )
    con.commit()
    return claim_id, vid, f"ALERT-{tag}"


@pytest.fixture()
def clean(con):  # type: ignore[no-untyped-def]
    """Remove every claim before and after, so counts are exact.

    An alert queue test that asserts "N entries" against a shared database is a
    test that passes until somebody adds a fixture.
    """
    cur = con.cursor()
    for sql in (
        "DELETE FROM adjudications",
        "DELETE FROM evidence",
        "DELETE FROM verdicts",
        "DELETE FROM claims",
        "DELETE FROM interventions",
        "DELETE FROM projects",
        "DELETE FROM micro_watersheds",
        "DELETE FROM sub_watersheds",
        "DELETE FROM watersheds",
    ):
        cur.execute(sql)
    con.commit()
    yield con
    con.rollback()


def test_the_alerts_routes_are_actually_mounted() -> None:
    """The bug this suite exists because of: the router was complete and
    unreachable. A route that 404s is indistinguishable from a feature that was
    never written, and FR-10 is P0."""
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/v1/alerts" in paths
    assert "/api/v1/alerts/summary" in paths


def test_a_contradicted_verdict_raises_an_alert(clean) -> None:  # type: ignore[no-untyped-def]
    """FR-10.1 stated as an assertion rather than as a requirement document."""
    _, vid, unique = alert_claim(clean, level="N3_contradicted", score=-0.6, district=HOME_DISTRICT)

    body = client().get("/api/v1/alerts").json()
    assert [a["unique_id"] for a in body] == [unique]
    assert body[0]["verdict_id"] == vid
    assert body[0]["level"] == "N3_contradicted"
    assert body[0]["priority"] == 1
    assert body[0]["reason"], "an alert with no stated reason is not actionable"


def test_a_corroborated_verdict_raises_no_alert(clean) -> None:  # type: ignore[no-untyped-def]
    """The queue is a work list, not a report. A corroborated claim in it would
    send an officer to a site that needs nothing."""
    alert_claim(clean, level="L4_control_differenced", score=1.0, district=HOME_DISTRICT)

    assert client().get("/api/v1/alerts").json() == []


def test_the_queue_is_ordered_by_urgency(clean) -> None:  # type: ignore[no-untyped-def]
    alert_claim(clean, level="N1_inconclusive", score=-0.1, district=HOME_DISTRICT)
    _, _, contradicted = alert_claim(
        clean, level="N3_contradicted", score=-0.7, district=HOME_DISTRICT
    )
    alert_claim(clean, level="N2_unsupported", score=-0.4, district=HOME_DISTRICT)

    body = client().get("/api/v1/alerts").json()
    assert len(body) == 3
    assert body[0]["unique_id"] == contradicted, "N3 must outrank N2 and N1"
    assert [a["priority"] for a in body] == [1, 2, 3]


def test_the_limit_keeps_the_urgent_end(clean) -> None:  # type: ignore[no-untyped-def]
    """Truncation after ranking, not before. A limit that returned an arbitrary
    N would quietly hide the worst claims — the precise opposite of the
    endpoint's purpose."""
    alert_claim(clean, level="N1_inconclusive", score=-0.1, district=HOME_DISTRICT)
    _, _, contradicted = alert_claim(
        clean, level="N3_contradicted", score=-0.7, district=HOME_DISTRICT
    )

    body = client().get("/api/v1/alerts?limit=1").json()
    assert [a["unique_id"] for a in body] == [contradicted]


def test_the_summary_agrees_with_the_list(clean) -> None:  # type: ignore[no-untyped-def]
    """Both endpoints share one query so they cannot drift. Asserted because
    "cannot drift" is a property of today's implementation, not of the API."""
    alert_claim(clean, level="N3_contradicted", score=-0.7, district=HOME_DISTRICT)
    alert_claim(clean, level="N2_unsupported", score=-0.4, district=HOME_DISTRICT)
    alert_claim(clean, level="L4_control_differenced", score=1.0, district=HOME_DISTRICT)

    c = client()
    queue = c.get("/api/v1/alerts").json()
    summary = c.get("/api/v1/alerts/summary").json()

    assert summary["total"] == len(queue) == 2
    assert summary["highest_priority_reason"] == queue[0]["reason"]
    assert summary["by_level"]["N3_contradicted"] == 1
    assert summary["by_level"]["N2_unsupported"] == 1


def test_the_summary_reports_every_band_including_the_empty_ones(clean) -> None:  # type: ignore[no-untyped-def]
    """A header that omits a band is read as the band not existing."""
    alert_claim(clean, level="N3_contradicted", score=-0.7, district=HOME_DISTRICT)

    by_level = client().get("/api/v1/alerts/summary").json()["by_level"]
    assert by_level["N1_inconclusive"] == 0
    assert by_level["N2_unsupported"] == 0


def test_an_empty_queue_invents_no_reassuring_text(clean) -> None:  # type: ignore[no-untyped-def]
    """`None`, not "nothing to do". The client decides how to say that; putting
    the sentence in the API would assert something the data does not."""
    summary = client().get("/api/v1/alerts/summary").json()
    assert summary["total"] == 0
    assert summary["highest_priority_reason"] is None


def test_adjudicated_reflects_a_real_signature(clean) -> None:  # type: ignore[no-untyped-def]
    """`EXISTS` against the ledger, not `verdicts.status`. The failure mode of
    trusting the denormalised flag is unsigned work hidden at the bottom of the
    queue."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.services.audit.ledger import append

    _, vid, unique = alert_claim(clean, level="N3_contradicted", score=-0.7, district=HOME_DISTRICT)
    assert client().get("/api/v1/alerts").json()[0]["adjudicated"] is False

    uid = clean.cursor()
    uid.execute("SELECT id::text FROM users WHERE username = %s", (OFFICER,))
    officer_id = uid.fetchone()[0]

    engine = create_engine(sqlalchemy_url(DSN or ""), future=True)
    with Session(engine, future=True) as s:
        append(
            s,
            verdict_id=vid,
            user_id=officer_id,
            decision="reject",
            reason="verified on the ground; no structure present",
        )
        # Undo the denormalised flag so only the ledger row can explain a True.
        s.execute(text("UPDATE verdicts SET status = 'pending' WHERE id = :v"), {"v": vid})
        s.commit()
    engine.dispose()

    body = client().get("/api/v1/alerts").json()
    assert [a["unique_id"] for a in body] == [unique]
    assert body[0]["adjudicated"] is True, "a signature exists; the flag does not"


def test_another_district_is_not_visible(clean) -> None:  # type: ignore[no-untyped-def]
    """Jurisdiction scoping. `wcdc.nanded` is scoped to 520; 522 exists in the
    seeded accounts precisely to make this denial testable."""
    _, _, home = alert_claim(clean, level="N3_contradicted", score=-0.7, district=HOME_DISTRICT)
    alert_claim(clean, level="N3_contradicted", score=-0.8, district="522")

    body = client().get("/api/v1/alerts").json()
    assert [a["unique_id"] for a in body] == [home]


def test_a_national_role_sees_both_districts(clean) -> None:  # type: ignore[no-untyped-def]
    """The control for the test above: scoping that hid everything would pass it
    for the wrong reason."""
    alert_claim(clean, level="N3_contradicted", score=-0.7, district=HOME_DISTRICT)
    alert_claim(clean, level="N3_contradicted", score=-0.8, district="522")

    body = client("admin.dolr").get("/api/v1/alerts").json()
    assert {a["district_lgd"] for a in body} == {HOME_DISTRICT, "522"}


def test_the_queue_requires_authentication() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/api/v1/alerts").status_code == 401


def test_only_the_newest_verdict_version_appears(clean) -> None:  # type: ignore[no-untyped-def]
    """An older version must never surface: a claim re-adjudicated up to L3
    would otherwise keep appearing under whatever it used to be."""
    from conftest import _insert_verdict

    claim_id, _, unique = alert_claim(
        clean, level="N3_contradicted", score=-0.7, district=HOME_DISTRICT
    )
    cur = clean.cursor()
    cur.execute("UPDATE verdicts SET status = 'superseded' WHERE claim_id = %s", (claim_id,))
    _insert_verdict(
        cur,
        {
            "claim_id": claim_id,
            "version": 2,
            "level": "L3_multi_indicator",
            "rule_path": ["TEST"],
            "score": 0.9,
            "confidence": 0.4,
            "coverage": 1.0,
            "quality": 1.0,
            "data_sufficiency": 0.9,
            "dissent": ["re-run with better imagery"],
            "recommended_action": {"action": "no_action"},
            "engine_version": "engine-v1",
            "weights": {"terrain": 0.25},
            "status": "pending",
            "lineage": {"engine_version": "engine-v1"},
            "bundle_digest": os.urandom(32).hex(),
            "verdict_digest": os.urandom(32).hex(),
        },
    )
    clean.commit()

    body = client().get("/api/v1/alerts").json()
    assert [a["unique_id"] for a in body] == [], f"stale version surfaced: {unique}"
