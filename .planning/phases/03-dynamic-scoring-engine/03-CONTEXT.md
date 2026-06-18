# Phase 3: Dynamic Scoring Engine - Context

**Gathered:** 2026-06-18 (autonomous discuss — recommended options chosen per operating agreement)
**Status:** Ready for planning

<domain>
## Phase Boundary

Implement the Dynamic Scoring Engine (Block 2): after each ReAct step, analyze the
`SessionState.window` and flag reasoning loops, tool-call flapping, and structural-constraint
demands — producing a `ScoringResult(anomaly, kind, score, detector)`. All thresholds come from
`RouterConfig`; a per-session escalation cap is enforced and every escalation logged, BEFORE any
real/paid model calls exist. In scope: SCORE-01..SCORE-05. Out of scope: the actual RouteLLM call /
threshold injection wiring (Phase 4) — this phase only computes the decision and updates
`SessionState.current_threshold` / `escalation_count`. FREE phase: detectors run on recorded-window
fixtures + a local fastembed embedder; no LLM API calls.
</domain>

<decisions>
## Implementation Decisions

Recommended options chosen. The phase researcher MUST resolve the tool/observation capture gap
(below) against installed dspy 3.2.1 source before the planner writes tasks.

### Detector ordering (SCORE-04 override path)
- **D-01:** `StructuralConstraintScanner` (pure regex, no embeddings, no LM) runs FIRST as an
  override: if the input demands strict formatting (JSON Schema / valid XML / compilable syntax),
  return `kind="structural_constraint"` immediately and skip the probabilistic detectors. Then
  `ToolCallFlappingMonitor`, then `LoopVelocityProfiler` (most expensive — embeddings).

### Loop Velocity Profiler (SCORE-02, false-positive gate P10)
- **D-02:** Embed `output_text` of consecutive turns; flag a loop when similarity ≥
  `RouterConfig.loop_similarity_threshold` (default 0.85) across ≥2 consecutive turns AND the
  observation did NOT change. ALSO embed the observation — a changed observation means the agent is
  making progress, so it must NOT flag (P10). Point-detection on a single pair is not enough; require
  consecutive evidence.
- **D-03 (embedder):** Use `fastembed` (BAAI/bge-small-en-v1.5) behind the `[embed]` optional extra,
  imported LAZILY inside the profiler (a clear error if missing). Cosine similarity via numpy. The
  profiler keeps its OWN embedding cache keyed by `call_id` (TurnRecord is frozen and stores only
  `output_text` — no embedding field, per Phase-1/2 design). Warm the embedder once, not per-call.

### Tool-Call Flapping Monitor (SCORE-03)
- **D-04:** Flag when the same `tool_name` is called >2 times (i.e. 3rd occurrence) with only slightly
  varied args and no change in observation content → `kind="tool_flapping"`.

### THE capture gap (researcher must resolve — affects whether Phase 1/2 code changes)
- **D-05:** Phase 2's `TrajectoryCallback` currently sets `tool_name=None` / `tool_args=None` on every
  TurnRecord and does NOT capture tool observations. Flapping (D-04) and the loop false-positive gate
  (D-02, "observation changed") REQUIRE tool name, args, and observation. **Recommended:** extend
  `TrajectoryCallback` with `on_tool_start`/`on_tool_end` to populate `tool_name`/`tool_args` and
  capture the observation, correlating tool execution to the owning step. The researcher must verify
  the `on_tool_start`/`on_tool_end` signatures and ReAct's tool→observation flow against source, and
  decide the exact shape: populate the existing (frozen) TurnRecord at creation time by parsing the
  react predict's `next_tool_name`/`next_tool_args` from the LM output, vs. add a parallel
  observation store on SessionState, vs. a small contract addition. Flag clearly if this requires
  touching `state.py`/`capture.py` (a controlled cross-phase change with its own tests).

### Config-driven thresholds (SCORE-04)
- **D-06:** Every threshold (`loop_similarity_threshold`, flapping count, etc.) is read from
  `RouterConfig` at scoring time — changing config changes behavior with zero code change. The scorer
  receives the config (or reads the session's bound config).

### Escalation cap + logging (SCORE-05 / ROUTE-05 scoring side)
- **D-07:** On anomaly, the scorer sets `SessionState.current_threshold=0.0` and increments
  `escalation_count` — but once `escalation_count >= RouterConfig.max_escalations_per_session`, it
  STOPS forcing 0.0 regardless of further anomalies (runaway-cost safety valve). Every escalation
  event is logged with the triggering detector name + score. No real model call here — just the
  decision + state update for Phase 4 to consume.

### No LLM judge (SCORE-05)
- **D-08:** All detection is telemetry/regex/embedding/post-generation only. No pre-inference LLM
  query anywhere (honors the scope's hard boundary).

### Claude's Discretion
- Module layout (`scoring.py` with `ScoringEngine` + detector classes vs a `scoring/` subpackage),
  where `ScoringResult` lives, and whether the scorer is invoked from the callback's `on_module_end`
  or as a separate post-step call — left to planner/researcher provided the success criteria hold and
  clean boundaries (capture writes window; scoring reads window + writes threshold) are respected.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Verified research
- `dev/research-dspy-routellm.md` — callback hooks incl. `on_tool_start`/`on_tool_end` (tool name +
  params), ReAct loop/trajectory shape
- `.planning/research/PITFALLS.md` — P8 (no universal cosine threshold; calibrate), P9 (embedder
  warm/cold latency), P10 (tool retry vs pathological loop false positive), P17 (escalation cap)
- `.planning/research/STACK.md` — fastembed + bge-small decision (no torch), numpy cosine
- `.planning/research/ARCHITECTURE.md` — scoring engine boundaries (reads window, writes threshold)

### Project specs + Phase 1/2 code (the substrate)
- `.planning/REQUIREMENTS.md` §"Dynamic Scoring Engine" — SCORE-01..SCORE-05
- `.planning/ROADMAP.md` §"Phase 3" — goal + 5 success criteria
- `agent_router/state.py` — `SessionState` (window, current_threshold, escalation_count, cost_log,
  _lock), `TurnRecord` (tool_name/tool_args currently None; output_text present)
- `agent_router/capture.py` — `TrajectoryCallback` to extend for tool/observation capture (D-05)
- `agent_router/config.py` — `RouterConfig` thresholds (loop_similarity_threshold,
  max_escalations_per_session)
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `SessionState.window` (deque of TurnRecord), `current_threshold`, `escalation_count`, `_lock`.
- `RouterConfig` thresholds already defined (Phase 1).
- `TrajectoryCallback` (Phase 2) — extend for tool capture.

### Established Patterns
- `from __future__ import annotations`, Python 3.10+, `mypy --strict` clean, light-import discipline.
- fastembed MUST be lazy (only loaded when LoopVelocityProfiler actually runs); keep
  `import agent_router` free of it.
- pytest unit tests on recorded-window fixtures + a tiny real-embedder smoke; no network.

### Integration Points
- Scoring reads `SessionState.window`, writes `current_threshold`/`escalation_count` — the handoff
  to Phase 4 (RouteLLM routing reads current_threshold).
</code_context>

<specifics>
## Specific Ideas

Acceptance is behavioral: a window with two consecutive high-similarity, observation-unchanged turns
→ `anomaly, kind="loop_velocity"`; a changed-observation retry → no flag; 3× same-tool varied-args
no-observation-change → `tool_flapping`; JSON-Schema/XML input → `structural_constraint` before any
probabilistic detector; config threshold change alters behavior; escalation cap halts forced-0.0.
</specifics>

<deferred>
## Deferred Ideas

- Zero-dep hash-fingerprint loop fallback (PERF-02) → v2. The `[embed]`-optional layout keeps it open.
- Actual RouteLLM threshold injection / paid escalation → Phase 4.
- Hard budget cap / auto-stop (COST-01) → v2; this phase only logs + caps escalation count.

None belong in Phase 3 — discussion stayed within scope.
</deferred>

---

*Phase: 3-Dynamic Scoring Engine*
*Context gathered: 2026-06-18*
