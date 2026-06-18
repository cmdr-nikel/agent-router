---
phase: 01-foundation-contracts
plan: "02"
subsystem: contracts
tags: [dataclass, frozen, mypy, state, telemetry, threading]

# Dependency graph
requires:
  - phase: 01-01
    provides: "package skeleton, pyproject.toml, dev tooling, test infrastructure"
provides:
  - "TurnRecord: frozen dataclass with D-06 telemetry fields (call_id, step_idx, signature_name, tool_name, tool_args, input_token_count, output_token_count, output_text, cache_hit, exception)"
  - "CostRecord: frozen dataclass separating billed vs cache-free cost (billed_cost=None on cache hit)"
  - "SessionState: mutable dataclass with session_id, window (deque), current_threshold, escalation_count, cost_log, threading.Lock"
  - "_SESSION_REGISTRY: module-level dict[str, SessionState] for session lookup"
  - "mypy --strict clean on agent_router/state.py"
  - "6/6 contract tests green in tests/unit/test_contracts.py"
affects:
  - "01-03 (RouterConfig), Phase 2 (TrajectoryTracker writes to SessionState.window), Phase 3 (ScoringEngine reads window, writes current_threshold), Phase 4 (DynamicRouteLM reads current_threshold)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "stdlib @dataclass(frozen=True) for immutable telemetry records (TurnRecord, CostRecord)"
    - "mutable @dataclass + threading.Lock for concurrent SessionState"
    - "from __future__ import annotations throughout for Python 3.10 compat"
    - "No dspy/fastembed/routellm at module level — state.py is a pure-stdlib module"

key-files:
  created: []
  modified:
    - "agent_router/state.py — TurnRecord, CostRecord, SessionState, _SESSION_REGISTRY"
    - "tests/unit/test_contracts.py — 6 frozen/mutable/field-presence assertions"

key-decisions:
  - "TurnRecord stores output_text: str|None (not output_embedding) — frozen dataclass cannot be lazily assigned; embedding computation deferred to Phase 3 LoopVelocityProfiler"
  - "exception field typed as Exception|None on TurnRecord — captures on_lm_end(exception=...) callbacks without import of dspy"
  - "_SESSION_REGISTRY intentionally has no cleanup logic in Phase 1 — documented with TODO comment; cleanup is TrajectoryTracker.__exit__ responsibility in Phase 2"
  - "SessionState._lock uses field(default_factory=threading.Lock, repr=False) — excluded from repr to avoid threading noise in test output"

patterns-established:
  - "Pattern: frozen stdlib dataclass for append-only telemetry records — no pydantic overhead at capture time"
  - "Pattern: mutable stdlib dataclass + threading.Lock for session state updated by concurrent callbacks"
  - "Pattern: state.py imports only stdlib (dataclasses, collections, threading) — mypy --strict passes in isolation without any heavy-dep overrides"

requirements-completed: [LIB-01]

# Metrics
duration: 5min
completed: "2026-06-18"
---

# Phase 01 Plan 02: Data Contracts Summary

**Three stdlib dataclass contracts (TurnRecord frozen, CostRecord frozen, SessionState mutable) plus session registry — all D-06 fields present, mypy --strict clean, 6/6 contract tests green.**

## Performance

- **Duration:** ~5 min (verification + summary; implementation pre-landed in 01-01)
- **Started:** 2026-06-18T15:31:33Z
- **Completed:** 2026-06-18T15:36:00Z
- **Tasks:** 1 (TDD task verified as fully green)
- **Files modified:** 0 new changes (pre-landed in 01-01 commit `519fddb`)

## Accomplishments

- Verified `agent_router/state.py` against all D-06 field requirements and plan acceptance criteria
- Confirmed `mypy --strict agent_router/state.py` exits 0 with no issues
- Confirmed 6/6 contract tests pass: frozen mutation raises FrozenInstanceError, SessionState accepts mutation, all required fields present
- Confirmed no `output_embedding` field on TurnRecord (D-03 / Pitfall P2 honored)
- Confirmed no dspy/fastembed/routellm imports at module level (Pitfall P3 honored)

## Task Commits

This plan's implementation was pre-landed by plan 01-01:

1. **Task 1: Implement TurnRecord, CostRecord, SessionState, and the session registry** — `519fddb` (feat(01-01): package skeleton + pyproject.toml + directory structure)
   - state.py with all three contracts, _SESSION_REGISTRY

Contract tests pre-landed in:
- `8dd26cc` (feat(01-01): Nyquist test scaffold + editable install smoke gate)
  - test_contracts.py with 6 frozen/mutable/field-presence assertions

**Plan metadata:** see final docs commit below

## Files Created/Modified

- `agent_router/state.py` — TurnRecord (frozen), CostRecord (frozen), SessionState (mutable + _lock), _SESSION_REGISTRY; stdlib-only imports; mypy --strict clean
- `tests/unit/test_contracts.py` — 6 contract assertions: directory structure, TurnRecord frozen + D-06 fields, CostRecord frozen + cache-hit None, SessionState mutable + D-06 fields + Lock

## Decisions Made

- `output_text: str | None` stored on TurnRecord, NOT `output_embedding` — frozen dataclasses cannot be lazily assigned; embedding lives in Phase 3 LoopVelocityProfiler
- `exception: Exception | None` captured directly — no dspy import required at state.py level
- `_SESSION_REGISTRY` has a TODO comment documenting Phase 2 cleanup obligation — no logic in Phase 1

## Deviations from Plan

None — plan executed exactly as specified. The implementation was pre-landed in plan 01-01's commit (`519fddb`), which bundled both the package skeleton and the data contracts in a single wave. All acceptance criteria verified green on the pre-landed code.

## Issues Encountered

None. The `grep -n output_embedding agent_router/state.py` acceptance check does match one line (the comment "NO output_embedding here (lazy in Phase 3)") — but this is a comment, not a field, and confirms the prohibition is explicitly documented in the source.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Data contracts are the stable integration surface for all downstream phases
- Phase 2 (TrajectoryTracker): writes TurnRecord entries to SessionState.window via DSPy callbacks
- Phase 3 (ScoringEngine): reads SessionState.window, writes SessionState.current_threshold
- Phase 4 (DynamicRouteLM): reads SessionState.current_threshold to build the router-mf-{threshold} model string
- 01-03 (RouterConfig): next plan in this phase — pydantic BaseSettings config object

---
*Phase: 01-foundation-contracts*
*Completed: 2026-06-18*
