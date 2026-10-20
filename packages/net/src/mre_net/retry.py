"""Retry with exponential backoff and full jitter (§4.3).

Only transient failures are retried: timeouts, connection errors, 429 and 5xx.
``Retry-After`` is honored (capped at ``cap``).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum

import httpx
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class CallType(StrEnum):
    LLM = "llm"
    ZEP = "zep"
    S3 = "s3"
    SCRAPER = "scraper"
    ENGINE = "engine"


DEFAULT_MAX_ATTEMPTS: dict[CallType, int] = {
    CallType.LLM: 4,
    CallType.ZEP: 5,
    CallType.S3: 5,
    CallType.SCRAPER: 2,
    CallType.ENGINE: 3,
}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base: float = 1.0
    cap: float = 60.0

    @classmethod
    def for_call(cls, call_type: CallType, max_attempts: int | None = None) -> RetryPolicy:
        return cls(max_attempts=max_attempts or DEFAULT_MAX_ATTEMPTS[call_type])


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, ConnectionResetError)


def raise_for_retryable_status(response: httpx.Response) -> httpx.Response:
    """Raise HTTPStatusError for 429/5xx so the retry loop sees it; pass others through."""
    if response.status_code in RETRYABLE_STATUS:
        raise httpx.HTTPStatusError(
            f"retryable status {response.status_code}",
            request=response.request,
            response=response,
        )
    return response


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return max(0.0, (when - current).total_seconds())


def backoff_delay(attempt: int, *, base: float, cap: float, rng: random.Random) -> float:
    """Full jitter: uniform(0, min(cap, base * 2**(attempt-1)))."""
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    return rng.uniform(0, ceiling)


def _retry_after_of(exc: BaseException | None) -> float | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return parse_retry_after(exc.response.headers.get("Retry-After"))
    return None


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    *,
    call_type: CallType,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
    retry_on: Callable[[BaseException], bool] = is_retryable,
) -> T:
    """Call ``fn`` until it succeeds, a non-retryable error occurs, or attempts run out."""
    pol = policy or RetryPolicy.for_call(call_type)
    rand = rng or random.Random()  # noqa: S311 - jitter, not crypto

    def wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        delay = backoff_delay(state.attempt_number, base=pol.base, cap=pol.cap, rng=rand)
        hinted = _retry_after_of(exc)
        if hinted is not None:
            delay = max(delay, hinted)
        return min(delay, pol.cap)

    retrying = AsyncRetrying(
        stop=stop_after_attempt(pol.max_attempts),
        wait=wait,
        retry=retry_if_exception(retry_on),
        sleep=sleep,
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            return await fn()
    raise AssertionError("unreachable")  # pragma: no cover
