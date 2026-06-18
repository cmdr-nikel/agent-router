# tests/unit/test_scoring.py
# Phase 3 — Dynamic Scoring Engine acceptance tests (SCORE-01..05 + D-05 tool capture).
# Free phase: detectors run on constructed fixtures + a local fastembed embedder. No network/LLM.
from __future__ import annotations

import inspect
import logging
from collections import deque
from typing import Any

import dspy

from agent_router.config import RouterConfig
from agent_router.scoring import ScoringEngine
from agent_router.state import SessionState, ToolEvent, TurnRecord
from tests.conftest import DummyLM, dummy_tool


def _turn(call_id: str, idx: int, output_text: str) -> TurnRecord:
    return TurnRecord(
        call_id=call_id,
        step_idx=idx,
        signature_name="S",
        tool_name=None,
        tool_args=None,
        input_token_count=10,
        output_token_count=5,
        output_text=output_text,
        cache_hit=False,
        exception=None,
    )


def _session(
    turns: tuple[TurnRecord, ...] = (),
    tool_events: tuple[ToolEvent, ...] = (),
) -> SessionState:
    s = SessionState(
        session_id="t",
        window=deque(turns, maxlen=50),
        current_threshold=0.11593,
        escalation_count=0,
        cost_log=[],
    )
    s.tool_events.extend(tool_events)
    return s


def _evt(call_id: str, tool: str, obs: str) -> ToolEvent:
    return ToolEvent(call_id=call_id, tool_name=tool, tool_args={"q": "x"}, observation=obs)


# --- SCORE-04: structural override ---------------------------------------------------


def test_structural() -> None:
    eng = ScoringEngine(RouterConfig())
    r = eng.score(_session(), input_text='Return output as a JSON Schema with "type": "object"')
    assert r.anomaly and r.kind == "structural_constraint" and r.detector == "StructuralConstraintScanner"


def test_structural_fires_first(monkeypatch: Any) -> None:
    """Structural override returns before the loop profiler — no embedder load."""
    import agent_router.scoring as sc

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("embedder must NOT load when structural override fires")

    monkeypatch.setattr(sc._Embedder, "encode", staticmethod(_boom))
    # A loopy window that WOULD trigger the profiler, but structural input must short-circuit.
    turns = (_turn("a", 0, "identical"), _turn("b", 1, "identical"))
    events = (_evt("ta", "s", "o"), _evt("tb", "s", "o"))
    r = ScoringEngine(RouterConfig()).score(_session(turns, events), input_text="must be valid XML <root></root>")
    assert r.kind == "structural_constraint"


# --- SCORE-03: tool flapping --------------------------------------------------------


def test_flapping() -> None:
    cfg = RouterConfig()  # flapping_min_repeats=3
    events = tuple(_evt(f"c{i}", "search", "same observation") for i in range(3))
    r = ScoringEngine(cfg).score(_session(tool_events=events))
    assert r.anomaly and r.kind == "tool_flapping"


def test_flapping_progress_no_flag() -> None:
    """Same tool 3x but observations change each time -> progress, not flapping."""
    cfg = RouterConfig()
    events = (_evt("c0", "search", "obs0"), _evt("c1", "search", "obs1"), _evt("c2", "search", "obs2"))
    r = ScoringEngine(cfg).score(_session(tool_events=events))
    assert not r.anomaly


# --- SCORE-02: loop velocity (+ P10 false-positive gate) ----------------------------


def test_loop() -> None:
    cfg = RouterConfig()
    text = "The derivative is computed by applying the chain rule to the inner function."
    turns = (_turn("a", 0, text), _turn("b", 1, text))
    events = (_evt("ta", "search", "obs unchanged"), _evt("tb", "search", "obs unchanged"))
    r = ScoringEngine(cfg).score(_session(turns, events))
    assert r.anomaly and r.kind == "loop_velocity" and r.score >= cfg.loop_similarity_threshold


def test_loop_false_positive() -> None:
    """High output similarity but a CHANGED observation = progress -> no flag (P10)."""
    cfg = RouterConfig()
    text = "Let me look that up to find the value."
    turns = (_turn("a", 0, text), _turn("b", 1, text))
    events = (_evt("ta", "search", "observation A"), _evt("tb", "search", "observation B is different"))
    r = ScoringEngine(cfg).score(_session(turns, events))
    assert not r.anomaly


# --- SCORE-04: config-driven thresholds --------------------------------------------


def test_config_threshold() -> None:
    """Same window, different loop_similarity_threshold -> different verdict, no code change."""
    turns = (_turn("a", 0, "a sentence about astronomy and stars"), _turn("b", 1, "a sentence about cooking and recipes"))
    events = (_evt("ta", "s", "o"), _evt("tb", "s", "o"))  # observation unchanged
    sess_hi = _session(turns, events)
    sess_lo = _session(turns, events)
    assert not ScoringEngine(RouterConfig(loop_similarity_threshold=0.99)).score(sess_hi).anomaly
    assert ScoringEngine(RouterConfig(loop_similarity_threshold=0.05)).score(sess_lo).anomaly


# --- SCORE-05: escalation cap + logging --------------------------------------------


def test_cap(caplog: Any) -> None:
    cfg = RouterConfig(max_escalations_per_session=2, loop_similarity_threshold=0.05)
    text = "looping output"
    turns = (_turn("a", 0, text), _turn("b", 1, text))
    events = (_evt("ta", "s", "o"), _evt("tb", "s", "o"))
    s = _session(turns, events)
    eng = ScoringEngine(cfg)

    with caplog.at_level(logging.INFO, logger="agent_router.scoring"):
        eng.score_and_apply(s)
        assert s.current_threshold == 0.0 and s.escalation_count == 1
        s.current_threshold = 0.5  # router would reset between calls
        eng.score_and_apply(s)
        assert s.escalation_count == 2
        s.current_threshold = 0.5
        eng.score_and_apply(s)  # cap reached
        assert s.escalation_count == 2 and s.current_threshold == 0.5  # NOT forced to 0.0

    messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "detector=LoopVelocityProfiler" in messages and "score=" in messages
    assert "escalation_cap_reached" in messages


# --- SCORE-05: no LLM judge ---------------------------------------------------------


def test_no_llm_judge() -> None:
    """Scoring must never invoke an LLM: the module imports no LM client at all."""
    src = inspect.getsource(__import__("agent_router.scoring", fromlist=["x"]))
    for forbidden in ("import dspy", "dspy.LM", "litellm", "openai", "BaseLM"):
        assert forbidden not in src, f"scoring.py must not reference {forbidden!r} (no LM judge, SCORE-05)"


# --- D-05: tool/observation capture via a real ReAct run ----------------------------


def test_tool_capture(dummy_lm: DummyLM) -> None:
    """A real dspy.ReAct under TrajectoryTracker(config) populates session.tool_events."""
    dspy.configure(lm=dummy_lm)  # conftest dummy_lm: 3 react steps (2 dummy_tool + finish) + extract
    react = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=10)
    with TrajectoryTracker(session_id="tool-cap", config=RouterConfig()) as tr:
        react(question="go")

    events = tr._session.tool_events
    assert events, "no ToolEvents captured — on_tool_end must append to session.tool_events"
    real = [e for e in events if e.tool_name != "finish"]
    assert real, "expected at least one non-finish tool event"
    assert all(e.observation is not None for e in real), "tool observations must be captured"
    assert real[0].tool_name == "dummy_tool"
    assert real[0].tool_args is not None


# Import here (not top-level) so the no_llm_judge source check stays focused on scoring.py.
from agent_router.tracker import TrajectoryTracker  # noqa: E402
