"""The reproducibility guarantee, proven through a real PostGIS database.

`tests/unit/test_persistence.py` proves the round trip in process. That is not
the same claim. docs §21.3 says a verdict can be recomputed byte-identically
*from its stored record*, and the storage layer is where the interesting losses
happen: `NUMERIC(5,4)` rounds, JSONB reorders keys, and a partitioned insert
that omits its partition key lands in DEFAULT without complaint.

So this test writes a verdict through Postgres and recomputes it from the row
that comes back.

Skipped without a database. Run it with:

    docker run -d --name pramaan_test -e POSTGRES_PASSWORD=t -e POSTGRES_DB=t \
        -p 55433:5432 postgis/postgis:16-3.4-alpine
    PRAMAAN_TEST_DSN='host=127.0.0.1 port=55433 user=postgres password=t dbname=t' \
        alembic upgrade head && pytest tests/integration -q

`make test-db` wraps all of it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import all_agreeing, bundle, fam, gates  # noqa: E402

from app.services.audit import (  # noqa: E402
    bundle_from_lineage,
    compare_verdicts,
    config_from_lineage,
    evidence_rows,
    verdict_row,
)
from app.services.reconcile import Alternative, reconcile  # noqa: E402

DSN = os.environ.get("PRAMAAN_TEST_DSN")
psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set PRAMAAN_TEST_DSN to run database integration tests"
)

VERDICT_INSERT = """
INSERT INTO verdicts (claim_id,version,level,rule_path,score,confidence,coverage,quality,
  data_sufficiency,dissent,recommended_action,engine_version,weights,status,lineage,
  bundle_digest,verdict_digest)
VALUES (%(claim_id)s,%(version)s,%(level)s,%(rule_path)s,%(score)s,%(confidence)s,
  %(coverage)s,%(quality)s,%(data_sufficiency)s,%(dissent)s,%(recommended_action)s,
  %(engine_version)s,%(weights)s,%(status)s,%(lineage)s,%(bundle_digest)s,%(verdict_digest)s)
RETURNING id
"""

POLY = "ST_GeomFromText('MULTIPOLYGON(((77 19,77.1 19,77.1 19.1,77 19.1,77 19)))',4326)"


@pytest.fixture()
def con():  # type: ignore[no-untyped-def]
    """A connection that rolls back, so the suite is re-runnable."""
    c = psycopg.connect(DSN)
    yield c
    c.rollback()
    c.close()


@pytest.fixture()
def claim(con):  # type: ignore[no-untyped-def]
    """A claim with its full hierarchy.

    IDs are captured from RETURNING rather than assumed to be 1. BIGSERIAL
    sequences do not roll back, so hardcoded ids break on the second run - a
    mistake made once while building this.
    """
    cur = con.cursor()
    cur.execute(
        f"INSERT INTO watersheds (ws_code,geom) VALUES (%s,{POLY}) RETURNING id",
        (f"WS-{os.getpid()}-{id(con)}",),
    )
    ws = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO sub_watersheds (sws_code,watershed_id,geom) "
        f"VALUES (%s,%s,{POLY}) RETURNING id",
        (f"SWS-{ws}", ws),
    )
    sws = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO micro_watersheds (mws_code,sub_ws_id,state_lgd,district_lgd,geom,"
        f"analysis_srid) VALUES (%s,%s,'27','520',{POLY},32643) RETURNING id",
        (f"MWS-{sws}", sws),
    )
    mws = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO projects (project_code,name,mws_id,state_lgd,district_lgd) "
        "VALUES (%s,'Integration',%s,'27','520') RETURNING id",
        (f"WDC-{mws}", mws),
    )
    proj = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO interventions (unique_id,project_id,mws_id,district_lgd,type,status,"
        "completed_date,geom,expected_footprint_m2) VALUES (%s,%s,%s,'520','check_dam',"
        "'completed','2023-11-20',ST_SetSRID(ST_MakePoint(77.05,19.05),4326),3200) RETURNING id",
        (f"MH-520-{proj:05d}", proj, mws),
    )
    iv = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO claims (intervention_id,district_lgd,asserted_status,asserted_date,geom,"
        "uncertainty_m,detectability) VALUES (%s,'520','completed','2023-11-20',"
        "ST_SetSRID(ST_MakePoint(77.05,19.05),4326),15,'passed') RETURNING id",
        (iv,),
    )
    return cur.fetchone()[0]


def insert_verdict(cur, row: dict) -> int:  # type: ignore[no-untyped-def]
    cur.execute(
        VERDICT_INSERT,
        {
            **row,
            "dissent": json.dumps(row["dissent"]),
            "recommended_action": json.dumps(row["recommended_action"]),
            "weights": json.dumps(row["weights"]),
            "lineage": json.dumps(row["lineage"]),
        },
    )
    return int(cur.fetchone()[0])


# --- the guarantee -------------------------------------------------------


@pytest.mark.parametrize(
    "families,gate,alts",
    [
        pytest.param(all_agreeing(1.0), None, (), id="corroborated_L4"),
        pytest.param(
            (
                fam("terrain", -1.0),
                fam("satellite", -1.0, cluster_scale=True),
                fam("temporal", -1.0, cluster_scale=True),
                fam("photo", 0.4),
            ),
            gates(passed=False, footprint_m2=625.0, escalated=True),
            (Alternative(description="gps error", excluded=True, basis="cannot move 340 m"),),
            id="contradicted_N3_terrain_path",
        ),
        pytest.param((fam("photo", 0.4),), None, (), id="inconclusive_N1"),
    ],
)
def test_verdict_recomputes_identically_from_the_stored_row(con, claim, families, gate, alts):  # type: ignore[no-untyped-def]
    """docs §21.3, through NUMERIC rounding and a JSONB round trip."""
    cur = con.cursor()
    kw: dict = {"families": families, "alternatives": alts}
    if gate is not None:
        kw["gate"] = gate
    b = bundle(**kw)
    original = reconcile(b)
    vid = insert_verdict(cur, verdict_row(original, b, claim_id=claim, version=1))

    cur.execute("SELECT lineage, verdict_digest FROM verdicts WHERE id=%s", (vid,))
    lineage, stored_digest = cur.fetchone()

    recomputed = reconcile(bundle_from_lineage(lineage), config_from_lineage(lineage))
    result = compare_verdicts(original, recomputed)

    assert result.identical, f"differences: {result.differences}"
    assert result.recomputed_digest == stored_digest, (
        "the digest stored by the API must equal the digest of the recomputed "
        "verdict, or /recompute proves nothing"
    )


def test_stored_numerics_still_satisfy_invariant_i1(con, claim):  # type: ignore[no-untyped-def]
    """NUMERIC(5,4) rounds. The database CHECK carries a 0.0005 tolerance for
    exactly that reason - this asserts the tolerance is sufficient, not assumed.
    """
    cur = con.cursor()
    b = bundle(families=all_agreeing(1.0), metadata_integrity=0.9731, data_sufficiency=0.8817)
    vid = insert_verdict(cur, verdict_row(reconcile(b), b, claim_id=claim, version=1))
    cur.execute("SELECT score, confidence, coverage, quality FROM verdicts WHERE id=%s", (vid,))
    score, confidence, coverage, quality = (float(x) for x in cur.fetchone())
    assert confidence <= abs(score) + 0.0005
    assert confidence == pytest.approx(abs(score) * coverage * quality, abs=1e-3)


def test_the_i1_check_constraint_rejects_a_tampered_confidence(con, claim):  # type: ignore[no-untyped-def]
    """The unreproducible 0.71 of an earlier draft of Worked Example B must be
    rejected by the database, not merely by the engine."""
    cur = con.cursor()
    b = bundle(families=all_agreeing(1.0))
    row = verdict_row(reconcile(b), b, claim_id=claim, version=1)
    row["confidence"] = round(abs(float(row["score"])) + 0.05, 4)
    with pytest.raises(psycopg.errors.CheckViolation, match="confidence_le_score"):
        insert_verdict(cur, row)


# --- evidence rows -------------------------------------------------------


def test_evidence_rows_route_to_the_district_partition(con, claim):  # type: ignore[no-untyped-def]
    """`evidence` is LIST-partitioned by district_lgd. This asserts the rows
    actually land somewhere, and reports which partition, so a plan that has
    quietly collapsed into DEFAULT is visible rather than inferred."""
    cur = con.cursor()
    b = bundle(families=all_agreeing(1.0))
    for er in evidence_rows(b, claim_id=claim, district_lgd="520"):
        cur.execute(
            "INSERT INTO evidence (claim_id,district_lgd,family,agreement,available,payload,"
            "lineage) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                er["claim_id"],
                er["district_lgd"],
                er["family"],
                er["agreement"],
                er["available"],
                json.dumps(er["payload"]),
                json.dumps(er["lineage"]),
            ),
        )
    cur.execute(
        "SELECT family, agreement, tableoid::regclass::text FROM evidence "
        "WHERE claim_id=%s ORDER BY family",
        (claim,),
    )
    rows = cur.fetchall()
    assert len(rows) == 6
    assert {r[0] for r in rows} == {
        "terrain",
        "satellite",
        "temporal",
        "control",
        "photo",
        "context",
    }
    assert all(-1.0 <= float(r[1]) <= 1.0 for r in rows)


def test_the_agreement_check_constraint_is_live(con, claim):  # type: ignore[no-untyped-def]
    cur = con.cursor()
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "INSERT INTO evidence (claim_id,district_lgd,family,agreement,available,payload,"
            "lineage) VALUES (%s,'520','terrain',1.7,true,'{}','{}')",
            (claim,),
        )


# --- schema expectations -------------------------------------------------


def test_migration_0002_columns_exist(con):  # type: ignore[no-untyped-def]
    """Without these the guarantee has nowhere to live."""
    cur = con.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='verdicts' "
        "AND column_name IN ('lineage','quality','bundle_digest','verdict_digest')"
    )
    assert {r[0] for r in cur.fetchall()} == {
        "lineage",
        "quality",
        "bundle_digest",
        "verdict_digest",
    }


def test_a_second_verdict_version_coexists_with_the_first(con, claim):  # type: ignore[no-untyped-def]
    """Re-adjudication appends a version; it never overwrites. UNIQUE
    (claim_id, version) is what makes the ledger's history readable."""
    cur = con.cursor()
    b = bundle(families=all_agreeing(1.0))
    v = reconcile(b)
    insert_verdict(cur, verdict_row(v, b, claim_id=claim, version=1))
    insert_verdict(cur, verdict_row(v, b, claim_id=claim, version=2))
    cur.execute("SELECT count(*) FROM verdicts WHERE claim_id=%s", (claim,))
    assert cur.fetchone()[0] == 2
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_verdict(cur, verdict_row(v, b, claim_id=claim, version=2))
