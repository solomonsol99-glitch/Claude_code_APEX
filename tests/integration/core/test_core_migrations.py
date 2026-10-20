"""Migration round trip on a separate database."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db


def test_upgrade_downgrade_upgrade(pg_superuser_url: str, run_alembic: Callable[..., None]) -> None:
    name = f"mre_mig_{uuid.uuid4().hex[:8]}"
    admin = create_engine(pg_superuser_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(pg_superuser_url).set(database=name).render_as_string(hide_password=False)
    try:
        run_alembic(url, "upgrade", "head")
        run_alembic(url, "downgrade", "base")
        eng = create_engine(url)
        with eng.connect() as c:
            assert set(inspect(c).get_table_names()) <= {"alembic_version"}
        eng.dispose()
        run_alembic(url, "upgrade", "head")
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
