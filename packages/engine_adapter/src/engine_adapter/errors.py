"""Engine error taxonomy. ``kind`` follows §4.3: transient, input, budget, engine_bug.

These will be reconciled with ``mre_core.errors`` once that package settles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

if TYPE_CHECKING:
    from engine_adapter.base import RunHandle

ErrorKind = Literal["transient", "input", "budget", "engine_bug"]


class EngineError(Exception):
    kind: ClassVar[ErrorKind] = "engine_bug"

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        # Partial handle so the caller can still clean up engine and memory state.
        self.handle: RunHandle | None = None


class EngineUnavailableError(EngineError):
    kind = "transient"


class EngineStageTimeoutError(EngineError):
    kind = "transient"


class EngineStageFailedError(EngineError):
    kind = "engine_bug"
    status_code: int | None = None


class EngineResponseInvalidError(EngineError):
    """The engine (or the LLM behind it) returned something we cannot parse."""

    kind = "engine_bug"


class EngineInputRejectedError(EngineError):
    kind = "input"


class EngineBudgetExceededError(EngineError):
    kind = "budget"


class AgentCountOutOfBoundsError(EngineError):
    kind = "engine_bug"

    def __init__(self, *, actual: int, target: int, tolerance: float, min_agents: int) -> None:
        super().__init__(
            f"agent count {actual} outside target {target} ± {tolerance:.0%} (min {min_agents})",
            stage="PROFILES_READY",
        )
        self.actual = actual
        self.target = target
        self.tolerance = tolerance
        self.min_agents = min_agents


class InjectedFaultError(EngineError):
    """Raised by MockEngine fault injection."""

    kind = "transient"
