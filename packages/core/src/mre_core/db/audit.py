"""Append-only, hash-chained audit log (§3.9). One chain per tenant, plus a platform chain."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mre_core.db.models import AuditEvent

GENESIS_HASH = "0" * 64
HASHED_FIELDS = (
    "tenant_id", "seq", "ts", "actor", "actor_type", "action", "resource",
    "result", "ip", "user_agent", "trace_id", "details",
)  # fmt: skip


class AuditEventIn(BaseModel):
    """What callers supply. ``details`` must never hold tenant content (prompts, seed text)."""

    model_config = ConfigDict(extra="forbid")

    actor: str
    actor_type: str
    action: str
    resource: str
    result: str = "success"
    ip: str | None = None
    user_agent: str | None = None
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def canonical_json(event: Mapping[str, Any]) -> str:
    return json.dumps(
        {k: event.get(k) for k in HASHED_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        default=_jsonable,
        ensure_ascii=False,
    )


def compute_hash(prev_hash: str, event: Mapping[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(event)).encode("utf-8")).hexdigest()


def _event_dict(row: AuditEvent) -> dict[str, Any]:
    return {f: getattr(row, f) for f in HASHED_FIELDS}


def append_audit_event(
    session: Session, event: AuditEventIn, tenant_id: uuid.UUID | None
) -> AuditEvent:
    """Append to the tenant's chain. Serialised per chain with an advisory lock."""
    chain_key = str(tenant_id) if tenant_id else "platform"
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": "audit:" + chain_key}
    )
    stmt = select(AuditEvent.seq, AuditEvent.hash)
    stmt = stmt.where(
        AuditEvent.tenant_id == tenant_id if tenant_id else AuditEvent.tenant_id.is_(None)
    )
    last = session.execute(stmt.order_by(AuditEvent.seq.desc()).limit(1)).first()
    seq, prev_hash = (last[0] + 1, last[1]) if last else (1, GENESIS_HASH)
    fields = event.model_dump()
    fields["ts"] = event.ts.astimezone(UTC)
    payload = {**fields, "tenant_id": tenant_id, "seq": seq}
    row = AuditEvent(**payload, prev_hash=prev_hash, hash=compute_hash(prev_hash, payload))
    session.add(row)
    session.flush()
    return row


def verify_chain(events: Iterable[Mapping[str, Any]]) -> int | None:
    """Return the ``seq`` of the first broken link, or ``None`` if the chain is intact.

    Each event needs the hashed fields plus ``prev_hash`` and ``hash``, ordered by ``seq``.
    """
    prev = GENESIS_HASH
    expected_seq = 1
    for ev in events:
        seq = int(ev["seq"])
        if seq != expected_seq or ev["prev_hash"] != prev or compute_hash(prev, ev) != ev["hash"]:
            return seq
        prev = str(ev["hash"])
        expected_seq += 1
    return None


def verify_tenant_chain(session: Session, tenant_id: uuid.UUID | None) -> int | None:
    stmt = select(AuditEvent).where(
        AuditEvent.tenant_id == tenant_id if tenant_id else AuditEvent.tenant_id.is_(None)
    )
    rows = session.scalars(stmt.order_by(AuditEvent.seq)).all()
    return verify_chain({**_event_dict(r), "prev_hash": r.prev_hash, "hash": r.hash} for r in rows)
