"""The reconciliation task: producer outputs -> verdict -> database.

This is the M8 integration seam (docs §28.3). It owns exactly one
responsibility — assembling the six family adapters into an `EvidenceBundle`,
running the frozen engine, and persisting the result inside one transaction.

## What this task does not do

It does not fetch a raster, run a model, or read a DEM. Each family's producer
already owns its own IO and is tested independently; this task consumes their
outputs. Keeping the seam this thin is deliberate:

* the engine stays pure and the task stays testable without a raster on disk;
* a producer failing is a *family unavailable*, not a task crash — which is the
  behaviour docs §16.2 requires, since an unavailable family lowers coverage
  rather than voiding the verdict.

That last point is the whole reason this is a single task and not a Celery
chain: a chain aborts on first failure, and aborting is precisely wrong here. A
claim whose satellite family is unavailable must still produce an N1 verdict
with a dissent panel saying so.

## Where the producer-side assembly lives

Not here. Turning producer outputs into an `EvidenceBundle` needs each family
adapter's real signature, and an earlier draft of this module guessed five of
them and got all five wrong — caught by `mypy --strict`, which is the only
reason it never shipped. That assembly belongs with the producers that call it
(M5/M6), written against the adapters rather than against a guess. What crosses
the broker is `audit.wire_payload(bundle)`, which is real and tested today.

## Transaction boundary

One transaction per claim, covering the verdict row and all six evidence rows.
A half-written bundle would leave a verdict whose evidence tree disagrees with
its own score, which is unauditable. `session_scope` rolls back on any
exception.
"""

from __future__ import annotations

from typing import Any

from app.db.session import session_scope
from app.db.verdicts import save_verdict
from app.services.audit import (
    bundle_from_lineage,
    config_from_lineage,
)
from app.services.reconcile import reconcile
from app.workers.celery_app import celery_app


@celery_app.task(
    name="app.workers.reconcile.reconcile_claim",
    # A verdict is a pure function of its inputs, so a retry cannot produce a
    # different answer. Retries exist for the database, not the engine.
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    max_retries=3,
)
def reconcile_claim(
    claim_id: int,
    wire: dict[str, Any],
    extra_lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile one claim from its wire payload and persist it.

    ## Why the argument is a payload, not a dataclass

    Celery is configured `task_serializer="json"` (see `celery_app`), so a task
    argument must be JSON. `ProducerOutputs` and the family dataclasses are not,
    and a task that accepts them fails at dispatch — before any code in this
    module runs.

    Rather than invent a second wire format, this takes the **same shape as the
    `lineage` column**: `audit.wire_payload` produces it and
    `bundle_from_lineage` consumes it, so the structure that crosses the broker
    is the structure already proven to reconstruct a byte-identical verdict by
    the recompute tests. One format, one set of tests, no drift.

    Producers therefore assemble in-process with `build_bundle` and enqueue
    `wire_payload(bundle)`.

    The return value is a summary, not the verdict. The verdict lives in the
    database; returning a copy through the result backend would create a second
    version that can disagree with the row.
    """
    bundle = bundle_from_lineage(wire)
    cfg = config_from_lineage(wire)
    verdict = reconcile(bundle, cfg)

    with session_scope() as session:
        verdict_id = save_verdict(
            session,
            verdict,
            bundle,
            claim_id=claim_id,
            cfg=cfg,
            extra_lineage=extra_lineage,
        )

    return {
        "verdict_id": verdict_id,
        "claim_id": claim_id,
        "level": verdict.level.value,
        "label": verdict.label,
        "confidence": verdict.confidence,
        "coverage": verdict.coverage,
        "families_available": len(bundle.available()),
        "status": "pending",
    }
