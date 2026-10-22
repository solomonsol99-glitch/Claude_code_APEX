# ruff: noqa: S104, S108, S310  (container-only service: binds all ifaces, logs to tmpfs, fixed https upstream)
"""Deny-all egress proxy for the Phase 0 live run (blueprint §2.4.4, §3.7).

Accepts HTTPS CONNECT to hosts in EGRESS_ALLOW only, port 443 only. Everything else
(other hosts, plain HTTP, other ports) is refused. Every attempt is logged as JSON lines
to EGRESS_LOG so the run proves which hosts the engine actually needs (A4, A5).

Optional UPSTREAM_PROXY (http://host:port) chains allowed tunnels through another proxy
(e.g. a corporate or sandbox proxy). Stdlib only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from urllib.parse import urlsplit

ALLOW = {h.strip().lower() for h in os.environ.get("EGRESS_ALLOW", "").split(",") if h.strip()}
LOG = os.environ.get("EGRESS_LOG", "/tmp/egress.jsonl")
UPSTREAM = os.environ.get("UPSTREAM_PROXY", "")
PORT = int(os.environ.get("EGRESS_PORT", "3128"))


def log(**rec: object) -> None:
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = json.dumps(rec, sort_keys=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter, counter: list[int]) -> None:
    try:
        while data := await r.read(65536):
            counter[0] += len(data)
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        w.close()


async def open_upstream(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if not UPSTREAM:
        return await asyncio.open_connection(host, port)
    u = urlsplit(UPSTREAM)
    r, w = await asyncio.open_connection(u.hostname, u.port or 3128)
    w.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    await w.drain()
    head = await r.readuntil(b"\r\n\r\n")
    status = head.split(b" ", 2)[1] if b" " in head else b"?"
    if status != b"200":
        w.close()
        raise ConnectionError(f"upstream proxy answered {status.decode(errors='replace')}")
    return r, w


async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    peer = cw.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
        cw.close()
        return
    line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    parts = line.split()
    method, target = (parts[0], parts[1]) if len(parts) >= 2 else ("?", "?")
    host, _, port_s = target.rpartition(":") if method == "CONNECT" else (target, "", "")
    host = host.strip("[]").lower()
    port = int(port_s) if port_s.isdigit() else 0
    if method != "CONNECT" or port != 443 or host not in ALLOW:
        log(event="deny", method=method, target=target[:200], client=str(peer))
        cw.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await cw.drain()
        cw.close()
        return
    try:
        ur, uw = await asyncio.wait_for(open_upstream(host, port), 30)
    except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
        log(event="error", host=host, error=str(exc)[:200])
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await cw.drain()
        cw.close()
        return
    cw.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await cw.drain()
    up, down = [0], [0]
    t0 = time.monotonic()
    await asyncio.gather(pipe(cr, uw, up), pipe(ur, cw, down))
    log(event="allow", host=host, bytes_up=up[0], bytes_down=down[0],
        seconds=round(time.monotonic() - t0, 2))


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    print(f"egress proxy on :{PORT}, allow={sorted(ALLOW)}, upstream={UPSTREAM or 'direct'}",
          file=sys.stderr, flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
