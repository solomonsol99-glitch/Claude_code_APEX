import httpx
import pytest
from engine_adapter.base import InterviewQuestion
from engine_adapter.documents import mapping_loader
from engine_adapter.errors import (
    AgentCountOutOfBoundsError,
    EngineResponseInvalidError,
    EngineStageFailedError,
    EngineStageTimeoutError,
    EngineUnavailableError,
)
from engine_adapter.mirofish import MiroFishEngine, dossier_entity_name, match_personas
from engine_adapter.mirofish.engine import _to_answer, action_type_counts
from engine_adapter.testing import (
    SEED_URI,
    FakeMiroFish,
    make_persona_cards,
    make_run_spec,
    seed_document,
)
from mre_net import RetryPolicy


async def no_sleep(_: float) -> None:
    return None


def _engine(fake: FakeMiroFish, **kw) -> MiroFishEngine:  # type: ignore[no-untyped-def]
    cards = make_persona_cards(24)
    return MiroFishEngine(
        transport=fake.transport(),
        load_document=mapping_loader({SEED_URI: seed_document(cards)}),
        sleep=no_sleep,
        **kw,
    )


async def _run(engine: MiroFishEngine, spec):  # type: ignore[no-untyped-def]
    h = await engine.start(await engine.prepare(spec))
    while (await engine.status(h)).state != "completed":
        pass
    return h


async def test_prepare_maps_profiles_to_cards() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake)
    spec = make_run_spec()
    h = await engine.prepare(spec)
    assert h.agents_actual == 24
    assert set(h.engine_ids) >= {"project_id", "graph_id", "simulation_id"}
    assert h.agent_personas["0"] == "pc_0000"
    assert h.stage == "PROFILES_READY"
    assert "POST /api/simulation/prepare/status" in fake.calls


async def test_agent_shortfall_raises_with_partial_handle() -> None:
    fake = FakeMiroFish(agent_shortfall=6)
    engine = _engine(fake)
    with pytest.raises(AgentCountOutOfBoundsError) as exc:
        await engine.prepare(make_run_spec())
    assert exc.value.actual == 18
    handle = exc.value.handle
    assert handle is not None
    assert "simulation_id" in handle.engine_ids
    report = await engine.cleanup(handle)
    assert report.complete
    assert any(d.startswith("project:") for d in fake.deleted)


async def test_graph_task_failure() -> None:
    engine = _engine(FakeMiroFish(graph_task_fails=True))
    with pytest.raises(EngineStageFailedError, match="zep build failed") as exc:
        await engine.prepare(make_run_spec())
    assert exc.value.handle is not None
    assert "project_id" in exc.value.handle.engine_ids


async def test_poll_timeout() -> None:
    now = [0.0]

    async def tick(s: float) -> None:
        now[0] += 100

    fake = FakeMiroFish(polls_until_done=10_000)
    cards = make_persona_cards(24)
    engine = MiroFishEngine(
        transport=fake.transport(),
        load_document=mapping_loader({SEED_URI: seed_document(cards)}),
        sleep=tick,
        clock=lambda: now[0],
    )
    with pytest.raises(EngineStageTimeoutError):
        await engine.prepare(make_run_spec(cards))


async def test_transient_status_error_is_retried() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake)
    spec = make_run_spec()
    h = await engine.start(await engine.prepare(spec))
    sim = h.engine_ids["simulation_id"]
    fake.fail_once[f"GET /api/simulation/{sim}/run-status"] = 503
    st = await engine.status(h)
    assert st.state in ("running", "completed")


async def test_persistent_5xx_becomes_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    engine = MiroFishEngine(
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
        retry_policy=RetryPolicy(max_attempts=2),
    )
    h = (await _run(_engine(FakeMiroFish()), make_run_spec())).model_copy()
    with pytest.raises(EngineUnavailableError):
        await engine.status(h)


async def test_engine_error_strips_traceback() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake)
    h = await engine.prepare(make_run_spec())
    with pytest.raises(EngineStageFailedError) as exc:
        await engine.start(h.model_copy(update={"engine_ids": {"simulation_id": "sim_missing"}}))
    assert "Traceback" not in str(exc.value)


async def test_non_json_reply() -> None:
    engine = MiroFishEngine(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")),
        sleep=no_sleep,
    )
    h = await _run(_engine(FakeMiroFish()), make_run_spec())
    with pytest.raises(EngineResponseInvalidError):
        await engine.status(h)


async def test_health() -> None:
    assert await _engine(FakeMiroFish()).health()

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert not await MiroFishEngine(transport=httpx.MockTransport(boom)).health()


async def test_interview_requires_live_env() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake)
    spec = make_run_spec()
    h = await _run(engine, spec)
    fake.env_alive = False
    with pytest.raises(EngineStageFailedError, match="not alive"):
        await engine.interview(h, spec.interview_questions)


async def test_interview_one_batch_per_question_and_parsing() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake, interview_batch_size=10)
    spec = make_run_spec()
    h = await _run(engine, spec)
    answers = await engine.interview(h, spec.interview_questions)
    batches = [c for c in fake.calls if c.endswith("/interview/batch")]
    assert len(batches) == 3 * 3  # 3 questions x ceil(24/10)
    assert len(answers) == 24 * 3
    intent = {a.agent_id: a.likert for a in answers if a.question_id == "q_intent"}
    assert intent["0"] == 1
    assert intent["4"] == 5
    vw = [a.price_value for a in answers if a.question_id == "q_vw_exp"]
    assert all(v is not None for v in vw)
    assert all(v >= 60 for v in vw if v is not None)


async def test_report_failure_is_a_warning() -> None:
    fake = FakeMiroFish(report_fails=True)
    engine = _engine(fake)
    h = await _run(engine, make_run_spec())
    art = await engine.collect(h)
    assert art.engine_report_markdown is None
    assert any("engine report unavailable" in w for w in art.engine_meta.warnings)


async def test_collect_attributes_posts_and_comments() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake, page_size=5)
    h = await _run(engine, make_run_spec())
    art = await engine.collect(h)
    assert art.engine_report_markdown == "# Engine report\n"
    assert h.engine_ids["report_id"].startswith("report_")
    assert art.comments
    assert all(c.agent_id is not None for c in art.comments)
    assert all(p.round is not None for p in art.posts)
    # twitter rows come from CSV (string ids, "description" column)
    agent = art.agents[0]
    assert agent.profiles["twitter"].bio
    assert agent.profiles["reddit"].bio
    assert action_type_counts(art.actions)["CREATE_POST"] == len(art.posts)
    assert art.stats.truncated is False
    assert "simulation_s" in art.engine_meta.timings_s


async def test_unmatched_rows_fall_back_to_learned_user_map() -> None:
    from engine_adapter.base import Action
    from engine_adapter.mirofish.engine import _to_posts

    actions = [
        Action(type="CREATE_POST", actor="3", round=1, platform="reddit", args={"content": "hello"})
    ]
    rows = [
        {"post_id": 1, "user_id": 4, "content": "hello"},
        {"post_id": 2, "user_id": 4, "content": "edited later"},
        {"post_id": 3, "user_id": 9, "content": "stranger"},
    ]
    posts = _to_posts(rows, actions, "reddit")
    assert [p.agent_id for p in posts] == ["3", "3", None]


async def test_cleanup_finds_report_and_records_errors() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake)
    h = await _run(engine, make_run_spec())
    await engine.collect(h)
    h.engine_ids.pop("report_id")  # lost on checkpoint: cleanup must look it up
    sim = h.engine_ids["simulation_id"]
    proj = h.engine_ids["project_id"]
    fake.fail_once[f"DELETE /api/graph/project/{proj}"] = 400
    report = await engine.cleanup(h)
    assert report.deleted["report"] is True
    assert report.deleted["graph"] is True
    assert report.deleted["project"] is False
    assert report.errors
    assert not report.complete
    assert f"GET /api/report/by-simulation/{sim}" in fake.calls


async def test_cancel_without_simulation_is_noop() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake)
    h = await engine.prepare(make_run_spec())
    h.engine_ids.pop("simulation_id")
    await engine.cancel(h)
    with pytest.raises(EngineStageFailedError, match="no simulation_id"):
        await engine.status(h)


async def test_single_platform_start() -> None:
    fake = FakeMiroFish()
    engine = _engine(fake)
    h = await _run(engine, make_run_spec(platforms=["reddit"]))
    art = await engine.collect(h)
    assert {p.platform for p in art.posts} == {"reddit"}


def test_match_personas_fallbacks() -> None:
    cards = make_persona_cards(13)  # names repeat after 12: "Dana" twice
    profiles = [
        {"user_id": 0, "name": dossier_entity_name(cards[1])},
        {"user_id": 1, "name": "Lee"},  # unique display name -> match
        {"user_id": 2, "name": "Dana"},  # ambiguous -> None
        {"user_id": 3, "name": "someone", "persona": f"based on {cards[5].id}"},
        {"name": "Nobody"},
    ]
    m = match_personas(profiles, cards)
    assert m == {"0": "pc_0001", "1": "pc_0001", "2": None, "3": "pc_0005", "4": None}


def test_to_answer_parsing() -> None:
    from engine_adapter.testing import make_run_spec as mk

    h = (mk()).model_copy()
    from engine_adapter.base import handle_from_spec

    handle = handle_from_spec(h, engine_instance="e", stage="x")
    q = InterviewQuestion(id="q", kind="purchase_likelihood", text="1 to 5?")
    assert _to_answer(q, "1", {"response": "maybe 7, so 4"}, handle).likert == 4
    assert _to_answer(q, "1", {"response": "no idea"}, handle).likert is None
    q2 = InterviewQuestion(id="q2", kind="vw_bargain", text="price?")
    assert _to_answer(q2, "1", {"response": "about 12,50"}, handle).price_value == 12.5
