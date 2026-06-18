---
phase: 01-foundation-contracts
plan: "04"
subsystem: api
tags: [pep562, lazy-import, dspy, mypy, public-api, context-manager]

requires:
  - phase: 01-02
    provides: RouterConfig in agent_router.config
  - phase: 01-03
    provides: SessionState/TurnRecord types, py.typed marker

provides:
  - "PEP 562 lazy __init__.py re-exporting TrajectoryTracker, DynamicRouteLM, RouterConfig"
  - "TrajectoryTracker context-manager stub (no fastembed/onnxruntime at module load)"
  - "DynamicRouteLM dspy.BaseLM subclass stub (no routellm at module load)"
  - "test_public_api_import gate — import gate verified green"

affects:
  - phase-02-capture
  - phase-04-routing
  - any downstream consumer of `from agent_router import ...`

tech-stack:
  added: []
  patterns:
    - "PEP 562 module-level __getattr__ for deferred re-export of optional-extra symbols"
    - "type: ignore[misc] on dspy.BaseLM subclass (dspy untyped; ignore_missing_imports override in pyproject)"
    - "from __future__ import annotations in all new modules (D-02 convention)"

key-files:
  created:
    - agent_router/tracker.py
    - agent_router/routing/dynamic_lm.py
  modified:
    - agent_router/__init__.py
    - tests/unit/test_imports.py

key-decisions:
  - "DynamicRouteLM subclasses dspy.BaseLM in the Phase 1 stub (Phase 4 refines BaseLM vs LM decision per research Q1)"
  - "RouterConfig imported eagerly in __init__.py (pydantic-settings is core dep, always safe); TrajectoryTracker/DynamicRouteLM deferred via PEP 562"
  - "import dspy has no type: ignore[import-untyped] because dspy.* is covered by ignore_missing_imports in mypy overrides — type: ignore[misc] only on the class line where BaseLM resolves to Any"

patterns-established:
  - "Pattern: PEP 562 _LAZY_MAP dict + module-level __getattr__ for optional-extra re-exports"
  - "Pattern: stub modules carry only shell logic + TODO comment pointing to implementing phase"

requirements-completed: [LIB-01]

duration: 10min
completed: 2026-06-18
---

# Phase 01 Plan 04: Lazy Public API + Stubs Summary

**PEP 562 lazy __init__.py re-exports TrajectoryTracker/DynamicRouteLM/RouterConfig with zero fastembed/routellm import at load time; mypy --strict passes; 10/10 unit tests green.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-06-18T15:38:00Z
- **Completed:** 2026-06-18T15:44:49Z
- **Tasks:** 2 (both pre-landed by plan 01-01; 1 targeted mypy fix applied)
- **Files modified:** 1 (dynamic_lm.py mypy fix); 3 others pre-landed and verified

## Accomplishments

- Verified all three pre-landed artifacts (`__init__.py`, `tracker.py`, `routing/dynamic_lm.py`) satisfy every acceptance criterion from the plan
- Fixed 3 mypy --strict errors in the pre-landed `DynamicRouteLM` stub (unused type: ignore, bare `list` annotation, missing `type: ignore[misc]` on class line)
- Confirmed `from agent_router import TrajectoryTracker, DynamicRouteLM, RouterConfig` succeeds with neither fastembed nor onnxruntime nor routellm in sys.modules
- `python -m pytest tests/unit -q` — 10 passed, 0 failed

## Task Commits

1. **Pre-landed artifacts verified** — no commit needed (already present from 01-01)
2. **mypy --strict fix for DynamicRouteLM** — `ae62d41` (fix)

## Files Created/Modified

- `agent_router/__init__.py` — PEP 562 lazy __getattr__ public API (pre-landed by 01-01, verified here)
- `agent_router/tracker.py` — TrajectoryTracker context-manager stub (pre-landed by 01-01, verified here)
- `agent_router/routing/dynamic_lm.py` — DynamicRouteLM dspy.BaseLM stub; `ae62d41` fixed unused type-ignore + bare list annotation
- `tests/unit/test_imports.py` — import gate test (pre-landed by 01-01, verified green here)

## Decisions Made

- `type: ignore[misc]` only on the `class DynamicRouteLM(dspy.BaseLM):` line; removed `type: ignore[import-untyped]` from `import dspy` because the `[[tool.mypy.overrides]] module = ["dspy.*"]` entry already handles it — having both causes "unused ignore" error under strict
- `messages: list[Any]` annotation in `forward()` (was bare `list`, triggered `type-arg` under strict)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed 3 mypy --strict errors in pre-landed DynamicRouteLM stub**
- **Found during:** Task 2 verification (mypy --strict agent_router/)
- **Issue:** `type: ignore[import-untyped]` on `import dspy` line was flagged as unused (covered by pyproject override); bare `list` in forward() missing type args; class-level subclassing of `Any` typed BaseLM needed `type: ignore[misc]`
- **Fix:** Replaced unused `type: ignore[import-untyped]` with a plain comment; added `[misc]` ignore on class line; annotated messages param as `list[Any]`
- **Files modified:** `agent_router/routing/dynamic_lm.py`
- **Verification:** `python -m mypy --strict agent_router/` exits 0 (Success: no issues found in 6 source files)
- **Committed in:** `ae62d41`

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug in pre-landed file)
**Impact on plan:** Targeted mypy fix only. No behavior change, no scope creep. All acceptance criteria met.

## Issues Encountered

- Pre-landed `dynamic_lm.py` used `# type: ignore[import-untyped]` on the dspy import, but the mypy override in pyproject.toml already suppresses the error via `ignore_missing_imports`, making the inline ignore redundant and triggering `[unused-ignore]` under `--strict`. Resolved by removing the redundant inline ignore.

## User Setup Required

None — no external service configuration required.

## Threat Coverage

| Threat ID | Status |
|-----------|--------|
| T-04-IMP | Mitigated — PEP 562 + test_public_api_import gate verified: fastembed/onnxruntime/routellm absent from sys.modules after named import |
| T-04-VER | Mitigated — `__version__ = "0.1.0"` literal preserved in __init__.py (grep verified) |
| T-04-DEF | Mitigated — __getattr__ raises AttributeError for any name not in _LAZY_MAP (verified: agent_router.Nope raises AttributeError) |

## Self-Check

- [x] `agent_router/__init__.py` exists with `__getattr__` and `__version__ = "0.1.0"`
- [x] `agent_router/tracker.py` — TrajectoryTracker context-manager shell, no fastembed
- [x] `agent_router/routing/dynamic_lm.py` — DynamicRouteLM dspy.BaseLM stub, no routellm
- [x] `tests/unit/test_imports.py` — test_public_api_import passes
- [x] `mypy --strict agent_router/` exits 0
- [x] `python -m pytest tests/unit -q` — 10 passed
- [x] Commit `ae62d41` exists

## Self-Check: PASSED

## Next Phase Readiness

- Public API surface is stable and type-checked — Phase 2 (Capture), Phase 3 (Scoring), and Phase 4 (Routing) can import against it without changes
- TrajectoryTracker stub ready for Phase 2 to add `__enter__`/`__exit__` logic via `dspy.context(callbacks=[...])`
- DynamicRouteLM stub ready for Phase 4 to implement `forward()` with RouteLLM threshold injection

---
*Phase: 01-foundation-contracts*
*Completed: 2026-06-18*
