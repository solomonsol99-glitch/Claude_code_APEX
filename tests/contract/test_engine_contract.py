"""Contract every SimulationEngine must satisfy (§2.4.1)."""

from __future__ import annotations

import json

import jsonschema
import pytest
from engine_adapter.base import RunArtifacts, RunHandle, SimulationEngine
from engine_adapter.schema import SCHEMA_PATH
from engine_adapter.testing.contract import EngineCase, build_case


async def run_to_completion(case: EngineCase, max_polls: int = 50) -> RunHandle:
    handle = await case.engine.prepare(case.spec)
    handle = await case.engine.start(handle)
    for _ in range(max_polls):
        st = await case.engine.status(handle)
        if st.state in ("completed", "failed", "cancelled"):
            break
    assert st.state == "completed"
    return handle


def test_is_simulation_engine(engine_case: EngineCase) -> None:
    assert isinstance(engine_case.engine, SimulationEngine)
    caps = engine_case.engine.capabilities()
    assert caps.name == engine_case.name
    assert caps.version


async def test_health(engine_case: EngineCase) -> None:
    assert await engine_case.engine.health() is True


async def test_full_lifecycle(engine_case: EngineCase) -> None:
    case = engine_case
    handle = await run_to_completion(case)
    RunHandle.model_validate_json(handle.model_dump_json())  # checkpointable

    answers = await case.engine.interview(handle, case.spec.interview_questions)
    qids = {q.id for q in case.spec.interview_questions}
    assert answers
    assert {a.question_id for a in answers} >= qids

    artifacts = await case.engine.collect(handle)
    assert artifacts.run_id == case.spec.run_id
    assert artifacts.tenant_id == case.spec.tenant_id
    assert artifacts.arm_id == case.spec.arm_id
    assert artifacts.stats.agents_actual == len(artifacts.agents)

    card_ids = {c.id for c in case.spec.persona_cards}
    for agent in artifacts.agents:
        assert agent.persona_card_id is None or agent.persona_card_id in card_ids
    agent_ids = {a.agent_id for a in artifacts.agents}
    for post in artifacts.posts:
        assert post.agent_id is None or post.agent_id in agent_ids

    schema = json.loads(SCHEMA_PATH.read_text())
    payload = json.loads(artifacts.model_dump_json())
    jsonschema.validate(payload, schema)
    assert RunArtifacts.model_validate(payload) == artifacts

    report = await case.engine.cleanup(handle)
    assert report.complete, report


async def test_cleanup_is_idempotent(engine_case: EngineCase) -> None:
    handle = await run_to_completion(engine_case)
    await engine_case.engine.collect(handle)
    first = await engine_case.engine.cleanup(handle)
    second = await engine_case.engine.cleanup(handle)
    assert first.complete
    assert not second.errors


async def test_cancel_does_not_raise(engine_case: EngineCase) -> None:
    handle = await engine_case.engine.prepare(engine_case.spec)
    handle = await engine_case.engine.start(handle)
    await engine_case.engine.cancel(handle)
    report = await engine_case.engine.cleanup(handle)
    assert not report.errors


@pytest.mark.parametrize("name", ["mock", "mirofish"])
async def test_simulating_engines_emit_activity(name: str) -> None:
    case = build_case(name)
    handle = await run_to_completion(case)
    art = await case.engine.collect(handle)
    assert art.posts
    assert art.actions
    assert art.timeline
    assert art.stats.rounds_completed == case.spec.max_rounds
    matched = [a for a in art.agents if a.persona_card_id]
    assert len(matched) == len(art.agents)
    attributed = [p for p in art.posts if p.agent_id is not None]
    assert len(attributed) == len(art.posts)
