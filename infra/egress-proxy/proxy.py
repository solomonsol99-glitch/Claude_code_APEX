"""Deny-all egress proxy for engine containers (blueprint §3.7, ADR 0001 A4/A5).

HTTPS CONNECT only, to hosts on EGRESS_ALLOW (comma-separated, exact names, port 443).
Everything else gets 403: plain-HTTP requests, other ports, IP literals, unknown hosts.
Every decision is appended to EGRESS_LOG as one JSON line, so a run leaves evidence of
which hosts the engine tried to reach (A4: all LLM traffic via LLM_BASE_URL; A5: Zep hosts).

Optional UPSTREAM_PROXY (host:port) chains allowed tunnels through another CONNECT proxy,
for sandboxes where direct egress is blocked. TLS is never terminated here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time

LISTEN_PORT = int(os.environ.get("EGRESS_PORT", "3128"))
ALLOW = {h.strip().lower() for h in os.environ.get("EGRESS_ALLOW", "").split(",") if h.strip()}
LOG_PATH = os.environ.get("EGRESS_LOG", "/tmp/egress.jsonl")  # noqa: S108 - tmpfs in the container
UPSTREAM = os.environ.get("UPSTREAM_PROXY", "").strip()
MAX_HEADER = 16 * 1024
FORBIDDEN = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def log(**event: object) -> None:
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = json.dumps(event, sort_keys=True)
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def decide(method: str, target: str) -> tuple[bool, str, str, int]:
    """Return (allowed, reason, host, port) for one request line."""
    if method != "CONNECT":
        return False, "not_connect", target[:200], 0
    host, _, port_s = target.rpartition(":")
    host = host.strip("[]").lower()
    if not host or not port_s.isdigit():
        return False, "bad_target", target[:200], 0
    port = int(port_s)
    if port != 443:
        return False, "port_not_allowed", host, port
    if host not in ALLOW:
        return False, "host_not_allowed", host, port
    return True, "allowed", host, port


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> int:
    total = 0
    try:
        while chunk := await reader.read(65536):
            total += len(chunk)
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()
    return total


async def open_tunnel(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if not UPSTREAM:
        return await asyncio.open_connection(host, port)
    up_host, _, up_port = UPSTREAM.rpartition(":")
    reader, writer = await asyncio.open_connection(up_host, int(up_port))
    writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    status = head.split(b" ", 2)[1] if b" " in head else b""
    if status != b"200":
        writer.close()
        raise ConnectionError(f"upstream proxy answered {status.decode(errors='replace')}")
    return reader, writer


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    peer = client_w.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), timeout=30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
        client_w.close()
        return
    request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = request_line.split()
    method, target = (parts[0].upper(), parts[1]) if len(parts) >= 2 else ("", "")
    allowed, reason, host, port = decide(method, target)
    if not allowed:
        log(decision="deny", reason=reason, method=method, host=host, port=port, peer=str(peer))
        client_w.write(FORBIDDEN)
        await client_w.drain()
        client_w.close()
        return
    started = time.monotonic()
    try:
        up_r, up_w = await asyncio.wait_for(open_tunnel(host, port), timeout=30)
    except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
        log(decision="error", reason=type(exc).__name__, host=host, port=port, peer=str(peer))
        client_w.write(BAD_GATEWAY)
        await client_w.drain()
        client_w.close()
        return
    client_w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_w.drain()
    sent, received = await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))
    log(
        decision="allow",
        host=host,
        port=port,
        peer=str(peer),
        bytes_out=sent,
        bytes_in=received,
        seconds=round(time.monotonic() - started, 2),
    )


async def main() -> None:
    host = "0.0.0.0"  # noqa: S104 - listens inside its own container
    server = await asyncio.start_server(handle, host, LISTEN_PORT, limit=MAX_HEADER)
    log(decision="start", allow=sorted(ALLOW), upstream=bool(UPSTREAM), port=LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
