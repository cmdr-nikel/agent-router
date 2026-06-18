---
phase: 01-foundation-contracts
reviewed: 2026-06-18T00:00:00Z
depth: deep
files_reviewed: 9
files_reviewed_list:
  - agent_router/__init__.py
  - agent_router/state.py
  - agent_router/config.py
  - agent_router/tracker.py
  - agent_router/routing/dynamic_lm.py
  - pyproject.toml
  - tests/unit/test_contracts.py
  - tests/unit/test_config.py
  - tests/unit/test_imports.py
findings:
  critical: 2
  warning: 3
  info: 2
  total: 7
status: clean
---

# Phase 1: Code Review Report

**Reviewed:** 2026-06-18
**Depth:** deep
**Files Reviewed:** 9
**Status:** issues_found

## Summary

Phase 1 delivers a clean skeleton. The lazy-import mechanism is correctly implemented (PEP 562
`__getattr__` + caching), `RouterConfig` reads from env vars without logging secrets, all stubs
are honestly labelled with `NotImplementedError` or comments, and `from __future__ import
annotations` is present everywhere. Two contract-level correctness bugs were found that will
produce incorrect or crashed behavior the moment Phase 2 writes its first line of code. Three
additional warnings affect correctness at a lower priority. Two info items are stale comments and
a missing quality-of-life hook.

---

## Critical Issues

### CR-01: `_SESSION_REGISTRY` has no protecting lock — TOCTOU race in Phase 2

**File:** `agent_router/state.py:52`

**Issue:** The module-level `_SESSION_REGISTRY: dict[str, SessionState] = {}` dict has no
associated `threading.Lock`. The architecture specifies concurrent sessions (CAP-07). Phase 2
will implement a check-then-insert pattern in `TrajectoryTracker.__enter__`:

```python
# What Phase 2 will write (naively):
if session_id not in _SESSION_REGISTRY:
    _SESSION_REGISTRY[session_id] = SessionState(...)
```

Between the `not in` check and the `[session_id] =` assignment, another thread can insert the
same key. The race corrupts the registry silently — the second thread's `SessionState` overwrites
the first's, losing its window and cost log. The existing `_lock` on each `SessionState` protects
only the session's fields *after* it is inserted; it does not protect insertion itself.

CPython's GIL makes individual dict `__setitem__` calls atomic, but it does not make the
check-then-insert sequence atomic.

**Fix:** Export a registry-level lock alongside the registry so Phase 2 can use it:

```python
# agent_router/state.py
_SESSION_REGISTRY: dict[str, SessionState] = {}
_REGISTRY_LOCK: threading.Lock = threading.Lock()
```

Phase 2 then writes:
```python
with _REGISTRY_LOCK:
    if session_id not in _SESSION_REGISTRY:
        _SESSION_REGISTRY[session_id] = SessionState(...)
```

Without `_REGISTRY_LOCK` in the Phase 1 contract, Phase 2 either re-invents the lock in the
wrong module or ships the race.

---

### CR-02: `SessionState._lock` participates in `__eq__` — two identical sessions are never equal

**File:** `agent_router/state.py:47`

**Issue:** The `_lock` field is declared as:

```python
_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
```

`repr=False` suppresses `__repr__` output. But `compare` defaults to `True`, so `_lock` is
included in the auto-generated `__eq__` and `__hash__` (neither is excluded). Because
`threading.Lock` objects use identity-based equality (each `Lock()` is a unique object),
two `SessionState` instances with identical data fields are never equal to each other:

```python
s1 = SessionState(session_id="a", window=deque(maxlen=10), ...)
s2 = SessionState(session_id="a", window=deque(maxlen=10), ...)
assert s1 == s2  # FAILS — s1._lock is not s2._lock
```

This was verified empirically. Any Phase 2/3 code that deduplicates, compares, or caches
`SessionState` objects by value will silently fail. The `repr=False` marker suggests the author
intended to exclude the lock from display but missed `compare=False`.

**Fix:**

```python
_lock: threading.Lock = field(
    default_factory=threading.Lock,
    repr=False,
    compare=False,   # <-- add this
    hash=False,      # <-- and this (for completeness)
)
```

---

## Warnings

### WR-01: `TurnRecord` is declared frozen (hashable) but `tool_args: dict | None` makes `hash()` fail at runtime

**File:** `agent_router/state.py:18`

**Issue:** `@dataclass(frozen=True)` auto-generates `__hash__` by hashing all fields. The
`tool_args: dict | None` field is an unhashable type when non-`None`. Calling `hash(record)` on
any `TurnRecord` where `tool_args` is a populated dict raises `TypeError: unhashable type: 'dict'`
at runtime — confirmed empirically:

```
TypeError: unhashable type: 'dict'
```

Phase 3 (LoopVelocityProfiler) and any set/dict keyed on `TurnRecord` will crash on real tool
calls. The frozen contract implies the record is hashable, but it is not.

**Fix — two options:**

Option A (minimal): Change `tool_args` to an immutable type:
```python
tool_args: tuple[tuple[str, object], ...] | None  # sorted key-value pairs
```

Option B (pragmatic for Phase 1): Suppress `__hash__` generation and rely only on identity, or
exclude `tool_args` from hashing. The cleanest dataclass approach:
```python
@dataclass(frozen=True, unsafe_hash=False)
class TurnRecord:
    ...
    tool_args: frozenset[tuple[str, object]] | None  # or tuple
```

If `TurnRecord` is never used as a dict key or in a set (only appended to `deque`), Option B
is lower-risk. Either way the field type must signal its un-hashability or become hashable.

---

### WR-02: `DynamicRouteLM.__init__` accepts `api_key` but silently discards it

**File:** `agent_router/routing/dynamic_lm.py:25`

**Issue:** The constructor signature is:

```python
def __init__(
    self,
    session_id: str,
    router: str = "mf",
    routellm_base: str = "http://localhost:6060/v1",
    api_key: str = "",
    **kwargs: Any,
) -> None:
    model = f"openai/router-{router}-0.11593"
    super().__init__(model=model, **kwargs)
    self.session_id = session_id
    self.router = router
    self.routellm_base = routellm_base
    # api_key is never stored
```

`api_key` is an explicit named parameter (not in `**kwargs`), so it is consumed by this
constructor and never reaches `super().__init__`. It is also never assigned to `self.api_key`.
When Phase 4 builds the OpenAI client against the RouteLLM base URL, it will have no `api_key`
to pass. Callers who provide an `api_key` at construction time will be silently ignored.

This is a stub, so the parameter could be intentionally deferred — but without storing it, the
Phase 4 implementer has no signal that it needs to be threaded through. A silent drop in a public
constructor is a maintenance trap.

**Fix:**
```python
self.api_key = api_key  # stored for Phase 4 OpenAI client construction
```

---

### WR-03: `__init__.py` missing `__dir__` override — lazy names invisible to `dir()` and IDEs

**File:** `agent_router/__init__.py:6`

**Issue:** PEP 562 specifies that a companion `__dir__` override should return `__all__` so that
`dir(agent_router)` includes the lazily-loaded names before they are first accessed. Without it,
`TrajectoryTracker` and `DynamicRouteLM` are absent from `dir(agent_router)` until after their
first access. This was confirmed empirically:

```python
import agent_router
"TrajectoryTracker" in dir(agent_router)  # False (before first access)
```

IDEs using `dir()` for autocomplete, `help(agent_router)`, and any introspection tool that
enumerates module contents will miss these names. `RouterConfig` is visible (eagerly imported)
but the two lazy names are not — an inconsistent and surprising experience for library users.

**Fix:** Add to `__init__.py`:

```python
def __dir__() -> list[str]:
    return __all__ + [
        name for name in globals() if not name.startswith("_")
    ]
```

Or minimally:
```python
def __dir__() -> list[str]:
    return list(__all__)
```

---

## Info

### IN-01: Stale `Status: RED until Plan 04` comment in `test_imports.py`

**File:** `tests/unit/test_imports.py:7`

**Issue:** The module docstring says "RED until Plan 04 wires the lazy API surface correctly."
Plan 04 IS this phase — the lazy API surface is fully wired. The test is GREEN. The stale comment
will confuse future readers into thinking the test is expected to fail.

**Fix:** Update the comment to reflect current status:
```python
# Status: GREEN — lazy import surface wired in Phase 1 (test_public_api_import passes).
```

---

### IN-02: `test_router_config_fields` not isolated from pre-existing environment variables

**File:** `tests/unit/test_config.py:16`

**Issue:** `test_router_config_fields` calls `RouterConfig()` and then asserts
`cfg.weak_model == "openai/gpt-4o-mini"`. If `AGENT_ROUTER_WEAK_MODEL` is already set in the
test environment (CI, developer's shell), pydantic-settings will override the default and the
assertion fails. `test_router_config_env` does correctly save/restore the env var — but
`test_router_config_fields` does not clear env vars before asserting defaults.

This is not a bug in the production code, but it makes the test suite brittle in CI environments
that set model overrides.

**Fix:** Wrap the `RouterConfig()` call in a context that clears `AGENT_ROUTER_*` env vars, or
use `monkeypatch` (already available via pytest) to patch the environment:

```python
def test_router_config_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_ROUTER_WEAK_MODEL", raising=False)
    monkeypatch.delenv("AGENT_ROUTER_STRONG_MODEL", raising=False)
    # ... rest of test
```

---

_Reviewed: 2026-06-18_
_Reviewer: Claude (adversarial review)_
_Depth: deep_
