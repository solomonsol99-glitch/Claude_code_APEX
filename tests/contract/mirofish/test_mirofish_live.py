"""Contract tests against a real, pinned MiroFish engine container (§7.2, §7.4).

Skipped unless MIROFISH_URL is set. Cheap read-only checks need only the container.
The full run calls the LLM gateway and Zep Cloud, so it also needs MIROFISH_LIVE_FULL=1
(spend approved by Peter on 2026-10-21, see ADR 0001). Run it through
infra/compose/phase0-live-run.sh, which puts the engine behind the egress proxy.
LIVE_AGENTS / LIVE_ROUNDS size the run (default 10 / 3, the ADR 0001 budget).
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest
from engine_adapter.base import StageTimeouts
from engine_adapter.documents import mapping_loader
from engine_adapter.mirofish import MiroFishEngine
from engine_adapter.testing import SEED_URI, make_persona_cards, make_run_spec, seed_document

URL = os.environ.get("MIROFISH_URL")
AGENTS = int(os.environ.get("LIVE_AGENTS", "10"))
ROUNDS = int(os.environ.get("LIVE_ROUNDS", "3"))

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
    # The shared fixture uses millisecond timeouts meant for the fake; use real ones here.
    spec = make_run_spec(
        cards,
        target_agents=AGENTS,
        agent_tolerance=0.25,
        max_rounds=ROUNDS,
        timeouts=StageTimeouts(),
    )
    engine = MiroFishEngine(
        base_url=URL or "",
        load_document=mapping_loader({SEED_URI: seed_document(cards)}),
        min_agents=min(AGENTS, 20),
    )
    handle = await engine.prepare(spec)
    try:
        handle = await engine.start(handle)
        deadline = asyncio.get_running_loop().time() + ROUNDS * spec.timeouts.per_round_s
        while (await engine.status(handle)).state not in ("completed", "failed", "cancelled"):
            assert asyncio.get_running_loop().time() < deadline, "simulation over time budget"
            await asyncio.sleep(spec.timeouts.poll_interval_s)
        assert (await engine.status(handle)).state == "completed"
        await engine.interview(handle, spec.interview_questions[:1])
        art = await engine.collect(handle)
        assert art.posts
        print(f"agents={len(art.agents)} posts={len(art.posts)} comments={len(art.comments)}")
    finally:
        report = await engine.cleanup(handle)
        assert report.complete, report
