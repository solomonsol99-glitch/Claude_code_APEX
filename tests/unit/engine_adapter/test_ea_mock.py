import asyncio

import pytest
from engine_adapter.errors import (
    AgentCountOutOfBoundsError,
    EngineResponseInvalidError,
    EngineStageFailedError,
    InjectedFaultError,
)
from engine_adapter.mock import MockEngine, MockFaults
from engine_adapter.testing import make_persona_cards, make_run_spec


async def _complete(engine: MockEngine, spec):  # type: ignore[no-untyped-def]
    h = await engine.start(await engine.prepare(spec))
    while (await engine.status(h)).state != "completed":
        pass
    return h


async def test_deterministic_for_same_seed() -> None:
    spec = make_run_spec()
    e1, e2 = MockEngine(), MockEngine()
    art1 = await e1.collect(await _complete(e1, spec))
    art2 = await e2.collect(await _complete(e2, spec))
    assert art1.model_dump() == art2.model_dump()
    e3 = MockEngine()
    art3 = await e3.collect(await _complete(e3, make_run_spec(random_seed=1001)))
    assert art3.model_dump() != art1.model_dump()


async def test_interview_answers_have_structure() -> None:
    engine = MockEngine()
    spec = make_run_spec()
    h = await _complete(engine, spec)
    answers = await engine.interview(h, spec.interview_questions)
    assert len(answers) == len(spec.persona_cards) * len(spec.interview_questions)
    intent = [a for a in answers if a.question_id == "q_intent"]
    assert all(a.likert and 1 <= a.likert <= 5 for a in intent)
    vw = [a for a in answers if a.question_id == "q_vw_exp"]
    assert all(a.price_value and a.price_value > 0 for a in vw)
    assert all(a.segment_id for a in answers)


async def test_fail_at_stage_recovers_after_fail_times() -> None:
    engine = MockEngine(faults=MockFaults(fail_at_stage="prepare", fail_times=1))
    with pytest.raises(InjectedFaultError):
        await engine.prepare(make_run_spec())
    assert (await engine.prepare(make_run_spec())).agents_actual == 24


async def test_agent_shortfall_raises_with_handle() -> None:
    engine = MockEngine(faults=MockFaults(agent_shortfall=0.5))
    with pytest.raises(AgentCountOutOfBoundsError) as exc:
        await engine.prepare(make_run_spec())
    assert exc.value.actual == 12
    assert exc.value.handle is not None


async def test_empty_json_and_garbage() -> None:
    engine = MockEngine(faults=MockFaults(empty_json=True))
    h = await _complete(engine, make_run_spec())
    with pytest.raises(EngineResponseInvalidError):
        await engine.collect(h)
    engine = MockEngine(faults=MockFaults(garbage_posts=True))
    art = await engine.collect(await _complete(engine, make_run_spec()))
    assert all(len(p.content) <= 3 for p in art.posts)


async def test_hang_status_never_completes() -> None:
    engine = MockEngine(faults=MockFaults(hang_at_stage="status"))
    h = await engine.start(await engine.prepare(make_run_spec()))
    for _ in range(10):
        assert (await engine.status(h)).state == "running"


async def test_hang_other_stage_blocks() -> None:
    engine = MockEngine(faults=MockFaults(hang_at_stage="collect"))
    h = await _complete(engine, make_run_spec())
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(engine.collect(h), timeout=0.05)


async def test_status_pending_and_cancelled() -> None:
    engine = MockEngine()
    h = await engine.prepare(make_run_spec())
    assert (await engine.status(h)).state == "pending"
    await engine.cancel(h)
    assert (await engine.status(h)).state == "cancelled"


async def test_unknown_run_after_recycle() -> None:
    engine = MockEngine()
    h = await engine.prepare(make_run_spec())
    await engine.cleanup(h)
    with pytest.raises(EngineStageFailedError):
        await engine.start(h)


async def test_more_agents_than_cards_cycles_cards() -> None:
    cards = make_persona_cards(5)
    engine = MockEngine()
    h = await engine.prepare(make_run_spec(cards, target_agents=12))
    assert h.agents_actual == 12
    assert set(h.agent_personas.values()) == {c.id for c in cards}
