"""SimulationEngine protocol, RunArtifacts schema, MiroFish/Baseline/Mock adapters (§2.4)."""

from engine_adapter.base import (
    RUN_ARTIFACTS_SCHEMA_VERSION,
    CleanupReport,
    EngineCapabilities,
    InterviewAnswer,
    InterviewQuestion,
    PersonaCard,
    RunArtifacts,
    RunHandle,
    RunSpec,
    RunStatus,
    SimulationEngine,
    StageTimeouts,
)
from engine_adapter.registry import available_engines, get_engine, register_engine

__all__ = [
    "RUN_ARTIFACTS_SCHEMA_VERSION",
    "CleanupReport",
    "EngineCapabilities",
    "InterviewAnswer",
    "InterviewQuestion",
    "PersonaCard",
    "RunArtifacts",
    "RunHandle",
    "RunSpec",
    "RunStatus",
    "SimulationEngine",
    "StageTimeouts",
    "available_engines",
    "get_engine",
    "register_engine",
]
