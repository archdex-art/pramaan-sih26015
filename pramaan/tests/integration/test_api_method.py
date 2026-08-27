"""API smoke tests for the method endpoints.

These need no database: the method endpoints are pure reads off the engine's
config. They exist to protect a specific promise from docs §14.4 — that the
weights the UI displays are the weights the engine used, served from one source.

If someone ever hardcodes a weight table into the frontend, the drift will not
be caught by a test in the frontend. It is caught here, by asserting the API's
numbers equal the engine's.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402
from app.services.reconcile import SIGNATURES, Level  # noqa: E402
from app.services.reconcile.types import FAMILIES  # noqa: E402
from app.services.reconcile.weights import (  # noqa: E402
    DEFAULT_WEIGHTS,
    ENGINE_VERSION,
    EngineConfig,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_healthz_reports_engine_version(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["engine_version"] == ENGINE_VERSION


def test_openapi_schema_is_served(client: TestClient) -> None:
    """The auto-generated OpenAPI doc is a submission deliverable (docs §29)."""
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "PRAMAAN API"
    assert "/healthz" in schema["paths"]
    assert "/api/v1/method/weights" in schema["paths"]


def test_weights_endpoint_matches_the_engine_exactly(client: TestClient) -> None:
    """The anti-drift guarantee: one copy of the weights, served, not duplicated."""
    r = client.get("/api/v1/method/weights")
    assert r.status_code == 200
    body = r.json()

    assert body["weights"] == dict(DEFAULT_WEIGHTS)
    assert body["weight_sum"] == pytest.approx(1.0)
    assert body["families"] == list(FAMILIES)
    assert body["engine_version"] == ENGINE_VERSION
    assert body["config_fingerprint"] == EngineConfig().fingerprint()
    # `metadata` must never appear as a family (ADR-001).
    assert "metadata" not in body["weights"]


def test_thresholds_endpoint_matches_the_engine(client: TestClient) -> None:
    cfg = EngineConfig()
    body = client.get("/api/v1/method/thresholds").json()
    assert body["agreement"]["agreeing_at_or_above"] == cfg.agreeing_threshold
    assert body["negative"]["n3_terrain_max_agreement"] == cfg.n3_terrain_max_agreement
    assert body["levels"]["l4_min_coverage"] == cfg.l4_min_coverage


def test_ladder_publishes_the_ceiling_and_refuses_l5(client: TestClient) -> None:
    body = client.get("/api/v1/method/ladder").json()
    assert body["ceiling"] == Level.L4_CONTROL_DIFFERENCED.value
    assert len(body["levels"]) == 8
    assert not any("L5" in level for level in body["levels"])
    assert "L5_causal" in body["refused"]
    # Both N3 paths must be published: the distinction is the D1 fix.
    assert set(body["n3_paths"]) == {"N3_SATELLITE_PATH", "N3_TERRAIN_PATH"}


def test_signatures_endpoint_publishes_the_honest_rows(client: TestClient) -> None:
    """The types the system cannot assess are published, not hidden (docs §18.1)."""
    body = client.get("/api/v1/method/signatures").json()
    assert set(body["signatures"]) == set(SIGNATURES)
    not_assessable = set(body["not_optically_assessable"])
    for expected in ("dug_well", "borewell", "livestock", "livelihood", "recharge_shaft"):
        assert expected in not_assessable, expected
    # Every signature must declare a ceiling the engine can enforce.
    valid = {level.value for level in Level}
    for key, sig in body["signatures"].items():
        assert sig["confidence_ceiling"] in valid, key
