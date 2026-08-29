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
    engine = create_engine(
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
    _drop_privileges(engine)
    return engine


#: The role every application connection drops to after connecting.
APP_ROLE = "pramaan_app"


def _drop_privileges(engine: Engine) -> None:
    """`SET ROLE pramaan_app` on every pooled connection.

    ## Why this exists

    Migration 0001 revokes UPDATE and DELETE on `adjudications` from
    `pramaan_app`, which is what makes the ledger append-only. Measured against
    the running system, the control was doing nothing: the app connects as
    `pramaan`, the table **owner**, whose privileges no revoke on another role
    can restrict. The ledger was append-only in the migration and freely
    mutable in production.

    Dropping to the unprivileged role at connect time is what puts the revoke in
    force. It runs on `connect`, not per query, so it costs one statement per
    pooled connection.

    ## Why `SET ROLE` and not separate credentials

    A second set of credentials is stronger — a compromised process cannot
    `RESET ROLE` its way back to the owner — but it needs a password
    distributed to every service, and a password in a migration or a compose
    file is a worse defect than the one being fixed.

    So the honest scope of this control: it is enforced against **this
    application's own SQL**, which is the realistic path by which an
    append-only table gets updated by accident. It is not a defence against an
    attacker who already has arbitrary SQL execution — such an attacker can
    `RESET ROLE`. Stated plainly in `docs/17-roles-and-ledger.md` rather than
    left for a reviewer to discover.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_role(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            # Fails loudly if the role is missing: a silent fallback to owner
            # privileges would reinstate exactly the defect this closes.
            cursor.execute(f"SET ROLE {APP_ROLE}")
        finally:
            cursor.close()


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
