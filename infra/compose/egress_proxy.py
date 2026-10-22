"""Deny-all HTTPS egress proxy for the engine zone (blueprint §2.4.4, §3.7).

Only CONNECT to an allowlisted host on port 443 is relayed; everything else is refused.
Every attempt is logged as one JSON line, so a run's log shows exactly which hosts the
engine tried to reach (assumption A5).

Stdlib only. Configuration, all via environment:
  EGRESS_LISTEN       host:port to bind (default 0.0.0.0:3128)
  EGRESS_ALLOW        comma-separated exact hostnames to allow (required)
  EGRESS_LOG          JSONL log path (default: stdout)
  EGRESS_UPSTREAM     optional parent proxy (http://host:port) to tunnel through
  EGRESS_UPSTREAM_BYPASS  comma-separated hosts that skip the parent proxy
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from urllib.parse import urlsplit

ALLOW = {h.strip().lower() for h in os.environ.get("EGRESS_ALLOW", "").split(",") if h.strip()}
BYPASS = {
    h.strip().lower() for h in os.environ.get("EGRESS_UPSTREAM_BYPASS", "").split(",") if h.strip()
}
UPSTREAM = os.environ.get("EGRESS_UPSTREAM", "")
LOG_PATH = os.environ.get("EGRESS_LOG", "")
_log_file = open(LOG_PATH, "a", buffering=1) if LOG_PATH else sys.stdout  # noqa: SIM115


def log(**fields: object) -> None:
    fields["ts"] = round(time.time(), 3)
    _log_file.write(json.dumps(fields) + "\n")
    _log_file.flush()


async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter, counter: list[int]) -> None:
    try:
        while chunk := await src.read(65536):
            counter[0] += len(chunk)
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(Exception):
            dst.close()


async def open_upstream(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if not UPSTREAM or host in BYPASS:
        return await asyncio.wait_for(asyncio.open_connection(host, port), 15)
    up = urlsplit(UPSTREAM)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(up.hostname, up.port or 3128), 15
    )
    writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    status = head.split(b" ", 2)[1] if b" " in head else b"?"
    if status != b"200":
        writer.close()
        raise ConnectionError(f"upstream proxy answered {status.decode(errors='replace')}")
    return reader, writer


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    client = peer[0] if peer else "?"
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except Exception:
        writer.close()
        return
    line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    parts = line.split()
    method = parts[0] if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    if method != "CONNECT":
        log(event="deny", reason="not_connect", method=method, target=target[:200], client=client)
        writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
        writer.close()
        return
    host, _, port_s = target.rpartition(":")
    host = host.strip("[]").lower()
    port = int(port_s) if port_s.isdigit() else 0
    if host not in ALLOW or port != 443:
        log(event="deny", reason="not_allowlisted", host=host, port=port, client=client)
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        writer.close()
        return
    try:
        up_r, up_w = await open_upstream(host, port)
    except Exception as e:
        log(event="error", host=host, port=port, client=client, error=str(e)[:200])
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        writer.close()
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    log(event="open", host=host, port=port, client=client)
    sent, received, t0 = [0], [0], time.monotonic()
    await asyncio.gather(pipe(reader, up_w, sent), pipe(up_r, writer, received))
    log(
        event="close",
        host=host,
        port=port,
        client=client,
        bytes_out=sent[0],
        bytes_in=received[0],
        seconds=round(time.monotonic() - t0, 2),
    )


async def main() -> None:
    if not ALLOW:
        sys.exit("EGRESS_ALLOW is empty; refusing to start an allow-nothing proxy by accident")
    host, _, port = os.environ.get("EGRESS_LISTEN", "0.0.0.0:3128").rpartition(":")
    server = await asyncio.start_server(handle, host, int(port))
    log(event="start", listen=f"{host}:{port}", allow=sorted(ALLOW), upstream=bool(UPSTREAM))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
