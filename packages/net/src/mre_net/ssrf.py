"""SSRF guard (§2.3, §3.7): resolve once, refuse internal address ranges.

Callers must connect to the returned IPs (or re-check after every redirect) so that a
second DNS lookup cannot rebind the name to an internal address.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local incl. cloud metadata 169.254.169.254
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "::1/128",
        "::ffff:0:0/96",  # IPv4-mapped; the embedded IPv4 is checked separately
        "64:ff9b::/96",
        "100::/64",
        "2001:db8::/32",
        "fc00::/7",  # unique local, includes fd00::/8
        "fe80::/10",
        "ff00::/8",
    )
)

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


class SSRFBlockedError(ValueError):
    """The URL or one of its resolved addresses is not allowed."""


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    scheme: str
    host: str
    port: int
    ips: tuple[str, ...]


def check_ip(ip: str | IPAddress) -> None:
    addr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        check_ip(addr.ipv4_mapped)
    for net in BLOCKED_NETWORKS:
        if addr.version == net.version and addr in net:
            raise SSRFBlockedError(f"address {addr} is in blocked range {net}")


async def default_resolver(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


async def resolve_and_check(
    url: str,
    *,
    https_only: bool = True,
    allowed_ports: frozenset[int] = frozenset({80, 443}),
    resolver: Resolver = default_resolver,
) -> ResolvedTarget:
    """Validate scheme, host and every resolved address. Raises SSRFBlockedError."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ({"https"} if https_only else {"http", "https"}):
        raise SSRFBlockedError(f"scheme {scheme!r} not allowed")
    if parts.username or parts.password:
        raise SSRFBlockedError("credentials in URL not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise SSRFBlockedError("URL has no host")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise SSRFBlockedError("invalid port") from exc
    if port not in allowed_ports:
        raise SSRFBlockedError(f"port {port} not allowed")
    if host == "localhost" or host.endswith((".localhost", ".internal", ".local")):
        raise SSRFBlockedError(f"host {host!r} not allowed")

    try:
        literal: IPAddress | None = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        check_ip(literal)
        return ResolvedTarget(scheme, host, port, (str(literal),))

    try:
        ips = tuple(await resolver(host, port))
    except OSError as exc:
        raise SSRFBlockedError(f"could not resolve {host!r}") from exc
    if not ips:
        raise SSRFBlockedError(f"no addresses for {host!r}")
    for ip in ips:
        check_ip(ip)
    return ResolvedTarget(scheme, host, port, ips)
