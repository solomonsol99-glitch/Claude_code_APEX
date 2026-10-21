"""Contract tests against a real, pinned MiroFish engine container (§7.2, §7.4).

Skipped unless MIROFISH_URL is set. Cheap read-only checks need only the container.
The full run calls the LLM gateway and Zep Cloud, so it also needs MIROFISH_LIVE_FULL=1
and Peter's spend approval. `make live-run` sets all of this up.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from engine_adapter.base import RunHandle, StageTimeouts
from engine_adapter.documents import mapping_loader
from engine_adapter.errors import EngineError
from engine_adapter.mirofish import MiroFishEngine
from engine_adapter.testing import SEED_URI, make_persona_cards, make_run_spec, seed_document

URL = os.environ.get("MIROFISH_URL")

pytestmark = [
    pytest.mark.live,
    pytest.mark.docker,
    pytest.mark.skipif(not URL, reason="MIROFISH_URL not set"),
]


async def test_health_route() -> None:
    assert await MiroFishEngine(base_url=URL or "").health()


@pytest.mark.parametrize("path", ["/api/simulation/list", "/api/graph/project/list"])
async def test_read_routes_exist(path: str) -> None:
    async with httpx.AsyncClient(base_url=URL or "", timeout=10) as c:
        r = await c.get(path)
    assert r.status_code == 200
    assert r.json()["success"] is True


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


@pytest.mark.skipif(os.environ.get("MIROFISH_LIVE_FULL") != "1", reason="spend not approved")
async def test_full_small_run() -> None:
    """One end-to-end run (ADR 0001 live budget: 10 agents / 3 rounds by default).

    Agent-count bounds are wide on purpose: this run characterizes A1, it does not gate
    on it. Cleanup always runs, including when prepare fails part-way, so no Zep graph
    is left behind. A JSON summary goes to ``MIROFISH_LIVE_OUT`` when set.
    """
    agents = _env_int("MIROFISH_LIVE_AGENTS", 10)
    rounds = _env_int("MIROFISH_LIVE_ROUNDS", 3)
    deadline_s = _env_int("MIROFISH_LIVE_DEADLINE_S", 45 * 60)
    cards = make_persona_cards(agents)
    spec = make_run_spec(
        cards,
        target_agents=agents,
        agent_tolerance=1.0,
        max_rounds=rounds,
        timeouts=StageTimeouts(poll_interval_s=5),
    )
    engine = MiroFishEngine(
        base_url=URL or "",
        load_document=mapping_loader({SEED_URI: seed_document(cards)}),
        min_agents=1,
    )
    summary: dict[str, Any] = {"target_agents": agents, "max_rounds": rounds}
    started = time.monotonic()
    handle: RunHandle | None = None
    try:
        try:
            handle = await engine.prepare(spec)
        except EngineError as exc:
            handle = exc.handle
            summary["error"] = f"prepare: {exc}"
            raise
        summary["agents_actual"] = handle.agents_actual
        summary["unmatched_agents"] = sum(1 for v in handle.agent_personas.values() if v is None)
        handle = await engine.start(handle)
        while True:
            st = await engine.status(handle)
            summary["last_status"] = st.model_dump()
            if st.state in ("completed", "failed", "cancelled"):
                break
            assert time.monotonic() - started < deadline_s, "live run deadline exceeded"
            await asyncio.sleep(10)
        assert st.state == "completed", st
        answers = await engine.interview(handle, spec.interview_questions[:1])
        summary["interview_answers"] = len(answers)
        art = await engine.collect(handle)
        summary["stats"] = art.stats.model_dump()
        summary["timings_s"] = art.engine_meta.timings_s
        summary["warnings"] = art.engine_meta.warnings
        summary["engine_report_chars"] = len(art.engine_report_markdown or "")
        summary["sample_posts"] = [p.content[:200] for p in art.posts[:3]]
        summary["sample_answers"] = [a.answer_text[:200] for a in answers[:3]]
        assert art.posts
    finally:
        summary["elapsed_s"] = round(time.monotonic() - started, 1)
        if handle is not None:
            report = await engine.cleanup(handle)
            summary["engine_ids"] = handle.engine_ids
            summary["cleanup"] = report.model_dump()
        _write_summary(summary)
    assert report.complete, report


def _write_summary(summary: dict[str, Any]) -> None:
    out = os.environ.get("MIROFISH_LIVE_OUT")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
