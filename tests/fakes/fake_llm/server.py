"""Minimal OpenAI-compatible fake for the Phase 0 mock spike (no network, no spend).

Answers /v1/chat/completions and /v1/models. Requests that look like an ontology
request get a small ontology with a `Consumer` entity type; everything else gets a
short JSON object. Every request is appended to a JSONL log so the spike can count
calls and confirm all engine LLM traffic arrived here (assumption A4).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_PATH = os.environ.get("FAKE_LLM_LOG", "/tmp/fake_llm_requests.jsonl")

ONTOLOGY = {
    "entity_types": [
        {
            "name": "Consumer",
            "description": "A simulated consumer persona from the dossier.",
            "attributes": [{"name": "segment", "type": "text", "description": "Segment label"}],
            "examples": ["Dana"],
        },
        {
            "name": "Product",
            "description": "The product being introduced.",
            "attributes": [{"name": "category", "type": "text", "description": "Category"}],
            "examples": ["Trail Pack"],
        },
    ],
    "edge_types": [
        {
            "name": "CONSIDERS",
            "description": "A consumer considers a product.",
            "source_targets": [{"source": "Consumer", "target": "Product"}],
            "attributes": [],
        }
    ],
    "analysis_summary": "Fake ontology for contract testing.",
}


def _answer(body: dict[str, object]) -> str:
    text = json.dumps(body.get("messages", ""), ensure_ascii=False)
    if "entity_types" in text and "edge_types" in text:
        return json.dumps(ONTOLOGY)
    if body.get("response_format"):
        return json.dumps({"ok": True, "note": "fake-llm"})
    return "This is a fake completion used for contract testing."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:  # quiet
        return

    def _send(self, code: int, payload: object) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": "fake-model", "object": "model"}]})
        elif self.path in ("/health", "/healthz"):
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "path": self.path,
                        "model": body.get("model"),
                        "has_response_format": bool(body.get("response_format")),
                    }
                )
                + "\n"
            )
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": "unsupported in fake"}})
            return
        content = _answer(body)
        self._send(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "fake-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )


def main() -> None:
    port = int(os.environ.get("PORT", "4000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
