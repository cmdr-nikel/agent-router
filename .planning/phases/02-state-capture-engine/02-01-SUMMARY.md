---
phase: 02-state-capture-engine
plan: 01
subsystem: test-infrastructure
tags: [tdd, wave-0, dspy, dummy-lm, react, callbacks]
dependency_graph:
  requires: []
  provides:
    - tests/conftest.py:DummyLM — network-free test double for all Phase 2 unit tests
    - tests/unit/test_capture.py — 7 RED stubs encoding the CAP-01..CAP-07 contract
  affects:
    - plans/02-02 — capture.py implementation verified against these 7 tests
    - plans/02-03 — tracker wiring verified against these 7 tests
tech_stack:
  added: []
  patterns:
    - dspy.utils.DummyLM subclass with non-zero token usage override
    - CacheHit sentinel for cache-hit response simulation
    - Exception-in-responses-list for on_lm_end exception path
key_files:
  created:
    - tests/conftest.py
    - tests/unit/test_capture.py
  modified: []
decisions:
  - Subclassed dspy.utils.DummyLM (not BaseLM directly) to leverage its built-in adapter-aware text formatting; override forward() to patch non-zero usage and handle CacheHit/Exception items
  - CacheHit sentinel rather than FakeResponse class: formats text via parent (ensures adapter parseability), then clears usage and sets cache_hit=True on the dotdict response, matching Cache._prepare_cached_response exactly
  - All 7 RED tests assert session in _SESSION_REGISTRY as the first failure gate; this is the cleanest signal that the stub __enter__ is unimplemented and avoids fragile AttributeError chains
  - dummy_lm_factory fixture returns a callable (not a fixture factory via request.param) for simple per-call DummyLM construction in isolation tests
metrics:
  duration: "13 minutes"
  completed: "2026-06-18"
  tasks_completed: 2
  files_created: 2
  files_modified: 0
---

# Phase 2 Plan 1: DummyLM test double + 7 RED test stubs — Summary

Wave 0 test infrastructure for the State Capture Engine: a network-free DummyLM test double that drives a real dspy.ReAct with non-zero token usage, plus 7 RED stubs in tests/unit/test_capture.py mapping 1:1 to CAP-01..CAP-07.

## What Was Built

### Task 1: DummyLM test double + fixtures (tests/conftest.py)

`DummyLM` subclasses `dspy.utils.DummyLM` (itself a `BaseLM` subclass, verified against dspy 3.2.1). The parent class handles adapter-aware text formatting so responses are parseable by dspy's ChatAdapter/JSONAdapter. The override in `forward()` does three things:

1. **Non-zero usage** — normal dict responses get `usage = dotdict(prompt_tokens=10, completion_tokens=5)`, so `lm.history[-1]["usage"]` yields non-empty counts (CAP-05 requirement; built-in DummyLM returns zeros).
2. **Cache-hit simulation** — a `CacheHit(fields_dict)` item formats the text via the parent but sets `response.usage = {}` and `response.cache_hit = True`, matching `Cache._prepare_cached_response` exactly (verified: dspy/clients/cache.py lines 149-155).
3. **Exception injection** — an `Exception` instance in the responses list causes `forward()` to raise before calling `super()`, exercising the `on_lm_end(exception=..., outputs=None)` callback path (CAP-06).

Fixtures provided:
- `dummy_lm` — fresh 3-iter DummyLM per test
- `dummy_lm_factory` — callable returning a fresh DummyLM per call (CAP-07 isolation)
- `pre_existing_callback` — counting `BaseCallback` for CAP-02 preservation test
- `dummy_tool` — deterministic no-network tool for ReAct harness
- `CacheHit` — sentinel class exported for inline test use

### Task 2: 7 RED test stubs (tests/unit/test_capture.py)

| Test | CAP | -k keyword | RED reason |
|------|-----|-----------|-----------|
| test_wrap | CAP-01 | `wrap` | `'cap01-wrap' in _SESSION_REGISTRY` is False (stub __enter__ creates no session) |
| test_preserve_callbacks | CAP-02 | `preserve_callbacks` | same session-registry assertion |
| test_signature_identity | CAP-03 | `signature_identity` | same session-registry assertion |
| test_overcount | CAP-04 | `overcount` | same session-registry assertion |
| test_tokens | CAP-05 | `tokens` | same session-registry assertion |
| test_exception | CAP-06 | `exception` | same session-registry assertion |
| test_isolation | CAP-07 | `isolation` | same session-registry assertion |

All 7 tests assert `session_id in _SESSION_REGISTRY` as the first gate inside the `with TrajectoryTracker(...)` block. The stub `__enter__` returns `self` without creating any SessionState, so all 7 fail with a clear `AssertionError` naming the exact contract that's unimplemented.

Later assertions (window length, step_idx ordering, token counts, exception presence, call_id non-overlap) encode the full behavioral contract for each CAP requirement — they will turn GREEN when Plans 02-02 and 02-03 land.

## Verification Results

```
python -m pytest tests/unit/test_capture.py --collect-only -q
→ 7 tests collected, 0 errors

python -m pytest tests/unit/test_capture.py -q
→ 7 failed (RED) — AssertionError (unimplemented capture), not ImportError/NameError

python -m pytest tests/unit/test_imports.py tests/unit/test_contracts.py tests/unit/test_config.py -q
→ 14 passed (Phase-1 tests unaffected)

python -m pytest tests/unit -q
→ 7 failed, 14 passed (correct split)
```

-k keyword selectors (each selects exactly 1 test):
`wrap`, `preserve_callbacks`, `signature_identity`, `overcount`, `tokens`, `exception`, `isolation`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Built-in DummyLM unsuitable for direct subclassing at BaseLM level**
- **Found during:** Task 1 implementation
- **Issue:** The plan described writing a custom `DummyLM(BaseLM)` from scratch with a `FakeResponse` class. During testing, responses formatted by a from-scratch DummyLM triggered `AdapterParseError` in dspy's JSON adapter because the raw text didn't match the adapter's expected field-header format.
- **Fix:** Subclassed `dspy.utils.DummyLM` instead, which already implements adapter-aware text formatting via `format_field_with_value`. Override `forward()` to intercept responses and patch usage/cache_hit/exception handling. The `FakeResponse` class was dropped in favour of inline dotdict mutation (cleaner, no extra class).
- **Files modified:** `tests/conftest.py`
- **Commit:** 172ec93

**2. [Rule 1 - Bug] test_wrap was GREEN with stub (assertion logic was inverted)**
- **Found during:** Task 2 first-run verification
- **Issue:** Initial `test_wrap` asserted `session is None` after exit — which is True for BOTH the stub (never creates session) and the correct implementation (cleans up on exit). The test passed trivially with the stub.
- **Fix:** Moved the registry assertion INSIDE the `with TrajectoryTracker(...)` block (`assert session_id in _SESSION_REGISTRY`) where the stub's failure is unambiguous. Added `tracker._session` access and window content assertion after the block for full contract coverage.
- **Files modified:** `tests/unit/test_capture.py`
- **Commit:** 26835bc (same commit, fixed before commit)

## Known Stubs

None — this plan creates test infrastructure only. No production code stubs introduced.

## Threat Flags

None — test-only files, no new network endpoints, no auth paths, no schema changes.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| tests/conftest.py | FOUND |
| tests/unit/test_capture.py | FOUND |
| 02-01-SUMMARY.md | FOUND |
| commit 172ec93 (feat conftest) | FOUND |
| commit 26835bc (test capture stubs) | FOUND |
