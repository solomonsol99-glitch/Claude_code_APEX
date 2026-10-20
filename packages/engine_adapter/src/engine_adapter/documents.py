"""Seed-document loading. Adapters receive S3 URIs; the worker injects a real loader."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

DocumentLoader = Callable[[str], Awaitable[bytes]]


def mapping_loader(docs: Mapping[str, bytes | str]) -> DocumentLoader:
    """Loader backed by an in-memory mapping (tests, MockEngine fixtures)."""

    async def load(uri: str) -> bytes:
        try:
            value = docs[uri]
        except KeyError:
            raise FileNotFoundError(uri) from None
        return value.encode() if isinstance(value, str) else value

    return load


async def missing_loader(uri: str) -> bytes:
    raise RuntimeError(f"no document loader configured (cannot load {uri})")
