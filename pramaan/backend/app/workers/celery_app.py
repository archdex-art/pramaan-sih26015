"""Celery application.

Queues are separated by resource profile, not by feature. Raster work is
CPU-and-memory heavy and slow; inference is CPU heavy and bursty; ingestion is
IO bound and fast. Putting them on one queue means a district-wide index rebuild
blocks a field officer's photo upload for twenty minutes.

Task modules are registered here as they land per stage (docs §28.2):
  Stage 3 — ingestion, inference, satellite, terrain
  Stage 5 — reports
Nothing is imported speculatively: an `include` entry for a module that does not
exist yet makes the worker crash on boot, which would defeat Gate 0.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "pramaan",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Registered as each stage lands. `reconcile` has no external
    # dependencies, so it is safe to include from the moment it exists.
    include=["app.workers.reconcile"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # A raster job that dies must not vanish silently: acks_late plus
    # reject_on_worker_lost means an interrupted district onboarding is retried
    # rather than half-applied.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Long raster tasks must not be killed by a default soft limit.
    task_soft_time_limit=3600,
    task_time_limit=4200,
    # Prefetching a batch of 30-minute raster jobs starves other workers.
    worker_prefetch_multiplier=1,
    task_routes={
        "app.workers.ingestion.*": {"queue": "ingest"},
        "app.workers.inference.*": {"queue": "infer"},
        "app.workers.satellite.*": {"queue": "raster"},
        "app.workers.terrain.*": {"queue": "raster"},
        "app.workers.reports.*": {"queue": "reports"},
        # Reconciliation is pure CPU and sub-millisecond; it shares the
        # fast queue rather than waiting behind a raster job.
        "app.workers.reconcile.*": {"queue": "ingest"},
    },
)


@celery_app.task(name="app.workers.healthcheck")
def healthcheck() -> dict[str, str]:
    """Round-trip probe used by Gate 0 to prove the broker path works."""
    return {"status": "ok", "engine_version": settings.engine_version}
