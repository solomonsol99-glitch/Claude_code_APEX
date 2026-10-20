"""Thin HTTP client for the MiroFish engine container.

MiroFish wraps every reply as ``{"success": bool, "data": ..., "error": str}``. Error
replies can carry a Python ``traceback``; we drop it so it never reaches our logs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from mre_net import (
    CallType,
    CircuitBreaker,
    RetryPolicy,
    Timeouts,
    make_async_client,
    raise_for_retryable_status,
    retry_async,
)

from engine_adapter.errors import (
    EngineResponseInvalidError,
    EngineStageFailedError,
    EngineUnavailableError,
)

MAX_ERROR_CHARS = 300


class MiroFishClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeouts: Timeouts | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = make_async_client(
            base_url=self.base_url,
            timeouts=timeouts or Timeouts(connect=5, read=60, write=60, pool=5),
            transport=transport,
            headers=headers,
        )
        self._policy = retry_policy or RetryPolicy.for_call(CallType.ENGINE)
        self._breaker = breaker
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def raw_get(self, path: str) -> httpx.Response:
        return await self._http.get(path)

    async def call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
        stage: str | None = None,
        retry: bool = True,
    ) -> Any:
        """Send a request and return the envelope's ``data`` (or the whole body)."""

        async def once() -> httpx.Response:
            resp = await self._http.request(
                method, path, json=json, params=params, data=data, files=files
            )
            return raise_for_retryable_status(resp)

        async def guarded() -> httpx.Response:
            return await (self._breaker.call(once) if self._breaker else once())

        kwargs: dict[str, Any] = {"call_type": CallType.ENGINE, "policy": self._policy}
        if not retry:
            kwargs["policy"] = RetryPolicy(max_attempts=1)
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        try:
            resp = await retry_async(guarded, **kwargs)
        except httpx.HTTPStatusError as exc:
            raise EngineUnavailableError(
                f"{method} {path} -> {exc.response.status_code} after retries", stage=stage
            ) from exc
        except httpx.TransportError as exc:
            raise EngineUnavailableError(
                f"{method} {path} transport error: {exc}", stage=stage
            ) from exc
        return _unwrap(resp, method, path, stage)


def _unwrap(resp: httpx.Response, method: str, path: str, stage: str | None) -> Any:
    try:
        body = resp.json()
    except ValueError as exc:
        raise EngineResponseInvalidError(f"{method} {path}: non-JSON reply", stage=stage) from exc
    if not isinstance(body, dict):
        raise EngineResponseInvalidError(f"{method} {path}: unexpected reply shape", stage=stage)
    body.pop("traceback", None)
    if body.get("success") is False or resp.status_code >= 400:
        msg = str(body.get("error") or f"HTTP {resp.status_code}")[:MAX_ERROR_CHARS]
        # Engine-side rejections are our integration problem, not customer input errors.
        err = EngineStageFailedError(f"{method} {path}: {msg}", stage=stage)
        err.status_code = resp.status_code
        raise err
    return body.get("data", body)
