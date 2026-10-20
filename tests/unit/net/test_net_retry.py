import random
from datetime import UTC, datetime

import httpx
import pytest
from mre_net import CallType, RetryPolicy, backoff_delay, is_retryable, retry_async
from mre_net.retry import parse_retry_after, raise_for_retryable_status


def _resp(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, request=httpx.Request("GET", "https://x.test"))


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def test_default_attempts_per_call_type() -> None:
    assert RetryPolicy.for_call(CallType.LLM).max_attempts == 4
    assert RetryPolicy.for_call(CallType.ZEP).max_attempts == 5
    assert RetryPolicy.for_call(CallType.S3).max_attempts == 5
    assert RetryPolicy.for_call(CallType.SCRAPER).max_attempts == 2
    assert RetryPolicy.for_call(CallType.ENGINE, max_attempts=7).max_attempts == 7


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ReadTimeout("t"), True),
        (httpx.ConnectError("c"), True),
        (ConnectionResetError(), True),
        (httpx.HTTPStatusError("x", request=_resp(429).request, response=_resp(429)), True),
        (httpx.HTTPStatusError("x", request=_resp(503).request, response=_resp(503)), True),
        (httpx.HTTPStatusError("x", request=_resp(400).request, response=_resp(400)), False),
        (ValueError("no"), False),
    ],
)
def test_is_retryable(exc: BaseException, expected: bool) -> None:
    assert is_retryable(exc) is expected


def test_raise_for_retryable_status_passes_4xx() -> None:
    assert raise_for_retryable_status(_resp(404)).status_code == 404
    with pytest.raises(httpx.HTTPStatusError):
        raise_for_retryable_status(_resp(502))


def test_backoff_full_jitter_bounds() -> None:
    rng = random.Random(1)
    for attempt in range(1, 12):
        d = backoff_delay(attempt, base=1.0, cap=60.0, rng=rng)
        assert 0 <= d <= min(60.0, 2 ** (attempt - 1))


def test_parse_retry_after() -> None:
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert parse_retry_after("Thu, 01 Jan 2026 00:00:30 GMT", now=now) == 30.0


async def test_retries_then_succeeds() -> None:
    sleeps = Sleeps()
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ReadTimeout("slow")
        return "ok"

    out = await retry_async(fn, call_type=CallType.LLM, sleep=sleeps, rng=random.Random(0))
    assert out == "ok"
    assert calls == 3
    assert len(sleeps.calls) == 2


async def test_gives_up_after_max_attempts() -> None:
    sleeps = Sleeps()
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await retry_async(fn, call_type=CallType.SCRAPER, sleep=sleeps)
    assert calls == 2


async def test_non_retryable_fails_fast() -> None:
    calls = 0

    async def fn() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("input error")

    with pytest.raises(ValueError, match="input error"):
        await retry_async(fn, call_type=CallType.LLM, sleep=Sleeps())
    assert calls == 1


async def test_honors_retry_after_capped() -> None:
    sleeps = Sleeps()
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise_for_retryable_status(_resp(429, {"Retry-After": "12"}))
        if calls == 2:
            raise_for_retryable_status(_resp(503, {"Retry-After": "999"}))
        return "ok"

    await retry_async(fn, call_type=CallType.LLM, sleep=sleeps, rng=random.Random(0))
    assert sleeps.calls[0] == 12.0
    assert sleeps.calls[1] == 60.0
