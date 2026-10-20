"""Engine contract cases: every adapter must pass the same suite (tests/contract/)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from engine_adapter.base import RunSpec, SimulationEngine
from engine_adapter.baseline import BaselineEngine
from engine_adapter.documents import mapping_loader
from engine_adapter.llm import FakeLLMClient, Message
from engine_adapter.mirofish import MiroFishEngine
from engine_adapter.mock import MockEngine
from engine_adapter.testing.fake_mirofish import FakeMiroFish
from engine_adapter.testing.fixtures import (
    SEED_URI,
    make_persona_cards,
    make_run_spec,
    seed_document,
)


async def no_sleep(_: float) -> None:
    return None


def baseline_responder(messages: Sequence[Message], route: str) -> dict[str, Any]:
    qids = re.findall(r"question_id=(\S+)", messages[-1]["content"])
    answers = []
    for q in qids:
        a: dict[str, Any] = {"question_id": q, "answer_text": "Seems fine."}
        if q == "q_intent":
            a["likert"] = 3
        if q.startswith("q_vw"):
            a["price_value"] = 70.0
        answers.append(a)
    return {
        "answers": answers,
        "overall_stance": "neutral",
        "objections": ["price"],
        "summary": "Mixed.",
    }


@dataclass
class EngineCase:
    name: str
    engine: SimulationEngine
    spec: RunSpec
    fake: FakeMiroFish | None = None


def build_case(name: str, **fake_kwargs: Any) -> EngineCase:
    cards = make_persona_cards(24)
    spec = make_run_spec(cards)
    loader = mapping_loader({SEED_URI: seed_document(cards)})
    if name == "mock":
        return EngineCase(name, MockEngine(), spec)
    if name == "baseline":
        return EngineCase(
            name, BaselineEngine(llm=FakeLLMClient(baseline_responder), load_document=loader), spec
        )
    fake = FakeMiroFish(**fake_kwargs)
    engine = MiroFishEngine(
        base_url="http://engine-1:5001",
        transport=fake.transport(),
        load_document=loader,
        sleep=no_sleep,
        page_size=7,  # small pages exercise pagination
    )
    return EngineCase(name, engine, spec, fake)


ENGINE_NAMES = ("mock", "baseline", "mirofish")
