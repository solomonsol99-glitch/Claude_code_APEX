from collections.abc import Sequence

import pytest
from mre_net import SSRFBlockedError, check_ip, resolve_and_check


def resolver_for(*ips: str):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> Sequence[str]:
        return list(ips)

    return resolve


async def failing_resolver(host: str, port: int) -> Sequence[str]:
    raise OSError("nxdomain")


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_blocked_ips(ip: str) -> None:
    with pytest.raises(SSRFBlockedError):
        check_ip(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip: str) -> None:
    check_ip(ip)


async def test_public_host_resolves() -> None:
    t = await resolve_and_check(
        "https://acme.example/pricing", resolver=resolver_for("93.184.216.34")
    )
    assert t.host == "acme.example"
    assert t.port == 443
    assert t.ips == ("93.184.216.34",)


async def test_dns_rebinding_to_private_is_blocked() -> None:
    with pytest.raises(SSRFBlockedError):
        await resolve_and_check(
            "https://acme.example/", resolver=resolver_for("93.184.216.34", "10.0.0.7")
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://acme.example/",
        "ftp://acme.example/",
        "https://user:pw@acme.example/",
        "https://localhost/",
        "https://metadata.google.internal/",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/",
        "https://acme.example:8443/",
        "https:///nohost",
    ],
)
async def test_bad_urls_blocked(url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        await resolve_and_check(url, resolver=resolver_for("93.184.216.34"))


async def test_http_allowed_when_not_https_only() -> None:
    t = await resolve_and_check(
        "http://acme.example/", https_only=False, resolver=resolver_for("93.184.216.34")
    )
    assert t.port == 80


async def test_public_ip_literal() -> None:
    t = await resolve_and_check("https://93.184.216.34/")
    assert t.ips == ("93.184.216.34",)


async def test_resolution_failure_and_empty() -> None:
    with pytest.raises(SSRFBlockedError):
        await resolve_and_check("https://acme.example/", resolver=failing_resolver)
    with pytest.raises(SSRFBlockedError):
        await resolve_and_check("https://acme.example/", resolver=resolver_for())


async def test_invalid_port() -> None:
    with pytest.raises(SSRFBlockedError):
        await resolve_and_check("https://acme.example:99999/", resolver=resolver_for("1.1.1.1"))
