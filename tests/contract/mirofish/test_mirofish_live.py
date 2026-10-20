"""Contract tests against a real, pinned MiroFish engine container (§7.2, §7.4).

Skipped unless MIROFISH_URL is set. Cheap read-only checks need only the container.
The full run calls the LLM gateway and Zep Cloud, so it also needs MIROFISH_LIVE_FULL=1
and Peter's spend approval.
"""

from __future__ import annotations

import os

import httpx
import pytest
from engine_adapter.documents import mapping_loader
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


@pytest.mark.skipif(os.environ.get("MIROFISH_LIVE_FULL") != "1", reason="spend not approved")
async def test_full_small_run() -> None:
    cards = make_persona_cards(25)
    spec = make_run_spec(cards, target_agents=25, agent_tolerance=0.25, max_rounds=3)
    engine = MiroFishEngine(
        base_url=URL or "",
        load_document=mapping_loader({SEED_URI: seed_document(cards)}),
    )
    handle = await engine.prepare(spec)
    try:
        handle = await engine.start(handle)
        while (await engine.status(handle)).state not in ("completed", "failed", "cancelled"):
            pass
        await engine.interview(handle, spec.interview_questions[:1])
        art = await engine.collect(handle)
        assert art.posts
    finally:
        report = await engine.cleanup(handle)
        assert report.complete, report
