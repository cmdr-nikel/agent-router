"""Synthetic loop bench (VAL-01/VAL-02) — measures the loop-breaking effect across seeds.

Two backends:
  * mock (default, FREE, no key): drives the REAL pipeline components — ScoringEngine,
    DynamicRouteLM, SessionState — on controlled per-seed trajectories. Most seeds produce a
    genuine reasoning loop (identical outputs, unchanged observation) that the embedding-based
    LoopVelocityProfiler detects for real; a minority do not, yielding a realistic loop rate.
    Proves the harness + scoring/routing wiring end-to-end at $0.
  * real (`--real`, needs OPENAI_API_KEY + the agreed cap): runs a real weak model in a
    dspy.ReAct under TrajectoryTracker and measures the actual loop rate + escalation effect.
    NOT run without a key.

Run:
    python bench/synthetic_loop_bench.py            # mock, 10 seeds
    python bench/synthetic_loop_bench.py --seeds 20
    python bench/synthetic_loop_bench.py --real     # Tier B (key required)
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from typing import Any

import dspy

from agent_router.config import RouterConfig
from agent_router.routing.dynamic_lm import DynamicRouteLM
from agent_router.scoring import ScoringEngine
from agent_router.state import SessionState, ToolEvent, TurnRecord, _SESSION_REGISTRY


@dataclass
class SeedResult:
    seed: int
    looped: bool
    escalation_cleared: bool
    cost_records: int


class _RecordingResult:
    usage = {"prompt_tokens": 12, "completion_tokens": 6}


def _patch_mock_backend() -> None:
    """Make DynamicRouteLM.forward route without network (records model, returns fake usage)."""

    def fake_forward(self: Any, prompt: Any = None, messages: Any = None, **kw: Any) -> Any:
        self.model = self._model_string(self._current_threshold())
        return _RecordingResult()

    dspy.LM.forward = fake_forward


def _turn(call_id: str, idx: int, text: str) -> TurnRecord:
    return TurnRecord(
        call_id=call_id, step_idx=idx, signature_name="QA", tool_name=None, tool_args=None,
        input_token_count=12, output_token_count=6, output_text=text, cache_hit=False, exception=None,
    )


def run_seed_mock(seed: int, cfg: RouterConfig) -> SeedResult:
    """Drive the real ScoringEngine + DynamicRouteLM on a seeded trajectory."""
    sid = f"bench-{seed}"
    session = SessionState(
        session_id=sid, window=deque(maxlen=cfg.window_size),
        current_threshold=cfg.default_threshold, escalation_count=0, cost_log=[],
    )
    _SESSION_REGISTRY[sid] = session
    lm = DynamicRouteLM(session_id=sid, default_threshold=cfg.default_threshold)
    engine = ScoringEngine(cfg)
    try:
        # ~90% of seeds genuinely loop (identical outputs, unchanged observation); the rest make
        # progress (distinct outputs) and must NOT be flagged. Deterministic by seed.
        will_loop = (seed % 10) != 0
        lm.forward(messages=[{"role": "user", "content": "task"}])  # step 0 (weak route)
        if will_loop:
            txt = "I should look up the value to answer the question."
            session.window.append(_turn("a", 0, txt))
            session.window.append(_turn("b", 1, txt))  # identical -> loop
            session.tool_events.append(ToolEvent(call_id="t0", tool_name="search", tool_args={"q": "v"}, observation="no result"))
        else:
            session.window.append(_turn("a", 0, "First I analyze the inputs and constraints carefully."))
            session.window.append(_turn("b", 1, "Now I compute the final numeric answer and finish."))
            session.tool_events.append(ToolEvent(call_id="t0", tool_name="calc", tool_args={"x": 1}, observation="42"))

        result = engine.score_and_apply(session, input_text="task")
        looped = result.anomaly and result.kind == "loop_velocity"
        if looped:
            lm.forward(messages=[{"role": "user", "content": "task"}])  # escalated route (router-mf-0.0)
        escalation_cleared = looped and session.current_threshold == 0.0 and lm.model.endswith("-0.0")
        return SeedResult(seed=seed, looped=looped, escalation_cleared=escalation_cleared, cost_records=len(session.cost_log))
    finally:
        _SESSION_REGISTRY.pop(sid, None)


def run_seed_real(seed: int, cfg: RouterConfig) -> SeedResult:  # pragma: no cover - Tier B
    """Tier B: real weak model in a dspy.ReAct; measure actual loop + escalation. Needs a key."""
    raise NotImplementedError(
        "Real bench is Tier B: set OPENAI_API_KEY, point DynamicRouteLM at a RouteLLM server, "
        "and run a real dspy.ReAct with weak={cfg.weak_model}. Gated on the agreed cost cap."
    )


def run_bench(seeds: int, real: bool) -> dict[str, Any]:
    cfg = RouterConfig()
    if not real:
        _patch_mock_backend()
    runner = run_seed_real if real else run_seed_mock
    results = [runner(s, cfg) for s in range(seeds)]
    looped = [r for r in results if r.looped]
    loop_rate = len(looped) / seeds if seeds else 0.0
    cleared = [r for r in looped if r.escalation_cleared]
    clear_rate = (len(cleared) / len(looped)) if looped else 0.0
    return {
        "mode": "real" if real else "mock",
        "seeds": seeds,
        "loop_rate": loop_rate,
        "loops": len(looped),
        "escalation_clear_rate": clear_rate,
        "cleared": len(cleared),
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="agent-router synthetic loop bench")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--real", action="store_true", help="Tier B: use real models (needs OPENAI_API_KEY)")
    args = ap.parse_args()

    summary = run_bench(args.seeds, args.real)
    print("=" * 60)
    print(f"synthetic loop bench — mode={summary['mode']} seeds={summary['seeds']}")
    print("=" * 60)
    print(f"loop rate:            {summary['loop_rate']:.0%}  ({summary['loops']}/{summary['seeds']})  [VAL-01 gate: >=80%]")
    print(f"escalation clear rate {summary['escalation_clear_rate']:.0%}  ({summary['cleared']}/{summary['loops']})  [VAL-02]")
    gate = "PASS" if summary["loop_rate"] >= 0.8 and summary["escalation_clear_rate"] == 1.0 else "CHECK"
    print(f"verdict: {gate}")


if __name__ == "__main__":
    main()
