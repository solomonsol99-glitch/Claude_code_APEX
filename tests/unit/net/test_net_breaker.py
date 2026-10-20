import pytest
from mre_net import BreakerConfig, CircuitBreaker, CircuitOpenError, InMemoryBreakerStore
from mre_net.breaker import State


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _fail() -> None:
    raise RuntimeError("boom")


async def _ok() -> str:
    return "ok"


async def test_opens_after_consecutive_failures_and_recovers() -> None:
    clock = Clock()
    br = CircuitBreaker("llm:sim-primary", clock=clock)
    for _ in range(5):
        with pytest.raises(RuntimeError):
            await br.call(_fail)
    assert await br.current_state() is State.OPEN
    with pytest.raises(CircuitOpenError):
        await br.call(_ok)

    clock.now += 30
    assert await br.current_state() is State.HALF_OPEN
    assert await br.call(_ok) == "ok"
    assert await br.current_state() is State.CLOSED


async def test_half_open_failure_reopens_and_limits_trials() -> None:
    clock = Clock()
    br = CircuitBreaker("zep", clock=clock, config=BreakerConfig(consecutive_failures=1))
    with pytest.raises(RuntimeError):
        await br.call(_fail)
    clock.now += 31
    await br.before_call()  # takes the single trial slot
    with pytest.raises(CircuitOpenError):
        await br.before_call()
    await br.record_failure()
    assert await br.current_state() is State.OPEN


async def test_opens_on_failure_rate_over_window() -> None:
    clock = Clock()
    br = CircuitBreaker("llm", clock=clock)
    # alternate so consecutive failures never reach 5; 11 failures of 21 calls > 50%
    for i in range(21):
        clock.now += 1
        if i % 2 == 0:
            with pytest.raises(RuntimeError):
                await br.call(_fail)
        else:
            await br.call(_ok)
    assert await br.current_state() is State.OPEN


async def test_window_expires_old_calls() -> None:
    clock = Clock()
    br = CircuitBreaker("llm", clock=clock)
    for _ in range(4):
        with pytest.raises(RuntimeError):
            await br.call(_fail)
    await br.call(_ok)
    clock.now += 120
    for _ in range(4):
        with pytest.raises(RuntimeError):
            await br.call(_fail)
    assert await br.current_state() is State.CLOSED


async def test_non_failure_exceptions_do_not_trip() -> None:
    br = CircuitBreaker("llm", counts_as_failure=lambda e: not isinstance(e, ValueError))
    for _ in range(10):
        with pytest.raises(ValueError, match="input"):
            await br.call(_raise_value)
    assert await br.current_state() is State.CLOSED


async def _raise_value() -> None:
    raise ValueError("input")


async def test_store_is_shared_between_instances() -> None:
    store = InMemoryBreakerStore()
    clock = Clock()
    a = CircuitBreaker("zep", store=store, clock=clock)
    b = CircuitBreaker("zep", store=store, clock=clock)
    for _ in range(5):
        with pytest.raises(RuntimeError):
            await a.call(_fail)
    with pytest.raises(CircuitOpenError):
        await b.call(_ok)
