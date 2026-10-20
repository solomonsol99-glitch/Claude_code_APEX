"""Deterministic, fixture-style engine for tests, golden reports and chaos tests.

Output depends only on (random_seed, persona cards, run inputs). Never touches a network.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections import Counter
from dataclasses import dataclass, field

from engine_adapter.base import (
    Action,
    Agent,
    CleanupReport,
    Comment,
    EngineCapabilities,
    EngineMeta,
    InterviewAnswer,
    InterviewQuestion,
    PersonaCard,
    Platform,
    PlatformProfile,
    Post,
    RunArtifacts,
    RunHandle,
    RunSpec,
    RunStats,
    RunStatus,
    TimelineEntry,
    check_agent_bounds,
    handle_from_spec,
    utc_now_iso,
)
from engine_adapter.errors import (
    AgentCountOutOfBoundsError,
    EngineResponseInvalidError,
    EngineStageFailedError,
    InjectedFaultError,
)

VERSION = "mock-1"
STAGES = ("prepare", "start", "status", "interview", "collect", "cleanup")

_STANCE_LINES = {
    "positive": [
        "Honestly this looks useful for how I live. Might try it.",
        "The price seems fair for what you get.",
        "I like that it focuses on {feature}. That is what I need.",
    ],
    "neutral": [
        "Not sure yet. Would want to see reviews first.",
        "Looks fine, but I already use something similar.",
        "Interesting idea. Depends on {feature} in practice.",
    ],
    "negative": [
        "Too expensive for me right now.",
        "I do not believe the claims about {feature}.",
        "Another subscription? Hard pass.",
    ],
    "confused": [
        "I can't tell what this actually does.",
        "Is this a one-time purchase or monthly?",
    ],
}
_GARBAGE = ["ok", "lol", "hm", "yes", "."]


@dataclass
class MockFaults:
    """Fault injection. ``fail_times`` limits how many calls fail before recovering."""

    fail_at_stage: str | None = None
    fail_times: int = 1_000_000
    hang_at_stage: str | None = None  # "status": never completes; others: sleep forever
    empty_json: bool = False  # collect() behaves like an engine returning {}
    agent_shortfall: float = 0.0  # fraction of agents the engine "loses"
    garbage_posts: bool = False
    _failures: Counter[str] = field(default_factory=Counter)

    def maybe_fail(self, stage: str) -> None:
        if self.fail_at_stage == stage and self._failures[stage] < self.fail_times:
            self._failures[stage] += 1
            raise InjectedFaultError(f"injected failure at {stage}", stage=stage)


def _seed_for(random_seed: int, cards: list[PersonaCard], salt: str = "") -> int:
    digest = hashlib.sha256(
        f"{random_seed}|{salt}|{','.join(sorted(c.id for c in cards))}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big")


class MockEngine:
    def __init__(
        self,
        *,
        faults: MockFaults | None = None,
        min_agents: int = 1,
        polls_to_complete: int = 2,
        posts_per_agent_round: float = 0.4,
    ) -> None:
        self.faults = faults or MockFaults()
        self.min_agents = min_agents
        self.polls_to_complete = polls_to_complete
        self.posts_per_agent_round = posts_per_agent_round
        self._runs: dict[str, _MockRun] = {}

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            name="mock",
            version=VERSION,
            platforms=["twitter", "reddit"],
            supports_interviews=True,
            supports_agent_count_control="exact",
            supports_resume=True,
            max_agents_tested=5000,
            memory_backend="none",
        )

    async def health(self) -> bool:
        return True

    async def _gate(self, stage: str) -> None:
        self.faults.maybe_fail(stage)
        if self.faults.hang_at_stage == stage and stage != "status":
            await asyncio.Event().wait()

    async def prepare(self, spec: RunSpec) -> RunHandle:
        await self._gate("prepare")
        cards = spec.persona_cards
        rng = random.Random(_seed_for(spec.random_seed, cards, "agents"))  # noqa: S311 - fixtures, not crypto
        n = round(spec.target_agents * (1 - self.faults.agent_shortfall))
        # Cycle cards so every card gets a voice, then fill to n.
        assigned = [cards[i % len(cards)] for i in range(n)]
        handle = handle_from_spec(spec, engine_instance="mock-0", stage="PROFILES_READY")
        handle.engine_ids = {"simulation_id": f"mock_{spec.run_id}"}
        handle.agents_actual = n
        handle.agent_personas = {str(i): c.id for i, c in enumerate(assigned)}
        self._runs[spec.run_id] = _MockRun(spec=spec, rng_seed=rng.getrandbits(64))
        try:
            check_agent_bounds(
                actual=n,
                target=spec.target_agents,
                tolerance=spec.agent_tolerance,
                min_agents=self.min_agents,
            )
        except AgentCountOutOfBoundsError as exc:
            exc.handle = handle
            raise
        return handle

    def _run(self, handle: RunHandle) -> _MockRun:
        try:
            return self._runs[handle.run_id]
        except KeyError:
            raise EngineStageFailedError("unknown run (engine was recycled?)") from None

    async def start(self, handle: RunHandle) -> RunHandle:
        await self._gate("start")
        self._run(handle).started = True
        return handle.model_copy(update={"stage": "SIMULATING"})

    async def status(self, handle: RunHandle) -> RunStatus:
        await self._gate("status")
        run = self._run(handle)
        if run.cancelled:
            return RunStatus(
                stage=handle.stage, state="cancelled", progress=0, last_heartbeat_at=utc_now_iso()
            )
        if not run.started:
            return RunStatus(
                stage=handle.stage, state="pending", progress=0, last_heartbeat_at=utc_now_iso()
            )
        if self.faults.hang_at_stage != "status":
            run.polls += 1
        done = run.polls >= self.polls_to_complete
        frac = min(1.0, run.polls / max(1, self.polls_to_complete))
        return RunStatus(
            stage=handle.stage,
            state="completed" if done else "running",
            progress=frac,
            current_round=round(frac * handle.max_rounds),
            agents_actual=handle.agents_actual,
            last_heartbeat_at=utc_now_iso(),
        )

    async def interview(
        self, handle: RunHandle, qs: list[InterviewQuestion]
    ) -> list[InterviewAnswer]:
        await self._gate("interview")
        run = self._run(handle)
        cards = {c.id: c for c in run.spec.persona_cards}
        rng = random.Random(run.rng_seed ^ 0x1E7)  # noqa: S311 - fixtures, not crypto
        answers: list[InterviewAnswer] = []
        for agent_id, card_id in handle.agent_personas.items():
            card = cards[card_id] if card_id else None
            lean = _lean(card)
            for q in qs:
                answers.append(_answer(q, agent_id, card, lean, rng, handle.platforms[0]))
        return answers

    async def collect(self, handle: RunHandle) -> RunArtifacts:
        await self._gate("collect")
        if self.faults.empty_json:
            raise EngineResponseInvalidError("engine returned empty JSON", stage="COLLECTED")
        run = self._run(handle)
        spec = run.spec
        cards = {c.id: c for c in spec.persona_cards}
        rng = random.Random(run.rng_seed)  # noqa: S311 - fixtures, not crypto
        agents: list[Agent] = []
        for agent_id, card_id in handle.agent_personas.items():
            card = cards.get(card_id) if card_id else None
            agents.append(
                Agent(
                    agent_id=agent_id,
                    persona_card_id=card_id,
                    segment_id=card.segment_id if card else None,
                    profiles={
                        p: PlatformProfile(
                            username=f"user{agent_id}",
                            name=card.display_name if card else f"agent{agent_id}",
                        )
                        for p in handle.platforms
                    },
                )
            )

        posts: list[Post] = []
        comments: list[Comment] = []
        actions: list[Action] = []
        feature = "the main feature"
        for rnd in range(1, handle.max_rounds + 1):
            for a in agents:
                card = cards.get(a.persona_card_id) if a.persona_card_id else None
                platform = handle.platforms[rng.randrange(len(handle.platforms))]
                roll = rng.random()
                if roll < self.posts_per_agent_round:
                    stance = _pick_stance(_lean(card), rng)
                    text = (
                        rng.choice(_GARBAGE)
                        if self.faults.garbage_posts
                        else rng.choice(_STANCE_LINES[stance]).format(feature=feature)
                    )
                    pid = f"p{len(posts)}"
                    posts.append(
                        Post(
                            post_id=pid,
                            agent_id=a.agent_id,
                            platform=platform,
                            round=rnd,
                            content=text,
                            likes=rng.randrange(0, 6),
                            shares=rng.randrange(0, 3),
                        )
                    )
                    actions.append(
                        Action(
                            type="CREATE_POST",
                            actor=a.agent_id,
                            target=pid,
                            round=rnd,
                            platform=platform,
                            args={"content": text},
                        )
                    )
                elif roll < self.posts_per_agent_round + 0.25 and posts:
                    target = posts[rng.randrange(len(posts))]
                    stance = _pick_stance(_lean(card), rng)
                    text = rng.choice(_STANCE_LINES[stance]).format(feature=feature)
                    cid = f"c{len(comments)}"
                    comments.append(
                        Comment(
                            comment_id=cid,
                            post_id=target.post_id,
                            agent_id=a.agent_id,
                            platform=target.platform,
                            round=rnd,
                            content=text,
                        )
                    )
                    actions.append(
                        Action(
                            type="CREATE_COMMENT",
                            actor=a.agent_id,
                            target=target.post_id,
                            round=rnd,
                            platform=target.platform,
                            args={"content": text},
                        )
                    )
                elif roll < 0.85 and posts:
                    target = posts[rng.randrange(len(posts))]
                    target.likes += 1
                    actions.append(
                        Action(
                            type="LIKE_POST",
                            actor=a.agent_id,
                            target=target.post_id,
                            round=rnd,
                            platform=target.platform,
                        )
                    )

        timeline = _timeline(actions)
        interviews = (
            await self.interview(handle, spec.interview_questions)
            if spec.interview_questions
            else []
        )
        return RunArtifacts(
            tenant_id=handle.tenant_id,
            job_id=handle.job_id,
            run_id=handle.run_id,
            arm_id=handle.arm_id,
            agents=agents,
            posts=posts,
            comments=comments,
            actions=actions,
            interviews=interviews,
            engine_report_markdown="# Mock engine report\n\nDeterministic fixture output.\n",
            timeline=timeline,
            stats=RunStats(
                agents_target=handle.target_agents,
                agents_actual=len(agents),
                rounds_planned=handle.max_rounds,
                rounds_completed=handle.max_rounds,
                posts=len(posts),
                comments=len(comments),
                actions=len(actions),
            ),
            engine_meta=EngineMeta(
                engine="mock",
                version=VERSION,
                model_route=handle.model_route,
                random_seed=handle.random_seed,
                rounds=handle.max_rounds,
            ),
        )

    async def cancel(self, handle: RunHandle) -> None:
        await self._gate("cancel")
        if handle.run_id in self._runs:
            self._runs[handle.run_id].cancelled = True

    async def cleanup(self, handle: RunHandle) -> CleanupReport:
        await self._gate("cleanup")
        self._runs.pop(handle.run_id, None)  # idempotent: already gone counts as deleted
        return CleanupReport(
            run_id=handle.run_id,
            engine_instance=handle.engine_instance,
            deleted={"simulation": True},
            memory_deleted_verified=True,
        )


@dataclass
class _MockRun:
    spec: RunSpec
    rng_seed: int
    started: bool = False
    cancelled: bool = False
    polls: int = 0


def _lean(card: PersonaCard | None) -> float:
    """-1..1 tendency. Value-seekers lean skeptical, early adopters lean positive."""
    if card is None:
        return 0.0
    a = card.attributes
    lean = 0.0
    if a.price_orientation and "value" in a.price_orientation:
        lean -= 0.3
    if a.price_orientation and "premium" in a.price_orientation:
        lean += 0.2
    if a.tech_adoption and "early" in a.tech_adoption:
        lean += 0.3
    if a.tech_adoption and "late" in a.tech_adoption:
        lean -= 0.2
    h = int(hashlib.sha256(card.id.encode()).hexdigest()[:4], 16) / 0xFFFF
    return max(-1.0, min(1.0, lean + (h - 0.5) * 0.4))


def _pick_stance(lean: float, rng: random.Random) -> str:
    p_pos = 0.35 + 0.3 * lean
    p_neg = 0.30 - 0.3 * lean
    r = rng.random()
    if r < p_pos:
        return "positive"
    if r < p_pos + p_neg:
        return "negative"
    if r < 0.92:
        return "neutral"
    return "confused"


def _answer(
    q: InterviewQuestion,
    agent_id: str,
    card: PersonaCard | None,
    lean: float,
    rng: random.Random,
    platform: Platform,
) -> InterviewAnswer:
    def mk(text: str, likert: int | None = None, price: float | None = None) -> InterviewAnswer:
        return InterviewAnswer(
            question_id=q.id,
            agent_id=agent_id,
            persona_card_id=card.id if card else None,
            segment_id=card.segment_id if card else None,
            platform=platform,
            answer_text=text,
            likert=likert,
            price_value=price,
        )

    if q.kind == "purchase_likelihood":
        score = max(1, min(5, round(3 + 2 * lean + rng.uniform(-1, 1))))
        return mk(f"I'd say {score} out of 5.", likert=score)
    vw = {
        "vw_too_cheap": 0.4,
        "vw_bargain": 0.7,
        "vw_getting_expensive": 1.1,
        "vw_too_expensive": 1.5,
    }
    if q.kind in vw:
        anchor = q.price or 50.0
        value = round(anchor * vw[q.kind] * (1 + 0.3 * lean + rng.uniform(-0.1, 0.1)), 2)
        return mk(f"Around {value}.", price=value)
    if q.kind in ("price_reaction", "price_point"):
        label = "fair" if lean > 0.1 else ("expensive" if lean > -0.4 else "too expensive")
        return mk(f"The price feels {label}.")
    stance = _pick_stance(lean, rng)
    return mk(rng.choice(_STANCE_LINES[stance]).format(feature="it"))


def _timeline(actions: list[Action]) -> list[TimelineEntry]:
    by_round: dict[int, list[Action]] = {}
    for a in actions:
        by_round.setdefault(a.round, []).append(a)
    return [
        TimelineEntry(
            round=rnd,
            actions_total=len(acts),
            active_agents=len({a.actor for a in acts}),
            by_platform=dict(Counter(a.platform for a in acts)),
            action_types=dict(Counter(a.type for a in acts)),
        )
        for rnd, acts in sorted(by_round.items())
    ]
