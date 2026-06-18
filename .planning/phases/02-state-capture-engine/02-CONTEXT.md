# Phase 2: State Capture Engine - Context

**Gathered:** 2026-06-18 (autonomous discuss — recommended options chosen per operating agreement)
**Status:** Ready for planning

<domain>
## Phase Boundary

Implement the non-intrusive State Capture Engine (Block 1): `TrajectoryTracker(session_id=...)` as a
context manager that registers DSPy callbacks on enter, captures accurate per-step telemetry into the
`SessionState.window` built in Phase 1, and tears down on exit — all without the developer changing
their agent code. In scope: CAP-01..CAP-07. Out of scope: scoring/detectors (Phase 3) and any
RouteLLM routing (Phase 4). No paid API calls — validate against `dspy.ReAct` with a mock/dummy LM.
</domain>

<decisions>
## Implementation Decisions

All gray areas resolved to the recommended option (autonomous mode; the research + PITFALLS already
constrain these tightly). The phase researcher must FINALIZE the exact overcount/usage mechanics
against installed dspy 3.2.1 source before the planner writes tasks.

### Callback registration (CAP-02 — preserve existing callbacks)
- **D-01:** Register via `dspy.context(callbacks=<existing> + [tracker_cb])` inside `__enter__`, and
  restore on `__exit__`. MUST NOT use `dspy.settings.configure(callbacks=...)` — that REPLACES and
  silently drops a user's Langfuse/W&B callbacks (Pitfall P4). Read the current callback list and
  append; never overwrite.

### Step indexing (CAP-04 — ReAct overcount gate)
- **D-02:** A "step" = one agent reasoning turn. ReAct fires callbacks for the outer ReAct module +
  the inner Predict (per iteration) + the final extract, so naive `on_module_start` counting triples
  the count (Pitfall P7). Recommended approach: derive one `TurnRecord` per LM call via
  `on_lm_end`, with `step_idx` from a per-session monotonic counter, and filter so a 5-iteration
  ReAct yields exactly 5 records (handle/exclude the trailing extract call). **Researcher to confirm
  the exact filter** (call_id nesting depth vs module-type filter vs lm-call counting) against dspy
  source; the success criterion (5 iters → step_idx==5, not 15) is the acceptance bar.

### Token usage (CAP-05)
- **D-03:** Token counts do NOT come from `on_lm_end.outputs` (that's processed text — Pitfall P1).
  Read usage from the LM instance history (`lm.history[-1]["usage"]`) or DSPy's `UsageTracker` /
  `track_usage`. Cache-hit steps set `cache_hit=True` and record their distinct token state rather
  than silently showing zero. Researcher confirms the exact field path.

### Signature identity (CAP-03 — StringSignature gate)
- **D-04:** `signature_name` = the Signature class `__name__` when meaningful, else (for inline string
  signatures that all surface as `StringSignature`, Pitfall P2) a derived identity built from the
  class name + sorted input/output field names, so distinct inline signatures are distinguishable.

### Session isolation & concurrency (CAP-07)
- **D-05:** Each `TrajectoryTracker` owns a `session_id`; on enter it creates/looks up the
  `SessionState` in `_SESSION_REGISTRY` under `_REGISTRY_LOCK` (the lock added in Phase 1 for the
  TOCTOU fix). Per-session field mutations use the SessionState `_lock`. Note the ContextVar caveat
  (Pitfall P3: `ACTIVE_CALL_ID` is a ContextVar — thread-spawned ReAct workers may see `None`);
  document the limitation and ensure two concurrent sessions with different `session_id` never bleed
  window entries or step counts (success criterion 5). On `__exit__`, remove the session from the
  registry to prevent unbounded growth (the Phase-1 TODO).

### Failure capture (CAP-06)
- **D-06:** Record success/failure per step from the callback `exception` argument on the `*_end`
  hooks; handle `outputs=None` on exception (Pitfall P6). A failed step still produces a TurnRecord
  with its exception populated.

### Claude's Discretion
- Internal module layout (callback class in `tracker.py` vs a `capture/` submodule), how the tracker
  threads the live SessionState into the callback instance, and test-double LM design — left to the
  planner/executor provided the success criteria and the decisions above hold.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Verified technical research (authoritative on dspy callback shapes)
- `dev/research-dspy-routellm.md` — DSPy callback hooks (`on_module_*`, `on_lm_*`, `on_tool_*`),
  args (`call_id`, `instance`, `inputs`/`outputs`, `exception`), usage tracking, ReAct loop shape
- `.planning/phases/01-foundation-contracts/01-RESEARCH.md` — Phase 1 research (token-usage path,
  contract field rationale)
- `.planning/research/PITFALLS.md` — P1 (usage not in outputs), P2 (StringSignature), P3 (ContextVar
  threading), P4 (configure replaces callbacks), P6 (outputs=None), P7 (ReAct overcount) — all DSPy-
  callback-source-verified; these define this phase's gates

### Project specs
- `.planning/REQUIREMENTS.md` §"State Capture Engine" — CAP-01..CAP-07
- `.planning/ROADMAP.md` §"Phase 2" — goal + 5 success criteria (acceptance bar)
- `agent_router/state.py` — the Phase-1 contracts this phase writes into (`TurnRecord`,
  `SessionState`, `_SESSION_REGISTRY`, `_REGISTRY_LOCK`)
- `agent_router/tracker.py` — the Phase-1 `TrajectoryTracker` stub to flesh out
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent_router/state.py`: `TurnRecord` (now hashable), `SessionState` (mutable, `_lock`),
  `_SESSION_REGISTRY`, `_REGISTRY_LOCK` — capture writes here.
- `agent_router/tracker.py`: `TrajectoryTracker` context-manager shell from Phase 1.

### Established Patterns
- `from __future__ import annotations`, Python 3.10+ floor, `mypy --strict` clean, fastembed/routellm
  kept out of import path. New capture code must NOT import fastembed/routellm/heavy deps at module load.
- pytest unit tests with a mock/dummy DSPy LM (no network — free phase).

### Integration Points
- The callback writes `TurnRecord`s into `SessionState.window`; Phase 3 scoring reads that window.
- The registry + locks are the concurrency boundary (CAP-07).
</code_context>

<specifics>
## Specific Ideas

Acceptance is behavioral and must be tested with a real `dspy.ReAct` driven by a mock LM:
5 iterations → exactly 5 TurnRecords with monotonic step_idx; a pre-existing callback still fires;
no `StringSignature` identities; non-zero token counts; two concurrent sessions stay isolated.
</specifics>

<deferred>
## Deferred Ideas

- Background-thread scoring (PERF-01) and zero-dep fallback (PERF-02) → v2; not this phase.
- Actual scoring/detectors → Phase 3.

None belong in Phase 2 — discussion stayed within scope.
</deferred>

---

*Phase: 2-State Capture Engine*
*Context gathered: 2026-06-18*
