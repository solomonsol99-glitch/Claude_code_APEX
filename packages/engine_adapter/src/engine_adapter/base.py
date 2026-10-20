"""SimulationEngine protocol and the engine-agnostic data contract (§2.4.1, §2b.1).

Analysis and reporting read only ``RunArtifacts``. Swapping engines means writing a new
adapter that emits this schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

RUN_ARTIFACTS_SCHEMA_VERSION: Literal["run_artifacts.v1"] = "run_artifacts.v1"

Platform = Literal["twitter", "reddit"]
Grounding = Literal["census", "cohort_aggregate", "llm_prior"]
RunState = Literal["pending", "running", "completed", "failed", "cancelled"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- persona cards (§2b.1) -------------------------------------------------------------


class PersonaAttributes(_Strict):
    age_band: str | None = None
    household: str | None = None
    income_band: str | None = None
    region: str | None = None
    urbanicity: str | None = None
    life_stage: str | None = None
    interests: list[str] = Field(default_factory=list)
    media_habits: list[str] = Field(default_factory=list)
    tech_adoption: str | None = None
    price_orientation: str | None = None
    # Per-attribute provenance, e.g. {"age_band": "census", "interests": "llm_prior"}.
    sources: dict[str, Grounding] = Field(default_factory=dict)


class PersonaConstraints(_Strict):
    no_protected_traits: bool = True


class PersonaCard(_Strict):
    id: str = Field(pattern=r"^pc_[A-Za-z0-9]+$")
    segment_id: str
    segment_label: str
    weight: float = Field(gt=0, le=1)
    source_mode: Literal["synthetic", "cohort"]
    grounding: Grounding
    display_name: str = Field(min_length=1, max_length=40)
    attributes: PersonaAttributes
    narrative: str
    constraints: PersonaConstraints = Field(default_factory=PersonaConstraints)


# --- run inputs ---------------------------------------------------------------------------

QuestionKind = Literal[
    "open",
    "purchase_likelihood",  # 1-5
    "price_reaction",
    "price_point",  # reaction to an alternative price
    "vw_too_cheap",
    "vw_bargain",
    "vw_getting_expensive",
    "vw_too_expensive",
]


class InterviewQuestion(_Strict):
    id: str
    kind: QuestionKind
    text: str = Field(min_length=1, max_length=2000)
    price: float | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class InterviewAnswer(_Strict):
    question_id: str
    agent_id: str
    persona_card_id: str | None = None
    segment_id: str | None = None
    platform: Platform | None = None
    answer_text: str
    likert: int | None = Field(default=None, ge=1, le=5)
    price_value: float | None = None
    # Baseline answers speak for a whole segment, not one agent.
    scope: Literal["agent", "segment", "overall"] = "agent"


class StageTimeouts(_Strict):
    graph_s: float = 15 * 60
    profiles_s: float = 20 * 60
    per_round_s: float = 3 * 60
    interview_s: float = 10 * 60
    report_s: float = 15 * 60
    http_s: float = 30
    poll_interval_s: float = 5


def _default_platforms() -> list[Platform]:
    return ["twitter", "reddit"]


class RunSpec(_Strict):
    tenant_id: str
    job_id: str
    run_id: str
    arm_id: str
    seed_documents: list[str] = Field(min_length=1)  # S3 URIs to sanitized seed docs
    simulation_requirement: str = Field(min_length=1)
    persona_cards: list[PersonaCard] = Field(min_length=1)
    target_agents: int = Field(gt=0)
    agent_tolerance: float = Field(default=0.10, ge=0, le=1)
    platforms: list[Platform] = Field(default_factory=_default_platforms, min_length=1)
    max_rounds: int = Field(gt=0)
    model_route: str
    llm_virtual_key_ref: str
    random_seed: int
    interview_questions: list[InterviewQuestion] = Field(default_factory=list)
    timeouts: StageTimeouts = Field(default_factory=StageTimeouts)
    enable_graph_memory_update: bool = False

    @model_validator(mode="after")
    def _unique_ids(self) -> RunSpec:
        ids = [c.id for c in self.persona_cards]
        if len(ids) != len(set(ids)):
            raise ValueError("persona card ids must be unique")
        qids = [q.id for q in self.interview_questions]
        if len(qids) != len(set(qids)):
            raise ValueError("interview question ids must be unique")
        return self


# --- run lifecycle ---------------------------------------------------------------------


class RunHandle(BaseModel):
    """Everything an adapter needs to resume work on a run. Serializable for checkpoints."""

    model_config = ConfigDict(extra="forbid")

    engine_instance: str
    engine_ids: dict[str, str] = Field(default_factory=dict)
    stage: str
    run_id: str
    tenant_id: str
    job_id: str
    arm_id: str
    platforms: list[Platform]
    max_rounds: int
    target_agents: int
    agents_actual: int | None = None
    # engine agent id -> persona card id (None if unmatched)
    agent_personas: dict[str, str | None] = Field(default_factory=dict)
    model_route: str = ""
    random_seed: int = 0


class RunStatus(_Strict):
    stage: str
    state: RunState
    progress: float = Field(ge=0, le=1)
    current_round: int | None = None
    agents_actual: int | None = None
    last_heartbeat_at: str
    error: str | None = None


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --- RunArtifacts v1 -------------------------------------------------------------------


class PlatformProfile(_Strict):
    username: str | None = None
    name: str | None = None
    bio: str | None = None


class Agent(_Strict):
    agent_id: str
    persona_card_id: str | None = None
    segment_id: str | None = None
    profiles: dict[Platform, PlatformProfile] = Field(default_factory=dict)


class Post(_Strict):
    post_id: str
    agent_id: str | None
    platform: Platform
    round: int | None = None
    content: str
    original_post_id: str | None = None
    quote_content: str | None = None
    likes: int = 0
    dislikes: int = 0
    shares: int = 0


class Comment(_Strict):
    comment_id: str
    post_id: str
    agent_id: str | None
    platform: Platform
    round: int | None = None
    content: str
    likes: int = 0
    dislikes: int = 0


class Action(_Strict):
    type: str
    actor: str
    target: str | None = None
    round: int
    platform: Platform
    args: dict[str, Any] = Field(default_factory=dict)
    success: bool = True


class TimelineEntry(_Strict):
    round: int
    actions_total: int
    active_agents: int
    by_platform: dict[str, int] = Field(default_factory=dict)
    action_types: dict[str, int] = Field(default_factory=dict)


class RunStats(_Strict):
    agents_target: int
    agents_actual: int
    rounds_planned: int
    rounds_completed: int
    posts: int
    comments: int
    actions: int
    truncated: bool = False


class EngineMeta(_Strict):
    engine: str
    version: str
    model_route: str
    random_seed: int
    rounds: int
    timings_s: dict[str, float] = Field(default_factory=dict)
    token_usage: dict[str, int] | None = None
    warnings: list[str] = Field(default_factory=list)


class RunArtifacts(_Strict):
    schema_version: Literal["run_artifacts.v1"] = RUN_ARTIFACTS_SCHEMA_VERSION
    tenant_id: str
    job_id: str
    run_id: str
    arm_id: str
    agents: list[Agent]
    posts: list[Post] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    interviews: list[InterviewAnswer] = Field(default_factory=list)
    engine_report_markdown: str | None = None
    timeline: list[TimelineEntry] = Field(default_factory=list)
    stats: RunStats
    engine_meta: EngineMeta


class CleanupReport(_Strict):
    run_id: str
    engine_instance: str
    deleted: dict[str, bool] = Field(default_factory=dict)  # resource -> deleted?
    memory_deleted_verified: bool | None = None  # None = not verifiable by this adapter
    errors: list[str] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.errors and all(self.deleted.values())


class EngineCapabilities(_Strict):
    name: str
    version: str
    platforms: list[Platform]
    supports_interviews: bool
    supports_agent_count_control: Literal["exact", "approximate", "none"]
    supports_resume: bool
    max_agents_tested: int
    memory_backend: str


@runtime_checkable
class SimulationEngine(Protocol):
    def capabilities(self) -> EngineCapabilities: ...
    async def health(self) -> bool: ...
    async def prepare(self, spec: RunSpec) -> RunHandle: ...  # seed -> graph -> profiles
    async def start(self, handle: RunHandle) -> RunHandle: ...
    async def status(self, handle: RunHandle) -> RunStatus: ...
    async def interview(
        self, handle: RunHandle, qs: list[InterviewQuestion]
    ) -> list[InterviewAnswer]: ...
    async def collect(self, handle: RunHandle) -> RunArtifacts: ...
    async def cancel(self, handle: RunHandle) -> None: ...
    async def cleanup(self, handle: RunHandle) -> CleanupReport: ...  # deletes engine + memory


def check_agent_bounds(*, actual: int, target: int, tolerance: float, min_agents: int) -> None:
    """Raise AgentCountOutOfBoundsError unless actual is within target ± tolerance and >= min."""
    from engine_adapter.errors import AgentCountOutOfBoundsError

    low = target * (1 - tolerance)
    high = target * (1 + tolerance)
    if actual < min_agents or not (low <= actual <= high):
        raise AgentCountOutOfBoundsError(
            actual=actual, target=target, tolerance=tolerance, min_agents=min_agents
        )


def handle_from_spec(spec: RunSpec, *, engine_instance: str, stage: str) -> RunHandle:
    return RunHandle(
        engine_instance=engine_instance,
        stage=stage,
        run_id=spec.run_id,
        tenant_id=spec.tenant_id,
        job_id=spec.job_id,
        arm_id=spec.arm_id,
        platforms=list(spec.platforms),
        max_rounds=spec.max_rounds,
        target_agents=spec.target_agents,
        model_route=spec.model_route,
        random_seed=spec.random_seed,
    )
