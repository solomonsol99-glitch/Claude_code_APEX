"""Circuit breaker (§4.4) with a pluggable state store so all workers can share state.

Opens after ``consecutive_failures`` failures in a row, or when the failure rate in the
sliding window exceeds ``failure_rate`` over at least ``window_min_calls`` calls.
After ``open_seconds`` it goes half-open and lets ``half_open_trials`` calls through.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol, TypeVar

T = TypeVar("T")


class State(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    consecutive_failures: int = 5
    failure_rate: float = 0.5
    window_min_calls: int = 20
    window_seconds: float = 60.0
    open_seconds: float = 30.0
    half_open_trials: int = 1


@dataclass(slots=True)
class BreakerState:
    state: State = State.CLOSED
    consecutive_failures: int = 0
    # (timestamp, ok) per call inside the window
    window: list[tuple[float, bool]] = field(default_factory=list)
    opened_at: float | None = None
    half_open_in_flight: int = 0


class BreakerStateStore(Protocol):
    async def load(self, name: str) -> BreakerState | None: ...
    async def save(self, name: str, state: BreakerState) -> None: ...


class InMemoryBreakerStore:
    """Process-local store. Production uses a Redis-backed store with the same interface."""

    def __init__(self) -> None:
        self._states: dict[str, BreakerState] = {}

    async def load(self, name: str) -> BreakerState | None:
        state = self._states.get(name)
        return replace(state, window=list(state.window)) if state else None

    async def save(self, name: str, state: BreakerState) -> None:
        self._states[name] = replace(state, window=list(state.window))


class CircuitOpenError(Exception):
    def __init__(self, name: str, retry_in: float) -> None:
        super().__init__(f"circuit {name!r} is open; retry in {retry_in:.1f}s")
        self.name = name
        self.retry_in = retry_in


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        store: BreakerStateStore | None = None,
        config: BreakerConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        counts_as_failure: Callable[[BaseException], bool] = lambda _exc: True,
    ) -> None:
        self.name = name
        self.store = store or InMemoryBreakerStore()
        self.config = config or BreakerConfig()
        self.clock = clock
        self.counts_as_failure = counts_as_failure

    async def _load(self) -> BreakerState:
        return await self.store.load(self.name) or BreakerState()

    async def current_state(self) -> State:
        st = await self._load()
        if st.state is State.OPEN and self._open_elapsed(st):
            return State.HALF_OPEN
        return st.state

    def _open_elapsed(self, st: BreakerState) -> bool:
        return st.opened_at is not None and self.clock() - st.opened_at >= self.config.open_seconds

    async def before_call(self) -> None:
        """Raise CircuitOpenError if the call is not allowed; otherwise reserve a slot."""
        st = await self._load()
        if st.state is State.OPEN:
            if not self._open_elapsed(st):
                assert st.opened_at is not None
                remaining = self.config.open_seconds - (self.clock() - st.opened_at)
                raise CircuitOpenError(self.name, remaining)
            st.state = State.HALF_OPEN
            st.half_open_in_flight = 0
        if st.state is State.HALF_OPEN:
            if st.half_open_in_flight >= self.config.half_open_trials:
                raise CircuitOpenError(self.name, self.config.open_seconds)
            st.half_open_in_flight += 1
        await self.store.save(self.name, st)

    async def record_success(self) -> None:
        st = await self._load()
        if st.state is State.HALF_OPEN:
            await self.store.save(self.name, BreakerState())
            return
        st.consecutive_failures = 0
        self._push(st, ok=True)
        await self.store.save(self.name, st)

    async def record_failure(self) -> None:
        st = await self._load()
        now = self.clock()
        if st.state is State.HALF_OPEN:
            await self.store.save(self.name, BreakerState(state=State.OPEN, opened_at=now))
            return
        st.consecutive_failures += 1
        self._push(st, ok=False)
        if self._should_open(st):
            st = BreakerState(state=State.OPEN, opened_at=now)
        await self.store.save(self.name, st)

    def _push(self, st: BreakerState, *, ok: bool) -> None:
        now = self.clock()
        cutoff = now - self.config.window_seconds
        st.window = [(ts, good) for ts, good in st.window if ts >= cutoff]
        st.window.append((now, ok))

    def _should_open(self, st: BreakerState) -> bool:
        if st.consecutive_failures >= self.config.consecutive_failures:
            return True
        calls = len(st.window)
        if calls < self.config.window_min_calls:
            return False
        failures = sum(1 for _, ok in st.window if not ok)
        return failures / calls > self.config.failure_rate

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        await self.before_call()
        try:
            result = await fn()
        except Exception as exc:
            if self.counts_as_failure(exc):
                await self.record_failure()
            else:
                await self.record_success()
            raise
        await self.record_success()
        return result
