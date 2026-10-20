"""httpx client factory. Every client gets explicit timeouts; there is no default."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

USER_AGENT = "CoresetMRE/1.0"


@dataclass(frozen=True, slots=True)
class Timeouts:
    """Explicit per-phase timeouts in seconds. All four are required."""

    connect: float
    read: float
    write: float
    pool: float

    def __post_init__(self) -> None:
        for name in ("connect", "read", "write", "pool"):
            value = getattr(self, name)
            if not value or value <= 0:
                raise ValueError(f"timeout {name!r} must be a positive number")

    @classmethod
    def uniform(cls, seconds: float) -> Timeouts:
        return cls(connect=seconds, read=seconds, write=seconds, pool=seconds)

    def to_httpx(self) -> httpx.Timeout:
        return httpx.Timeout(connect=self.connect, read=self.read, write=self.write, pool=self.pool)


def make_async_client(
    *,
    timeouts: Timeouts,
    base_url: str = "",
    headers: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    proxy: str | None = None,
    follow_redirects: bool = False,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an AsyncClient. ``timeouts`` is mandatory; redirects are off unless asked."""
    if not isinstance(timeouts, Timeouts):
        raise TypeError("timeouts must be a mre_net.Timeouts instance")
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    return httpx.AsyncClient(
        base_url=base_url,
        headers=merged,
        timeout=timeouts.to_httpx(),
        transport=transport,
        proxy=proxy,
        follow_redirects=follow_redirects,
        **kwargs,
    )
