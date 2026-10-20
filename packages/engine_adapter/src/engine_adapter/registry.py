"""Engine registry: tenants pick an engine by name in config (``simulation.engine``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from engine_adapter.base import SimulationEngine

EngineFactory = Callable[..., SimulationEngine]

_REGISTRY: dict[str, EngineFactory] = {}


def register_engine(name: str, factory: EngineFactory) -> None:
    if name in _REGISTRY:
        raise ValueError(f"engine {name!r} already registered")
    _REGISTRY[name] = factory


def available_engines() -> list[str]:
    _ensure_builtins()
    return sorted(_REGISTRY)


def get_engine(name: str, **deps: Any) -> SimulationEngine:
    """Build the named engine. ``deps`` are passed to its constructor."""
    _ensure_builtins()
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown engine {name!r}; known: {sorted(_REGISTRY)}") from None
    return factory(**deps)


def _ensure_builtins() -> None:
    if _REGISTRY:
        return
    from engine_adapter.baseline import BaselineEngine
    from engine_adapter.mirofish import MiroFishEngine
    from engine_adapter.mock import MockEngine

    _REGISTRY.update({"mock": MockEngine, "baseline": BaselineEngine, "mirofish": MiroFishEngine})
