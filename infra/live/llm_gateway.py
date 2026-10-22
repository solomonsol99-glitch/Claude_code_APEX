# ruff: noqa: S104, S108, S310  (container-only service: binds all ifaces, logs to tmpfs, fixed https upstream)
"""Minimal LLM gateway for the Phase 0 live run (stands in for the §2.4 gateway).

- Holds the real upstream key; the engine only ever sees GATEWAY_ENGINE_KEY.
- Forces every request onto UPSTREAM_MODEL (one approved route).
- Hard budget: refuses with 429 once spend reaches BUDGET_USD (priced from usage), or
  once MAX_REQUESTS is reached.
- Compatibility shim for OpenAI-compatible upstreams that reject parameters MiroFish
  sends (finding from Phase 0): drops sampling params (temperature, top_p, ...), and
  turns response_format={"type":"json_object"} into a JSON-only instruction plus
  code-fence stripping on the reply. Each shim use is counted in the usage log.
- Logs one JSON line per request to GATEWAY_LOG (tokens, cost, latency, shims, status).
  Prompts and completions are NOT logged.

Non-streaming chat completions only; a streaming request is rejected (400) and logged.
Stdlib only. Outbound traffic honours HTTPS_PROXY (the deny-all egress proxy).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ["UPSTREAM_LLM_BASE_URL"].rstrip("/")
UPSTREAM_KEY = os.environ["UPSTREAM_LLM_API_KEY"]
UPSTREAM_MODEL = os.environ["UPSTREAM_MODEL"]
ENGINE_KEY = os.environ.get("GATEWAY_ENGINE_KEY", "")
BUDGET_USD = float(os.environ.get("BUDGET_USD", "5"))
MAX_REQUESTS = int(os.environ.get("MAX_REQUESTS", "5000"))
PRICE_IN = float(os.environ.get("PRICE_IN_PER_MTOK", "0"))
PRICE_OUT = float(os.environ.get("PRICE_OUT_PER_MTOK", "0"))
LOG = os.environ.get("GATEWAY_LOG", "/tmp/gateway.jsonl")
PORT = int(os.environ.get("GATEWAY_PORT", "4000"))
DROP = (
    "temperature",
    "top_p",
    "top_k",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "logit_bias",
    "logprobs",
    "top_logprobs",
    "n",
)
JSON_ONLY = "Respond with one valid JSON object only. No prose, no markdown, no code fences."
FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)

_lock = threading.Lock()
_state = {"spent_usd": 0.0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0}


def _log(rec: dict[str, object]) -> None:
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _lock, open(LOG, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def _shim(body: dict[str, object]) -> list[str]:
    used = []
    for k in DROP:
        if k in body:
            body.pop(k)
            used.append(f"drop:{k}")
    rf = body.get("response_format")
    if isinstance(rf, dict) and rf.get("type") == "json_object":
        body.pop("response_format")
        msgs = body.get("messages")
        if isinstance(msgs, list):
            first = msgs[0] if msgs else None
            if (
                isinstance(first, dict)
                and first.get("role") == "system"
                and isinstance(first.get("content"), str)
            ):
                first["content"] = f"{first['content']}\n\n{JSON_ONLY}"
            else:
                msgs.insert(0, {"role": "system", "content": JSON_ONLY})
        used.append("json_object")
    if body.get("model") != UPSTREAM_MODEL:
        used.append(f"model:{body.get('model')}")
        body["model"] = UPSTREAM_MODEL
    return used


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_: object) -> None:  # quiet default access log
        pass

    def _send(self, code: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code: int, msg: str, kind: str) -> None:
        self._send(code, {"error": {"message": msg, "type": kind, "code": kind}})

    def do_GET(self) -> None:
        if self.path in ("/health", "/v1/health"):
            with _lock:
                self._send(200, {"status": "ok", **_state, "budget_usd": BUDGET_USD})
        else:
            self._err(404, "not found", "not_found")

    def do_POST(self) -> None:
        t0 = time.monotonic()
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if ENGINE_KEY and self.headers.get("Authorization") != f"Bearer {ENGINE_KEY}":
            self._err(401, "bad gateway key", "authentication_error")
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            _log({"event": "reject", "path": self.path, "reason": "unsupported path"})
            self._err(404, "only /v1/chat/completions is routed", "not_found")
            return
        try:
            body = json.loads(raw)
        except ValueError:
            self._err(400, "invalid JSON", "invalid_request_error")
            return
        if body.get("stream"):
            _log({"event": "reject", "reason": "stream"})
            self._err(400, "streaming is not supported by this gateway", "invalid_request_error")
            return
        with _lock:
            over = _state["spent_usd"] >= BUDGET_USD or _state["requests"] >= MAX_REQUESTS
            if not over:
                _state["requests"] += 1
        if over:
            _log({"event": "budget_block", **_state})
            self._err(429, "run budget exhausted", "budget_exceeded")
            return
        shims = _shim(body)
        req = urllib.request.Request(
            f"{UPSTREAM}/chat/completions",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {UPSTREAM_KEY}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                status, out = resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                out = json.loads(e.read())
            except ValueError:
                out = {"error": {"message": f"upstream HTTP {e.code}", "type": "upstream"}}
        except OSError as e:
            _log({"event": "upstream_error", "error": str(e)[:200], "shims": shims})
            self._err(502, "upstream unreachable", "upstream_error")
            return
        usage = out.get("usage") or {}
        pin, pout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        cost = pin / 1e6 * PRICE_IN + pout / 1e6 * PRICE_OUT
        if "json_object" in shims:
            for ch in out.get("choices") or []:
                msg = ch.get("message") or {}
                if isinstance(msg.get("content"), str) and (m := FENCE.match(msg["content"])):
                    msg["content"] = m.group(1)
        with _lock:
            _state["spent_usd"] += cost
            _state["prompt_tokens"] += pin
            _state["completion_tokens"] += pout
            spent = _state["spent_usd"]
        rec: dict[str, object] = {
            "event": "call",
            "status": status,
            "prompt_tokens": pin,
            "completion_tokens": pout,
            "cost_usd": round(cost, 6),
            "spent_usd": round(spent, 6),
            "shims": shims,
            "max_tokens": body.get("max_tokens"),
            "seconds": round(time.monotonic() - t0, 2),
            "finish": [c.get("finish_reason") for c in out.get("choices") or []],
        }
        if status >= 400:
            rec["error"] = str((out.get("error") or {}).get("message", ""))[:300]
        _log(rec)
        self._send(status, out)


if __name__ == "__main__":
    print(
        f"llm gateway on :{PORT} -> {UPSTREAM} model={UPSTREAM_MODEL} budget=${BUDGET_USD}",
        flush=True,
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
