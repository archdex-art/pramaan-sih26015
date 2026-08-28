from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import claims, method, temporal, verdicts
from app.core.config import get_settings
from app.services.reconcile.weights import ENGINE_VERSION

app = FastAPI(
    title="PRAMAAN API",
    version="0.1.0",
    description=(
        "Evidence reconciliation for geo-coded watershed development claims.\n\n"
        "Every verdict this API returns is PROVISIONAL until a named officer "
        "accepts, edits or rejects it. The engine's ceiling is L4 "
        "(control-differenced); it never issues a causal verdict."
    ),
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
)

app.include_router(method.router, prefix="/api/v1")
app.include_router(verdicts.router, prefix="/api/v1")
app.include_router(temporal.router, prefix="/api/v1")
app.include_router(claims.router, prefix="/api/v1")


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    """Liveness probe. Used by the compose healthcheck and by Gate 0."""
    settings = get_settings()
    return {
        "status": "ok",
        "engine_version": ENGINE_VERSION,
        "offline_mode": str(settings.pramaan_offline).lower(),
    }
