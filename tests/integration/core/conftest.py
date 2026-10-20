# ruff: noqa: S603, S608, E501 - test-only process and DDL helpers with constant inputs
"""Real-Postgres fixtures. Uses DATABASE_URL (a superuser URL) if set, else a throwaway cluster."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

REPO = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO / "packages/core/alembic.ini"
PG_BIN = Path(os.environ.get("PG_BIN", "/usr/lib/postgresql/16/bin"))
APP_LOGIN = "mre_app_test_login"
ADMIN_LOGIN = "mre_admin_test_login"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _as_pg_user(cmd: list[str]) -> list[str]:
    # initdb refuses to run as root; drop to the postgres system user when needed.
    if os.geteuid() == 0:
        return ["su", "postgres", "-s", "/bin/sh", "-c", " ".join(cmd)]
    return cmd


@pytest.fixture(scope="session")
def pg_superuser_url() -> Iterator[str]:
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        yield env_url
        return
    if not (PG_BIN / "initdb").exists():
        pytest.skip("no DATABASE_URL and no local PostgreSQL binaries")
    tmp = Path(tempfile.mkdtemp(prefix="mre-pg-"))
    tmp.chmod(0o777)
    if os.geteuid() == 0:
        shutil.chown(tmp, "postgres")
    port = _free_port()
    data = tmp / "data"
    subprocess.run(
        _as_pg_user([str(PG_BIN / "initdb"), "-D", str(data), "-U", "postgres", "-A", "trust"]),
        check=True,
        capture_output=True,
    )
    opts = f"'-p {port} -k {tmp} -c listen_addresses=127.0.0.1 -c fsync=off'"
    ctl = [str(PG_BIN / "pg_ctl"), "-D", str(data), "-l", str(tmp / "log"), "-w"]
    subprocess.run(_as_pg_user([*ctl, "-o", opts, "start"]), check=True, capture_output=True)
    try:
        yield f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(_as_pg_user([*ctl, "-m", "immediate", "stop"]), capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _alembic(url: str, *args: str) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    getattr(command, args[0])(cfg, *args[1:])


@pytest.fixture(scope="session")
def run_alembic() -> Callable[..., None]:
    """Expose the Alembic runner to tests without importing conftest as a module."""
    return _alembic


@pytest.fixture(scope="session")
def migrated_db(pg_superuser_url: str) -> Iterator[str]:
    """A fresh database migrated to head. Yields its superuser URL."""
    name = f"mre_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(pg_superuser_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(pg_superuser_url).set(database=name).render_as_string(hide_password=False)
    _alembic(url, "upgrade", "head")
    su = create_engine(url, isolation_level="AUTOCOMMIT")
    with su.connect() as c:
        for login, group in ((APP_LOGIN, "mre_app"), (ADMIN_LOGIN, "mre_admin")):
            c.execute(
                text(
                    f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{login}') "
                    f"THEN CREATE ROLE {login} LOGIN PASSWORD 'test' IN ROLE {group}; END IF; END $$"
                )
            )
    su.dispose()
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _login_url(url: str, user: str) -> str:
    return make_url(url).set(username=user, password="test").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def su_engine(migrated_db: str) -> Iterator[Engine]:
    eng = create_engine(migrated_db)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def app_engine(migrated_db: str) -> Iterator[Engine]:
    eng = create_engine(_login_url(migrated_db, APP_LOGIN))
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def admin_engine(migrated_db: str) -> Iterator[Engine]:
    eng = create_engine(_login_url(migrated_db, ADMIN_LOGIN))
    yield eng
    eng.dispose()


@pytest.fixture
def two_tenants(su_engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    a, b = uuid.uuid4(), uuid.uuid4()
    with su_engine.begin() as c:
        for tid in (a, b):
            c.execute(
                text("INSERT INTO tenants (id, slug, display_name) VALUES (:id, :slug, :n)"),
                {"id": tid, "slug": f"t-{tid.hex[:10]}", "n": "Test"},
            )
    return a, b
