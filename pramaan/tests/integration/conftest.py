"""Shared fixtures for the database integration suites.

These exist so the ledger suite and the recompute suite build their claim
hierarchies the same way. Two files inventing two slightly different claim
fixtures is how integration suites start disagreeing about what a valid row
looks like — and the schema is the thing under test, so that disagreement would
be invisible.

Skipped without a database. Run with:

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

DSN = os.environ.get("PRAMAAN_TEST_DSN")

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
    psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")
    if not DSN:
        pytest.skip("set PRAMAAN_TEST_DSN to run database integration tests")
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
    # `os.urandom`, not `id(con)`: CPython reuses object addresses after a
    # connection is collected, so two tests in one process could mint the same
    # `ws_code`. That was latent while every row rolled back, and became a
    # UniqueViolation as soon as a fixture committed one.
    tag = os.urandom(5).hex()
    cur.execute(
        f"INSERT INTO watersheds (ws_code,geom) VALUES (%s,{POLY}) RETURNING id",
        (f"WS-{tag}",),
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


def _insert_verdict(cur, row: dict) -> int:  # type: ignore[no-untyped-def]
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


@pytest.fixture()
def insert_verdict():  # type: ignore[no-untyped-def]
    """Exposed as a fixture rather than imported.

    Two `conftest.py` modules exist under `tests/`, and an explicit
    `from bundles import ...` resolves by `sys.path` order rather than by
    proximity. Fixture injection is pytest's own mechanism and has no such
    ambiguity.
    """
    return _insert_verdict


@pytest.fixture()
def verdict(con, claim):  # type: ignore[no-untyped-def]
    """A minimal PROVISIONAL verdict, ready to be adjudicated.

    The ledger suite needs "a verdict that nobody has signed yet" and nothing
    else about it, so this deliberately does not run the engine. Its numbers
    satisfy invariant I1 (`confidence <= score`) because the CHECK constraint
    is live and would otherwise reject the row.
    """
    cur = con.cursor()
    return _insert_verdict(
        cur,
        {
            "claim_id": claim,
            "version": 1,
            "level": "L2_corroborated",
            "rule_path": ["L2_TWO_FAMILIES"],
            "score": 0.5,
            "confidence": 0.4,
            "coverage": 0.5,
            "quality": 1.0,
            "data_sufficiency": 1.0,
            "dissent": ["single season observed"],
            "recommended_action": {"action": "no action"},
            "engine_version": "engine-v1",
            "weights": {"terrain": 0.25},
            # 'pending' is the schema default and the only unsigned value the
            # verdict_status_vocabulary CHECK admits (migration 0005).
            "status": "pending",
            "lineage": {"engine_version": "engine-v1"},
            "bundle_digest": "0" * 64,
            "verdict_digest": "1" * 64,
        },
    )
