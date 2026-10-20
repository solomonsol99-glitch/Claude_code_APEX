import json

import httpx
import pytest
from engine_adapter.llm import GatewayLLMClient, LLMRequestError
from mre_net import BreakerConfig, CircuitBreaker, CircuitOpenError


async def no_sleep(_: float) -> None:
    return None


def _client(handler, **kw) -> GatewayLLMClient:  # type: ignore[no-untyped-def]
    return GatewayLLMClient(
        base_url="http://llm-gateway:4000/v1/",
        api_key="vk-test",
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
        **kw,
    )


def _completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "sim-primary",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "note": "x"},
        },
    )


async def test_sends_openai_compatible_request() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return _completion('{"a": 1}')

    c = _client(handler)
    out = await c.chat_json([{"role": "user", "content": "hi"}], route="sim-primary", seed=7)
    await c.aclose()
    assert out.content == '{"a": 1}'
    assert out.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert seen["url"] == "http://llm-gateway:4000/v1/chat/completions"
    assert seen["auth"] == "Bearer vk-test"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "sim-primary"
    assert body["seed"] == 7
    assert body["response_format"] == {"type": "json_object"}


async def test_retries_5xx_then_succeeds() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(503) if n < 3 else _completion("{}")

    assert (await _client(handler).chat_json([], route="r")).content == "{}"
    assert n == 3


async def test_client_errors_and_malformed() -> None:
    with pytest.raises(LLMRequestError) as exc:
        await _client(lambda r: httpx.Response(400, json={})).chat_json([], route="r")
    assert exc.value.status == 400
    with pytest.raises(LLMRequestError, match="malformed"):
        await _client(lambda r: httpx.Response(200, json={"x": 1})).chat_json([], route="r")


async def test_breaker_opens() -> None:
    br = CircuitBreaker("llm:r", config=BreakerConfig(consecutive_failures=2))
    c = _client(lambda r: httpx.Response(500), breaker=br)
    for _ in range(2):
        with pytest.raises((httpx.HTTPStatusError, CircuitOpenError)):
            await c.chat_json([], route="r")
    with pytest.raises(CircuitOpenError):
        await c.chat_json([], route="r")
