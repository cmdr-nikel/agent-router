# tests/unit/test_routing.py
# Block 3 — DynamicRouteLM. Tier-A (free, no network): the real backend call is monkeypatched
# to a recorder, so we verify the routing DECISION (model string per threshold) and the causal
# chain loop-detected -> threshold 0.0 -> strong routing, without any API key or RouteLLM server.
from __future__ import annotations

from collections import deque
from typing import Any

import dspy

from agent_router.config import RouterConfig
from agent_router.routing.dynamic_lm import DynamicRouteLM
from agent_router.scoring import ScoringEngine
from agent_router.state import (
    SessionState,
    ToolEvent,
    TurnRecord,
    _SESSION_REGISTRY,
)


class _FakeResult:
    usage = {"prompt_tokens": 7, "completion_tokens": 3}


def _register(session_id: str, threshold: float) -> SessionState:
    s = SessionState(
        session_id=session_id,
        window=deque(maxlen=50),
        current_threshold=threshold,
        escalation_count=0,
        cost_log=[],
    )
    _SESSION_REGISTRY[session_id] = s
    return s


def _patch_backend(monkeypatch: Any, sink: dict[str, Any]) -> None:
    def fake_forward(self: Any, prompt: Any = None, messages: Any = None, **kw: Any) -> Any:
        sink["model"] = self.model
        sink["messages"] = messages
        return _FakeResult()

    monkeypatch.setattr(dspy.LM, "forward", fake_forward)


def test_model_string_tracks_threshold(monkeypatch: Any) -> None:
    """Each call composes router-mf-{current_threshold} from the live session."""
    sink: dict[str, Any] = {}
    _patch_backend(monkeypatch, sink)
    sid = "route-track"
    sess = _register(sid, threshold=0.5)
    lm = DynamicRouteLM(session_id=sid, router="mf")

    lm.forward(messages=[{"role": "user", "content": "hi"}])
    assert sink["model"] == "openai/router-mf-0.5"

    # Scorer would set this to 0.0 on escalation:
    sess.current_threshold = 0.0
    lm.forward(messages=[{"role": "user", "content": "hi"}])
    assert sink["model"] == "openai/router-mf-0.0", "threshold change must change the routed model"

    del _SESSION_REGISTRY[sid]


def test_normalize_messages_strips_extra_keys() -> None:
    msgs = [{"role": "user", "content": "x", "dspy_demo": True, "weird": 1}]
    out = DynamicRouteLM._normalize_messages(msgs)
    assert out == [{"role": "user", "content": "x"}], "few-shot/extra keys must be stripped (ROUTE-04)"


def test_cost_logged(monkeypatch: Any) -> None:
    sink: dict[str, Any] = {}
    _patch_backend(monkeypatch, sink)
    sid = "route-cost"
    sess = _register(sid, threshold=0.3)
    lm = DynamicRouteLM(session_id=sid)
    lm.forward(messages=[{"role": "user", "content": "hi"}])
    assert len(sess.cost_log) == 1
    rec = sess.cost_log[0]
    assert rec.input_tokens == 7 and rec.output_tokens == 3 and rec.is_cache_hit is False
    del _SESSION_REGISTRY[sid]


def test_escalation_chain(monkeypatch: Any) -> None:
    """THE Tier-A demo: a detected loop escalates the routing to the strong model.

    1) two consecutive near-identical outputs with an unchanged observation,
    2) ScoringEngine flags loop_velocity and forces current_threshold = 0.0,
    3) the very next DynamicRouteLM call routes via router-mf-0.0 (strong).
    No API key, no RouteLLM server — the backend is a recorder.
    """
    sink: dict[str, Any] = {}
    _patch_backend(monkeypatch, sink)
    cfg = RouterConfig()  # loop_similarity_threshold=0.85, default_threshold=0.11593
    sid = "route-escalate"
    sess = _register(sid, threshold=cfg.default_threshold)
    lm = DynamicRouteLM(session_id=sid, default_threshold=cfg.default_threshold)

    # Before any anomaly: routes at the (weak-leaning) default threshold.
    lm.forward(messages=[{"role": "user", "content": "solve this"}])
    assert sink["model"] == f"openai/router-mf-{cfg.default_threshold}"

    # Simulate a stuck agent: two identical outputs, observation unchanged.
    looping = "I should search for the value to answer the question."
    sess.window.append(_mk_turn("c0", 0, looping))
    sess.window.append(_mk_turn("c1", 1, looping))
    sess.tool_events.append(ToolEvent(call_id="t0", tool_name="search", tool_args={"q": "v"}, observation="no result"))

    result = ScoringEngine(cfg).score_and_apply(sess, input_text="solve this")
    assert result.anomaly and result.kind == "loop_velocity"
    assert sess.current_threshold == 0.0 and sess.escalation_count == 1

    # Next routed call now escalates to the strong model.
    lm.forward(messages=[{"role": "user", "content": "solve this"}])
    assert sink["model"] == "openai/router-mf-0.0", "after escalation the LM must route to the strong model"

    del _SESSION_REGISTRY[sid]


def _mk_turn(call_id: str, idx: int, text: str) -> TurnRecord:
    return TurnRecord(
        call_id=call_id,
        step_idx=idx,
        signature_name="S",
        tool_name=None,
        tool_args=None,
        input_token_count=5,
        output_token_count=5,
        output_text=text,
        cache_hit=False,
        exception=None,
    )
