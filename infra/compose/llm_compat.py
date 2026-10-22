"""Phase 0 LLM compatibility shim (stands in for the step-4 LLM gateway, blueprint §2.4).

Sits between the engine and an OpenAI-compatible provider and:
  - holds the real API key, so the engine only ever sees a placeholder;
  - drops request fields the provider rejects (Anthropic's OpenAI-compatible endpoint
    refuses `temperature` on current models and `response_format: json_object`; the
    engine already strips the Markdown JSON fence the model adds instead);
  - logs one JSON line per call with token usage;
  - refuses calls once a total token budget is spent.

Stdlib only. Configuration, all via environment:
  COMPAT_LISTEN        host:port to bind (default 0.0.0.0:4000)
  COMPAT_UPSTREAM      provider base URL, e.g. https://api.anthropic.com/v1/ (required)
  COMPAT_API_KEY       provider key (required)
  COMPAT_DROP          comma-separated top-level fields to drop (default: temperature)
  COMPAT_TOKEN_BUDGET  total prompt+completion tokens allowed (default 3000000)
  COMPAT_LOG           JSONL log path (default: stdout)
Outbound requests honour HTTPS_PROXY and SSL_CERT_FILE.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ["COMPAT_UPSTREAM"].rstrip("/")
if not UPSTREAM.startswith("https://"):
    sys.exit("COMPAT_UPSTREAM must be an https:// URL")
API_KEY = os.environ["COMPAT_API_KEY"]
DROP = [f.strip() for f in os.environ.get("COMPAT_DROP", "temperature").split(",") if f.strip()]
BUDGET = int(os.environ.get("COMPAT_TOKEN_BUDGET", "3000000"))
LOG_PATH = os.environ.get("COMPAT_LOG", "")
_log_file = open(LOG_PATH, "a", buffering=1) if LOG_PATH else sys.stdout  # noqa: SIM115
_lock = threading.Lock()
_used = {"prompt": 0, "completion": 0, "calls": 0}


def log(**fields: object) -> None:
    fields["ts"] = round(time.time(), 3)
    with _lock:
        _log_file.write(json.dumps(fields) + "\n")
        _log_file.flush()


def rewrite(body: dict[str, object]) -> list[str]:
    dropped = [f for f in DROP if body.pop(f, None) is not None]
    rf = body.get("response_format")
    if isinstance(rf, dict) and rf.get("type") == "json_object":
        del body["response_format"]
        dropped.append("response_format")
    return dropped


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path in ("/health", "/healthz"):
            self._reply(200, {"status": "ok", "used": _used, "budget": BUDGET})
        else:
            self._reply(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self.path.startswith("/v1/"):
            self._reply(404, {"error": {"message": "not found"}})
            return
        with _lock:
            spent = _used["prompt"] + _used["completion"]
        if spent >= BUDGET:
            log(event="refused", reason="budget", used=spent, budget=BUDGET)
            self._reply(429, {"error": {"message": "phase 0 token budget spent"}})
            return
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self._reply(400, {"error": {"message": "body is not JSON"}})
            return
        dropped = rewrite(body)
        stream = bool(body.get("stream"))
        req = urllib.request.Request(  # noqa: S310 (https enforced at startup)
            UPSTREAM + self.path[len("/v1") :],
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        )
        t0 = time.monotonic()
        try:
            resp = urllib.request.urlopen(req, timeout=600)  # noqa: S310
            status = resp.status
        except urllib.error.HTTPError as e:
            resp, status = e, e.code
        except Exception as e:
            log(event="error", path=self.path, error=str(e)[:300])
            self._reply(502, {"error": {"message": f"upstream unreachable: {e}"[:300]}})
            return
        self.send_response(status)
        self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
        if stream:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while chunk := resp.read(8192):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.write(b"0\r\n\r\n")
            usage: dict[str, int] = {}
            error = None
        else:
            data = resp.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            try:
                parsed = json.loads(data)
            except ValueError:
                parsed = {}
            usage = parsed.get("usage") or {}
            error = (parsed.get("error") or {}).get("message") if status >= 400 else None
        p, c = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        with _lock:
            _used["prompt"] += p
            _used["completion"] += c
            _used["calls"] += 1
            totals = dict(_used)
        log(
            event="call",
            path=self.path,
            model=body.get("model"),
            stream=stream,
            dropped=dropped,
            status=status,
            prompt_tokens=p,
            completion_tokens=c,
            seconds=round(time.monotonic() - t0, 2),
            error=error[:300] if error else None,
            totals=totals,
        )


def main() -> None:
    host, _, port = os.environ.get("COMPAT_LISTEN", "0.0.0.0:4000").rpartition(":")
    log(event="start", upstream=UPSTREAM, drop=DROP, budget=BUDGET)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
