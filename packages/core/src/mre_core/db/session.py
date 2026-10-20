"""Tenant-scoped database sessions. All tenant data access goes through ``tenant_session``."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction

from mre_core.errors import MissingTenantContext

APP_ROLE = "mre_app"
ADMIN_ROLE = "mre_admin"


def create_db_engine(url: str, **kwargs: Any) -> Engine:
    """Engine for the app role. ``pool_pre_ping`` survives Postgres restarts."""
    return create_engine(url, pool_pre_ping=True, future=True, **kwargs)


def _coerce_tenant_id(tenant_id: uuid.UUID | str | None) -> uuid.UUID:
    if tenant_id is None or (isinstance(tenant_id, str) and not tenant_id.strip()):
        raise MissingTenantContext("a tenant id is required for tenant data access")
    try:
        return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    except ValueError as exc:
        raise MissingTenantContext("tenant id is not a valid UUID") from exc


def _apply_context(conn: Connection, role: str, settings: dict[str, str]) -> None:
    # SET LOCAL lasts until the transaction ends, so it is re-applied on every BEGIN.
    conn.execute(text(f"SET LOCAL ROLE {role}"))
    for key, value in settings.items():
        conn.execute(text("SELECT set_config(:k, :v, true)"), {"k": key, "v": value})


@contextmanager
def _scoped_session(engine: Engine, role: str, settings: dict[str, str]) -> Iterator[Session]:
    session = Session(engine, expire_on_commit=False)

    @event.listens_for(session, "after_begin")
    def _on_begin(_sess: Session, _tx: SessionTransaction, conn: Connection) -> None:
        _apply_context(conn, role, settings)

    try:
        session.begin()  # autobegin also re-fires the hook if the caller commits midway
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def tenant_session(engine: Engine, tenant_id: uuid.UUID | str | None) -> Iterator[Session]:
    """Transaction as ``mre_app`` with ``app.tenant_id`` set; commits on success.

    Raises ``MissingTenantContext`` if no tenant id is given. RLS policies then
    restrict every read and write to that tenant.
    """
    tid = _coerce_tenant_id(tenant_id)
    with _scoped_session(engine, APP_ROLE, {"app.tenant_id": str(tid)}) as session:
        yield session


@contextmanager
def admin_session(engine: Engine, *, actor: str, reason: str) -> Iterator[Session]:
    """Cross-tenant staff access as ``mre_admin``. Callers must audit-log the access."""
    if not actor.strip() or not reason.strip():
        raise ValueError("admin access requires an actor and a reason")
    settings = {"app.admin_actor": actor, "app.admin_reason": reason}
    with _scoped_session(engine, ADMIN_ROLE, settings) as session:
        yield session
