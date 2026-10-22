"""Contract tests against a real, pinned MiroFish engine container (§7.2, §7.4).

Skipped unless MIROFISH_URL is set. Cheap read-only checks need only the container.
The full run calls the LLM gateway and Zep Cloud, so it also needs MIROFISH_LIVE_FULL=1
and Peter's spend approval (given 2026-10-21 for 10 agents / 3 rounds; see ADR 0001).
`make live-run` sets all of this up behind a deny-all egress proxy.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from engine_adapter.base import StageTimeouts
from engine_adapter.documents import mapping_loader
from engine_adapter.mirofish import MiroFishEngine
from engine_adapter.testing import SEED_URI, make_persona_cards, make_run_spec, seed_document

URL = os.environ.get("MIROFISH_URL")
AGENTS = int(os.environ.get("MIROFISH_LIVE_AGENTS", "10"))
ROUNDS = int(os.environ.get("MIROFISH_LIVE_ROUNDS", "3"))
OUT = os.environ.get("MIROFISH_LIVE_OUT")
SIM_TIMEOUT_S = 60 * 60

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


@pytest.mark.skipif(os.environ.get("MIROFISH_LIVE_FULL") != "1", reason="spend not approved")
async def test_full_small_run() -> None:
    cards = make_persona_cards(AGENTS)
    spec = make_run_spec(
        cards,
        target_agents=AGENTS,
        # A1 is open: the engine may pad or drop agents. Record the count, don't fail on it.
        agent_tolerance=1.0,
        max_rounds=ROUNDS,
        timeouts=StageTimeouts(poll_interval_s=5),
    )
    engine = MiroFishEngine(
        base_url=URL or "",
        load_document=mapping_loader({SEED_URI: seed_document(cards)}),
        min_agents=1,
    )
    summary: dict[str, object] = {"agents_target": AGENTS, "rounds_target": ROUNDS}
    t0 = time.monotonic()
    handle = await engine.prepare(spec)
    summary["agents_actual"] = handle.agents_actual
    summary["prepare_s"] = round(time.monotonic() - t0, 1)
    try:
        handle = await engine.start(handle)
        deadline = time.monotonic() + SIM_TIMEOUT_S
        while (st := await engine.status(handle)).state not in ("completed", "failed", "cancelled"):
            assert time.monotonic() < deadline, f"simulation exceeded {SIM_TIMEOUT_S}s"
            await asyncio.sleep(5)
        summary["sim_state"] = st.state
        summary["sim_error"] = st.error
        summary["rounds_seen"] = st.current_round
        await engine.interview(handle, spec.interview_questions[:1])
        art = await engine.collect(handle)
        summary["posts"] = len(art.posts)
        assert art.posts
    finally:
        report = await engine.cleanup(handle)
        summary["cleanup_complete"] = report.complete
        summary["total_s"] = round(time.monotonic() - t0, 1)
        if OUT:
            path = Path(OUT, "run-summary.json")
            await asyncio.to_thread(path.write_text, json.dumps(summary, indent=2, default=str))
        print("RUN SUMMARY", json.dumps(summary, default=str))
        assert report.complete, report
