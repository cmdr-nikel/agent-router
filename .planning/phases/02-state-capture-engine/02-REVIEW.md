---
phase: 02-state-capture-engine
reviewed: 2026-06-18T00:00:00Z
depth: deep
files_reviewed: 4
files_reviewed_list:
  - agent_router/capture.py
  - agent_router/tracker.py
  - tests/conftest.py
  - tests/unit/test_capture.py
findings:
  critical: 1
  warning: 4
  info: 2
  total: 7
status: clean
---

# Phase 2: Code Review Report

**Reviewed:** 2026-06-18
**Depth:** deep
**Files Reviewed:** 4
**Status:** issues_found

## Summary

The four files are well-structured and follow the research document closely. The core
architecture — one `TurnRecord` per `on_lm_end`, ChainOfThought sentinel, `dspy.context`
for callback scoping, per-session `_lock`, registry TOCTOU guard — is implemented
correctly for the standard single-threaded ReAct use case. All 7 CAP tests pass green.

One logic error is confirmed against installed DSPy 3.2.1 source: token counts for
exception records silently read from the **previous** call's history entry, not the
failed call. The tests do not assert on token counts for the exception path so they
pass, but any Phase 3 consumer that reads `input_token_count`/`output_token_count` on
a `TurnRecord` with `exception != None` will receive stale data from an earlier step.

Four warnings address the `_in_extract` sentinel's fragility under non-standard agent
shapes, the DummyLM iterator desync that will silently corrupt future test data, the
missing concurrent-session test, and an undocumented window-count invariant broken by
the extract-exception recording logic. Two info items cover test tightness and a minor
security note.

---

## Critical Issues

### CR-01: Stale `lm.history[-1]` on exception path — wrong token counts in TurnRecord

**File:** `agent_router/capture.py:189`

**Issue:** When an LM call raises an exception, `BaseLM._process_lm_response()` is
never reached, so no history entry is appended for the failed call. The code reads:

```python
entry: dict[str, Any] = lm.history[-1] if lm.history else {}
```

If `lm.history` is non-empty (i.e., at least one prior call succeeded), `history[-1]`
returns the **previous successful call's entry**, not anything related to the failed
call. The resulting `TurnRecord` gets `input_token_count` and `output_token_count`
copied from that earlier step — entirely wrong data.

Verified empirically: a Predict that succeeds on call 0 then raises on call 1 produces
a TurnRecord for call 1 with `prompt_tokens=10, completion_tokens=5` copied verbatim
from call 0's history entry.

The test at `test_exception` passes because it only asserts `exception is not None` and
`output_text is None`, never touching token counts. This masks the bug from CI.

This also affects the extract-exception path (when `_in_extract=True` and
`exception is not None`): the record created there carries stale tokens from the last
successful react step.

**Fix:** Guard the history read with the exception state. On exception, no history was
written, so default immediately to zero tokens without reading `history[-1]`:

```python
def on_lm_end(
    self,
    call_id: str,
    outputs: Any | None,
    exception: Exception | None = None,
) -> None:
    lm = self._pending_lm.pop(call_id, None)
    if lm is None:
        return
    if self._in_extract and exception is None:
        return

    # history[-1] is only valid when the call succeeded: _process_lm_response
    # appends the entry BEFORE this callback fires, but ONLY on the non-exception path.
    if exception is None and lm.history:
        entry: dict[str, Any] = lm.history[-1]
    else:
        entry = {}  # failed call: no history entry was written
    usage: dict[str, Any] = entry.get("usage", {}) or {}
    response: Any = entry.get("response") if entry else None
    is_cache_hit: bool = bool(getattr(response, "cache_hit", False))
    ...
```

Simultaneously, `test_exception` should add:

```python
for r in exception_records:
    assert r.input_token_count == 0, (
        "Exception record must have zero input tokens (no history entry was written)"
    )
    assert r.output_token_count == 0, (
        "Exception record must have zero output tokens"
    )
```

---

## Warnings

### WR-01: `_in_extract` sentinel does not stack — nested or multiple ChainOfThought modules corrupt state

**File:** `agent_router/capture.py:117-121`

**Issue:** `_in_extract` is a single boolean and `_react_extract_id` is a single
string. If any `ChainOfThought` module fires `on_module_start` while `_in_extract` is
already `True` (nested CoT, or user tool that internally uses CoT), the second
`on_module_start` overwrites `_react_extract_id` with the inner call_id:

```
on_module_start CoT_outer  -> _in_extract=True,  _react_extract_id=id_outer
on_module_start CoT_inner  -> _in_extract=True,  _react_extract_id=id_inner  (overwrites!)
on_module_end   CoT_inner  -> id_inner == _react_extract_id -> _in_extract=False  (too early!)
on_lm_end       (CoT_outer's LM) -> _in_extract=False -> RECORD EMITTED (should be skipped)
on_module_end   CoT_outer  -> id_outer != None -> sentinel NOT cleared (already False)
```

The outer CoT's LM call is emitted as a TurnRecord when it should be skipped. This
does not affect the standard dspy.ReAct shape (one flat ChainOfThought), but the
module docstring only notes Assumption A1 without indicating the code is unsafe for any
agent that uses ChainOfThought inside a tool or sub-module.

**Fix (minimal):** Replace the single sentinel with a counter or a stack:

```python
# In __init__:
self._extract_depth: int = 0
self._react_extract_ids: list[str] = []

# In on_module_start:
if isinstance(instance, ChainOfThought):
    self._extract_depth += 1
    self._react_extract_ids.append(call_id)

# In on_module_end:
if self._react_extract_ids and call_id == self._react_extract_ids[-1]:
    self._react_extract_ids.pop()
    self._extract_depth -= 1

# In on_lm_end:
if self._extract_depth > 0 and exception is None:
    return
```

Alternatively, add a clear runtime assertion or exception with a descriptive message
when nesting is detected, so the failure is loud rather than silent data corruption.

---

### WR-02: `DummyLM` parent iterator desyncs after an Exception response

**File:** `tests/conftest.py:99-112`

**Issue:** The parent `dspy.utils.DummyLM` converts `answers` (the `parent_dicts`
list) into an iterator via `iter(answers)` in `__init__`. The custom `forward()` calls
`super().forward()` (consuming one item from that iterator) **only for non-exception
items**. Exception items cause an early raise before `super().forward()` is called, so
their placeholder slot stays unconsumed.

After an exception item at position `k`, all subsequent calls to `super().forward()`
return the wrong response (shifted by the number of preceding exception items). For
example:

```python
responses = [dict_0, RuntimeError("boom"), dict_2]
# Call 0: dict_0  -> super() consumes parent_dicts[0] -> OK
# Call 1: RuntimeError -> raises, super() NOT called -> parent iter at parent_dicts[1]
# Call 2: dict_2  -> super() consumes parent_dicts[1] = PLACEHOLDER -> wrong text returned
```

This does not affect any current test (all tests place the exception as the last
response or after the agent exits), but it is a latent bug that will corrupt future
tests that need a successful call after an exception call.

**Fix:** Skip `super().forward()` call accounting by advancing the parent iterator
explicitly for exception items, OR stop relying on the parent iterator altogether and
format responses directly:

```python
def __init__(self, responses):
    # Only pass NON-exception dicts to parent; track a separate skip counter
    parent_dicts = []
    self._exception_positions = set()
    for i, item in enumerate(responses):
        if isinstance(item, Exception):
            self._exception_positions.add(i)
            parent_dicts.append({"answer": "_exception_placeholder_"})
        elif isinstance(item, CacheHit):
            parent_dicts.append(item.fields)
        else:
            parent_dicts.append(item)
    super().__init__(parent_dicts)
    # Advance parent iterator past exception placeholders by consuming them eagerly
    # -- OR -- use a separate index-based approach instead of the parent iterator.
```

The cleanest fix is to not use `super().forward()` for the response-formatting step
at all and format field values directly using the adapter, but that requires more
invasive changes.

---

### WR-03: CAP-07 only tests **sequential** isolation; true concurrent isolation is untested

**File:** `tests/unit/test_capture.py:382-448`

**Issue:** The test is titled "concurrent/sequential" but runs both sessions strictly
sequentially in the same thread. It proves that two `TrajectoryTracker` instances with
different `session_id` values do not share `SessionState` objects, which is correct.
However, it does not exercise:

1. Two `with TrajectoryTracker(...)` blocks running at the same time from two threads
   sharing a single `dspy.LM` instance — the documented `lm.history[-1]` race (RESEARCH
   Open Question 2, Assumption A2).
2. Two threads both calling `__enter__` simultaneously, racing on the `_REGISTRY_LOCK`
   check-then-insert.
3. ContextVar inheritance: child threads spawned inside a `TrajectoryTracker` context
   will not inherit `dspy.settings.callbacks` (ContextVar threading caveat), so the
   callback will silently not fire there.

The CONTEXT.md acceptance criterion says "two concurrent sessions with different
session_id never bleed window entries." That criterion is only proven for sequential
runs. A real concurrent test is needed to confirm the lock correctly serialises
simultaneous `__enter__` calls.

**Fix:** Add a `test_concurrent_isolation_threading` test that spins two threads, each
running a `TrajectoryTracker` with its own `DummyLM`, joins them, and asserts
non-overlapping windows and correct step counts:

```python
import threading

def test_concurrent_isolation_threading(dummy_lm_factory):
    results = {}
    def run_session(sid, n):
        lm = dummy_lm_factory(n_iters=n)
        with dspy.context(lm=lm):
            react = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=10)
            with TrajectoryTracker(session_id=sid) as t:
                react(question=f"q-{sid}")
        results[sid] = list(t._session.window)

    threads = [
        threading.Thread(target=run_session, args=("t1", 2)),
        threading.Thread(target=run_session, args=("t2", 3)),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(results["t1"]) == 2
    assert len(results["t2"]) == 3
```

Note that this test would likely expose the ContextVar threading caveat (callbacks may
not fire in child threads). Documenting the expected-failure mode is acceptable; the
test at minimum proves the registry lock is correct.

---

### WR-04: Extract-exception path breaks the "exactly N records for N-iter ReAct" invariant

**File:** `agent_router/capture.py:181-184` and `tests/unit/test_capture.py:214-248`

**Issue:** The intentional design decision in `capture.py` (lines 181-184) is:

```python
# Skip SUCCESSFUL extract calls ... Exception: if the extract LM call itself
# raised, still capture the record ...
if self._in_extract and exception is None:
    return
```

This means:
- Successful 5-iter ReAct: 5 records (N, as per CAP-04). Correct.
- 5-iter ReAct with extract failure: 6 records (N+1). Undocumented.

The CAP-04 test only exercises the success path. Phase 3 scoring consumers have no
documented contract for the N+1 case. Any Phase 3 code that relies on
`len(session.window) == expected_steps` will silently miscount on extract failures.

This is not necessarily wrong by itself, but the invariant break is undocumented and
the code comment does not say "this may produce N+1 records." The Phase 3 handoff
needs a clear contract.

**Fix:** Add a docstring note to `on_lm_end` and a test case:

```python
# In on_lm_end docstring, add:
# Exception on extract: if the extract ChainOfThought LM call raises, a record
# IS created (exception != None, step_idx = N) so that failures are never silent.
# This means window length = N+1 for a failed extract vs N for a successful one.
# Phase 3 consumers must check record.exception rather than inferring failure
# from window length alone.

# And in test_capture.py, add:
def test_exception_in_extract_produces_n_plus_1_records():
    n_iters = 2
    lm = DummyLM(responses=[
        {"next_thought": "step 0", "next_tool_name": "dummy_tool", "next_tool_args": {"query": "x"}},
        {"next_thought": "done", "next_tool_name": "finish", "next_tool_args": {}},
        RuntimeError("extract failed"),  # extract step raises
    ])
    dspy.configure(lm=lm)
    react = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=10)
    with TrajectoryTracker(session_id="cap06b-extract-fail") as t:
        with pytest.raises(RuntimeError):
            react(question="crash extract")
    records = list(t._session.window)
    assert len(records) == n_iters + 1, "extract failure produces N+1 records"
    assert records[-1].exception is not None
```

---

## Info

### IN-01: `test_exception` does not assert zero tokens on the exception record

**File:** `tests/unit/test_capture.py:325-367`

**Issue:** CAP-06 asserts that `exception != None` and `output_text is None` for the
failed step. It does not assert token counts. Once CR-01 is fixed, the correct value
for `input_token_count` and `output_token_count` on an exception `TurnRecord` is `0`.
Without a token assertion, a regression in CR-01's fix would not be caught.

**Fix:** Add after `assert r.output_text is None`:

```python
    assert r.input_token_count == 0, (
        f"Exception TurnRecord step {r.step_idx}: expected zero input tokens, "
        f"got {r.input_token_count}. No history entry is written on LM exception."
    )
    assert r.output_token_count == 0, (
        f"Exception TurnRecord step {r.step_idx}: expected zero output tokens, "
        f"got {r.output_token_count}."
    )
```

---

### IN-02: PII / secret capture in `output_text` is noted in security comment but not in public API docstring

**File:** `agent_router/capture.py:9` and `agent_router/tracker.py`

**Issue:** The file-level comment in `capture.py` (lines 6-9) acknowledges that
`TurnRecord.output_text` stores raw LM output that may contain prompt-derived sensitive
text, and that sanitization is the caller's responsibility. However, this note is only
in a source file header comment. It is not in the `TrajectoryTracker` class docstring,
which is the entry point that library users encounter via `help()` or type stubs.

For a production-ready pip-installable library, callers need this warning at the API
boundary they interact with.

**Fix:** Add a `Security note:` section to `TrajectoryTracker.__doc__`:

```python
class TrajectoryTracker:
    """
    ...
    Security note: TurnRecord.output_text stores the raw LM output text which
    may contain prompt-derived data, user inputs, or other sensitive content.
    Downstream logging, storage, or transmission of session windows is the
    caller's responsibility. This library performs no sanitization or
    redaction (per design decision T-02-05).
    """
```

---

_Reviewed: 2026-06-18_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
