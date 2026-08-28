"""Database engine and session handling.

## Why Core and not ORM models

The schema is defined once, as raw DDL, in `db/migrations/versions/`. Declaring
SQLAlchemy models beside it would create a second source of truth that drifts:
the partitioning plan, the `confidence_le_score` CHECK and the `evidence_family`
enum are all expressed in DDL that no ORM model would carry. A model layer would
have to restate them and would be believed instead of the migration.

So queries are explicit `text()` against the migrated schema. The cost is
hand-written SQL; the benefit is that the migration is the only place the schema
exists, and `make test-db` runs every query against the real thing.

## Pool sizing

`pool_pre_ping` is on because the API sits behind a connection that idles
between adjudications and Postgres may have closed it. Without it the first
request after an idle period fails with a stale-connection error that looks like
an outage.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """One engine per process. Cached: creating an engine per request leaks
    connection pools and is the classic way to exhaust `max_connections`."""
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        # A raster worker holding a transaction open for an hour blocks vacuum
        # and hides lock contention. Long work happens outside transactions;
        # this is a guard, not a policy.
        pool_recycle=1800,
        future=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception.

    Used by workers. The API uses `db_session` so FastAPI owns the lifecycle.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_session() -> Iterator[Session]:
    """FastAPI dependency. Does not commit: a GET must not, and a writing
    endpoint commits explicitly so the commit point is visible in the handler."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
