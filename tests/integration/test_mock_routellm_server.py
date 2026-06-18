# tests/integration/test_mock_routellm_server.py
# LIB-03 integration: exercise DynamicRouteLM's REAL HTTP path (dspy.LM -> litellm -> OpenAI-
# compatible endpoint) against a tiny in-process mock RouteLLM server. No API key, no real
# RouteLLM (no torch). Proves the per-request model string `router-mf-{threshold}` actually
# reaches the server and that escalation (threshold -> 0.0) changes the routed model.
from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from agent_router.routing.dynamic_lm import DynamicRouteLM
from agent_router.state import SessionState, _SESSION_REGISTRY

_received_models: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a: Any) -> None:  # silence
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _received_models.append(str(body.get("model", "")))
        payload = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "router-mf-?"),
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "mock answer"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def mock_server() -> Any:
    _received_models.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.shutdown()


def test_threshold_reaches_server_and_escalates(mock_server: str) -> None:
    sid = "integ-route"
    session = SessionState(
        session_id=sid, window=deque(maxlen=10), current_threshold=0.5, escalation_count=0, cost_log=[]
    )
    _SESSION_REGISTRY[sid] = session
    try:
        lm = DynamicRouteLM(session_id=sid, router="mf", api_base=mock_server, api_key="sk-test")

        out = lm.forward(messages=[{"role": "user", "content": "hi"}])
        assert _received_models[-1] == "router-mf-0.5", "server must receive the threshold-derived model"
        assert getattr(out, "usage", None), "round-trip response should carry usage"

        # Escalate: scoring would force this to 0.0.
        session.current_threshold = 0.0
        lm.forward(messages=[{"role": "user", "content": "hi"}])
        assert _received_models[-1] == "router-mf-0.0", "escalation must route to the strong model"

        # Cost was logged for both calls (ROUTE-06).
        assert len(session.cost_log) == 2
    finally:
        _SESSION_REGISTRY.pop(sid, None)
