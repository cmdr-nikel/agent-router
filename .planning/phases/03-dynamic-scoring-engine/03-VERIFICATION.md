---
status: passed
phase: 3-dynamic-scoring-engine
verifier: inline (main loop — subagent verifier unavailable due to account spend limit)
date: 2026-06-18
score: 5/5
---

# Phase 3 — Verification

**Note:** Verified inline by the main loop. The GSD subagent pipeline (researcher / planner /
plan-checker / executor / verifier / code-reviewer) was unavailable mid-phase because the account
hit its monthly spend limit. Research, planning, implementation, tests, and review were therefore
done directly. All checks below were RUN, not asserted from claims.

## Goal
After each ReAct step the scoring engine analyzes the session window and flags reasoning loops,
tool-call flapping, and structural-constraint demands — every threshold from config, escalation cap
in place — before any real model calls.

## Success criteria (5/5)

1. **Loop velocity + P10 gate (SCORE-02)** — PASS. `test_loop` (identical outputs + unchanged obs →
   `loop_velocity`, score ≥ threshold) and `test_loop_false_positive` (identical outputs but CHANGED
   observation → no flag). Real fastembed bge-small embeddings, numpy cosine.
2. **Tool flapping (SCORE-03)** — PASS. `test_flapping` (same tool ≥3× unchanged obs → `tool_flapping`);
   `test_flapping_progress` (changing obs → no flag).
3. **Structural constraint, no LM, runs first (SCORE-04)** — PASS. `test_structural` + `test_structural_fires`
   (embedder monkeypatched to raise — proves the override short-circuits before the profiler).
4. **Config-driven thresholds (SCORE-04)** — PASS. `test_config_threshold`: same window, different
   `loop_similarity_threshold` → different verdict, zero code change.
5. **Escalation cap + logging (SCORE-05)** — PASS. `test_cap`: forces `current_threshold=0.0` until
   `max_escalations_per_session`, then stops; each escalation logged with detector + score; cap-reached
   logged. `test_no_llm_judge`: scoring.py references no LM client.

## Requirements traceability
SCORE-01 (sliding window — reuses Phase-1 `SessionState.window`), SCORE-02, SCORE-03, SCORE-04,
SCORE-05 — all covered. Gap D-05 (tool/observation capture) resolved via `on_tool_start/on_tool_end`
→ `ToolEvent` / `SessionState.tool_events` (`test_tool_capture`, real ReAct).

## Gates
- `mypy --strict agent_router/` → clean (8 files)
- `python -m pytest tests/unit -q` → 31 passed
- `import agent_router` does NOT load fastembed/routellm (light-import preserved; fastembed lazy)

## Known caveats (documented, Phase-5 calibration)
- Loop-detector live alignment: observation lags output by one step in the live path (tool runs after
  on_lm_end); the P10 gate uses the two most-recent available observations as a proxy. Exact on
  fixtures; live skew is a calibration item, not a correctness bug (commented in scoring.py).
- `loop_similarity_threshold=0.85` and `flapping_min_repeats=3` are hypotheses to calibrate on the
  synthetic bench (Phase 5).
- Self-review (no reviewer subagent): findings folded in before commit — frozen ToolEvent excludes
  tool_args from hash (mirrors TurnRecord WR-01); embedder is a warmed singleton; per-session embedding
  cache keyed by call_id (no cross-session bleed).
