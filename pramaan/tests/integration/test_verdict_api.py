"""The M8 integration gate: task -> database -> API -> recompute.

docs §28.3's gate is *"upload one real photograph -> a fully populated
EvidenceBundle -> engine-v1 returns a Verdict with non-empty dissent -> row in
DB"*. The photograph half needs a model checkpoint and a district on disk
(M3/M6). This file proves everything downstream of the producers, against a real
PostGIS instance and the real FastAPI app:

    canonical bundle payload
      -> reconcile_claim (the Celery task body)
      -> verdicts + evidence rows
      -> GET /api/v1/verdicts/{id}
      -> POST /api/v1/verdicts/{id}/recompute  == identical

Skipped without `PRAMAAN_TEST_DSN`. `make test-db` runs it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for extra in (REPO_ROOT / "backend", REPO_ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

DSN = os.environ.get("PRAMAAN_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set PRAMAAN_TEST_DSN to run database integration tests"
)
psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")


def _sqlalchemy_url(dsn: str) -> str:
    """`PRAMAAN_TEST_DSN` is libpq keyword form; SQLAlchemy needs a URL."""
    parts = dict(kv.split("=", 1) for kv in dsn.split())
    return (
        f"postgresql+psycopg://{parts['user']}:{parts['password']}"
        f"@{parts['host']}:{parts.get('port', '5432')}/{parts['dbname']}"
    )


@pytest.fixture(scope="module", autouse=True)
def _point_app_at_the_test_database():
    """Set DATABASE_URL before anything imports the engine, and clear the
    cached engine/sessionmaker afterwards so no other test inherits it."""
    assert DSN is not None
    os.environ["DATABASE_URL"] = _sqlalchemy_url(DSN)

    from app.core.config import get_settings
    from app.db.session import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


POLY = "ST_GeomFromText('MULTIPOLYGON(((77 19,77.1 19,77.1 19.1,77 19.1,77 19)))',4326)"


@pytest.fixture()
def claim_id() -> int:
    """A committed claim. Committed, not rolled back, because the task under
    test opens its own session and would not see an uncommitted row."""
    con = psycopg.connect(DSN)
    cur = con.cursor()
    tag = os.urandom(4).hex()
    cur.execute(f"INSERT INTO watersheds (ws_code,geom) VALUES ('WS-{tag}',{POLY}) RETURNING id")
    ws = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO sub_watersheds (sws_code,watershed_id,geom) "
        f"VALUES ('SWS-{tag}',%s,{POLY}) RETURNING id",
        (ws,),
    )
    sws = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO micro_watersheds (mws_code,sub_ws_id,state_lgd,district_lgd,geom,"
        f"analysis_srid) VALUES ('MWS-{tag}',%s,'27','520',{POLY},32643) RETURNING id",
        (sws,),
    )
    mws = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO projects (project_code,name,mws_id,state_lgd,district_lgd) "
        f"VALUES ('WDC-{tag}','M8',%s,'27','520') RETURNING id",
        (mws,),
    )
    proj = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO interventions (unique_id,project_id,mws_id,district_lgd,type,status,"
        "completed_date,geom,expected_footprint_m2) VALUES "
        f"('MH-520-{tag}',%s,%s,'520','check_dam','completed','2023-11-20',"
        "ST_SetSRID(ST_MakePoint(77.05,19.05),4326),3200) RETURNING id",
        (proj, mws),
    )
    iv = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO claims (intervention_id,district_lgd,asserted_status,asserted_date,"
        "geom,uncertainty_m,detectability) VALUES (%s,'520','completed','2023-11-20',"
        "ST_SetSRID(ST_MakePoint(77.05,19.05),4326),15,'passed') RETURNING id",
        (iv,),
    )
    cid = int(cur.fetchone()[0])
    con.commit()
    yield cid
    # Ordered teardown: migration 0001 does not put ON DELETE CASCADE on the
    # hierarchy FKs, so deleting the watershed first is a foreign-key violation.
    # Claims cascade to verdicts and evidence, which is why those are not listed.
    for stmt, arg in (
        ("DELETE FROM claims WHERE intervention_id=%s", iv),
        ("DELETE FROM interventions WHERE id=%s", iv),
        ("DELETE FROM projects WHERE id=%s", proj),
        ("DELETE FROM micro_watersheds WHERE id=%s", mws),
        ("DELETE FROM sub_watersheds WHERE id=%s", sws),
        ("DELETE FROM watersheds WHERE id=%s", ws),
    ):
        cur.execute(stmt, (arg,))
    con.commit()
    con.close()


def _payload(**kwargs):  # type: ignore[no-untyped-def]
    """The canonical JSON the task takes on the wire.

    Shaped exactly like the `lineage` column, which is why the same
    `bundle_from_lineage` consumes both.
    """
    from conftest import all_agreeing, bundle

    from app.services.audit import wire_payload

    kwargs.setdefault("families", all_agreeing(1.0))
    return wire_payload(bundle(**kwargs))


def _client():  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# --- the task ------------------------------------------------------------


def test_task_persists_a_verdict_and_six_evidence_rows(claim_id: int) -> None:
    from app.workers.reconcile import reconcile_claim

    result = reconcile_claim(claim_id, _payload())

    assert result["claim_id"] == claim_id
    assert result["level"] == "L4_control_differenced"
    assert result["label"] == "CORROBORATED"
    assert result["families_available"] == 6
    assert result["status"] == "pending", "a fresh verdict is PROVISIONAL"

    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute("SELECT count(*) FROM evidence WHERE claim_id=%s", (claim_id,))
    assert cur.fetchone()[0] == 6
    cur.execute(
        "SELECT version, status, lineage IS NOT NULL, verdict_digest IS NOT NULL "
        "FROM verdicts WHERE id=%s",
        (result["verdict_id"],),
    )
    version, status, has_lineage, has_digest = cur.fetchone()
    assert (version, status) == (1, "pending")
    assert has_lineage and has_digest, "without these /recompute cannot answer"
    con.close()


def test_task_is_atomic_when_evidence_insert_fails(claim_id: int) -> None:
    """A verdict whose evidence tree disagrees with its own score is
    unauditable, so the two writes share one transaction."""
    from sqlalchemy.exc import DatabaseError

    from app.workers.reconcile import reconcile_claim

    payload = _payload()
    # 'terrain ' with a trailing space is not a member of the evidence_family
    # enum, so the evidence insert fails after the verdict insert succeeded.
    payload["bundle"]["families"][0]["family"] = "terrain "

    with pytest.raises((DatabaseError, ValueError, KeyError)):
        reconcile_claim(claim_id, payload)

    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute("SELECT count(*) FROM verdicts WHERE claim_id=%s", (claim_id,))
    assert cur.fetchone()[0] == 0, "the verdict row must have rolled back too"
    con.close()


def test_second_run_appends_a_version_and_supersedes_the_first(claim_id: int) -> None:
    """Re-adjudication appends; it never overwrites. That is what makes the
    ledger's history readable."""
    from app.workers.reconcile import reconcile_claim

    first = reconcile_claim(claim_id, _payload())
    second = reconcile_claim(claim_id, _payload())
    assert second["verdict_id"] != first["verdict_id"]

    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute(
        "SELECT version, status FROM verdicts WHERE claim_id=%s ORDER BY version",
        (claim_id,),
    )
    rows = cur.fetchall()
    assert [r[0] for r in rows] == [1, 2]
    assert [r[1] for r in rows] == ["superseded", "pending"]
    con.close()


def test_an_unavailable_family_lowers_coverage_rather_than_failing(claim_id: int) -> None:
    """A cloud gap must not void a verdict. The task must still persist one,
    with the gap disclosed in the dissent panel."""
    from conftest import fam

    from app.workers.reconcile import reconcile_claim

    result = reconcile_claim(claim_id, _payload(families=(fam("terrain", 1.0), fam("photo", 0.9))))
    assert result["families_available"] == 2
    assert result["coverage"] < 1.0
    assert result["level"].startswith(("L", "N"))

    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute(
        "SELECT jsonb_array_length(dissent) FROM verdicts WHERE id=%s",
        (result["verdict_id"],),
    )
    assert cur.fetchone()[0] > 0, "a verdict without stated dissent is not shippable"
    con.close()


# --- the API -------------------------------------------------------------


def test_get_verdict_marks_an_unadjudicated_verdict_provisional(claim_id: int) -> None:
    from app.workers.reconcile import reconcile_claim

    vid = reconcile_claim(claim_id, _payload())["verdict_id"]
    r = _client().get(f"/api/v1/verdicts/{vid}")
    assert r.status_code == 200
    body = r.json()
    assert body["provisional"] is True
    assert body["status"] == "pending"
    assert "PROVISIONAL" in body["note"]
    assert body["dissent"], "dissent must survive the round trip to the API"
    assert body["level"] == "L4_control_differenced"


def test_latest_verdict_endpoint_returns_the_newest_version(claim_id: int) -> None:
    from app.workers.reconcile import reconcile_claim

    reconcile_claim(claim_id, _payload())
    second = reconcile_claim(claim_id, _payload())
    body = _client().get(f"/api/v1/claims/{claim_id}/verdict").json()
    assert body["id"] == second["verdict_id"]
    assert body["version"] == 2


def test_unknown_ids_are_404_not_500() -> None:
    client = _client()
    assert client.get("/api/v1/verdicts/99999999").status_code == 404
    assert client.get("/api/v1/claims/99999999/verdict").status_code == 404


# --- the recompute proof -------------------------------------------------


@pytest.mark.parametrize(
    "families_key",
    ["corroborated", "contradicted", "inconclusive"],
)
def test_recompute_through_the_api_is_identical(claim_id: int, families_key: str) -> None:
    """docs §21.3, as an HTTP request a judge can click."""
    from conftest import all_agreeing, fam, gates

    from app.services.reconcile import Alternative
    from app.workers.reconcile import reconcile_claim

    cases = {
        "corroborated": {"families": all_agreeing(1.0)},
        "contradicted": {
            "families": (
                fam("terrain", -1.0),
                fam("satellite", -1.0, cluster_scale=True),
                fam("temporal", -1.0, cluster_scale=True),
                fam("photo", 0.4),
            ),
            "gate": gates(passed=False, footprint_m2=625.0, escalated=True),
            "alternatives": (Alternative(description="gps error", excluded=True, basis="340 m"),),
        },
        "inconclusive": {"families": (fam("photo", 0.4),)},
    }
    vid = reconcile_claim(claim_id, _payload(**cases[families_key]))["verdict_id"]

    r = _client().post(f"/api/v1/verdicts/{vid}/recompute")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identical"] is True
    assert body["differences"] == []
    assert body["hash_before"] == body["hash_after"]
    assert len(body["hash_after"]) == 64
    assert body["engine_version_stored"] == body["engine_version_current"]


def test_recompute_does_not_write_a_new_verdict(claim_id: int) -> None:
    """Proving reproducibility must not mutate the record being proved."""
    from app.workers.reconcile import reconcile_claim

    vid = reconcile_claim(claim_id, _payload())["verdict_id"]
    client = _client()
    for _ in range(3):
        assert client.post(f"/api/v1/verdicts/{vid}/recompute").status_code == 200

    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute("SELECT count(*) FROM verdicts WHERE claim_id=%s", (claim_id,))
    assert cur.fetchone()[0] == 1
    con.close()


def test_recompute_of_a_row_without_lineage_is_422_with_a_reason(claim_id: int) -> None:
    """ "Cannot recompute" without a reason is indistinguishable from a bug."""
    from app.workers.reconcile import reconcile_claim

    vid = reconcile_claim(claim_id, _payload())["verdict_id"]
    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute("UPDATE verdicts SET lineage='{}'::jsonb WHERE id=%s", (vid,))
    con.commit()
    con.close()

    r = _client().post(f"/api/v1/verdicts/{vid}/recompute")
    assert r.status_code == 422
    assert "migration 0002" in r.json()["detail"]


def test_a_tampered_digest_is_409_and_names_what_moved(claim_id: int) -> None:
    """The failure mode this endpoint exists to detect. 409, not 500: a
    mismatch is a correct answer about the data, not a server fault."""
    from app.workers.reconcile import reconcile_claim

    vid = reconcile_claim(claim_id, _payload())["verdict_id"]
    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute(
        "UPDATE verdicts SET verdict_digest=%s, level='N3_contradicted' WHERE id=%s",
        ("0" * 64, vid),
    )
    con.commit()
    con.close()

    r = _client().post(f"/api/v1/verdicts/{vid}/recompute")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["identical"] is False
    assert any("level" in d for d in detail["differences"]), detail["differences"]
    assert detail["hash_before"] != detail["hash_after"]


def test_reason_prose_survives_the_wire(claim_id: int) -> None:
    """`bundle_payload` deliberately excludes prose so it cannot affect a
    digest. `wire_payload` carries it separately, because the persisted
    evidence rows are what the UI's evidence tree displays — and a first draft
    of the task lost it, replacing every reason with a placeholder.
    """
    from app.workers.reconcile import reconcile_claim

    reconcile_claim(claim_id, _payload())

    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute(
        "SELECT family, payload->>'reason' FROM evidence WHERE claim_id=%s",
        (claim_id,),
    )
    reasons = dict(cur.fetchall())
    con.close()

    assert len(reasons) == 6
    assert all(r for r in reasons.values()), "no reason may be empty"
    assert not any("not retained" in r for r in reasons.values()), (
        f"reason prose was lost on the wire: {reasons}"
    )


def test_evidence_is_updated_in_place_not_duplicated(claim_id: int) -> None:
    """`UNIQUE (claim_id, family, district_lgd)` makes evidence current-state.
    Versioned history lives in each verdict's immutable lineage instead, so
    there is exactly one place an auditor reads history from."""
    from conftest import fam

    from app.workers.reconcile import reconcile_claim

    reconcile_claim(claim_id, _payload())
    reconcile_claim(claim_id, _payload(families=(fam("terrain", -1.0),)))

    con = psycopg.connect(DSN)
    cur = con.cursor()
    cur.execute("SELECT count(*) FROM evidence WHERE claim_id=%s", (claim_id,))
    assert cur.fetchone()[0] == 6, "a re-run must update, never append"
    cur.execute(
        "SELECT agreement FROM evidence WHERE claim_id=%s AND family='terrain'",
        (claim_id,),
    )
    assert float(cur.fetchone()[0]) == -1.0, "the row must carry the newest value"
    cur.execute("SELECT count(*) FROM verdicts WHERE claim_id=%s", (claim_id,))
    assert cur.fetchone()[0] == 2, "verdicts, by contrast, are versioned"
    con.close()


# Both rows below reach L4_control_differenced. Only the score differs, and the
# label differs with it — so a derivation from `level` alone cannot pass both.
# Found by searching the engine rather than reasoning about it, after a first
# guess ("agreement 0.3 gives PARTIAL") turned out to give N1 INCONCLUSIVE.
_LABEL_CASES = {
    "corroborated": ((1.0, 1.0, 1.0, 1.0, 1.0, 1.0), "CORROBORATED"),
    "partial": ((1.0, 1.0, -0.6, 1.0, -0.6, -1.0), "PARTIAL"),
}


@pytest.mark.parametrize("case", sorted(_LABEL_CASES))
def test_api_label_matches_the_engine_not_a_reimplementation(claim_id: int, case: str) -> None:
    """`label` is derived, not stored, and depends on level AND score.

    An earlier draft of the read model mapped it from `level` alone and returned
    "L4_control_differenced" where the engine said "CORROBORATED". Both cases
    here sit at the same level, so that shortcut fails one of them.
    """
    from conftest import bundle as make_bundle
    from conftest import fam

    from app.services.audit import wire_payload
    from app.services.reconcile import reconcile
    from app.workers.reconcile import reconcile_claim

    agreements, expected_label = _LABEL_CASES[case]
    families = tuple(
        fam(name, value)
        for name, value in zip(
            ("terrain", "satellite", "temporal", "control", "photo", "context"),
            agreements,
            strict=True,
        )
    )
    b = make_bundle(families=families)
    engine_verdict = reconcile(b)
    assert engine_verdict.level.value == "L4_control_differenced", (
        "both cases must share a level or this test proves nothing"
    )

    vid = reconcile_claim(claim_id, wire_payload(b))["verdict_id"]
    body = _client().get(f"/api/v1/verdicts/{vid}").json()

    assert body["level"] == engine_verdict.level.value
    assert body["label"] == engine_verdict.label, (
        "the API must derive the label the same way the engine does"
    )
    assert body["label"] == expected_label
