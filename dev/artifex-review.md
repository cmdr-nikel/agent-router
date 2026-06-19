# Code Review — `artifex` branch (trajectory-level routing signals)

**Reviewed:** 2026-06-19 · **Branch:** `origin/artifex` (1 commit `dcfd62b` off `main` `00090b6`, additive)
**Status:** NOT merge-ready — 2 HIGH blockers + 3 MEDIUM; 3 existing tests fail; no tests for new code.
**Next session:** fix in the order below, then write the test suite (see "Test plan"). Tests were
deliberately deferred to a separate session.

## What the branch adds (context)

Six new detectors + two mechanisms, all additive in 4 files (`scoring.py` +498/−97, `config.py` +41,
`state.py` +5, `routing/dynamic_lm.py` +8). Tests/bench untouched.

New detectors (in `scoring.py`): `exception_rate`, `hedging_density`, `step_overrun`,
`token_burn_acceleration`, `low_semantic_velocity` (`SemanticVelocityDetector`, window-wide),
`context_pressure`. Kept: `structural_constraint`, `tool_flapping`, `loop_velocity`.

New mechanisms:
- **Sticky escalation** — `SessionState.escalate_session: bool`; `DynamicRouteLM._current_threshold`
  returns `0.0` for ALL remaining calls once set (vs our per-call escalation).
- **De-escalation** — `ScoringResult.de_escalate`; `score_and_apply` resets `current_threshold` to
  `default_threshold` + clears `escalate_session` when semantic velocity recovers. New capability
  beyond v1 scope (we only escalated).
- `ScoringResult` gained `escalate_session` + `de_escalate` fields.
- Detector order (cheap→expensive): structural → exception_rate → hedging → step_overrun →
  burn_acceleration → context_pressure → flapping → semantic_velocity → loop_velocity.

Reproduce locally:
```bash
git worktree add /tmp/artifex-check origin/artifex
cd /tmp/artifex-check && mypy --strict agent_router/ && python -m pytest tests/ -q
# then: cd <repo> && git worktree remove /tmp/artifex-check --force
```
Result: `mypy --strict` clean; `pytest` = 3 failed, 36 passed.

---

## 🔴 HIGH (blockers)

### H1 — Global `_EMBEDDING_CACHE` keyed by `call_id` only → cross-session collisions + leak
`scoring.py` (`_EMBEDDING_CACHE`, `_get_embedding`).
- Module-global dict keyed solely by `call_id`. Assumes `call_id → text` is globally stable forever.
  If two different texts ever share a `call_id` (across sessions, or repeated test fixture ids), the
  **wrong cached embedding** is returned.
- **This is the root cause of the 3 failing tests** (not stale tests): `test_config_threshold`
  caches `"a" → "...astronomy..."`; later `test_loop` reuses `call_id="a"` for different text and
  gets the astronomy vector → cosine low → `loop_velocity` doesn't fire → `test_loop`,
  `test_bench_mock_meets_gates`, `test_seed_chain_sets_strong_route` fail.
- Also a real **memory leak**: entries are never evicted on session end; the "bounded by
  window×sessions" comment is wrong. Grows unboundedly on long-running processes.
- **Fix:** key by `(session_id, call_id)` AND/OR make the cache per-`ScoringEngine` instance (as the
  original `LoopVelocityProfiler._cache` was — GC'd with the tracker). Evict on session `__exit__`.

### H2 — Sticky `escalate_session` bypasses the escalation cap → cost safety valve defeated
`state.py:escalate_session`, `routing/dynamic_lm.py:_current_threshold`, `scoring.py:score_and_apply`.
- Once any sticky detector (`step_overrun` / `burn_acceleration` / `context_pressure` /
  `low_semantic_velocity`) sets `escalate_session=True`, `DynamicRouteLM` forces `threshold=0.0` for
  every remaining call **regardless of `max_escalations_per_session`**. The cap (ROUTE-05 / Pitfall
  P17 — the runaway-cost guard) no longer bounds strong-model calls.
- Conflicts with the project's cost-safety stance. "Stay on strong for the rest of the session" =
  unbounded session cost by construction.
- **Fix:** subordinate sticky escalation to a budget (e.g. cap total strong calls, or a sticky-TTL),
  or have `_current_threshold` still honor the cap, or log+enforce a session strong-call ceiling.
  Decide the intended semantics with the colleague first.

---

## 🟡 MEDIUM

### M1 — `step_overrun` is monotonic + sticky → permanently blocks de-escalation
`scoring.py:detect_step_overrun`. `actual = len(window)` only grows; once `ratio >= step_overrun_factor`
it returns an anomaly on EVERY step (detector #4) and short-circuits before `SemanticVelocityDetector`
(#8) where `de_escalate` lives. So de-escalation can essentially never fire after step_overrun trips
(and it already set sticky). **Fix:** make step_overrun non-sticky, or measure steps-since-last-progress
rather than absolute `len(window)`, or evaluate de-escalation earlier/independently of detector order.

### M2 — `burn_acceleration` confounded by ReAct's growing context
`scoring.py:detect_burn_acceleration`. Sums `input + output` tokens. In ReAct the **input grows every
step** (history is re-sent), so tokens/step rise for any long-but-healthy agent → false positives.
**Fix:** use `output_token_count` only (or normalize input by step index).

### M3 — De-escalation is sluggish / rarely reachable
`scoring.py:SemanticVelocityDetector`. `avg_velocity` is averaged over the WHOLE window incl. the early
stuck steps, which drag the mean down; the recovery threshold (`semantic_velocity_threshold ×
de_escalation_velocity_multiplier` = 0.15×2 = 0.3) is hard to reach until stuck steps roll off the
deque. Combined with M1, de-escalation is mostly dead. **Fix:** judge recovery on the most-recent K
pairs, not the full window.

---

## 🟢 LOW / tuning

- **L1** `_HEDGING_PATTERNS` overlap → double counting ("I cannot determine" matches both
  `I cannot determine` and `I cannot`), so the effective `hedging_min_matches` is lower than 3. Also
  `I think` / `possibly` / `it seems` are common in normal CoT → false positives. Tighten patterns /
  raise threshold / require distinct spans.
- **L2** `_estimate_complexity` is crude — a one-sentence but genuinely hard task (e.g. "sum of all
  primes < 1000") yields a low estimate → false `step_overrun`.
- **L3** `assert prev.output_text is not None` in production code — stripped under `python -O`. Harmless
  here (type-narrowing, guaranteed by the comprehension) but prefer an explicit guard.
- **L4** `_observation_changed` only compares the last two observations for a window-wide velocity
  decision — weak gate if the loop is mid-window. (Inherited from the original design.)

---

## Positives (keep)
- `mypy --strict` clean; all new thresholds in `RouterConfig` (env-overridable) — matches house style.
- De-escalation is a genuinely valuable new idea (cost savings after a block clears) — worth keeping
  once M1/M3 are fixed.
- `exception_rate` + `hedging_density` realize the "quality signal" direction (positive result-quality
  signals, not just stuck-process signals).
- Detectors are individually small and readable; structural/flapping/loop preserved.

---

## Test plan (NEXT session)

Order: land fixes H1 → H2 → M1 → M2 → M3 (decide semantics with colleague for H2/M1), then:

1. **Fix the 3 red tests** — after H1 they should largely recover; decide `loop_velocity` vs
   `low_semantic_velocity` ownership for the 2-identical-output case (keep both? merge? order?).
   Update `run_seed_mock` in `bench/synthetic_loop_bench.py` accordingly.
2. **New unit tests (one per detector)** in `tests/unit/test_scoring.py`:
   - exception_rate: ≥threshold fraction of failed steps fires; below doesn't.
   - hedging_density: ≥N distinct hedges fires; normal CoT doesn't (guard L1).
   - step_overrun: high steps/complexity fires + (decided) sticky behavior; low doesn't.
   - burn_acceleration: rising output tokens fires; flat doesn't; NOT tripped by growing input alone (M2 regression).
   - context_pressure: near-limit input fires.
   - semantic_velocity: window-wide low velocity + unchanged obs fires; changed obs doesn't (P10).
3. **De-escalation tests:** escalate → velocity recovers → threshold resets + `escalate_session`
   cleared; assert it actually reaches the de_escalate path (M1 regression).
4. **Cap-vs-sticky tests (H2):** assert the agreed cost-bound holds even with sticky escalation.
5. **Cache isolation tests (H1):** two sessions reusing the same `call_id` with different text get
   different embeddings; cache does not grow unbounded across sessions.
6. **Detector-ordering tests:** structural pre-empts all; cheap detectors pre-empt embedding ones.
7. Re-run `mypy --strict` + full `pytest` green before merge.
