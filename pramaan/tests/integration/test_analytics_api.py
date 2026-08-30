"""District-level analytics (S3), through a real database.

The properties under test are the ones that would be invisible if broken:

* **Superseded verdicts are not aggregated.** The regression that matters. A
  filter in the same SELECT as `DISTINCT ON` runs before the newest-row
  selection and silently surfaces the older version — it was a live bug in
  `v1.alerts` and every row it returns is a genuine row, so nothing looks
  wrong. A headline "12% contradicted" built that way is untraceable.
* **Jurisdiction scoping, with a national control.** Scoping that hid
  everything would pass the exclusion test for the wrong reason.
* **All eight levels present, including the zeroes.** An omitted band reads as
  a band that does not exist.
* **`means` are `null`, not `0`, on an empty scope.** Zero is a measurement.
* **`refused` is non-empty.** The list of what this endpoint declines to compute
  is a response field, not a comment.

Structured after `test_alerts_api.py`, including its fixture strategy: claims
are built and committed directly rather than through the function-scoped `claim`
fixture, because that fixture hardcodes district 520 and cross-district
exclusion is one of the properties under test.
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
NATIONAL = "admin.dolr"
HOME_DISTRICT = "520"
OTHER_DISTRICT = "522"

#: The eight levels, written out rather than imported, so a change to the engine
#: enum that drops a band fails here instead of being mirrored silently.
LADDER = [
    "L0_recorded",
    "L1_observed",
    "L2_corroborated",
    "L3_multi_indicator",
    "L4_control_differenced",
    "N1_inconclusive",
    "N2_unsupported",
    "N3_contradicted",
]


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


def app_under_test():  # type: ignore[no-untyped-def]
    """The real app, with this router mounted if `main` has not done so yet.

    `app/main.py` is owned by the integrator, so this suite has to pass both
    before and after that wiring lands. Mounting onto the real app rather than a
    bare `FastAPI()` keeps the auth dependency graph, the settings and the
    exception handling under test instead of a stand-in that could pass while
    the shipped app 401s.
    """
    from app.api.v1 import analytics
    from app.main import app

    if not any(getattr(route, "path", None) == "/api/v1/analytics" for route in app.routes):
        app.include_router(analytics.router, prefix="/api/v1")
    return app


def client(username: str = OFFICER):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient
    from seed_users import PASSWORD

    app = app_under_test()
    unauthenticated = TestClient(app)
    r = unauthenticated.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token = r.json()["access_token"]
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def analytics_claim(  # type: ignore[no-untyped-def]
    con,
    *,
    level: str,
    district: str,
    score: float = 0.5,
    confidence: float = 0.4,
    coverage: float = 1.0,
    data_sufficiency: float = 0.9,
    action: str = "field_verification",
    detectability: str | None = "passed",
    provenance: str | None = None,
    families: dict[str, tuple[float, bool]] | None = None,
):
    """A claim with its hierarchy and one committed verdict at `level`.

    Committed because the endpoint opens its own session and would not see an
    open transaction. `provenance` goes into the verdict lineage exactly as
    `scripts/seed_golden.py` writes it, so the measured/golden split under test
    is the one the register badges.
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
        f"VALUES ('WDC-{tag}','Analytics',%s,'27',%s) RETURNING id",
        (mws, district),
    )
    proj = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO interventions (unique_id,project_id,mws_id,district_lgd,type,status,"
        "completed_date,geom,expected_footprint_m2) VALUES "
        f"('ANA-{tag}',%s,%s,%s,'check_dam','completed','2023-11-20',"
        "ST_SetSRID(ST_MakePoint(77.05,19.05),4326),3200) RETURNING id",
        (proj, mws, district),
    )
    iv = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO claims (intervention_id,district_lgd,asserted_status,asserted_date,geom,"
        "uncertainty_m,detectability) VALUES (%s,%s,'completed','2023-11-20',"
        "ST_SetSRID(ST_MakePoint(77.05,19.05),4326),15,%s) RETURNING id",
        (iv, district, detectability),
    )
    claim_id = cur.fetchone()[0]

    lineage: dict[str, object] = {"engine_version": "engine-v1"}
    if provenance is not None:
        lineage["provenance"] = provenance

    vid = _insert_verdict(
        cur,
        {
            "claim_id": claim_id,
            "version": 1,
            "level": level,
            "rule_path": ["TEST"],
            "score": score,
            "confidence": confidence,
            "coverage": coverage,
            "quality": 1.0,
            "data_sufficiency": data_sufficiency,
            "dissent": ["synthetic analytics fixture"],
            "recommended_action": {"action": action},
            "engine_version": "engine-v1",
            "weights": {"terrain": 0.25},
            "status": "pending",
            "lineage": lineage,
            "bundle_digest": os.urandom(32).hex(),
            "verdict_digest": os.urandom(32).hex(),
        },
    )

    for family, (agreement, available) in (families or {}).items():
        cur.execute(
            "INSERT INTO evidence (claim_id,district_lgd,family,agreement,available,"
            "payload,lineage) VALUES (%s,%s,%s,%s,%s,'{}'::jsonb,'{}'::jsonb)",
            (claim_id, district, family, agreement, available),
        )

    con.commit()
    return claim_id, vid, f"ANA-{tag}"


@pytest.fixture()
def clean(con):  # type: ignore[no-untyped-def]
    """Remove every claim before and after, so counts are exact.

    An analytics test asserting "N verdicts" against a shared database is a test
    that passes until somebody adds a fixture.
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


def test_the_officer_can_read_district_analytics(clean) -> None:  # type: ignore[no-untyped-def]
    analytics_claim(clean, level="L3_multi_indicator", district=HOME_DISTRICT)

    r = client().get("/api/v1/analytics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["claims"] == 1
    assert body["totals"]["verdicts"] == 1
    assert body["district_lgds"] == [HOME_DISTRICT]


def test_analytics_requires_authentication() -> None:
    from fastapi.testclient import TestClient

    assert TestClient(app_under_test()).get("/api/v1/analytics").status_code == 401


def test_another_district_is_not_aggregated(clean) -> None:  # type: ignore[no-untyped-def]
    """`wcdc.nanded` is scoped to 520. A claim in 522 must not reach any count,
    including the totals a reader would treat as a denominator."""
    analytics_claim(clean, level="L3_multi_indicator", district=HOME_DISTRICT)
    analytics_claim(clean, level="N3_contradicted", district=OTHER_DISTRICT)

    body = client().get("/api/v1/analytics").json()
    assert body["district_lgds"] == [HOME_DISTRICT]
    assert body["totals"]["verdicts"] == 1
    assert body["by_level"]["N3_contradicted"] == 0


def test_a_national_role_aggregates_both_districts(clean) -> None:  # type: ignore[no-untyped-def]
    """The control for the test above: scoping that returned nothing at all would
    pass it for the wrong reason."""
    analytics_claim(clean, level="L3_multi_indicator", district=HOME_DISTRICT)
    analytics_claim(clean, level="N3_contradicted", district=OTHER_DISTRICT)

    body = client(NATIONAL).get("/api/v1/analytics").json()
    assert body["district_lgds"] == [HOME_DISTRICT, OTHER_DISTRICT]
    assert body["totals"]["verdicts"] == 2
    assert body["by_level"]["N3_contradicted"] == 1


def test_every_level_is_reported_including_the_empty_ones(clean) -> None:  # type: ignore[no-untyped-def]
    """A distribution that omits a band is read as the band not existing, and the
    ladder's shape is the product."""
    analytics_claim(clean, level="N1_inconclusive", district=HOME_DISTRICT)

    by_level = client().get("/api/v1/analytics").json()["by_level"]
    assert sorted(by_level) == sorted(LADDER)
    assert by_level["N1_inconclusive"] == 1
    assert by_level["L4_control_differenced"] == 0


def test_the_means_are_null_on_an_empty_scope(clean) -> None:  # type: ignore[no-untyped-def]
    """Zero is a measurement: a mean confidence of 0.0 says the engine assessed
    claims and found no support. An empty scope says it assessed nothing."""
    body = client().get("/api/v1/analytics").json()

    assert body["totals"]["verdicts"] == 0
    assert body["means"] == {
        "confidence": None,
        "coverage": None,
        "data_sufficiency": None,
    }
    assert body["inconclusive_share"] is None


def test_the_means_average_only_the_newest_verdicts(clean) -> None:  # type: ignore[no-untyped-def]
    analytics_claim(clean, level="L3_multi_indicator", district=HOME_DISTRICT, confidence=0.2)
    analytics_claim(clean, level="L2_corroborated", district=HOME_DISTRICT, confidence=0.4)

    means = client().get("/api/v1/analytics").json()["means"]
    assert means["confidence"] == pytest.approx(0.3)
    assert means["coverage"] == pytest.approx(1.0)


def test_a_superseded_verdict_is_not_aggregated(clean) -> None:  # type: ignore[no-untyped-def]
    """The regression this suite exists for.

    One claim, v1 at N3 and v2 at L3. Filtering inside the `DISTINCT ON` select
    would discard the L3 row first, leave v1 as the only survivor, and count the
    superseded N3 as current — with every returned row a genuine row, so nothing
    looks wrong. Asserted on both bands and on the total, because counting the
    stale row *in addition* is as wrong as counting it instead.
    """
    from conftest import _insert_verdict

    claim_id, _, _ = analytics_claim(
        clean,
        level="N3_contradicted",
        district=HOME_DISTRICT,
        score=-0.7,
        confidence=0.7,
        action="field_verification",
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

    body = client().get("/api/v1/analytics").json()
    assert body["totals"]["verdicts"] == 1, "both versions of one claim were counted"
    assert body["by_level"]["N3_contradicted"] == 0, "a superseded verdict was aggregated"
    assert body["by_level"]["L3_multi_indicator"] == 1
    assert body["by_action"] == {"no_action": 1}
    # The mean is the newest verdict's confidence alone. 0.55 here would mean
    # both versions were averaged, which is the same bug wearing a different hat.
    assert body["means"]["confidence"] == pytest.approx(0.4)


def test_the_inconclusive_share_is_exact(clean) -> None:  # type: ignore[no-untyped-def]
    """A headline number the pitch uses deliberately, so it is not rounded in the
    API — 1/3 must arrive as 1/3 and be formatted by the client."""
    analytics_claim(clean, level="N1_inconclusive", district=HOME_DISTRICT)
    analytics_claim(clean, level="L3_multi_indicator", district=HOME_DISTRICT)
    analytics_claim(clean, level="L2_corroborated", district=HOME_DISTRICT)

    body = client().get("/api/v1/analytics").json()
    assert body["inconclusive_share"] == pytest.approx(1 / 3)


def test_claims_without_a_verdict_widen_the_claim_total_only(clean) -> None:  # type: ignore[no-untyped-def]
    """`totals.claims` counts the reconciliation backlog; `totals.verdicts` does
    not. Counting only assessed claims would make the register look complete."""
    analytics_claim(clean, level="L2_corroborated", district=HOME_DISTRICT)
    cur = clean.cursor()
    cur.execute("DELETE FROM verdicts")
    clean.commit()

    body = client().get("/api/v1/analytics").json()
    assert body["totals"]["claims"] == 1
    assert body["totals"]["verdicts"] == 0
    assert body["means"]["confidence"] is None


def test_adjudicated_reflects_a_real_signature(clean) -> None:  # type: ignore[no-untyped-def]
    """`EXISTS` against `adjudications`, not `verdicts.status`. The failure mode
    of trusting that denormalised flag is a provisional count that understates
    how much of the register is unsigned.

    Written through the real ledger, not a hand-built row: `adjudications` is
    hash-chained, and an INSERT with a fabricated `row_hash` would test the
    aggregate against a row the ledger verifier would reject.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.services.audit.ledger import append

    _, verdict_id, _ = analytics_claim(
        clean, level="N3_contradicted", district=HOME_DISTRICT, score=-0.7, confidence=0.7
    )
    assert client().get("/api/v1/analytics").json()["totals"]["provisional"] == 1

    cur = clean.cursor()
    cur.execute("SELECT id::text FROM users WHERE username = %s", (OFFICER,))
    officer_id = cur.fetchone()[0]

    engine = create_engine(sqlalchemy_url(DSN or ""), future=True)
    with Session(engine, future=True) as s:
        append(
            s,
            verdict_id=verdict_id,
            user_id=officer_id,
            decision="reject",
            reason="verified on the ground; no structure present",
        )
        # Undo the denormalised flag so only the ledger row can explain a count.
        s.execute(text("UPDATE verdicts SET status = 'pending' WHERE id = :v"), {"v": verdict_id})
        s.commit()
    engine.dispose()

    totals = client().get("/api/v1/analytics").json()["totals"]
    assert totals["adjudicated"] == 1
    assert totals["provisional"] == 0


def test_the_family_breakdown_partitions_the_available_count(clean) -> None:  # type: ignore[no-untyped-def]
    """`available` is the denominator; agreeing / neutral / disagreeing partition
    it exactly. An unavailable family is in none of the three, because "we did
    not look" is not a neutral reading — its stored agreement is not a
    measurement.

    Bands are the engine's own published thresholds
    (`EngineConfig.agreeing_threshold` +/-0.35), so these counts are the counts
    the level rules used. 0.20 is inside the neutral band on purpose: a family
    that leans positive but is not "agreeing" to the engine must not be counted
    as agreeing here.
    """
    analytics_claim(
        clean,
        level="L2_corroborated",
        district=HOME_DISTRICT,
        families={
            "terrain": (0.9, True),
            "satellite": (0.2, True),
            "temporal": (-0.9, True),
            "control": (0.8, False),
        },
    )

    families = client().get("/api/v1/analytics").json()["families"]
    assert sorted(families) == sorted(
        ["terrain", "satellite", "temporal", "photo", "control", "context"]
    )
    assert families["terrain"] == {
        "available": 1,
        "agreeing": 1,
        "neutral": 0,
        "disagreeing": 0,
    }
    assert families["satellite"] == {
        "available": 1,
        "agreeing": 0,
        "neutral": 1,
        "disagreeing": 0,
    }
    assert families["temporal"] == {
        "available": 1,
        "agreeing": 0,
        "neutral": 0,
        "disagreeing": 1,
    }
    assert families["control"] == {
        "available": 0,
        "agreeing": 0,
        "neutral": 0,
        "disagreeing": 0,
    }, "an unavailable family's stored agreement was read as a measurement"
    assert families["photo"] == {
        "available": 0,
        "agreeing": 0,
        "neutral": 0,
        "disagreeing": 0,
    }, "a family with no rows at all must still carry a zero"


def test_family_evidence_from_another_district_is_not_aggregated(clean) -> None:  # type: ignore[no-untyped-def]
    """`evidence` is LIST-partitioned by district, so the family query joins
    `claims` for the jurisdiction predicate. If that join were dropped for
    speed, another district's evidence would appear in this district's summary.
    """
    analytics_claim(
        clean,
        level="L2_corroborated",
        district=HOME_DISTRICT,
        families={"terrain": (0.9, True)},
    )
    analytics_claim(
        clean,
        level="L2_corroborated",
        district=OTHER_DISTRICT,
        families={"terrain": (0.9, True), "satellite": (0.9, True)},
    )

    families = client().get("/api/v1/analytics").json()["families"]
    assert families["terrain"]["available"] == 1
    assert families["satellite"]["available"] == 0


def test_provenance_matches_the_register_badge(clean) -> None:  # type: ignore[no-untyped-def]
    """The same rule as the register's per-row badge, asserted against that
    function directly.

    Both classify a verdict lineage, one row at a time in Python and a whole
    register at a time in SQL. Nothing in the type system couples them, so a
    claim badged `measured` in the table could be counted `golden` in the summary
    of that same table. This is the test that makes the divergence loud.
    """
    from app.api.v1.claims import _provenance

    measured_note = "HLS S30/L30 granules, EDL presigned"
    golden_note = "GOLDEN CASE — synthetic bundle from 01_l4_check_dam_clean.yaml"
    assert _provenance(measured_note) == "measured"
    assert _provenance(golden_note) == "golden"
    assert _provenance(None) == "golden"

    analytics_claim(
        clean, level="L4_control_differenced", district=HOME_DISTRICT, provenance=measured_note
    )
    analytics_claim(clean, level="L2_corroborated", district=HOME_DISTRICT, provenance=golden_note)
    # No provenance key at all: the register defaults such a row to `golden`
    # because a synthetic row mislabelled as measured is the worse failure.
    analytics_claim(clean, level="L1_observed", district=HOME_DISTRICT, provenance=None)

    body = client().get("/api/v1/analytics").json()
    assert body["provenance"] == {"measured": 1, "golden": 2}


def test_an_unrecorded_detectability_gate_is_counted_as_neither(clean) -> None:  # type: ignore[no-untyped-def]
    """`claims.detectability` is nullable TEXT written as 'passed' or 'failed'.
    Mapping NULL onto 'failed' would invent a sensor-physics result, so the two
    counts deliberately do not have to sum to `totals.claims`."""
    analytics_claim(
        clean, level="L3_multi_indicator", district=HOME_DISTRICT, detectability="passed"
    )
    analytics_claim(clean, level="N1_inconclusive", district=HOME_DISTRICT, detectability="failed")
    analytics_claim(clean, level="L0_recorded", district=HOME_DISTRICT, detectability=None)

    body = client().get("/api/v1/analytics").json()
    assert body["detectability"] == {"passed": 1, "not_passed": 1}
    assert body["totals"]["claims"] == 3


def test_the_refusal_list_is_populated_and_names_its_reasons(clean) -> None:  # type: ignore[no-untyped-def]
    """`refused` is a response field, not a comment. An empty list would mean
    this endpoint claims it computes everything worth computing."""
    analytics_claim(clean, level="L3_multi_indicator", district=HOME_DISTRICT)

    refused = client().get("/api/v1/analytics").json()["refused"]
    assert len(refused) >= 4
    blob = " ".join(refused).lower()
    assert "accuracy" in blob
    assert "no labelled ground-truth set exists" in blob
    assert "l4_control_differenced" in blob
    assert "ndvi" in blob
    # Every entry carries a reason, not just a metric name. A bare list of
    # nouns would be a disclaimer; the reason is what makes it auditable.
    assert all(len(entry) > 40 for entry in refused), refused


def test_the_ndvi_refusal_states_its_own_coverage(clean) -> None:  # type: ignore[no-untyped-def]
    """The district mean NDVI delta is refused *because* of what is stored, so
    the reason quotes the measured share rather than asserting a generality."""
    analytics_claim(
        clean,
        level="L4_control_differenced",
        district=HOME_DISTRICT,
        provenance="HLS S30/L30 granules",
    )
    analytics_claim(clean, level="L2_corroborated", district=HOME_DISTRICT, provenance=None)

    refused = client().get("/api/v1/analytics").json()["refused"]
    ndvi = [entry for entry in refused if "NDVI" in entry]
    assert len(ndvi) == 1, refused
    assert "1 of 2" in ndvi[0], ndvi[0]
