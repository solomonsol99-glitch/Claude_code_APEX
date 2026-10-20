"""Hash-chain logic without a database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from mre_core.db.audit import GENESIS_HASH, canonical_json, compute_hash, verify_chain


def _chain(n: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    prev = GENESIS_HASH
    for i in range(1, n + 1):
        ev: dict[str, Any] = {
            "tenant_id": uuid.UUID(int=1), "seq": i, "ts": datetime(2026, 1, 1, tzinfo=UTC),
            "actor": "a", "actor_type": "user", "action": f"x{i}", "resource": "r",
            "result": "success", "ip": None, "user_agent": None, "trace_id": None,
            "details": {"b": 1, "a": 2},
        }  # fmt: skip
        ev["prev_hash"] = prev
        ev["hash"] = compute_hash(prev, ev)
        prev = ev["hash"]
        events.append(ev)
    return events


def test_canonical_json_is_order_independent() -> None:
    a = canonical_json({"actor": "x", "details": {"b": 1, "a": 2}})
    b = canonical_json({"details": {"a": 2, "b": 1}, "actor": "x"})
    assert a == b


def test_canonical_json_rejects_unknown_types() -> None:
    with pytest.raises(TypeError):
        canonical_json({"details": {"x": object()}})


def test_intact_chain() -> None:
    assert verify_chain(_chain(5)) is None
    assert verify_chain([]) is None


@pytest.mark.parametrize(
    ("index", "field", "value"),
    [(2, "action", "tampered"), (3, "prev_hash", "f" * 64), (1, "seq", 7), (4, "hash", "0" * 64)],
)
def test_tampering_detected(index: int, field: str, value: Any) -> None:
    events = _chain(5)
    events[index][field] = value
    assert verify_chain(events) == events[index]["seq"]


def test_deleted_event_detected() -> None:
    events = _chain(4)
    del events[1]
    assert verify_chain(events) == 3
