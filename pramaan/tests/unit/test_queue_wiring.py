"""Every routed Celery queue must actually be consumed by a worker.

## The bug this exists to prevent

`celery_app.conf.task_routes` sends each task to a named queue. A worker started
without `-Q` consumes only the default `celery` queue. Combine the two and the
broker cheerfully accepts tasks that no worker will ever run: no error, no
retry, no dead-letter — the task just sits in Redis.

That shipped. It was found by enqueuing a real task against the running stack
and watching `llen ingest` stay at 1 while the worker logged nothing. It was
invisible until then because the only existing task, Gate 0's `healthcheck`, has
no route entry and therefore lands on the default queue.

These tests read the two files that have to agree — `celery_app.py` and
`docker-compose.yml` — and assert they do. Cheap, and it closes a silent-failure
class rather than one instance of it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

yaml = pytest.importorskip("yaml", reason="pyyaml not installed")

COMPOSE = REPO_ROOT / "docker-compose.yml"
DEFAULT_QUEUE = "celery"


def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def worker_services() -> dict[str, list[str]]:
    """Service name -> its command argv, for every celery worker service."""
    out: dict[str, list[str]] = {}
    for name, spec in compose()["services"].items():
        cmd = spec.get("command")
        if not cmd:
            continue
        argv = cmd if isinstance(cmd, list) else cmd.split()
        if "celery" in argv and "worker" in argv:
            out[name] = argv
    return out


def consumed_queues(argv: list[str]) -> set[str]:
    """Queues a worker consumes. No `-Q` means the default queue only."""
    for flag in ("-Q", "--queues"):
        if flag in argv:
            return {q.strip() for q in argv[argv.index(flag) + 1].split(",") if q.strip()}
    return {DEFAULT_QUEUE}


def routed_queues() -> set[str]:
    from app.workers.celery_app import celery_app

    return {
        route["queue"]
        for route in celery_app.conf.task_routes.values()
        if isinstance(route, dict) and "queue" in route
    }


# --- the invariant -------------------------------------------------------


def test_at_least_one_worker_service_exists() -> None:
    """Guards the tests below: they all pass vacuously with no workers."""
    assert worker_services(), "no celery worker service found in docker-compose.yml"


def test_every_routed_queue_is_consumed_by_some_worker() -> None:
    """The actual bug. A routed queue nobody consumes is a silent black hole."""
    consumed: set[str] = set()
    for argv in worker_services().values():
        consumed |= consumed_queues(argv)

    orphaned = routed_queues() - consumed
    assert not orphaned, (
        f"tasks are routed to {sorted(orphaned)} but no worker consumes them. "
        f"Workers consume {sorted(consumed)}. Such tasks are accepted by the "
        "broker and never run, with no error anywhere."
    )


def test_the_default_queue_is_consumed() -> None:
    """Unrouted tasks — Gate 0's healthcheck, and anything added without a
    `task_routes` entry — land on `celery`. If nothing consumes it, adding a
    task without a route silently does nothing."""
    consumed: set[str] = set()
    for argv in worker_services().values():
        consumed |= consumed_queues(argv)
    assert DEFAULT_QUEUE in consumed, (
        "no worker consumes the default queue, so any task without an explicit route will never run"
    )


def test_every_worker_declares_its_queues_explicitly() -> None:
    """A worker without `-Q` silently consumes only the default queue, which is
    almost never what a multi-queue deployment intends. Requiring `-Q` makes the
    intent reviewable in the diff."""
    for name, argv in worker_services().items():
        assert "-Q" in argv or "--queues" in argv, (
            f"worker service '{name}' has no -Q, so it consumes only "
            f"'{DEFAULT_QUEUE}' regardless of task_routes"
        )


def test_raster_and_ingest_are_not_on_the_same_worker() -> None:
    """`celery_app`'s docstring justifies separate queues by saying a
    district-wide raster rebuild must not block a field officer's photo upload.
    Queues alone do not achieve that — one worker consuming both puts them back
    in the same line. This asserts the separation is real."""
    for name, argv in worker_services().items():
        queues = consumed_queues(argv)
        assert not ({"raster", "reports"} & queues and {"ingest", "infer"} & queues), (
            f"worker '{name}' consumes both raster and ingest queues "
            f"({sorted(queues)}), which defeats the separation celery_app "
            "exists to provide"
        )


def test_raster_worker_concurrency_is_bounded() -> None:
    """A windowed COG read holds a decompressed tile block in memory and runs
    for minutes. Oversubscribing the raster worker is how a district onboarding
    gets OOM-killed halfway through."""
    for name, argv in worker_services().items():
        if "raster" not in consumed_queues(argv):
            continue
        assert "--concurrency" in argv, f"'{name}' must pin raster concurrency"
        value = int(argv[argv.index("--concurrency") + 1])
        assert 1 <= value <= 2, (
            f"'{name}' raster concurrency {value} is too high for jobs that "
            "hold raster blocks in memory"
        )


def test_routes_cover_every_worker_task_module() -> None:
    """A task module with no route entry lands on the default queue. That may be
    intended, but it must be a decision rather than an oversight, so the route
    table is checked against the modules that actually exist."""
    from app.workers.celery_app import celery_app

    celery_app.loader.import_default_modules()
    modules = {
        name.rsplit(".", 1)[0]
        for name in celery_app.tasks
        if name.startswith("app.workers.") and name.count(".") > 2
    }
    routed_prefixes = {pattern.rstrip(".*") for pattern in celery_app.conf.task_routes}
    unrouted = modules - routed_prefixes
    assert not unrouted, (
        f"task modules {sorted(unrouted)} have no task_routes entry and will "
        "run on the default queue"
    )


def test_reconcile_task_is_registered_and_routed() -> None:
    """The M8 task specifically: registered, and on a queue a worker consumes."""
    from app.workers.celery_app import celery_app

    celery_app.loader.import_default_modules()
    name = "app.workers.reconcile.reconcile_claim"
    assert name in celery_app.tasks

    queue = celery_app.amqp.router.route({}, name)["queue"].name
    consumed: set[str] = set()
    for argv in worker_services().values():
        consumed |= consumed_queues(argv)
    assert queue in consumed, f"{name} routes to '{queue}', which nobody consumes"


def test_compose_worker_commands_reference_the_real_app() -> None:
    """A typo in `-A` produces a worker that starts, logs nothing unusual, and
    registers no tasks."""
    for name, argv in worker_services().items():
        assert "-A" in argv, f"'{name}' has no -A"
        app_path = argv[argv.index("-A") + 1]
        assert app_path == "app.workers.celery_app", f"'{name}' points -A at '{app_path}'"
        module = REPO_ROOT / "backend" / (app_path.replace(".", "/") + ".py")
        assert module.is_file(), f"'{name}' points -A at a module that does not exist"


def test_queue_names_are_plain_identifiers() -> None:
    """A queue name with a stray space or comma silently becomes a different
    queue, which is the same black hole by another route."""
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for queue in routed_queues():
        assert pattern.match(queue), f"suspicious queue name {queue!r}"
