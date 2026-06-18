# Phase 1: Foundation & Contracts - Context

**Gathered:** 2026-06-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver the library skeleton and the shared data contracts so Blocks 1-3 can be built against
stable interfaces. In scope: hatchling-based pip-installable package, the `SessionState` /
`TurnRecord` / `CostRecord` data contracts, the `RouterConfig` settings object, the public API
surface (`TrajectoryTracker`, `DynamicRouteLM`, `RouterConfig` importable without heavy deps), and
the documented directory structure (`agent_router/`, `tests/unit/`, `tests/integration/`,
`tests/bench/`). Out of scope: any block logic (capture/scoring/routing behavior) — those are
Phases 2-4. This is the "connector" phase of the contracts-first Horizontal Layers strategy.
</domain>

<decisions>
## Implementation Decisions

These four gray areas were resolved to the recommended option (user delegated the choice; all
"recommended" defaults accepted).

### Dependency layout
- **D-01:** The embedder (`fastembed`) is an OPTIONAL extra (`agent-router[embed]`), imported lazily
  with a clear error message if missing. Rationale: satisfies the success criterion "imports work
  without loading optional heavy deps," and prepares the ground for the v2 zero-dependency
  loop-detection fallback (PERF-02). RouteLLM server deps go under a `serve` extra; `eval`/`bench`
  deps under a `bench` extra. Core install stays light.

### Python support
- **D-02:** Minimum Python is 3.10 (not 3.14-only). Rationale: production-ready library for other
  developers — 3.14-only would exclude almost everyone. Type-hint syntax targets 3.10 compatibility
  (use `from __future__ import annotations` / `typing` where needed). Dev box runs 3.14, so test on
  3.14 too.

### Contract mutability
- **D-03:** `TurnRecord` is frozen/immutable (telemetry is append-only — safer under the concurrent
  sessions of CAP-07). `SessionState` is mutable (its sliding window is updated in place). `CostRecord`
  frozen. `RouterConfig` is a pydantic model (validated, with defaults).

### Configuration source
- **D-04:** `RouterConfig` (pydantic) is the programmatic config AND reads from environment variables
  for the weak/strong model pair and API keys (e.g. via pydantic-settings). Rationale: 12-factor —
  the library is configured without code edits; secrets stay out of source.

### Contract field expectations (from research, locked for the planner)
- **D-05:** `RouterConfig` must expose at least: `window_size`, `default_threshold`,
  `loop_similarity_threshold`, `max_escalations_per_session`, `weak_model`, `strong_model`
  (per ROADMAP success criterion 4).
- **D-06:** `TurnRecord` must carry the fields downstream blocks need: signature identity
  (class name + sorted field names), step index, input token count, output text + output length,
  a cache-hit flag, and a success/exception field. `SessionState` holds `session_id` + the turn
  window + the live `current_threshold` / `escalation_count`. `CostRecord` separates billed vs
  cache-free cost. (These mirror the verified callback data in `dev/research-dspy-routellm.md` and
  the pitfalls P1/P2/P6/P18.)

### Claude's Discretion
- Exact module split inside `agent_router/` (e.g. `contracts.py` vs a `contracts/` package),
  pydantic v2 specifics, and the precise lazy-import shim are left to the planner/executor, provided
  the public API surface and the contract fields above hold.

## Operating Agreement (cross-phase)
- This project runs in **autonomous delegation mode**: Claude drives discuss → research → plan →
  plan-check → execute → verify per phase, choosing the "recommended" option at each decision, and
  reports at every phase boundary for the user to review and correct.
- **Money gate (non-negotiable):** Phases 1-3 run autonomously (no paid API calls — contracts,
  capture on a mock LM, scoring on fixtures). A HARD STOP precedes Phase 4 (first paid frontier
  call): Claude presents a report + cost estimate and waits for explicit go + budget before any
  paid run. No silent spending.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project specs
- `.planning/PROJECT.md` — project context, constraints, key decisions
- `.planning/REQUIREMENTS.md` §"Library & Packaging" — LIB-01, LIB-02 (this phase's requirements)
- `.planning/ROADMAP.md` §"Phase 1" — goal + 5 success criteria (the acceptance bar)

### Verified technical research
- `dev/research-dspy-routellm.md` — verified DSPy 3.2.1 callback data shapes and RouteLLM
  mechanism; defines what `TurnRecord` must capture and what `DynamicRouteLM` must do
- `.planning/research/STACK.md` — packaging stack (hatchling + uv, fastembed vs sentence-transformers,
  routellm extras, pytest-asyncio), with versions
- `.planning/research/ARCHITECTURE.md` — `SessionState` registry design, component boundaries,
  data flow (the contract this phase must encode)
- `.planning/research/PITFALLS.md` — P1 (usage not in on_lm_end.outputs), P2 (StringSignature),
  P6 (outputs=None), P11 (LM.model mutation race), P18 (cache-hit cost) — inform contract fields
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None yet — greenfield. This phase creates the first code.

### Established Patterns
- None in-repo. External patterns to follow: hatchling dynamic-version layout (STACK.md), pydantic v2
  settings, `src/`-less or `src/`-based layout (planner to pick per STACK.md guidance).

### Integration Points
- The contracts defined here are the integration surface for all later phases. `SessionState` is the
  single coupling point between capture (writes window), scoring (reads window, writes threshold),
  and routing (reads threshold).
</code_context>

<specifics>
## Specific Ideas

Public import surface must work lightly:
`from agent_router import TrajectoryTracker, DynamicRouteLM, RouterConfig` — succeeds WITHOUT pulling
fastembed/onnxruntime or the RouteLLM server stack at import time (lazy imports behind the optional
extras).
</specifics>

<deferred>
## Deferred Ideas

- Zero-dependency hash-fingerprint loop-detection fallback → v2 (PERF-02); the `[embed]`-optional
  layout decided here (D-01) is what makes it possible later.
- Hard budget cap / auto-stop → v2 (COST-01); Phase 1 only defines `CostRecord` so v1 can log.

None of the above belong in Phase 1 — discussion stayed within scope.
</deferred>

---

*Phase: 1-Foundation & Contracts*
*Context gathered: 2026-06-18*
