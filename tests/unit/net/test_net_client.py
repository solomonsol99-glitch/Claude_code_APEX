import httpx
import pytest
from mre_net import Timeouts, make_async_client


def test_timeouts_required_positive() -> None:
    with pytest.raises(ValueError, match="read"):
        Timeouts(connect=1, read=0, write=1, pool=1)


def test_client_requires_timeouts_type() -> None:
    with pytest.raises(TypeError):
        make_async_client(timeouts=5)  # type: ignore[arg-type]


async def test_client_sets_timeouts_and_ua() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json={"ok": True})

    async with make_async_client(
        timeouts=Timeouts.uniform(3), transport=httpx.MockTransport(handler)
    ) as client:
        assert client.timeout.read == 3
        assert not client.follow_redirects
        r = await client.get("https://x.test/")
    assert r.json() == {"ok": True}
    assert seen["ua"].startswith("CoresetMRE/")
