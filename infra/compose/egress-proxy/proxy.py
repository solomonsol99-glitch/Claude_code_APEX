"""Deny-all HTTPS egress proxy for engine containers (blueprint §3.7, Phase 0 live run).

Accepts only ``CONNECT host:443`` for hosts in ``EGRESS_ALLOW`` (comma-separated, exact
match). Everything else gets 403. Plain-HTTP proxying is refused. Every attempt is
appended to ``EGRESS_LOG`` as JSONL, which is the evidence for assumptions A4/A5.

If ``UPSTREAM_PROXY`` (``host:port``) is set, allowed tunnels are chained through it
with a CONNECT of their own (for sandboxes whose only way out is another proxy).
Standard library only.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

ALLOW = {h.strip().lower() for h in os.environ.get("EGRESS_ALLOW", "").split(",") if h.strip()}
UPSTREAM = os.environ.get("UPSTREAM_PROXY", "").strip()
LOG_PATH = os.environ.get("EGRESS_LOG", "/tmp/egress.jsonl")  # noqa: S108 (tmpfs)
PORT = int(os.environ.get("EGRESS_PORT", "3128"))


def log(**fields: object) -> None:
    fields["ts"] = time.time()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(fields) + "\n")


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter, counter: list[int]) -> None:
    try:
        while chunk := await r.read(65536):
            counter[0] += len(chunk)
            w.write(chunk)
            await w.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        w.close()


async def read_head(r: asyncio.StreamReader) -> bytes:
    return await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), timeout=30)


async def open_upstream(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if not UPSTREAM:
        return await asyncio.open_connection(host, port)
    uh, _, up = UPSTREAM.rpartition(":")
    ur, uw = await asyncio.open_connection(uh, int(up))
    uw.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    await uw.drain()
    head = await read_head(ur)
    status = head.split(b" ", 2)[1] if head.count(b" ") >= 1 else b"?"
    if status != b"200":
        uw.close()
        raise ConnectionError(f"upstream proxy refused: {status.decode(errors='replace')}")
    return ur, uw


async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    peer = cw.get_extra_info("peername")
    try:
        head = await read_head(cr)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        cw.close()
        return
    line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split()
    method, target = (parts[0].upper(), parts[1]) if len(parts) >= 2 else ("?", "?")
    host, _, port_s = target.rpartition(":")
    host = host.strip("[]").lower()
    allowed = method == "CONNECT" and port_s == "443" and host in ALLOW
    if not allowed:
        log(event="deny", method=method, target=target[:200], peer=str(peer))
        cw.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await cw.drain()
        cw.close()
        return
    started = time.time()
    try:
        ur, uw = await open_upstream(host, 443)
    except (TimeoutError, OSError, ConnectionError) as exc:
        log(event="error", host=host, error=str(exc)[:200])
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await cw.drain()
        cw.close()
        return
    cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await cw.drain()
    up, down = [0], [0]
    await asyncio.gather(pipe(cr, uw, up), pipe(ur, cw, down))
    log(
        event="allow",
        host=host,
        bytes_out=up[0],
        bytes_in=down[0],
        seconds=round(time.time() - started, 2),
    )


async def main() -> None:
    log(event="start", allow=sorted(ALLOW), upstream=bool(UPSTREAM))
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)  # noqa: S104
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
