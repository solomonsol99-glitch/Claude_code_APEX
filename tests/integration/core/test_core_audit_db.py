"""Append-only, hash-chained audit log in Postgres (§3.9)."""

from __future__ import annotations

import uuid

import pytest
from mre_core.db.audit import AuditEventIn, append_audit_event, verify_tenant_chain
from mre_core.db.session import admin_session, tenant_session
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.db


def _ev(action: str) -> AuditEventIn:
    return AuditEventIn(
        actor="user-1", actor_type="user", action=action, resource="job/1",
        details={"n": 1}, trace_id="t1",
    )  # fmt: skip


def test_chain_appends_and_verifies(
    app_engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_tenants
    with tenant_session(app_engine, a) as s:
        e1 = append_audit_event(s, _ev("intake.submitted"), a)
        e2 = append_audit_event(s, _ev("job.created"), a)
        assert (e1.seq, e2.seq) == (1, 2)
        assert e2.prev_hash == e1.hash
    with tenant_session(app_engine, b) as s:
        assert append_audit_event(s, _ev("login"), b).seq == 1  # separate chain
    with tenant_session(app_engine, a) as s:
        assert verify_tenant_chain(s, a) is None


def test_platform_chain_via_admin(admin_engine: Engine) -> None:
    with admin_session(admin_engine, actor="ops", reason="test") as s:
        first = append_audit_event(s, _ev("tenant.onboarded"), None)
        second = append_audit_event(s, _ev("config.changed"), None)
        assert second.seq == first.seq + 1
        assert verify_tenant_chain(s, None) is None


def test_app_role_cannot_update_or_delete_audit(
    app_engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = two_tenants
    with tenant_session(app_engine, a) as s:
        append_audit_event(s, _ev("x"), a)
    for stmt in ("UPDATE audit_events SET result='tampered'", "DELETE FROM audit_events"):
        with pytest.raises(ProgrammingError, match="permission denied"):  # noqa: SIM117
            with tenant_session(app_engine, a) as s:
                s.execute(text(stmt))


def test_tampering_is_detected(
    su_engine: Engine, app_engine: Engine, two_tenants: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = two_tenants
    with tenant_session(app_engine, a) as s:
        for i in range(3):
            append_audit_event(s, _ev(f"a{i}"), a)
    with su_engine.begin() as c:  # superuser simulates an attacker with DB access
        c.execute(
            text("UPDATE audit_events SET details='{\"n\": 2}' WHERE tenant_id=:t AND seq=2"),
            {"t": a},
        )
    with tenant_session(app_engine, a) as s:
        assert verify_tenant_chain(s, a) == 2
