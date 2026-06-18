---
phase: 02-state-capture-engine
plan: "02"
subsystem: telemetry
tags: [dspy, callback, BaseCallback, TrajectoryCallback, TurnRecord, SessionState, mypy]

requires:
  - phase: 02-01-state-capture-engine
    provides: DummyLM test double + 7 RED test stubs (CAP-01..CAP-07)
  - phase: 01-foundation-contracts
    provides: TurnRecord/SessionState dataclasses, _SESSION_REGISTRY/_REGISTRY_LOCK

provides:
  - agent_router/capture.py — TrajectoryCallback(BaseCallback); _derive_signature_name; single TurnRecord creation point
  - agent_router/tracker.py — TrajectoryTracker.__enter__/__exit__ wired to dspy.context + SessionState registry

affects: [02-03-state-capture-engine, 03-scoring-engine]

tech-stack:
  added: []
  patterns:
    - "Per-session callback binding by object reference (isolation primitive)"
    - "ChainOfThought extract sentinel (_in_extract) to exclude N+1th LM call from ReAct step count"
    - "Read token usage from lm.history[-1]['usage'] — never from on_lm_end outputs"
    - "Cache hit detection via getattr(response, 'cache_hit', False)"
    - "dspy.context(callbacks=existing + [cb]) for scoped, non-destructive callback registration"
    - "_derive_signature_name: __name__ for named classes; StringSignature:<sorted-in>><sorted-out> for inline"
    - "Exception capture: skip only SUCCESSFUL extract calls; exception-bearing extract calls still produce TurnRecord"

key-files:
  created:
    - agent_router/capture.py
  modified:
    - agent_router/tracker.py

key-decisions:
  - "Exception path for extract step: skip only successful extract LM calls (_in_extract and exception is None); exception-carrying extract calls are recorded so CAP-06 covers all LM failures, not just react-step failures"
  - "Minimal TrajectoryTracker wiring in Plan 02-02 (Rule 3 deviation): the 4 target tests all run through TrajectoryTracker.__enter__, so minimal wiring was necessary to make CAP-03/04/05/06 tests pass — Plan 02-03 will add the remaining CAP-01/02/07 behaviors"
  - "type: ignore[misc] on TrajectoryCallback(BaseCallback) — dspy has no py.typed marker so mypy sees BaseCallback as Any; this is the correct narrow suppression"

patterns-established:
  - "Pattern: TrajectoryCallback holds self._session by direct Python reference — two concurrent trackers with different SessionState objects can never write to each other's windows, even without lock coordination at the callback level"
  - "Pattern: step_idx = len(self._session.window) inside self._session._lock before append — atomic, no separate counter needed"

requirements-completed: [CAP-03, CAP-04, CAP-05, CAP-06]

duration: 18min
completed: 2026-06-18
---

# Phase 2 Plan 02: State Capture Engine — TrajectoryCallback Summary

**TrajectoryCallback(BaseCallback) implements per-step telemetry via ChainOfThought extract sentinel, lm.history usage path, and exception-safe TurnRecord creation, turning CAP-03/04/05/06 RED tests GREEN.**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-06-18T18:11:00Z
- **Completed:** 2026-06-18T18:29:26Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- `agent_router/capture.py` created with `TrajectoryCallback(BaseCallback)` — the single TurnRecord creation point
- `_derive_signature_name` correctly returns `StringSignature:<sorted-in>><sorted-out>` for inline sigs, never bare `"StringSignature"`
- `_in_extract` sentinel excludes the N+1th LM call from a 5-iter ReAct; exception-carrying extract calls still emit a TurnRecord
- Token counts read from `lm.history[-1]["usage"]` (non-zero on real calls); cache hits flagged via `response.cache_hit`
- `TrajectoryTracker.__enter__/__exit__` wired minimally (Rule 3 deviation) — creates SessionState, registers callback via `dspy.context(callbacks=existing + [cb])`
- mypy `--strict` clean on all 7 source files; zero fastembed/routellm imports at module load
- All 21 unit tests pass (4 target tests GREEN + 17 previously passing)

## Task Commits

1. **Task 1: TrajectoryCallback core — sentinel overcount + signature identity** - `9842d22` (feat)
2. **Task 2: Token usage path + exception capture + wire TrajectoryTracker** - `4f9db66` (feat)

## Files Created/Modified

- `/home/cmdr-nikel/DataspellProjects/agent-router/agent_router/capture.py` — TrajectoryCallback; _derive_signature_name; light-import only (217 lines)
- `/home/cmdr-nikel/DataspellProjects/agent-router/agent_router/tracker.py` — TrajectoryTracker.__enter__/__exit__ wired (minimal; Plan 02-03 adds CAP-01/02/07)

## Decisions Made

1. **Exception path for extract step**: The initial implementation skipped ALL extract calls when `_in_extract=True`. But `test_exception` expects a TurnRecord even when the extract LM call raises. Fixed sentinel to: `if self._in_extract and exception is None: return` — successful extracts are skipped, failing extracts are captured. This matches the CAP-06 requirement that "a failed LM call still appends a TurnRecord."

2. **Minimal tracker wiring in Plan 02-02**: The 4 target tests all call `TrajectoryTracker.__enter__` which was a do-nothing stub. Without wiring, `assert session_id in _SESSION_REGISTRY` fails before the callback is even exercised. Added minimal `__enter__`/`__exit__` implementation as a Rule 3 blocking fix. Plan 02-03 remains responsible for CAP-01/02/07 (wrap, preserve_callbacks, isolation tests).

3. **type: ignore[misc]**: dspy 3.2.1 ships no `py.typed` marker; mypy sees `BaseCallback` as `Any`, making subclassing an error under `--strict`. Narrow suppression at the class line is the correct fix; the rest of the file is fully typed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Exception sentinel over-excluded extract failures**
- **Found during:** Task 2 (exception capture implementation)
- **Issue:** Initial sentinel `if self._in_extract: return` skipped ALL LM calls during the extract phase, including the RuntimeError case in `test_exception`. The test expects a TurnRecord for the failed extract call.
- **Fix:** Changed guard to `if self._in_extract and exception is None: return` — only skip successful extract calls; let exception-bearing ones through.
- **Files modified:** agent_router/capture.py
- **Verification:** `python -m pytest tests/unit/test_capture.py -k exception -q` passes (1 passed)
- **Committed in:** 4f9db66 (Task 2 commit)

**2. [Rule 3 - Blocking] Minimal TrajectoryTracker wiring to unblock 4 target tests**
- **Found during:** Task 1 (first test run)
- **Issue:** All 4 target tests (overcount, signature_identity, tokens, exception) call `TrajectoryTracker.__enter__` and immediately `assert session_id in _SESSION_REGISTRY`. With the stub __enter__ doing nothing, the tests fail at that assertion before reaching any callback logic.
- **Fix:** Implemented `TrajectoryTracker.__enter__/__exit__` with SessionState creation, TrajectoryCallback binding, and `dspy.context` registration. Plan 02-03 retains full responsibility for CAP-01/02/07.
- **Files modified:** agent_router/tracker.py
- **Verification:** All 21 unit tests pass
- **Committed in:** 4f9db66 (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 bug / Rule 1, 1 blocking / Rule 3)
**Impact on plan:** Both fixes necessary for test correctness. The tracker wiring deviation was anticipated by the plan's note "wrap/preserve_callbacks/isolation may stay RED until 02-03" — only the minimal SessionState + callback registration needed to unblock capture tests was added; full CAP-01/02/07 coverage deferred to Plan 02-03 as intended.

## Issues Encountered

None beyond the two deviations documented above.

## Known Stubs

None. TrajectoryCallback is fully implemented. The tracker stub note in Plan 02-03 (CAP-01/02/07) is tracked in the ROADMAP — those tests currently pass because the minimal wiring in tracker.py happens to satisfy them too (21/21 tests pass), but Plan 02-03 may need to add edge-case handling.

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema changes. The in-process telemetry captures raw LM output text (T-02-05 accepted; documented in capture.py docstring).

## Next Phase Readiness

- Plan 02-03 (TrajectoryTracker wiring: CAP-01/02/07) can verify that the minimal wiring already satisfies its tests before adding more
- Phase 3 scoring engine can read `session.window` (deque of TurnRecord) via the SessionState already established here
- TrajectoryCallback is the single insertion point for all future telemetry additions (tool_name, embeddings, etc.)

---
*Phase: 02-state-capture-engine*
*Completed: 2026-06-18*
