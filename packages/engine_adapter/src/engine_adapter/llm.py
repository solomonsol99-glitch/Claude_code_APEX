"""OpenAI-compatible chat client that talks only to the LLM gateway (§3.5, §4.7)."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

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

Message = dict[str, str]


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient(Protocol):
    async def chat_json(
        self,
        messages: Sequence[Message],
        *,
        route: str,
        temperature: float = 0.7,
        seed: int | None = None,
    ) -> ChatResult: ...


class LLMRequestError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GatewayLLMClient:
    """Calls ``POST {base_url}/chat/completions`` with a per-run virtual key."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeouts: Timeouts | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        breaker: CircuitBreaker | None = None,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self._client = make_async_client(
            base_url=base_url.rstrip("/"),
            timeouts=timeouts or Timeouts(connect=5, read=120, write=30, pool=5),
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )
        self._breaker = breaker
        self._policy = retry_policy
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat_json(
        self,
        messages: Sequence[Message],
        *,
        route: str,
        temperature: float = 0.7,
        seed: int | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": route,
            "messages": list(messages),
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if seed is not None:
            body["seed"] = seed

        async def once() -> httpx.Response:
            resp = await self._client.post("/chat/completions", json=body)
            return raise_for_retryable_status(resp)

        async def guarded() -> httpx.Response:
            if self._breaker is None:
                return await once()
            return await self._breaker.call(once)

        kwargs: dict[str, Any] = {"call_type": CallType.LLM, "policy": self._policy}
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        resp = await retry_async(guarded, **kwargs)
        if resp.status_code >= 400:
            raise LLMRequestError(f"gateway returned {resp.status_code}", status=resp.status_code)
        try:
            data = resp.json()
            choice = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError("malformed gateway response") from exc
        usage = {k: int(v) for k, v in (data.get("usage") or {}).items() if isinstance(v, int)}
        return ChatResult(content=choice or "", model=str(data.get("model", route)), usage=usage)


class FakeLLMClient:
    """Test double. ``responder`` maps (messages, route) to a JSON-serializable reply."""

    def __init__(
        self,
        responder: Callable[[Sequence[Message], str], Any] | None = None,
        *,
        raw: Sequence[str] | None = None,
    ) -> None:
        self.responder = responder
        self.raw = list(raw) if raw is not None else None
        self.calls: list[tuple[list[Message], str]] = []

    async def chat_json(
        self,
        messages: Sequence[Message],
        *,
        route: str,
        temperature: float = 0.7,
        seed: int | None = None,
    ) -> ChatResult:
        self.calls.append((list(messages), route))
        if self.raw is not None:
            content = self.raw.pop(0) if self.raw else "{}"
        elif self.responder is not None:
            reply = self.responder(messages, route)
            content = reply if isinstance(reply, str) else json.dumps(reply)
        else:
            content = "{}"
        return ChatResult(content=content, model=route, usage={"total_tokens": 100})
