# tests/unit/test_detectors.py
# New detector unit tests written after the artifex-review-fixes session.
# Tests: exception_rate, hedging_density (L1 distinct-span), step_overrun (M1 non-sticky +
# steps-since-progress), burn_acceleration (M2 output-only), context_pressure,
# semantic_velocity, de-escalation recent-K (M3), H2 cap-vs-sticky, H1 cache-isolation,
# detector ordering.
#
# Constraint: this file MUST NOT edit any existing test file or bench script.
# All tests are free-tier (fastembed local, no network).
from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from agent_router.config import RouterConfig
from agent_router.routing.dynamic_lm import DynamicRouteLM
from agent_router.scoring import (
    ScoringEngine,
    SemanticVelocityDetector,
    _count_distinct_hedging_spans,
    detect_burn_acceleration,
    detect_context_pressure,
    detect_exception_rate,
    detect_hedging,
    detect_step_overrun,
)
from agent_router.state import SessionState, ToolEvent, TurnRecord, _SESSION_REGISTRY


# ---------------------------------------------------------------------------
# Helpers (local — DO NOT import from test_scoring.py to keep files independent)
# ---------------------------------------------------------------------------


def _turn(
    call_id: str,
    idx: int,
    output_text: str,
    input_tok: int = 10,
    output_tok: int = 5,
    exception: Exception | None = None,
) -> TurnRecord:
    return TurnRecord(
        call_id=call_id,
        step_idx=idx,
        signature_name="S",
        tool_name=None,
        tool_args=None,
        input_token_count=input_tok,
        output_token_count=output_tok,
        output_text=output_text,
        cache_hit=False,
        exception=exception,
    )


def _session(
    turns: tuple[TurnRecord, ...] = (),
    tool_events: tuple[ToolEvent, ...] = (),
    escalation_count: int = 0,
    current_threshold: float = 0.11593,
    session_id: str = "test",
) -> SessionState:
    s = SessionState(
        session_id=session_id,
        window=deque(turns, maxlen=50),
        current_threshold=current_threshold,
        escalation_count=escalation_count,
        cost_log=[],
    )
    s.tool_events.extend(tool_events)
    return s


def _evt(call_id: str, tool: str, obs: str) -> ToolEvent:
    return ToolEvent(call_id=call_id, tool_name=tool, tool_args={"q": "x"}, observation=obs)


# ---------------------------------------------------------------------------
# 1. Exception rate detector
# ---------------------------------------------------------------------------


def test_exception_rate_detector() -> None:
    """At-or-above threshold fraction of failed steps triggers the detector."""
    cfg = RouterConfig(exception_rate_threshold=0.5, exception_rate_window=4)
    # 2 out of 4 recent steps failed -> rate 0.5 >= threshold 0.5
    turns = (
        _turn("a", 0, "ok"),
        _turn("b", 1, "ok"),
        _turn("c", 2, "err", exception=RuntimeError("boom")),
        _turn("d", 3, "err", exception=RuntimeError("boom")),
    )
    r = detect_exception_rate(_session(turns), cfg)
    assert r.anomaly and r.kind == "exception_rate" and r.detector == "ExceptionRateDetector"

    # 1 out of 4 (below threshold) -> no anomaly
    turns_ok = (
        _turn("a", 0, "ok"),
        _turn("b", 1, "ok"),
        _turn("c", 2, "ok"),
        _turn("d", 3, "err", exception=RuntimeError("boom")),
    )
    r2 = detect_exception_rate(_session(turns_ok), cfg)
    assert not r2.anomaly


# ---------------------------------------------------------------------------
# 2. Hedging density detector (L1 regression: distinct spans, no double-count)
# ---------------------------------------------------------------------------


def test_hedging_density_detector_distinct_spans() -> None:
    """Hedging fires on genuinely uncertain output; L1: distinct spans, no double-count."""
    cfg = RouterConfig(hedging_min_matches=3)

    # Three clearly distinct hedging phrases -> should fire.
    uncertain = "I'm not sure about this. I don't know the value. I'm unable to proceed."
    assert _count_distinct_hedging_spans(uncertain) >= 3
    turns = (_turn("x", 0, uncertain),)
    r = detect_hedging(_session(turns), cfg)
    assert r.anomaly and r.kind == "hedging_density"

    # Normal CoT with "I think" — should NOT fire (I think removed from patterns in L1).
    normal_cot = (
        "I think the best approach here is to analyse each step. "
        "Let me work through the math. The result is 42."
    )
    assert _count_distinct_hedging_spans(normal_cot) < cfg.hedging_min_matches
    turns_ok = (_turn("y", 0, normal_cot),)
    r2 = detect_hedging(_session(turns_ok), cfg)
    assert not r2.anomaly


def test_hedging_no_double_count_overlap() -> None:
    """'I cannot determine' must count as ONE span, not two (L1 fix)."""
    # Both "I cannot" and "I cannot determine" used to each contribute a match;
    # with the L1 fix only one distinct span is counted per overlapping match region.
    text = "I cannot determine the answer."
    count = _count_distinct_hedging_spans(text)
    # "I cannot" is the only remaining pattern that can match here.
    assert count == 1, f"Expected 1 distinct span, got {count}"


# ---------------------------------------------------------------------------
# 3. Step overrun — non-sticky (M1)
# ---------------------------------------------------------------------------


def test_step_overrun_non_sticky() -> None:
    """detect_step_overrun must NOT set escalate_session=True (M1 non-sticky fix)."""
    cfg = RouterConfig(step_overrun_factor=2.0)
    # 5 steps with a simple 1-sentence input (complexity=1) -> ratio=5 > 2.0 -> anomaly
    turns = tuple(_turn(f"t{i}", i, "output") for i in range(5))
    r = detect_step_overrun(_session(turns), cfg, "What is the answer?")
    assert r.anomaly and r.kind == "step_overrun"
    assert not r.escalate_session, "step_overrun must be non-sticky after M1 fix"


# ---------------------------------------------------------------------------
# 4. Step overrun — steps-since-last-progress (M1)
# ---------------------------------------------------------------------------


def test_step_overrun_steps_since_progress() -> None:
    """Overrun is measured from last observation change, not absolute window length."""
    # A simple 1-sentence task with complexity 1 and factor 2.0 needs ratio >= 2.
    # If there was a progress event at index 3, steps-since-progress = len(window) - 3.
    cfg = RouterConfig(step_overrun_factor=2.0)
    input_text = "Solve x."

    # 5 turns, observation changed at event index 3 -> steps-since = 5 - 3 = 2
    # complexity = 1, ratio = 2/1 = 2.0 >= 2.0 -> anomaly
    turns = tuple(_turn(f"t{i}", i, "out") for i in range(5))
    events = (
        _evt("e0", "s", "obs_A"),
        _evt("e1", "s", "obs_A"),
        _evt("e2", "s", "obs_A"),
        _evt("e3", "s", "obs_B"),  # progress at index 3
        _evt("e4", "s", "obs_B"),
    )
    r_with_progress = detect_step_overrun(_session(turns, events), cfg, input_text)
    assert r_with_progress.anomaly, "Should still fire: 2 steps since progress, factor 2.0"

    # Progress at index 4 -> steps-since = 5 - 4 = 1, ratio = 1 < 2.0 -> NO anomaly
    events_recent = (
        _evt("e0", "s", "obs_A"),
        _evt("e1", "s", "obs_A"),
        _evt("e2", "s", "obs_A"),
        _evt("e3", "s", "obs_A"),
        _evt("e4", "s", "obs_B"),  # progress at index 4
    )
    r_recent_progress = detect_step_overrun(_session(turns, events_recent), cfg, input_text)
    assert not r_recent_progress.anomaly, "Recent progress should suppress overrun"


# ---------------------------------------------------------------------------
# 5. Burn acceleration — output tokens only (M2 regression guard)
# ---------------------------------------------------------------------------


def test_burn_acceleration_output_only() -> None:
    """Growing INPUT tokens alone must NOT trip the burn detector (M2 regression)."""
    cfg = RouterConfig(burn_window_min_steps=4, burn_acceleration_factor=2.0)

    # Input grows (simulating ReAct history re-send), output stays flat.
    turns_growing_input = (
        _turn("a", 0, "ans", input_tok=100, output_tok=5),
        _turn("b", 1, "ans", input_tok=200, output_tok=5),
        _turn("c", 2, "ans", input_tok=400, output_tok=5),
        _turn("d", 3, "ans", input_tok=800, output_tok=5),
    )
    r = detect_burn_acceleration(_session(turns_growing_input), cfg)
    assert not r.anomaly, "Growing input alone must not trigger burn acceleration"

    # Output genuinely accelerates -> should fire.
    turns_growing_output = (
        _turn("a", 0, "ans", input_tok=100, output_tok=5),
        _turn("b", 1, "ans", input_tok=100, output_tok=5),
        _turn("c", 2, "ans", input_tok=100, output_tok=50),
        _turn("d", 3, "ans", input_tok=100, output_tok=50),
    )
    r2 = detect_burn_acceleration(_session(turns_growing_output), cfg)
    assert r2.anomaly and r2.kind == "token_burn_acceleration"


# ---------------------------------------------------------------------------
# 6. Context pressure detector
# ---------------------------------------------------------------------------


def test_context_pressure_detector() -> None:
    """Context pressure fires when input tokens approach the context window limit."""
    cfg = RouterConfig(context_window_limit=1000, context_pressure_threshold=0.75)

    # Latest step at 80% of limit -> should fire.
    turns_full = (_turn("a", 0, "out", input_tok=800),)
    r = detect_context_pressure(_session(turns_full), cfg)
    assert r.anomaly and r.kind == "context_pressure"

    # Latest step at 50% -> no anomaly.
    turns_ok = (_turn("a", 0, "out", input_tok=500),)
    r2 = detect_context_pressure(_session(turns_ok), cfg)
    assert not r2.anomaly


# ---------------------------------------------------------------------------
# 7. Semantic velocity detector — basic loop detection
# ---------------------------------------------------------------------------


def test_semantic_velocity_detector() -> None:
    """Window-wide low velocity + unchanged observation triggers low_semantic_velocity."""
    cfg = RouterConfig(
        velocity_min_window=3,
        semantic_velocity_threshold=0.15,
        de_escalation_enabled=False,  # suppress de-escalation so only anomaly path fires
    )
    # Use a repeated astronomy text so embeddings are identical -> distance 0.0 -> low velocity.
    text = "The derivative is computed by applying the chain rule to the inner function."
    turns = (
        _turn("a", 0, text),
        _turn("b", 1, text),
        _turn("c", 2, text),
    )
    # Same observation at every event -> no change -> anomaly expected.
    events = (
        _evt("e0", "s", "same observation"),
        _evt("e1", "s", "same observation"),
    )
    cache: dict[str, Any] = {}
    det = SemanticVelocityDetector(cache)
    r = det.detect(_session(turns, events), cfg)
    assert r.anomaly and r.kind == "low_semantic_velocity"


# ---------------------------------------------------------------------------
# 8. De-escalation uses only most-recent K pairs (M3)
# ---------------------------------------------------------------------------


def test_de_escalation_recent_k() -> None:
    """De-escalation fires based on the most-recent K pairs, not the whole-window average."""
    # Setup: 5-step window where the first 2 pairs have near-zero velocity (identical text)
    # but the last 2 pairs have high velocity (very different text). With the M3 fix and
    # de_escalation_recent_k=2, the recent high-velocity pairs should trigger de-escalation.
    # Without M3 (whole-window average), the early low-velocity pairs drag it down.

    cfg = RouterConfig(
        velocity_min_window=3,
        semantic_velocity_threshold=0.15,
        de_escalation_enabled=True,
        de_escalation_velocity_multiplier=2.0,   # need velocity >= 0.15*2 = 0.30
        de_escalation_recent_k=2,                 # only look at last 2 pairs
    )
    stuck_text = "The derivative is computed by applying the chain rule to the inner function."
    diverse_text_a = "Photosynthesis converts light energy into chemical energy stored in glucose."
    diverse_text_b = "The quick brown fox jumps over the lazy dog near the riverbank."
    diverse_text_c = "Machine learning models require large datasets and careful hyperparameter tuning."

    turns = (
        _turn("a", 0, stuck_text),
        _turn("b", 1, stuck_text),  # pair 0: near-zero velocity
        _turn("c", 2, stuck_text),  # pair 1: near-zero velocity
        _turn("d", 3, diverse_text_a),   # pair 2: high velocity
        _turn("e", 4, diverse_text_b),   # pair 3: high velocity
    )

    # Simulate that escalation already happened (escalation_count=1).
    sess = _session(turns, escalation_count=1)
    cache: dict[str, Any] = {}
    det = SemanticVelocityDetector(cache)
    r = det.detect(sess, cfg)

    # With de_escalation_recent_k=2, only the last 2 pairs determine recovery.
    # Expected: de_escalate=True because recent pairs have high velocity.
    assert r.de_escalate, (
        f"Expected de_escalate=True with recent-K=2 and high recent velocity; got {r}"
    )


# ---------------------------------------------------------------------------
# 9. H2: cap-vs-sticky — sticky flag respects max_escalations_per_session
# ---------------------------------------------------------------------------


def test_h2_cap_vs_sticky() -> None:
    """Once max_escalations_per_session is reached, sticky escalation must NOT force 0.0."""
    sid = "h2-test"
    cfg = RouterConfig(max_escalations_per_session=2)
    sess = SessionState(
        session_id=sid,
        window=deque(maxlen=10),
        current_threshold=cfg.default_threshold,
        escalation_count=2,  # already at cap
        cost_log=[],
    )
    sess.escalate_session = True  # sticky flag is set

    _SESSION_REGISTRY[sid] = sess
    try:
        lm = DynamicRouteLM(
            session_id=sid,
            default_threshold=cfg.default_threshold,
            max_escalations_per_session=cfg.max_escalations_per_session,
        )
        threshold = lm._current_threshold()
        # Cap reached -> should NOT be 0.0; sticky flag should be cleared.
        assert threshold != 0.0, (
            f"H2 failure: threshold={threshold} should not be 0.0 when cap is reached"
        )
        assert not sess.escalate_session, "H2 failure: escalate_session should be cleared after cap"
    finally:
        _SESSION_REGISTRY.pop(sid, None)


# ---------------------------------------------------------------------------
# 10. H1: cache isolation — same call_id, different text → different embeddings
# ---------------------------------------------------------------------------


def test_h1_cache_isolation() -> None:
    """Two ScoringEngine instances with the same call_ids but different text get different embeddings."""
    text_a = "Photosynthesis converts light energy into chemical energy stored in glucose."
    text_b = "Machine learning models require large datasets and careful hyperparameter tuning."

    # Engine 1: text_a for call_id "x"
    eng1 = ScoringEngine(RouterConfig())
    from agent_router.scoring import _get_embedding

    emb_a = _get_embedding("x", text_a, eng1._embedding_cache)

    # Engine 2: text_b for call_id "x" — must not pick up eng1's cached result.
    eng2 = ScoringEngine(RouterConfig())
    emb_b = _get_embedding("x", text_b, eng2._embedding_cache)

    import numpy as np

    sim = float(np.dot(emb_a, emb_b) / (np.linalg.norm(emb_a) * np.linalg.norm(emb_b)))
    # Astronomy vs ML text should have measurably different embeddings (similarity < 0.99).
    assert sim < 0.99, (
        f"H1 regression: embeddings for call_id='x' with different texts are almost identical "
        f"(similarity={sim:.4f}) — cache is leaking across engines"
    )


# ---------------------------------------------------------------------------
# 11. Detector ordering — structural fires first, expensive detectors skipped
# ---------------------------------------------------------------------------


def test_detector_ordering() -> None:
    """Structural constraint pre-empts all other detectors — no embedder loaded."""
    import agent_router.scoring as sc
    from typing import Any

    calls: list[str] = []

    original_encode = sc._Embedder.encode.__func__  # type: ignore[attr-defined]

    def _tracking_encode(cls: Any, text: str) -> Any:
        calls.append(text)
        return original_encode(cls, text)

    # Monkeypatch to track if the embedder is called.
    sc._Embedder.encode = classmethod(_tracking_encode)  # type: ignore[assignment]
    try:
        # Window that would trigger semantic_velocity / loop_velocity if reached.
        identical = "The derivative is computed by applying the chain rule."
        turns = (
            _turn("a", 0, identical),
            _turn("b", 1, identical),
            _turn("c", 2, identical),
        )
        events = (
            _evt("e0", "s", "same"),
            _evt("e1", "s", "same"),
        )
        r = ScoringEngine(RouterConfig()).score(
            _session(turns, events),
            input_text='Return output as valid JSON with "$schema" field',  # structural trigger
        )
        assert r.kind == "structural_constraint", f"Expected structural to fire first, got {r.kind}"
        assert not calls, "Embedder must not be called when structural fires first"
    finally:
        sc._Embedder.encode = classmethod(original_encode)  # type: ignore[assignment]
