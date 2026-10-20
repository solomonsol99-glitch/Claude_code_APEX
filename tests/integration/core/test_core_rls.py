"""Tenant isolation through Postgres RLS (§3.1, §7.2)."""

from __future__ import annotations

import uuid

import pytest
from mre_core.db.models import TENANT_TABLES, Base, Job
from mre_core.db.rls import tables_missing_rls
from mre_core.db.session import admin_session, tenant_session
from mre_core.errors import MissingTenantContext
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

pytestmark = pytest.mark.db


def _job(tid: uuid.UUID) -> Job:
    return Job(tenant_id=tid, config_version_hash="h" * 64)


def test_every_tenant_table_has_forced_rls(su_engine: Engine) -> None:
    with su_engine.connect() as c:
        assert tables_missing_rls(c) == []
        c.execute(text("SELECT mre_assert_rls()"))
        tables = set(inspect(c).get_table_names())
    assert set(TENANT_TABLES) <= tables
    assert {t.name for t in Base.metadata.sorted_tables} <= tables


def test_rls_check_detects_unprotected_table(su_engine: Engine) -> None:
    with su_engine.connect() as c, c.begin() as tx:
        c.execute(text("CREATE TABLE sneaky (id int, tenant_id uuid)"))
        assert tables_missing_rls(c) == ["sneaky"]
        with pytest.raises(DBAPIError, match="RLS not enabled"):
            c.execute(text("SELECT mre_assert_rls()"))
        tx.rollback()


def test_missing_tenant_context_raises(app_engine: Engine) -> None:
    for bad in (None, "", "   ", "not-a-uuid"):
        with pytest.raises(MissingTenantContext), tenant_session(app_engine, bad):
            pass


def test_no_tenant_setting_sees_nothing_and_cannot_write(
    app_engine: Engine, admin_engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = two_tenants
    with tenant_session(app_engine, a) as s:
        s.add(_job(a))
    # Raw app connection without app.tenant_id: zero rows, writes rejected.
    with app_engine.connect() as c:
        c.execute(text("SET ROLE mre_app"))
        assert c.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 0
        with pytest.raises(ProgrammingError, match="row-level security"):
            c.execute(
                text("INSERT INTO jobs (tenant_id, config_version_hash) VALUES (:t, 'x')"),
                {"t": a},
            )
        c.rollback()
        # An empty setting (what SET LOCAL leaves behind) must not error or match.
        c.execute(text("SELECT set_config('app.tenant_id', '', false)"))
        assert c.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 0


def test_cross_tenant_reads_writes_and_deletes_are_blocked(
    app_engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_tenants
    with tenant_session(app_engine, a) as s:
        job = _job(a)
        s.add(job)
        s.flush()
        job_id = job.id

    with tenant_session(app_engine, b) as s:
        assert s.scalars(select(Job)).all() == []
        assert s.get(Job, job_id) is None
        upd = s.execute(text("UPDATE jobs SET failure_code='x' WHERE id=:id"), {"id": job_id})
        assert upd.rowcount == 0  # type: ignore[attr-defined]
        dele = s.execute(text("DELETE FROM jobs WHERE id=:id"), {"id": job_id})
        assert dele.rowcount == 0  # type: ignore[attr-defined]
        tenants = s.execute(text("SELECT id FROM tenants")).scalars().all()
        assert tenants == [b]

    with pytest.raises(ProgrammingError, match="row-level security"):  # noqa: SIM117
        with tenant_session(app_engine, b) as s:
            s.add(_job(a))  # writing a row for tenant A while scoped to B

    with tenant_session(app_engine, a) as s:
        row = s.get(Job, job_id)
        assert row is not None
        assert row.failure_code is None


def test_context_survives_commit_inside_session(
    app_engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = two_tenants
    with tenant_session(app_engine, a) as s:
        s.add(_job(a))
        s.commit()  # ends the first transaction; the next BEGIN must re-apply context
        assert s.execute(text("SELECT current_setting('app.tenant_id')")).scalar_one() == str(a)
        assert s.execute(text("SELECT current_user")).scalar_one() == "mre_app"
        assert len(s.scalars(select(Job)).all()) >= 1


def test_admin_session_is_cross_tenant(
    app_engine: Engine, admin_engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_tenants
    for tid in (a, b):
        with tenant_session(app_engine, tid) as s:
            s.add(_job(tid))
    with admin_session(admin_engine, actor="staff@coreset.example", reason="test") as s:
        seen = set(s.scalars(select(Job.tenant_id)).all())
        assert {a, b} <= seen
    with (
        pytest.raises(ValueError, match="reason"),
        admin_session(admin_engine, actor="x", reason=""),
    ):
        pass


def test_app_role_cannot_bypass_rls(app_engine: Engine) -> None:
    with app_engine.connect() as c:
        c.execute(text("SET ROLE mre_app"))
        row = c.execute(
            text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user")
        ).one()
        assert tuple(row) == (False, False)


def test_create_db_engine(migrated_db: str) -> None:
    from mre_core.db.session import create_db_engine

    eng = create_db_engine(migrated_db)
    with eng.connect() as c:
        assert c.execute(text("SELECT 1")).scalar_one() == 1
    eng.dispose()
