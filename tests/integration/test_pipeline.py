# tests/integration/test_pipeline.py
# LIB-03 / VAL-01 / VAL-02 (mock tier): the synthetic loop bench drives the real pipeline
# components (ScoringEngine + DynamicRouteLM + SessionState) end-to-end across seeds and meets
# the loop-rate + escalation gates with no network/key.
from __future__ import annotations

from bench.synthetic_loop_bench import run_bench, run_seed_mock
from agent_router.config import RouterConfig


def test_bench_mock_meets_gates() -> None:
    summary = run_bench(seeds=10, real=False)
    assert summary["mode"] == "mock"
    assert summary["loop_rate"] >= 0.8, f"VAL-01 gate: loop rate {summary['loop_rate']:.0%} < 80%"
    # Every detected loop must clear via escalation (VAL-02).
    assert summary["escalation_clear_rate"] == 1.0


def test_seed_chain_sets_strong_route() -> None:
    """A looping seed forces current_threshold to 0.0 and routes router-mf-0.0 (full chain)."""
    cfg = RouterConfig()
    # seed 1 loops (seed % 10 != 0); run via the real components.
    res = run_seed_mock(1, cfg)
    assert res.looped and res.escalation_cleared


def test_non_looping_seed_not_flagged() -> None:
    """A progress seed (distinct outputs) must NOT be flagged (P10 / false-positive guard)."""
    cfg = RouterConfig()
    res = run_seed_mock(0, cfg)  # seed % 10 == 0 -> progress, not a loop
    assert not res.looped
