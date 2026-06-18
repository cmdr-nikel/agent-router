# Phase 3 Research — Dynamic Scoring Engine

**Authored:** 2026-06-18 (inline by main loop — subagent research unavailable due to account
monthly spend limit). Verified against installed dspy 3.2.1 source + fastembed 0.8.0.
**Confidence:** HIGH for dspy/tool mechanics (source-read); MEDIUM for detector thresholds
(empirical — calibrate on fixtures).

---

## 1. D-05 RESOLVED — tool/observation capture

`dspy.Tool.__call__` is decorated `@with_callbacks` (verified: `dspy/adapters/types/tool.py:176`).
So ReAct's `self.tools[name](**args)` calls fire `on_tool_start` / `on_tool_end`:

- `on_tool_start(call_id, instance, inputs)` — `instance` is the `dspy.Tool` (has `.name`), `inputs`
  is the args dict.
- `on_tool_end(call_id, outputs, exception)` — `outputs` is the tool's return value = the
  **observation**. `outputs=None` + `exception` set if the tool raised.

ReAct per iteration (verified `dspy/predict/react.py:100-114`): react Predict LM call
(→ `on_lm_end`, creates the step's TurnRecord) THEN `self.tools[next_tool_name](**next_tool_args)`
(→ `on_tool_start/end`, the observation). The trailing `finish` tool also fires callbacks.

**Decision:** capture tools in a PARALLEL structure, do NOT mutate the frozen TurnRecord.
- Add `ToolEvent` frozen dataclass to `state.py`: `call_id, tool_name, tool_args (compare/hash=False),
  observation: str | None, exception: Exception | None`.
- Add `SessionState.tool_events: list[ToolEvent]` (mutable, like `cost_log`; `field(default_factory=list)`).
- `TrajectoryCallback.on_tool_start`: stash `{call_id: (instance.name, dict(inputs))}` in a pending map.
- `TrajectoryCallback.on_tool_end`: pop, append `ToolEvent(observation=str(outputs) if outputs is not
  None else None, exception=exception)` under `session._lock`.
- The scorer aligns `window` (TurnRecords, per react step) with `tool_events` (per tool call) by order;
  it ignores the `finish` tool event.

This is a CONTROLLED cross-phase edit (state.py contract + capture.py) — add tests for ToolEvent and
for tool/observation capture alongside the Phase-3 tests. Phase-1/2 tests must stay green
(ToolEvent + tool_events are additive; existing TurnRecord unchanged).

## 2. ScoringResult contract (new `agent_router/scoring.py`)

```
@dataclass(frozen=True)
ScoringResult:
    anomaly: bool
    kind: str | None        # "structural_constraint" | "tool_flapping" | "loop_velocity" | None
    score: float            # detector-specific confidence/similarity (0.0 when no anomaly)
    detector: str | None    # detector class name that fired
```

## 3. Detector order (SCORE-04 override first)

`ScoringEngine.score(session, config, input_text) -> ScoringResult`:
1. `StructuralConstraintScanner(input_text)` — regex only, no embeddings/LM. If it matches → return
   `kind="structural_constraint"` immediately (override; skips probabilistic detectors).
2. `ToolCallFlappingMonitor(session.tool_events, config)` — counters.
3. `LoopVelocityProfiler(session.window, session.tool_events, config)` — embeddings (most expensive).

## 4. StructuralConstraintScanner (SCORE-04) — regex patterns on the INPUT prompt

No LM, no embeddings. Case-insensitive. Flag if any:
- `\bJSON\s*Schema\b`, `"\$schema"`, `"type"\s*:\s*"(object|array|string|number|boolean)"`
- `<\?xml\b`, `</[A-Za-z][\w:-]*>` (closing XML/HTML tag), `\bvalid\s+XML\b`
- ` ```(json|xml|python|sql|[a-z+]+)\b ` fenced code with a language, `\bmust\s+(compile|be valid)\b`
- `\bexecutable\b.*\b(syntax|code)\b`
Keep the list in one module constant so it's tunable. This is heuristic (P-structural): a few false
positives are acceptable since the action is "route to frontier", which is conservative.

## 5. LoopVelocityProfiler (SCORE-02, P8/P9/P10)

- Embedder: `fastembed.TextEmbedding(model_name="BAAI/bge-small-en-v1.5")`. `.embed(list[str])` →
  generator of `np.float32` vectors, dim 384. LAZY import inside the profiler; clear error if the
  `[embed]` extra is missing. Warm the model ONCE (module-level singleton or cached on the engine),
  not per call (P9: cold start is seconds, warm ~ms).
- Cosine: `float(a @ b / (norm(a)*norm(b)))` via numpy. Profiler caches embeddings by `call_id`.
- **Flag logic (P10 false-positive gate):** compare the last two consecutive TurnRecords' `output_text`
  embeddings. Flag `loop_velocity` ONLY when:
  (a) output cosine ≥ `config.loop_similarity_threshold` (default 0.85), AND
  (b) the corresponding observations did NOT change (compare the two steps' `ToolEvent.observation`:
      exact-equal OR observation cosine ≥ threshold → "unchanged").
  A changed observation = progress → NO flag. Require ≥2 consecutive turns (not a single point).
- `score` = the output cosine similarity.
- Threshold (0.85) is a HYPOTHESIS (P8) — read from config, calibrate on the synthetic bench (Phase 5).

## 6. ToolCallFlappingMonitor (SCORE-03)

Over `session.tool_events` (ignoring `finish`): flag `tool_flapping` when the SAME `tool_name` appears
> 2 times (≥3) within the window with only slightly-varied args AND no change in observation content.
"Slightly varied args": args dicts differ but same keys (or near-identical) — start simple: same
tool_name ≥3 times with observation unchanged across them. `score` = occurrence count / window size.

## 7. Invocation point

Keep capture/scoring boundaries clean. `TrajectoryTracker` constructs a `ScoringEngine` bound to the
session + config; `TrajectoryCallback`, after appending a TurnRecord in `on_lm_end` (and NOT during
extract), calls `engine.score_and_apply(session, config, input_text)` which:
- computes `ScoringResult`,
- on anomaly applies the escalation cap (§8),
- returns the result (also retrievable for tests).
ScoringEngine is independently unit-testable: build a `SessionState` with a fixture window +
tool_events and call `engine.score(...)` directly — no DSPy run needed for most tests.

## 8. Escalation cap + logging (SCORE-05 / P17)

In `score_and_apply`, under `session._lock`:
- if `result.anomaly` and `session.escalation_count < config.max_escalations_per_session`:
  `session.current_threshold = 0.0`; `session.escalation_count += 1`;
  `logging.getLogger("agent_router.scoring").info("escalation", extra={detector, kind, score, session_id, count})`.
- else if `result.anomaly` (cap reached): do NOT set 0.0 (leave current_threshold); log a
  "cap_reached" warning. This is the runaway-cost safety valve.
Testable via `caplog`.

## Validation Architecture

FREE phase — unit tests on constructed `SessionState` fixtures + one tiny real-embedder smoke
(downloads bge-small once). pytest, no network/LLM.

| Test (-k) | Req | Asserts |
|---|---|---|
| `structural` | SCORE-04 | JSON-Schema/XML input → kind=structural_constraint, fires before others, no embedder load |
| `flapping` | SCORE-03 | 3× same tool, obs unchanged → kind=tool_flapping |
| `loop` | SCORE-02 | 2 consecutive high-sim outputs + unchanged obs → kind=loop_velocity |
| `loop_false_positive` | SCORE-02/P10 | high-sim outputs but CHANGED observation → NO flag |
| `config_threshold` | SCORE-04 | lowering loop_similarity_threshold changes the verdict, no code change |
| `cap` | SCORE-05 | after max_escalations, no more forced 0.0; each escalation logged w/ detector+score |
| `no_llm_judge` | SCORE-05 | scoring path makes zero LM calls (assert via a spy / no dspy.LM use) |
| `tool_capture` | D-05 | on_tool_end populates ToolEvent (name/args/observation) in session.tool_events |

Wave 0: ToolEvent + tool_events contract edit (state.py) + capture on_tool_start/end + scoring test
stubs. Then detectors. fastembed already (being) installed; loop tests need it.

## Open / calibration
- loop_similarity_threshold 0.85 and flapping count=3 are hypotheses → calibrate on Phase-5 bench.
- "slightly varied args" similarity metric: start with same-tool+unchanged-observation; refine later.
