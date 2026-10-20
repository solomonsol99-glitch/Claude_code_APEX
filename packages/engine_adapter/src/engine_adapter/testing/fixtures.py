"""Builders for persona cards, questions and run specs."""

from __future__ import annotations

from typing import Any

from engine_adapter.base import (
    InterviewQuestion,
    PersonaAttributes,
    PersonaCard,
    RunSpec,
    StageTimeouts,
)

SEED_URI = "s3://mre-data/tenants/t1/jobs/j1/seed/seed_arm-a.md"

_NAMES = ["Dana", "Lee", "Sam", "Riya", "Omar", "Mia", "Jon", "Ana", "Kai", "Zoe", "Ben", "Ivy"]
_SEGMENTS = [
    ("seg_01", "Budget-conscious young families", "value-seeking", "pragmatic majority"),
    ("seg_02", "Urban early adopters", "premium-tolerant", "early adopter"),
    ("seg_03", "Retirees, suburban", "value-seeking", "late majority"),
]


def make_persona_cards(n: int = 24) -> list[PersonaCard]:
    cards = []
    for i in range(n):
        seg_id, label, price, tech = _SEGMENTS[i % len(_SEGMENTS)]
        cards.append(
            PersonaCard(
                id=f"pc_{i:04d}",
                segment_id=seg_id,
                segment_label=label,
                weight=1.0 / n,
                source_mode="synthetic",
                grounding="census",
                display_name=_NAMES[i % len(_NAMES)],
                attributes=PersonaAttributes(
                    age_band="30-39",
                    income_band="$50k-$75k",
                    region="South (Census region)",
                    price_orientation=price,
                    tech_adoption=tech,
                    interests=["home improvement"],
                    sources={"age_band": "census", "interests": "llm_prior"},
                ),
                narrative=f"{_NAMES[i % len(_NAMES)]} is a {label.lower()} shopper.",
            )
        )
    return cards


def default_questions() -> list[InterviewQuestion]:
    return [
        InterviewQuestion(id="q_open", kind="open", text="What is your first reaction?"),
        InterviewQuestion(
            id="q_intent",
            kind="purchase_likelihood",
            text="On a scale of 1 to 5, how likely are you to buy it at $49?",
            price=49.0,
            currency="USD",
        ),
        InterviewQuestion(
            id="q_vw_exp",
            kind="vw_too_expensive",
            text="At what price would it be too expensive to consider?",
            price=49.0,
            currency="USD",
        ),
    ]


def seed_document(cards: list[PersonaCard]) -> str:
    from engine_adapter.mirofish.engine import dossier_entity_name

    dossier = "\n".join(
        f"## {dossier_entity_name(c)}\nType: Consumer. {c.narrative}" for c in cards
    )
    return (
        "A new product is being introduced to the market.\n\n"
        "# Product\nA smart water bottle that tracks hydration. $49.\n\n"
        f"# Persona dossier\n{dossier}\n"
    )


def make_run_spec(cards: list[PersonaCard] | None = None, **overrides: Any) -> RunSpec:
    cards = cards if cards is not None else make_persona_cards()
    fields: dict[str, Any] = {
        "tenant_id": "3f6c2a9e-0d4b-4f0e-9a51-2b8e7d1c4a10",
        "job_id": "job_1",
        "run_id": "run_1",
        "arm_id": "arm-a",
        "seed_documents": [SEED_URI],
        "simulation_requirement": "Simulate how the consumers react to this product.",
        "persona_cards": cards,
        "target_agents": len(cards),
        "max_rounds": 4,
        "model_route": "sim-primary",
        "llm_virtual_key_ref": "vault://runs/run_1/llm#key",
        "random_seed": 1000,
        "interview_questions": default_questions(),
        "timeouts": StageTimeouts(poll_interval_s=0.01, graph_s=5, profiles_s=5),
    }
    fields.update(overrides)
    return RunSpec(**fields)
