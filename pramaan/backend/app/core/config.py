"""Runtime configuration. Single source of truth for env-derived settings.

Never read `os.environ` outside this module — that discipline is what lets
`services/reconcile` stay provably free of environment coupling (see the
purity test in tests/unit/test_engine_purity.py).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://pramaan:pramaan_dev@localhost:5432/pramaan"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    object_store_endpoint: str = Field(default="http://localhost:9000")
    object_store_access_key: str = Field(default="pramaan")
    object_store_secret_key: str = Field(default="pramaan_dev_key")

    jwt_access_ttl_minutes: int = 20
    jwt_refresh_ttl_hours: int = 12

    # Demo/offline mode: forbids outbound calls to external STAC/WMS sources;
    # producers must read from the pre-cached data/cache tree only.
    pramaan_offline: bool = False

    engine_version: str = "engine-v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
