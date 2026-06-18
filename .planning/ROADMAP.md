# Roadmap: agent-router

## Overview

Build a trajectory-monitoring router for DSPy agents in four phases after a foundation sprint. Phase 1 lays the package scaffold and the shared data contracts so Blocks 1-3 can be built and tested in isolation. Phases 2-4 implement each block against those contracts; Phase 2 (Capture) and Phase 3 (Scoring) must be sequential because scoring reads the window Capture writes, but Phase 4 (Routing) builds against a mock scoring signal in parallel and integrates at the seam. Phase 5 wires all three blocks end-to-end and runs the synthetic loop bench followed by the real benchmark confirmation.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation & Contracts** - Package scaffold, data contracts (SessionState/TurnRecord), shared interfaces, and config
- [ ] **Phase 2: State Capture Engine** - Non-intrusive DSPy callback integration, per-step telemetry, session isolation
- [ ] **Phase 3: Dynamic Scoring Engine** - Sliding window, three detectors (Loop Velocity / Flapping / Structural Constraint), escalation cap
- [ ] **Phase 4: RouteLLM Execution Layer** - DynamicRouteLM subclass, PayloadNormalizer, cost logging, RouteLLM server integration
- [ ] **Phase 5: Integration & Validation** - End-to-end wiring, synthetic loop bench, real benchmark, full pytest suite

## Phase Details

### Phase 1: Foundation & Contracts
**Goal**: The library installs cleanly, the shared data contracts exist and are typed, and the public API surface is defined so Blocks 1-3 can be built against stable interfaces
**Depends on**: Nothing (first phase)
**Requirements**: LIB-01, LIB-02
**Success Criteria** (what must be TRUE):
  1. `pip install -e .` succeeds in a clean env (hatchling build, pyproject.toml, correct extras for RouteLLM and fastembed)
  2. `from agent_router import TrajectoryTracker, DynamicRouteLM, RouterConfig` succeeds without importing optional heavy deps
  3. `SessionState`, `TurnRecord`, and `CostRecord` dataclasses exist, are fully typed (Python 3.14 type hints), and pass `mypy --strict` on the contracts alone
  4. `RouterConfig` exposes window_size, default_threshold, loop_similarity_threshold, max_escalations_per_session, weak_model, and strong_model with validated pydantic defaults
  5. Project directory matches the documented structure (`agent_router/`, `tests/unit/`, `tests/integration/`, `tests/bench/`)
**Plans**: 4 plans
  - [x] 01-01-PLAN.md — Package scaffold, pyproject.toml, dev-dep install, test scaffold (LIB-01)
  - [ ] 01-02-PLAN.md — Data contracts: TurnRecord/CostRecord/SessionState, mypy --strict (LIB-01)
  - [ ] 01-03-PLAN.md — RouterConfig (pydantic-settings, env-driven model pair) (LIB-02)
  - [ ] 01-04-PLAN.md — Lazy public API + TrajectoryTracker/DynamicRouteLM stubs (LIB-01)

### Phase 2: State Capture Engine
**Goal**: Developers can wrap any DSPy ReAct call in `with TrajectoryTracker(session_id=...):` and get accurate, session-isolated per-step telemetry without touching agent logic
**Depends on**: Phase 1
**Requirements**: CAP-01, CAP-02, CAP-03, CAP-04, CAP-05, CAP-06, CAP-07
**Success Criteria** (what must be TRUE):
  1. A 5-iteration `dspy.ReAct` run inside the context manager produces exactly 5 `TurnRecord` entries in `session.window` — not 10-15 (ReAct overcount gate: step_idx == N, not 3N)
  2. A pre-existing callback registered before `TrajectoryTracker.__enter__` is still called during the tracked session — callback preservation is verified by assertion, not assumption (dspy.context gate)
  3. All TurnRecord `signature_name` fields are non-`"StringSignature"` even for agents that use inline string signatures — the class+sorted-fields identity scheme is in effect
  4. Per-step `input_token_count` and `output_token_count` are non-zero and consistent with the actual request size; a cache-hit step is recorded with a distinct cache_hit flag rather than silently showing zero tokens
  5. Two concurrent sessions running under separate `TrajectoryTracker` instances (different `session_id`) do not bleed step counts or window entries into each other
**Plans**: TBD

### Phase 3: Dynamic Scoring Engine
**Goal**: After each ReAct step, the scoring engine analyzes the session window and correctly flags reasoning loops, tool-call flapping, and structural constraint demands — with every threshold exposed as config and a per-session escalation cap in place before any real model calls are made
**Depends on**: Phase 2
**Requirements**: SCORE-01, SCORE-02, SCORE-03, SCORE-04, SCORE-05
**Success Criteria** (what must be TRUE):
  1. A session window containing two consecutive turns whose output embeddings exceed the configured `loop_similarity_threshold` (default 0.85) AND whose step index has advanced without observation change causes `ScoringResult.anomaly=True` with `kind="loop_velocity"` — a single tool retry with a changed observation does NOT trigger (false-positive gate, P10)
  2. A session window where the same tool is called three times with slightly varied args and no change in observation content causes `ScoringResult.anomaly=True` with `kind="tool_flapping"`
  3. An input string containing JSON Schema or valid XML structure causes `ScoringResult.anomaly=True` with `kind="structural_constraint"` without any LM call — and this check fires before the probabilistic detectors
  4. `loop_similarity_threshold` and all detector thresholds are read from `RouterConfig` at scoring time — changing the config value changes detector behavior with no code change required
  5. The per-session escalation cap (`max_escalations_per_session`) is enforced: after N escalations the scoring engine stops setting `current_threshold=0.0` regardless of anomaly signal, and every escalation event is logged with the triggering detector name and score
**Plans**: TBD

### Phase 4: RouteLLM Execution Layer
**Goal**: `DynamicRouteLM` correctly routes each call through the RouteLLM server using a per-call model string derived from `session.current_threshold`, few-shot demos pass without KeyError, and every call's cost and token usage is logged separately for billed vs cache-hit calls
**Depends on**: Phase 3
**Requirements**: ROUTE-01, ROUTE-02, ROUTE-03, ROUTE-04, ROUTE-05, ROUTE-06
**Success Criteria** (what must be TRUE):
  1. `DynamicRouteLM.__call__` reads `session.current_threshold` at call time and sends model string `router-mf-{threshold}` to the RouteLLM server — two concurrent threads calling the same `DynamicRouteLM` instance with different session thresholds produce correct model strings in both call histories (thread-safety gate, P11)
  2. When `session.current_threshold == 0.0` (escalation), the RouteLLM server routes the call to the configured strong model — verified against a running RouteLLM server or a test-double that asserts the model string received
  3. A compiled DSPy program with at least one few-shot demo completes a round-trip through the RouteLLM server endpoint without `400 Bad Request` or `KeyError` — `PayloadNormalizer` is exercised on the demo messages (few-shot KeyError gate, P14)
  4. `session.cost_log` after a real-model call contains a non-zero `billed_cost` and correctly separates cache-hit calls into `estimated_free_calls` (cost None gate, P18)
  5. The `max_escalations_per_session` cap from Phase 3 is wired into the execution layer: a miscalibrated scoring engine cannot route more than the configured cap of calls to the frontier model in one session
**Plans**: TBD

### Phase 5: Integration & Validation
**Goal**: The full three-block pipeline works end-to-end, the synthetic loop bench demonstrates the core hypothesis (weak model loops ≥80% of seeds; escalation clears the block), the hypothesis is confirmed on a real benchmark, and the pytest suite covers all three blocks including a mock RouteLLM server
**Depends on**: Phase 4
**Requirements**: VAL-01, VAL-02, VAL-03, LIB-03
**Success Criteria** (what must be TRUE):
  1. The synthetic loop bench (`bench/synthetic_loop_bench.py`) runs the weak model with `cache=False` across ≥10 seeds and produces a ≥80% loop rate — non-trivial wall time per iteration confirms the cache is not masking the loops (bench reliability gate, P15/P16)
  2. On a looping seed from the bench, the full chain fires in order: `ScoringEngine` detects `anomaly=True` → `session.current_threshold` drops to 0.0 → `DynamicRouteLM` sends `router-mf-0.0` → RouteLLM routes to the strong model → the block is cleared (observable in the session's escalation log and cost_log)
  3. The escalation effect is confirmed on a real task set (GSM8K, HotpotQA, or code): accuracy with trajectory-aware escalation is measurably higher than weak-model-only on tasks where the weak model loops, with costs logged per run
  4. `pytest tests/` passes in full: unit tests cover capture, all three scoring detectors, and routing; integration tests cover the full tracker+scoring+routing pipeline using a mock RouteLLM server; bench is runnable as a standalone script
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation & Contracts | 1/4 | In Progress|  |
| 2. State Capture Engine | 0/TBD | Not started | - |
| 3. Dynamic Scoring Engine | 0/TBD | Not started | - |
| 4. RouteLLM Execution Layer | 0/TBD | Not started | - |
| 5. Integration & Validation | 0/TBD | Not started | - |
