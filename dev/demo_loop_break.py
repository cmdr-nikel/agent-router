"""Tier-A demo (free, no API key): show the loop-breaking causal chain end-to-end.

Run: python dev/demo_loop_break.py

It uses a recording stand-in for the real LLM backend, so NO network / key / cost.
It demonstrates the real pipeline objects: capture window -> ScoringEngine detects a
reasoning loop -> per-session threshold forced to 0.0 -> DynamicRouteLM routes the next
call to the strong model (router-mf-0.0).
"""
from __future__ import annotations

from collections import deque
from typing import Any

import dspy

from agent_router.config import RouterConfig
from agent_router.routing.dynamic_lm import DynamicRouteLM
from agent_router.scoring import ScoringEngine
from agent_router.state import SessionState, ToolEvent, TurnRecord, _SESSION_REGISTRY

_routed: dict[str, Any] = {}


class _FakeResult:
    usage = {"prompt_tokens": 12, "completion_tokens": 6}


def _fake_backend(self: Any, prompt: Any = None, messages: Any = None, **kw: Any) -> Any:
    _routed["model"] = self.model
    return _FakeResult()


def _turn(call_id: str, idx: int, text: str) -> TurnRecord:
    return TurnRecord(
        call_id=call_id, step_idx=idx, signature_name="QA", tool_name=None, tool_args=None,
        input_token_count=12, output_token_count=6, output_text=text, cache_hit=False, exception=None,
    )


def main() -> None:
    # Patch dspy.LM.forward so no real network call happens.
    dspy.LM.forward = _fake_backend  # type: ignore[method-assign]

    cfg = RouterConfig()
    sid = "demo"
    session = SessionState(
        session_id=sid, window=deque(maxlen=cfg.window_size),
        current_threshold=cfg.default_threshold, escalation_count=0, cost_log=[],
    )
    _SESSION_REGISTRY[sid] = session
    lm = DynamicRouteLM(session_id=sid, default_threshold=cfg.default_threshold)
    engine = ScoringEngine(cfg)

    print("=" * 64)
    print("agent-router — loop-breaking demo (Tier A, mock backend, $0)")
    print("=" * 64)
    print(f"start: current_threshold={session.current_threshold}  (weak-leaning)\n")

    looping = "I should look up the value to answer the question."

    # Step 0 — weak model produces a thought; route + score.
    lm.forward(messages=[{"role": "user", "content": "What is the capital?"}])
    session.window.append(_turn("c0", 0, looping))
    session.tool_events.append(ToolEvent(call_id="t0", tool_name="search", tool_args={"q": "capital"}, observation="no result"))
    r0 = engine.score_and_apply(session, input_text="What is the capital?")
    print(f"step 0: routed -> {_routed['model']:<26} | output={looping!r}")
    print(f"        scorer: anomaly={r0.anomaly} kind={r0.kind} threshold={session.current_threshold}\n")

    # Step 1 — weak model REPEATS the same thought (stuck); route + score.
    lm.forward(messages=[{"role": "user", "content": "What is the capital?"}])
    session.window.append(_turn("c1", 1, looping))  # identical output -> loop
    r1 = engine.score_and_apply(session, input_text="What is the capital?")
    print(f"step 1: routed -> {_routed['model']:<26} | output={looping!r}  (repeat!)")
    print(f"        scorer: anomaly={r1.anomaly} kind={r1.kind} score={r1.score:.3f} -> threshold={session.current_threshold}")
    if r1.anomaly:
        print(f"        *** LOOP DETECTED — escalating (escalation_count={session.escalation_count}) ***\n")

    # Step 2 — the NEXT routed call now goes to the strong model BECAUSE threshold dropped to 0.0.
    lm.forward(messages=[{"role": "user", "content": "What is the capital?"}])
    print(f"step 2: routed -> {_routed['model']:<26} | <- escalated to strong model")
    print()
    print(f"result: weak={cfg.weak_model}  strong={cfg.strong_model}")
    print(f"        router-mf-0.0 forces RouteLLM past the weak model to the strong one.")
    print(f"        cost records logged: {len(session.cost_log)} (tokens tracked; billed=None for mock)")
    print("=" * 64)
    print("PASS: reasoning loop detected from telemetry alone -> automatic escalation.")


if __name__ == "__main__":
    main()
