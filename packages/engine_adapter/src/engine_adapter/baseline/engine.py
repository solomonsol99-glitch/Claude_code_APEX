"""Single-call baseline (§2.4.2, §5.3 step 5): the control in every job.

One structured LLM call per segment plus one overall, answering the same questions
without any simulation. Emits RunArtifacts with ``interviews`` only.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engine_adapter.base import (
    Agent,
    CleanupReport,
    EngineCapabilities,
    EngineMeta,
    InterviewAnswer,
    InterviewQuestion,
    PersonaCard,
    RunArtifacts,
    RunHandle,
    RunSpec,
    RunStats,
    RunStatus,
    handle_from_spec,
    utc_now_iso,
)
from engine_adapter.documents import DocumentLoader, missing_loader
from engine_adapter.errors import EngineResponseInvalidError, EngineStageFailedError
from engine_adapter.llm import LLMClient, Message
from engine_adapter.prompting import DATA_INSTRUCTION, new_boundary, wrap_untrusted

VERSION = "baseline-1"
OVERALL = "overall"
MAX_SEED_CHARS = 24_000

SYSTEM_PROMPT = (
    "You are a market-research assistant. You describe how a described consumer group "
    "would likely react to a product, in their own voice, without simulation. "
    "Answer only with a JSON object of this shape: "
    '{"answers": [{"question_id": str, "answer_text": str, "likert": int 1-5 or null, '
    '"price_value": number or null}], "overall_stance": "positive"|"neutral"|"negative"|'
    '"confused", "objections": [str], "summary": str}. '
    "Do not include any other text. "
)


class BaselineAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question_id: str
    answer_text: str = Field(max_length=4000)
    likert: int | None = Field(default=None, ge=1, le=5)
    price_value: float | None = Field(default=None, ge=0)


class BaselineResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answers: list[BaselineAnswer]
    overall_stance: Literal["positive", "neutral", "negative", "confused"]
    objections: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(default="", max_length=4000)


@dataclass
class _BaselineRun:
    spec: RunSpec
    seed_text: str
    results: dict[str, BaselineResponse] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    done: bool = False
    cancelled: bool = False


class BaselineEngine:
    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        llm_factory: Callable[[RunSpec], LLMClient] | None = None,
        load_document: DocumentLoader = missing_loader,
        max_attempts_per_call: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if llm is None and llm_factory is None:
            raise ValueError("BaselineEngine needs llm or llm_factory")
        self._llm = llm
        self._llm_factory = llm_factory
        self._load = load_document
        self._max_attempts = max_attempts_per_call
        self._clock = clock
        self._runs: dict[str, _BaselineRun] = {}

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            name="baseline",
            version=VERSION,
            platforms=["twitter", "reddit"],
            supports_interviews=True,
            supports_agent_count_control="none",
            supports_resume=True,
            max_agents_tested=0,
            memory_backend="none",
        )

    async def health(self) -> bool:
        return True

    async def prepare(self, spec: RunSpec) -> RunHandle:
        parts: list[str] = []
        for uri in spec.seed_documents:
            raw = await self._load(uri)
            parts.append(raw.decode("utf-8", errors="replace"))
        seed_text = "\n\n".join(parts)[:MAX_SEED_CHARS]
        handle = handle_from_spec(spec, engine_instance="baseline", stage="PROFILES_READY")
        segments = _segments(spec.persona_cards)
        personas: dict[str, str | None] = {f"segment:{s}": None for s in segments}
        personas[OVERALL] = None
        handle.agent_personas = personas
        handle.agents_actual = len(handle.agent_personas)
        self._runs[spec.run_id] = _BaselineRun(spec=spec, seed_text=seed_text)
        return handle

    def _run(self, handle: RunHandle) -> _BaselineRun:
        try:
            return self._runs[handle.run_id]
        except KeyError:
            raise EngineStageFailedError("unknown baseline run") from None

    async def start(self, handle: RunHandle) -> RunHandle:
        run = self._run(handle)
        llm = self._llm or self._llm_factory(run.spec)  # type: ignore[misc]
        groups = _segments(run.spec.persona_cards)
        targets: list[tuple[str, list[PersonaCard]]] = [
            (f"segment:{sid}", cards) for sid, cards in groups.items()
        ]
        targets.append((OVERALL, run.spec.persona_cards))
        for key, cards in targets:
            if run.cancelled:
                break
            t0 = self._clock()
            run.results[key] = await self._ask(llm, run, cards, scope_key=key)
            run.timings[key] = self._clock() - t0
        run.done = True
        return handle.model_copy(update={"stage": "SIMULATING"})

    async def _ask(
        self, llm: LLMClient, run: _BaselineRun, cards: Sequence[PersonaCard], *, scope_key: str
    ) -> BaselineResponse:
        spec = run.spec
        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            messages = _messages(spec, run.seed_text, cards, scope_key)
            result = await llm.chat_json(
                messages, route=spec.model_route, temperature=0.7, seed=spec.random_seed + attempt
            )
            for k, v in result.usage.items():
                run.usage[k] = run.usage.get(k, 0) + v
            try:
                parsed = BaselineResponse.model_validate_json(result.content)
            except ValidationError as exc:
                last_error = f"{scope_key}: invalid baseline JSON ({exc.error_count()} errors)"
                run.warnings.append(f"{last_error}, attempt {attempt}")
                continue
            known = {q.id for q in spec.interview_questions}
            unknown = [a.question_id for a in parsed.answers if a.question_id not in known]
            if unknown:
                run.warnings.append(f"{scope_key}: dropped answers to unknown questions")
            parsed.answers = [a for a in parsed.answers if a.question_id in known]
            missing = known - {a.question_id for a in parsed.answers}
            if missing:
                run.warnings.append(f"{scope_key}: {len(missing)} questions unanswered")
            return parsed
        raise EngineResponseInvalidError(last_error or "baseline failed", stage="SIMULATING")

    async def status(self, handle: RunHandle) -> RunStatus:
        run = self._run(handle)
        state: Literal["pending", "running", "completed", "failed", "cancelled"]
        if run.cancelled:
            state = "cancelled"
        elif run.done:
            state = "completed"
        else:
            state = "pending"
        return RunStatus(
            stage=handle.stage,
            state=state,
            progress=1.0 if run.done else 0.0,
            agents_actual=handle.agents_actual,
            last_heartbeat_at=utc_now_iso(),
        )

    async def interview(
        self, handle: RunHandle, qs: list[InterviewQuestion]
    ) -> list[InterviewAnswer]:
        run = self._run(handle)
        wanted = {q.id for q in qs}
        answers: list[InterviewAnswer] = []
        for key, resp in run.results.items():
            segment_id = key.removeprefix("segment:") if key != OVERALL else None
            scope: Literal["segment", "overall"] = "overall" if key == OVERALL else "segment"
            answers.extend(
                InterviewAnswer(
                    question_id=a.question_id,
                    agent_id=key,
                    segment_id=segment_id,
                    answer_text=a.answer_text,
                    likert=a.likert,
                    price_value=a.price_value,
                    scope=scope,
                )
                for a in resp.answers
                if a.question_id in wanted
            )
            answers.append(
                InterviewAnswer(
                    question_id="baseline.summary",
                    agent_id=key,
                    segment_id=segment_id,
                    answer_text=_summary_text(resp),
                    scope=scope,
                )
            )
        return answers

    async def collect(self, handle: RunHandle) -> RunArtifacts:
        run = self._run(handle)
        if not run.done:
            raise EngineStageFailedError("baseline has not run yet", stage="COLLECTED")
        interviews = await self.interview(handle, run.spec.interview_questions)
        agents = [
            Agent(
                agent_id=key,
                segment_id=key.removeprefix("segment:") if key != OVERALL else None,
            )
            for key in run.results
        ]
        return RunArtifacts(
            tenant_id=handle.tenant_id,
            job_id=handle.job_id,
            run_id=handle.run_id,
            arm_id=handle.arm_id,
            agents=agents,
            interviews=interviews,
            stats=RunStats(
                agents_target=len(agents),
                agents_actual=len(agents),
                rounds_planned=0,
                rounds_completed=0,
                posts=0,
                comments=0,
                actions=0,
            ),
            engine_meta=EngineMeta(
                engine="baseline",
                version=VERSION,
                model_route=handle.model_route,
                random_seed=handle.random_seed,
                rounds=0,
                timings_s=dict(run.timings),
                token_usage=dict(run.usage) or None,
                warnings=list(run.warnings),
            ),
        )

    async def cancel(self, handle: RunHandle) -> None:
        if handle.run_id in self._runs:
            self._runs[handle.run_id].cancelled = True

    async def cleanup(self, handle: RunHandle) -> CleanupReport:
        self._runs.pop(handle.run_id, None)
        return CleanupReport(
            run_id=handle.run_id,
            engine_instance=handle.engine_instance,
            deleted={"baseline_state": True},
            memory_deleted_verified=None,
        )


def _segments(cards: Sequence[PersonaCard]) -> dict[str, list[PersonaCard]]:
    out: dict[str, list[PersonaCard]] = {}
    for c in cards:
        out.setdefault(c.segment_id, []).append(c)
    return out


def _describe(cards: Sequence[PersonaCard]) -> str:
    total = sum(c.weight for c in cards) or 1.0
    lines = []
    for c in cards:
        a = c.attributes
        attrs = ", ".join(
            f"{k}: {v}"
            for k, v in a.model_dump(exclude={"sources"}).items()
            if v not in (None, [], "")
        )
        lines.append(
            f"- {c.segment_label} (share {c.weight / total:.0%} of this group): {attrs}. "
            f"{c.narrative}"
        )
    return "\n".join(lines)


def _messages(
    spec: RunSpec, seed_text: str, cards: Sequence[PersonaCard], scope_key: str
) -> list[Message]:
    b = new_boundary()
    who = "the whole target market" if scope_key == OVERALL else "this consumer segment"
    questions = "\n".join(
        f"- question_id={q.id} kind={q.kind}"
        + (f" price={q.price} {q.currency or ''}" if q.price is not None else "")
        + f": {q.text}"
        for q in spec.interview_questions
    )
    user = "\n\n".join(
        [
            DATA_INSTRUCTION.format(b=b),
            f"Describe how {who} would react. Product information:",
            wrap_untrusted(seed_text, b, label="product information"),
            "Consumer group:",
            wrap_untrusted(_describe(cards), b, label="persona descriptions"),
            "Questions to answer (answer every question_id):",
            wrap_untrusted(questions or "(none)", b, label="questions"),
        ]
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _summary_text(resp: BaselineResponse) -> str:
    objections = "; ".join(resp.objections) if resp.objections else "none stated"
    return f"Stance: {resp.overall_stance}. Objections: {objections}. {resp.summary}".strip()
