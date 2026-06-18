# Phase 2: State Capture Engine - Research

**Researched:** 2026-06-18
**Domain:** DSPy 3.2.1 callback system, ReAct loop mechanics, token-usage plumbing
**Confidence:** HIGH — all claims verified directly against installed source at
`~/.local/lib/python3.14/site-packages/dspy/`

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01 Callback registration:** `dspy.context(callbacks=<existing> + [tracker_cb])` inside
  `__enter__`. MUST NOT use `dspy.settings.configure(callbacks=...)` — that REPLACES existing
  callbacks (Pitfall P4). Read current list, append, use `dspy.context`.
- **D-02 Step indexing:** One `TurnRecord` per LM call via `on_lm_end`. Step count via per-session
  monotonic counter. 5-iteration ReAct must yield exactly 5 records (success criterion 1). Researcher
  to confirm exact filter rule against source. See §4 below.
- **D-03 Token usage:** NOT from `on_lm_end.outputs` (Pitfall P1). From LM history entry's `"usage"`
  dict OR `UsageTracker`. Cache hits set `cache_hit=True` rather than silently zero. Researcher to
  confirm exact field path. See §5 below.
- **D-04 Signature identity:** `signature.__name__` when meaningful; else class name + sorted input+
  output field names for inline `StringSignature` (Pitfall P2). See §6 below.
- **D-05 Session isolation:** One `TrajectoryTracker` per session; creates/looks up `SessionState`
  in `_SESSION_REGISTRY` under `_REGISTRY_LOCK`. Per-session mutations use `SessionState._lock`.
  ContextVar caveat documented. On `__exit__` remove from registry. See §7 below.
- **D-06 Failure capture:** `exception` arg on `*_end` hooks; handle `outputs=None` on exception
  (Pitfall P6). Failed step still produces a `TurnRecord`. See §8 below.

### Claude's Discretion

- Internal module layout (callback class in `tracker.py` vs a `capture/` submodule).
- How the tracker threads the live `SessionState` into the callback instance.
- Test-double LM design.

### Deferred Ideas (OUT OF SCOPE)

- Background-thread scoring (PERF-01) and zero-dep fallback (PERF-02).
- Actual scoring/detectors (Phase 3).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CAP-01 | `with TrajectoryTracker(session_id=...):` wraps agent without touching agent code | Context manager shell exists in `tracker.py`; `__enter__`/`__exit__` to be fleshed out |
| CAP-02 | Callbacks registered without clobbering existing ones | §3: `dspy.context()` verified; `settings.context()` reads + extends without replacing |
| CAP-03 | Signature identity distinguishes inline `StringSignature` instances | §6: `input_fields.keys()` + `output_fields.keys()` are stable props on every sig class |
| CAP-04 | Step index is correct; ReAct overcount does not happen | §4: `on_lm_end`-based counting + `self.extract` exclusion rule; exactly N records for N iters |
| CAP-05 | Token counts non-zero; cache hits flagged | §5: `lm.history[-1]["usage"]` dict; `cache_hit` field set by cache layer |
| CAP-06 | Success/failure per step from `exception` arg | §8: confirmed `exception` arg on every `*_end`; `outputs=None` on exception path |
| CAP-07 | Per-session isolation; concurrent runs don't collide | §7: one callback instance per tracker; session-keyed state; ContextVar threading caveat |
</phase_requirements>

---

## Summary

Phase 2 implements `TrajectoryTracker` — a context manager that silently instruments any DSPy
program via the `BaseCallback` system, writing `TurnRecord` entries into `SessionState.window`
for the scoring engine in Phase 3. The six decision points in CONTEXT.md are now fully resolved
against the installed dspy 3.2.1 source:

1. `dspy.context()` is the correct, scope-restoring registration path (§3).
2. Counting `on_lm_end` fires (not `on_module_start`) gives exactly N records for an N-iteration
   ReAct, provided the final `self.extract` call is excluded via a sentinel flag (§4).
3. Token usage lives in `lm.history[-1]["usage"]` as `{"prompt_tokens": int, "completion_tokens": int}`;
   cache hits surface via `response.cache_hit = True` and `response.usage = {}` (§5).
4. Signature identity uses `signature.__name__` for real classes and a derived key of
   `f"{cls_name}:{','.join(sorted(input_keys))}>{','.join(sorted(output_keys))}"` for
   `StringSignature` (§6).
5. Session isolation: bind one callback instance per tracker instance; never share across sessions
   (§7). The `ContextVar` threading caveat is documented; step counter uses `SessionState._lock`.
6. Exception path: `outputs=None` is a valid state; guard before accessing (§8).

**Primary recommendation:** Use `on_lm_end` as the sole trigger for `TurnRecord` creation, with
a per-tracker `_in_extract: bool` sentinel flag that is set just before `self.extract` is called
and cleared after, so the extract LM call is skipped from counting.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Callback registration/teardown | `TrajectoryTracker.__enter__/__exit__` | — | Context manager owns DSPy settings scope |
| LM call interception | `BaseCallback.on_lm_end` | `on_lm_start` (for parent_call_id) | Only hook that fires once per completed LM inference |
| Token usage extraction | `BaseLM.history[-1]` (post-call) | `UsageTracker` (aggregate) | History entry is written inside `_process_lm_response` before callback fires |
| Signature identity derivation | `TrajectoryCallback.on_lm_end` via `instance.signature` | — | `instance` in `on_lm_end` is the `LM`, not the Predict; see §6 for correct access path |
| Step counter | `SessionState` + `_lock` | — | Per-session state; not a ContextVar |
| Session registry | `_SESSION_REGISTRY` + `_REGISTRY_LOCK` | — | Phase 1 contract; unchanged |
| `TurnRecord` creation | `TrajectoryCallback.on_lm_end` | — | Single point of truth |
| `SessionState.window` append | `TrajectoryCallback.on_lm_end` | — | Phase 3 reads this window |

---

## Standard Stack

### Core (all already installed — no new packages)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| dspy | 3.2.1 | Callback hooks + ReAct + Predict | Already installed; `BaseCallback` at `dspy/utils/callback.py` |
| Python stdlib `threading` | 3.14 | `Lock` for `SessionState._lock` and `_REGISTRY_LOCK` | Already used in Phase 1 contracts |
| Python stdlib `contextvars` | 3.14 | `ACTIVE_CALL_ID` (read-only for parent_call_id) | DSPy-internal; read but not mutated |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `copy.deepcopy` | stdlib | Safe snapshot of `inputs` dict in callbacks | Any time input dict must be stored; never store by reference (Pitfall P5) |
| pytest | 9.1.0 | Unit tests with mock LM | All tests; no live API calls in Phase 2 |
| pytest-asyncio | 1.4.0 | Async callback path tests | If async agent paths are tested |

**Installation:** No new packages needed for Phase 2. All dependencies were installed in Phase 1.

---

## Package Legitimacy Audit

No new external packages are introduced in Phase 2. All production code uses dspy 3.2.1 (already
installed) and Python stdlib. The test suite uses pytest 9.1.0 (already installed). No audit
needed.

---

## Architecture Patterns

### System Architecture Diagram

```
Developer code
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  with TrajectoryTracker(session_id="s1") as tracker:    │
│      agent(question="...")                              │
└──────────────┬──────────────────────────────────────────┘
               │  __enter__: dspy.context(callbacks=existing+[cb])
               │  _SESSION_REGISTRY["s1"] = SessionState(...)
               ▼
┌─────────────────────────────────────────────────────────┐
│  dspy.ReAct.forward()                                   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  for idx in range(max_iters):                    │   │
│  │      self.react(...)  ──→ Predict.forward()      │   │
│  │           └──→ BaseLM.__call__()  ─→ on_lm_end  │   │◄─── fires N times
│  │      self.tools[tool_name](...)                  │   │
│  │  self.extract(...)  ──→ ChainOfThought.forward() │   │
│  │       └──→ BaseLM.__call__()  ─→ on_lm_end      │   │◄─── excluded by sentinel
│  └──────────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
            TrajectoryCallback.on_lm_end
                       │
              if _in_extract: return  ← sentinel
                       │
              step_idx = atomic_increment(session)
              usage = lm.history[-1]["usage"]
              cache_hit = getattr(response, "cache_hit", False)
              record = TurnRecord(...)
              session.window.append(record)
                       │
                       ▼
            SessionState.window  (deque[TurnRecord])
                       │
                       ▼  (Phase 3 reads this)
            ScoringEngine
```

### Recommended Project Structure

```
agent_router/
├── tracker.py          # TrajectoryTracker (flesh out from Phase 1 stub)
├── capture.py          # TrajectoryCallback(BaseCallback) — may live here or inline in tracker.py
├── state.py            # Phase 1 contracts (unchanged)
└── __init__.py         # public API
tests/
└── unit/
    ├── test_tracker.py          # CAP-01..CAP-07 acceptance tests
    └── conftest.py              # DummyLM + mock DSPy setup
```

### Pattern 1: Callback Registration via `dspy.context` (D-01 / CAP-02)

**What:** Scope-restoring callback injection that preserves existing callbacks.

**Source verified:** `dspy/dsp/utils/settings.py` lines 216–257 [VERIFIED: installed source]

```python
# In TrajectoryTracker.__enter__:
def __enter__(self) -> "TrajectoryTracker":
    # 1. Create or look up session under registry lock (Phase 1 TOCTOU fix)
    with _REGISTRY_LOCK:
        if self.session_id not in _SESSION_REGISTRY:
            _SESSION_REGISTRY[self.session_id] = SessionState(
                session_id=self.session_id,
                window=deque(maxlen=self.config.window_size if self.config else 50),
                current_threshold=1.0,
                escalation_count=0,
                cost_log=[],
            )
        self._session = _SESSION_REGISTRY[self.session_id]

    # 2. Build callback preserving existing ones — NEVER use dspy.configure()
    existing = dspy.settings.get("callbacks", [])  # may be [] at init
    self._callback = TrajectoryCallback(session=self._session)
    # dspy.context returns a contextmanager; we enter it manually
    self._ctx = dspy.context(callbacks=existing + [self._callback])
    self._ctx.__enter__()
    return self
```

**How `dspy.context` restores state on exit** [VERIFIED: installed source]:
```python
# settings.py lines 250-257 (annotated):
def context(self, **kwargs):
    original_overrides = thread_local_overrides.get().copy()
    new_overrides = dotdict({**main_thread_config, **original_overrides, **kwargs})
    token = thread_local_overrides.set(new_overrides)   # ContextVar token
    try:
        yield
    finally:
        thread_local_overrides.reset(token)  # EXACT restore — no list diff needed
```

The `ContextVar.reset(token)` call restores the overrides to exactly what they were before the
`dspy.context(...)` block was entered. This is NOT a list-diff operation; it is a full snapshot
restore. Pre-existing callbacks (Langfuse, W&B) are alive before our `dspy.context(...)` block and
are restored exactly on `__exit__`. Inside our block, they remain because `new_overrides` is built
from `{**main_thread_config, **original_overrides, **kwargs}` — our new `callbacks` value includes
them.

**`__exit__` pattern:**
```python
def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    self._ctx.__exit__(exc_type, exc_val, exc_tb)  # restores callbacks via ContextVar.reset
    with _REGISTRY_LOCK:
        _SESSION_REGISTRY.pop(self.session_id, None)  # prevent unbounded growth (Phase 1 TODO)
```

### Pattern 2: One `TurnRecord` per LM Call — ReAct Overcount Solution (D-02 / CAP-04)

**The definitive ReAct callback fire sequence for N iterations** [VERIFIED: react.py, callback.py]:

For a 5-iteration `dspy.ReAct` (where iterations terminate via `finish` tool at iter 4):

```
on_module_start  ReAct instance (outer)                     ← 1 fire, episode-level
  on_module_start  Predict("react_sig") iter 0              ← 1 fire/iter × 5 = 5
    on_lm_start    LM                                       ← 1 fire/iter × 5 = 5
    on_lm_end      LM                                       ← 1 fire/iter × 5 = 5  ← EMIT RECORD
  on_module_end  Predict iter 0
  on_tool_start  Tool                                       ← 1 fire/iter × 5 = 5
  on_tool_end    Tool
  ... (iters 1–4 same pattern)
  on_module_start  ChainOfThought("extract_sig")            ← 1 fire, end of episode
    on_lm_start    LM                                       ← 1 fire
    on_lm_end      LM                                       ← 1 fire  ← DO NOT EMIT
  on_module_end  ChainOfThought
on_module_end  ReAct instance
```

**Total `on_lm_end` fires:** N + 1 (N react steps + 1 extract step).

**Why module-type filtering is the correct approach:**

Option A (call_id nesting depth): `ACTIVE_CALL_ID` in `callback.py` is set to the current call's
id and the parent is saved as `parent_call_id`. However, when `on_lm_end` fires, `ACTIVE_CALL_ID`
has already been reset to `parent_call_id` (see `sync_wrapper` lines 344–346). This means inside
`on_lm_end`, the current `ACTIVE_CALL_ID` is the parent Predict's call_id, not the LM's. Depth
tracking from `on_lm_end` alone is therefore unreliable without maintaining a call-stack in the
callback.

Option B (module-type filter on `on_module_start`): Does not help for `on_lm_end` since `instance`
in `on_lm_end` is always a `BaseLM`, regardless of which `Predict` called it.

**Option C (sentinel flag — RECOMMENDED):** A `_in_extract` boolean on the callback instance is
set to `True` just before `self.extract` would fire its `on_lm_end`, and back to `False` after.

The practical way to implement this without patching ReAct: hook into `on_module_start` to detect
when the `ChainOfThought` (the extract module) is being entered, and set the sentinel:

```python
class TrajectoryCallback(BaseCallback):
    def __init__(self, session: SessionState) -> None:
        self._session = session
        self._in_extract = False
        self._react_extract_id: str | None = None  # call_id of the extract module call

    def on_module_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        # Detect the extract call: it is a ChainOfThought whose parent ACTIVE_CALL_ID
        # is the ReAct outer call_id. Since we want zero-dependency, we use a simpler
        # heuristic: the extract Predict fires AFTER all on_tool_end events for this
        # episode. Set sentinel on ANY ChainOfThought after at least one tool has fired.
        #
        # Simpler and fully correct: track call_ids of all react-step Predicts. The
        # extract is the one ChainOfThought whose on_module_start fires AFTER the
        # last on_tool_end in the episode.
        #
        # Practical implementation: mark ANY on_lm_end where the immediately preceding
        # on_module_start was for a module named "ChainOfThought" or where the signature
        # has "trajectory" as an input field (extract sig always has it).
        from dspy.predict.chain_of_thought import ChainOfThought
        if isinstance(instance, ChainOfThought):
            # Check if this signature has the output fields of the original signature
            # (not the react_signature fields). Simpler: just flag the sentinel.
            self._in_extract = True
            self._react_extract_id = call_id

    def on_module_end(self, call_id: str, outputs: Any | None, exception: Exception | None = None) -> None:
        if call_id == self._react_extract_id:
            self._in_extract = False
            self._react_extract_id = None

    def on_lm_end(self, call_id: str, outputs: Any | None, exception: Exception | None = None) -> None:
        if self._in_extract:
            return  # skip the trailing extract LM call

        # --- emit TurnRecord ---
        ...
```

**Caveat on the ChainOfThought heuristic:** The extract module is a `ChainOfThought`; the react
step module is a `Predict`. In a ReAct run there will be exactly one `ChainOfThought.on_module_start`
per episode (the extract). If the user's own agent also uses `ChainOfThought` inside tools or custom
sub-modules, this heuristic may misfire.

**More robust alternative:** Track the `call_id` from the outer `ReAct.on_module_start`. Any
`on_lm_end` that fires while `ACTIVE_CALL_ID.get()` matches the outer ReAct call_id at the time
`on_lm_end` is dispatched is a react-step call; the extract call fires at the same nesting level
but after `on_tool_end` has not been seen for that iteration. For production robustness, the
safest implementation is:

```python
# At on_module_start for any Module:
#   - Record that we saw a ChainOfThought after the last tool_end
# At on_lm_end:
#   - If _in_extract: skip
```

This gives correct counts for all standard ReAct usages. The planner should verify with the 5-iter
test (success criterion 1) and adjust if the user's agent nests ChainOfThought inside tools.

**Step counter increment** (thread-safe):
```python
with self._session._lock:
    step_idx = self._session._step_counter  # add _step_counter: int = 0 to SessionState
    self._session._step_counter += 1
```

Note: `_step_counter` is not in the Phase 1 `SessionState` dataclass. The planner must add it, or
use `len(self._session.window)` as the step_idx (which is equivalent since window is appended once
per step).

### Pattern 3: Token Usage Extraction (D-03 / CAP-05)

**Where usage lives** [VERIFIED: base_lm.py lines 103–116, cache.py lines 149–155]:

`BaseLM._process_lm_response()` writes the history entry **before** returning `outputs` to the
caller of `__call__`. By the time `on_lm_end` fires (in the `finally` block of `sync_wrapper`,
after `results = fn(instance, ...)` returns), the history entry is already committed.

```python
# base_lm.py _process_lm_response — history entry structure:
entry = {
    "prompt": prompt,
    "messages": messages,
    "kwargs": kwargs,
    "response": response,          # the raw response object
    "outputs": outputs,            # processed text — NOT usage (Pitfall P1)
    "usage": dict(getattr(response, "usage", {})),   # ← token counts live here
    "cost": getattr(response, "_hidden_params", {}).get("response_cost"),
    "timestamp": ...,
    "uuid": ...,
    "model": self.model,
    "response_model": response.model,
    "model_type": self.model_type,
}
self.update_history(entry)
```

**Cache hit path** [VERIFIED: cache.py lines 149–155]:
```python
# Cache._prepare_cached_response — fires on cache hit:
def _prepare_cached_response(self, response):
    response = copy.deepcopy(response)
    if hasattr(response, "usage"):
        response.usage = {}        # cleared — so history["usage"] will be {}
        response.cache_hit = True  # ← set here
    return response
```

**Reading usage in `on_lm_end`:**
```python
def on_lm_end(self, call_id: str, outputs: Any | None, exception: Exception | None = None) -> None:
    if self._in_extract:
        return

    lm: BaseLM = ...  # need reference — see §7 for how to get it
    if not lm.history:
        return  # safety: should not happen unless history disabled

    entry = lm.history[-1]
    usage = entry.get("usage", {})
    is_cache_hit = getattr(entry.get("response"), "cache_hit", False)

    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
```

**Problem:** `on_lm_end` receives `call_id`, `outputs`, `exception` — it does NOT receive the `lm`
instance directly. The `instance` is passed to `on_lm_start`, not `on_lm_end`. Solution: capture
the `lm` reference in `on_lm_start` keyed by `call_id`:

```python
def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
    self._pending_lm[call_id] = instance  # instance is the BaseLM subclass

def on_lm_end(self, call_id: str, outputs: Any | None, exception: Exception | None = None) -> None:
    lm = self._pending_lm.pop(call_id, None)
    if lm is None or self._in_extract:
        return
    entry = lm.history[-1] if lm.history else {}
    usage = entry.get("usage", {})
    is_cache_hit = bool(getattr(entry.get("response"), "cache_hit", False))
    ...
```

**Usage dict keys** [VERIFIED: UsageTracker.add_usage + OpenAI response format]:
The keys come from LiteLLM's OpenAI-compatible response `.usage` object coerced to a dict:
- `prompt_tokens`: int (0 on cache hit — usage cleared to `{}`)
- `completion_tokens`: int (0 on cache hit)
- Additional keys may be present (`total_tokens`, `prompt_tokens_details`) — ignored by the tracker.

**Cache hit `cache_hit` flag source:** Set on the response object by
`Cache._prepare_cached_response()` at line 154. It is then accessible via
`entry["response"].cache_hit` after `_process_lm_response` stores the response in the history
entry. `getattr(entry["response"], "cache_hit", False)` is the safe read pattern.

### Pattern 4: Signature Identity (D-04 / CAP-03)

**Field access** [VERIFIED: signature.py lines 231–249]:
```python
# SignatureMeta properties (accessible on any Signature class):
sig = instance.signature  # on a Predict; the sig class, not an instance
sig.__name__              # "StringSignature" for inline / named class for explicit
sig.input_fields          # dict[str, FieldInfo] — keys are field names
sig.output_fields         # dict[str, FieldInfo] — keys are field names
```

**Identity derivation:**
```python
def _signature_name(sig) -> str:
    if sig.__name__ != "StringSignature":
        return sig.__name__
    # Inline signature: build stable identity from field names
    in_keys = sorted(sig.input_fields.keys())
    out_keys = sorted(sig.output_fields.keys())
    return f"StringSignature:{','.join(in_keys)}>{','.join(out_keys)}"
```

**Where to call this:** The callback receives the `lm` instance in `on_lm_start`, not the `Predict`
instance. The `Predict` instance is available via `on_module_start`. To connect them:

- In `on_module_start`: capture `instance.signature` for the current module keyed by
  `ACTIVE_CALL_ID.get()` (which is the parent's call_id at that point — see §4 caveat).
- OR: capture `instance.signature` keyed by `call_id` from `on_module_start` and then correlate
  to the downstream `on_lm_start` via the `ACTIVE_CALL_ID` nesting.

**Simpler approach (recommended):** In `on_module_start`, store
`self._active_signature = _signature_name(instance.signature)` as a simple last-seen field on the
callback. Since DSPy calls modules sequentially (each Predict calls its LM synchronously before the
next Predict starts), the last seen signature in `on_module_start` matches the next `on_lm_start`
in single-threaded operation. This is fragile with async agents; for async, use the call_id
correlation approach.

### Pattern 5: Session Binding & Concurrency (D-05 / CAP-07)

**The ContextVar threading problem** [VERIFIED: callback.py line 10, settings.py lines 48]:
```python
# callback.py:
ACTIVE_CALL_ID = ContextVar("active_call_id", default=None)
# settings.py:
thread_local_overrides = ContextVar("context_overrides", default=dotdict())
```

`ContextVar` values ARE copied to child async Tasks (Python 3.7+) but are NOT inherited by
`threading.Thread` spawned directly. A `ThreadPoolExecutor`-based parallel agent run will see
`thread_local_overrides` as the default empty `dotdict()`, meaning `dspy.settings.callbacks` will
return `[]` in the child thread — the tracker callback will not fire at all.

**Solution for CAP-07 (concurrent sessions, no bleed):**

Bind the `TrajectoryCallback` to its session by reference — it holds a direct Python reference to
the `SessionState` object, not a lookup-by-name. Since `_SESSION_REGISTRY["s1"]` and
`_SESSION_REGISTRY["s2"]` are different objects, two concurrent tracker instances can never write
to each other's windows, even if their callbacks run concurrently:

```python
class TrajectoryCallback(BaseCallback):
    def __init__(self, session: SessionState) -> None:
        self._session = session  # direct ref — isolation is by object identity
        self._pending_lm: dict[str, Any] = {}  # call_id → LM instance
        self._in_extract = False
        self._react_extract_id: str | None = None
```

**Step counter thread safety:** Use `SessionState._lock`:
```python
with self._session._lock:
    step_idx = len(self._session.window)  # next index
    # ...build TurnRecord...
    self._session.window.append(record)  # append inside the lock
```

**ContextVar caveat documentation (for Phase 2 code comments):**
> `TrajectoryTracker` registers its callback via `dspy.context(callbacks=...)`. This uses a
> `ContextVar` for thread-local override storage. Child threads spawned with `threading.Thread`
> do NOT inherit `ContextVar` values and will not fire the tracker callback. This affects users who
> run DSPy calls from a `ThreadPoolExecutor` or `dspy.Evaluate` with thread-based parallelism.
> Workaround: use `copy_context().run()` when spawning threads, or avoid thread-based parallelism
> inside a `TrajectoryTracker` context. Async (`asyncio`) is not affected — Tasks inherit context.

### Pattern 6: Exception Capture (D-06 / CAP-06)

**Confirmed `exception` arg** [VERIFIED: callback.py lines 82–95, 113–127]:
```python
# BaseCallback interface:
def on_lm_end(self, call_id: str, outputs: dict[str, Any] | None,
              exception: Exception | None = None):
    ...
def on_module_end(self, call_id: str, outputs: Any | None,
                  exception: Exception | None = None):
    ...
```

**How exceptions flow** [VERIFIED: callback.py sync_wrapper lines 333–346]:
```python
results = None
exception = None
try:
    results = fn(instance, *args, **kwargs)
    return results
except Exception as e:
    exception = e
    raise exception  # re-raises — caller sees the exception normally
finally:
    ACTIVE_CALL_ID.set(parent_call_id)
    _execute_end_callbacks(instance, fn, call_id, results, exception, callbacks)
```

When an LM call raises, `results` is `None` and `exception` is the caught exception. The end
callback fires in `finally` with `outputs=None, exception=<the error>`. The exception is
re-raised after the callback completes.

**TurnRecord for failed steps:**
```python
def on_lm_end(self, call_id: str, outputs: Any | None, exception: Exception | None = None) -> None:
    lm = self._pending_lm.pop(call_id, None)
    if lm is None or self._in_extract:
        return

    # Safe usage extraction — outputs is None on exception
    entry = lm.history[-1] if lm.history else {}
    usage = entry.get("usage", {})
    is_cache_hit = bool(getattr(entry.get("response"), "cache_hit", False))

    with self._session._lock:
        step_idx = len(self._session.window)
        record = TurnRecord(
            call_id=call_id,
            step_idx=step_idx,
            signature_name=self._active_signature or "unknown",
            tool_name=None,     # LM-level; tool context captured separately if needed
            tool_args=None,
            input_token_count=usage.get("prompt_tokens", 0),
            output_token_count=usage.get("completion_tokens", 0),
            output_text=outputs[0] if isinstance(outputs, list) and outputs else None,
            cache_hit=is_cache_hit,
            exception=exception,  # None on success, Exception on failure
        )
        self._session.window.append(record)
```

### Anti-Patterns to Avoid

- **Using `dspy.configure(callbacks=[...])` in `__enter__`:** Replaces all existing callbacks
  globally and permanently (until next configure call). Use `dspy.context()`. [VERIFIED: P4]
- **Reading `on_lm_end.outputs` for token counts:** `outputs` is a `list[str | dict]` of decoded
  text. Usage was consumed before the callback fired. [VERIFIED: P1]
- **Counting `on_module_start` for step index:** Fires for outer ReAct + each inner Predict +
  each extract = 2N+1 for N iterations. [VERIFIED: P7]
- **Storing `inputs` dict by reference:** The dict is the live arguments dict passed to the
  function. Mutation corrupts the call. Always extract scalars or deepcopy. [VERIFIED: P5]
- **Sharing a callback instance across sessions:** The callback holds a `SessionState` reference.
  Two trackers must use two callback instances. Never re-use.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Scoped settings override | Manual backup/restore of `dspy.settings.callbacks` | `dspy.context(callbacks=...)` | ContextVar-based; atomic restore even on exception in `__exit__` |
| Token counting from message text | Tiktoken-based tokenizer to count message chars | `lm.history[-1]["usage"]` | LiteLLM returns exact billed token counts from the provider; no approximation needed |
| Thread-safe counter | `threading.local()` counter | `len(session.window)` inside `session._lock` | Window length IS the step count by construction |
| Exception detection | Try/except wrapping LM calls | `on_lm_end(exception=...)` | DSPy callback already captures the exception; wrapping LM calls is the Strategy B anti-pattern |

**Key insight:** The callback system is purpose-built for non-intrusive telemetry. Any approach that
wraps, patches, or subclasses `dspy.LM` for telemetry is Strategy B (explicitly listed as wrong in
`research-dspy-routellm.md §A.2` and CLAUDE.md).

---

## Common Pitfalls

### Pitfall P1 (Confirmed): `on_lm_end.outputs` Has No Token Data

**What goes wrong:** `outputs` in `on_lm_end` is the return value of `BaseLM.__call__`, which is
the return of `_process_lm_response()` — a `list[str]` or `list[dict]` of decoded text. Usage was
stored in `lm.history[-1]["usage"]` and sent to `UsageTracker` inside `_process_lm_response`,
before `outputs` was returned.

**Source path:** `base_lm.py:__call__` → `_process_lm_response()` → `update_history(entry)` →
`return outputs`. The `@with_callbacks` decorator wraps `__call__`; `on_lm_end` fires in the
`finally` block with `results = outputs` (the text list).

**Prevention:** Read `lm.history[-1]["usage"]` in `on_lm_end`, after capturing `lm` from
`on_lm_start`.

### Pitfall P4 (Confirmed): `dspy.configure` Thread Restriction

**Additional source detail** [VERIFIED: settings.py lines 117–163]: `configure()` calls
`_ensure_configure_allowed()` which checks `config_owner_thread_id`. Only the thread that first
called `configure` may call it again. Any worker thread that tries `dspy.configure(callbacks=...)`
will raise `RuntimeError: dspy.settings can only be changed by the thread that initially configured
it.` This makes `dspy.configure` doubly wrong: it replaces callbacks AND it will crash if called
from a non-owner thread.

**Prevention:** `dspy.context()` is unrestricted — any thread can call it.

### Pitfall P7 (Confirmed): ReAct Callback Fire Count

For a 5-iteration ReAct with 5 `finish` not triggered until iter 4:
- `on_lm_end` fires exactly **6 times** (5 react + 1 extract)
- Without the sentinel, step_idx reaches 5 instead of stopping at 4.
- The success criterion requires step_idx values `[0, 1, 2, 3, 4]` — 5 records, max index = 4.

### Pitfall NEW: `lm.history` may lag under history-disabled settings

If `dspy.settings.disable_history = True`, `update_history` is a no-op and `lm.history` stays `[]`.
In that case `lm.history[-1]` raises `IndexError`. Guard: `if lm.history and not
dspy.settings.disable_history`. For Phase 2 tests, ensure history is enabled (default: enabled).

### Pitfall NEW: `on_module_start` fires before `on_lm_start` — signature is available earlier

The execution order within one Predict call:
1. `on_module_start(instance=Predict, ...)` — signature accessible here
2. `on_lm_start(instance=LM, ...)` — LM accessible here
3. (LM call happens)
4. `on_lm_end(instance=LM, ...)` — outputs/exception here

The signature must be captured in step 1 and used in step 4. A simple `_active_signature` field
on the callback is correct for single-threaded agents. For concurrent async agents, use a
`call_id → signature_name` dict populated in step 1 and consumed in step 4 via the common
`ACTIVE_CALL_ID` nesting.

---

## Code Examples

### Complete `TrajectoryCallback` skeleton

```python
# agent_router/capture.py (or inline in tracker.py)
# Source: verified against dspy 3.2.1 installed source
from __future__ import annotations

import copy
from typing import Any

import dspy
from dspy.utils.callback import BaseCallback, ACTIVE_CALL_ID

from agent_router.state import TurnRecord, SessionState


def _derive_signature_name(sig: Any) -> str:
    """Stable identity for any Signature class, including inline StringSignature."""
    name = getattr(sig, "__name__", "unknown")
    if name != "StringSignature":
        return name
    # Inline sig: build from sorted field names
    in_keys = sorted(getattr(sig, "input_fields", {}).keys())
    out_keys = sorted(getattr(sig, "output_fields", {}).keys())
    return f"StringSignature:{','.join(in_keys)}>{','.join(out_keys)}"


class TrajectoryCallback(BaseCallback):
    def __init__(self, session: SessionState) -> None:
        self._session = session
        self._pending_lm: dict[str, Any] = {}       # call_id → BaseLM instance
        self._active_signature: str = "unknown"     # last seen Predict sig
        self._in_extract: bool = False
        self._react_extract_id: str | None = None

    def on_module_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        # Capture signature for the next LM call
        sig = getattr(instance, "signature", None)
        if sig is not None:
            self._active_signature = _derive_signature_name(sig)

        # Detect extract ChainOfThought — it's a ChainOfThought, always the last module
        # in a ReAct episode. Flag it so on_lm_end can skip it.
        try:
            from dspy.predict.chain_of_thought import ChainOfThought
            if isinstance(instance, ChainOfThought):
                self._in_extract = True
                self._react_extract_id = call_id
        except ImportError:
            pass

    def on_module_end(self, call_id: str, outputs: Any | None,
                      exception: Exception | None = None) -> None:
        if call_id == self._react_extract_id:
            self._in_extract = False
            self._react_extract_id = None

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        # Store LM ref for on_lm_end (which does not receive instance)
        self._pending_lm[call_id] = instance

    def on_lm_end(self, call_id: str, outputs: Any | None,
                  exception: Exception | None = None) -> None:
        lm = self._pending_lm.pop(call_id, None)
        if lm is None or self._in_extract:
            return

        # Read usage from history — written by _process_lm_response BEFORE this fires
        entry = lm.history[-1] if lm.history else {}
        usage = entry.get("usage", {}) if entry else {}
        response = entry.get("response") if entry else None
        is_cache_hit = bool(getattr(response, "cache_hit", False))

        # Extract output text safely (outputs is None on exception)
        output_text: str | None = None
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, str):
                output_text = first
            elif isinstance(first, dict):
                output_text = first.get("text")

        with self._session._lock:
            step_idx = len(self._session.window)
            record = TurnRecord(
                call_id=call_id,
                step_idx=step_idx,
                signature_name=self._active_signature,
                tool_name=None,
                tool_args=None,
                input_token_count=usage.get("prompt_tokens", 0),
                output_token_count=usage.get("completion_tokens", 0),
                output_text=output_text,
                cache_hit=is_cache_hit,
                exception=exception,
            )
            self._session.window.append(record)
```

### DummyLM for tests (no network)

```python
# tests/unit/conftest.py
# Pattern: subclass BaseLM, return a fake response with usage populated
import dspy
from dspy.clients.base_lm import BaseLM


class FakeResponse:
    """Mimics the LiteLLM response object expected by _process_lm_response."""
    class Usage:
        def __init__(self, prompt, completion):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
        def __iter__(self):
            return iter([("prompt_tokens", self.prompt_tokens),
                         ("completion_tokens", self.completion_tokens)])

    def __init__(self, text: str, prompt_tokens: int = 10, completion_tokens: int = 5):
        self.choices = [type("Choice", (), {
            "message": type("Msg", (), {"content": text, "tool_calls": None})(),
            "finish_reason": "stop",
        })()]
        self.usage = self.Usage(prompt_tokens, completion_tokens)
        self.model = "dummy"
        self._hidden_params = {"response_cost": 0.0001}


class DummyLM(BaseLM):
    def __init__(self, responses: list[str]):
        super().__init__(model="dummy", cache=False)
        self._responses = iter(responses)

    def forward(self, prompt=None, messages=None, **kwargs):
        text = next(self._responses, "finish")
        return FakeResponse(text)
```

---

## Runtime State Inventory

SKIPPED — This is a greenfield implementation phase, not a rename/refactor/migration phase. No
runtime state exists to audit.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.1.0 |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/unit/test_tracker.py -x -q` |
| Full suite command | `pytest tests/unit/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| CAP-01 | `with TrajectoryTracker(...)` wraps agent | smoke | `pytest tests/unit/test_tracker.py::test_context_manager_api -x` | No — Wave 0 |
| CAP-02 | Pre-existing callback still fires inside context | unit | `pytest tests/unit/test_tracker.py::test_callback_preservation -x` | No — Wave 0 |
| CAP-03 | `signature_name` != `"StringSignature"` for inline sigs | unit | `pytest tests/unit/test_tracker.py::test_signature_identity -x` | No — Wave 0 |
| CAP-04 | 5-iter ReAct yields exactly 5 TurnRecords | unit | `pytest tests/unit/test_tracker.py::test_react_step_count -x` | No — Wave 0 |
| CAP-05 | Token counts non-zero; cache hit flagged | unit | `pytest tests/unit/test_tracker.py::test_token_counts -x` | No — Wave 0 |
| CAP-06 | Failed step produces TurnRecord with exception | unit | `pytest tests/unit/test_tracker.py::test_exception_capture -x` | No — Wave 0 |
| CAP-07 | Two concurrent sessions no bleed | unit | `pytest tests/unit/test_tracker.py::test_concurrent_isolation -x` | No — Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/test_tracker.py -x -q`
- **Per wave merge:** `pytest tests/unit/ -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_tracker.py` — covers all 7 CAP requirements
- [ ] `tests/unit/conftest.py` — `DummyLM`, `DummyReActTool`, fixture for pre-existing callback
- [ ] `agent_router/capture.py` (or `tracker.py` — planner decides layout) — `TrajectoryCallback`

---

## Security Domain

Phase 2 handles in-process telemetry only — no network calls, no user input to external systems,
no auth surface. ASVS assessment:

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth surface |
| V3 Session Management | Partial | `session_id` is caller-supplied; no validation needed in Phase 2 (no external surface) |
| V4 Access Control | No | Library code; no multi-user access control |
| V5 Input Validation | Yes | `session_id` should be non-empty string; `config` fields validated by pydantic (Phase 1 `RouterConfig`) |
| V6 Cryptography | No | No secrets or encryption in Phase 2 |

**Threat patterns for in-process telemetry:**

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Callback input mutation | Tampering | `copy.deepcopy(inputs)` or extract scalars only (Pitfall P5) |
| Unbounded `_pending_lm` dict growth | DoS | `_pending_lm.pop(call_id, None)` in `on_lm_end` (shown in code above); if `on_lm_end` never fires (crash before callback), add a bounded LRU or TTL |
| `_SESSION_REGISTRY` unbounded growth | DoS | `__exit__` must call `_SESSION_REGISTRY.pop(session_id, None)` under `_REGISTRY_LOCK` (Phase 1 TODO, now mandatory) |
| Sensitive prompt text in TurnRecord | Info disclosure | `output_text` stores LM output — caller's responsibility; library stores what DSPy produces; document in API docstring |

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| dspy | Callbacks + ReAct | Yes | 3.2.1 | — |
| Python threading stdlib | Session locking | Yes | 3.14 | — |
| pytest | Unit tests | Yes | 9.1.0 | — |
| pytest-asyncio | Async tests | Yes | 1.4.0 | — |

No missing dependencies. Phase 2 is fully executable without new installs.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `ChainOfThought` is always the extract module in ReAct (never used for react steps) | §4 extract sentinel | If user passes a custom extract class, sentinel misses the extract; step count off by 1 |
| A2 | `lm.history[-1]` is the entry for the call that just completed | §5 usage path | If LM is shared across concurrent threads without lock, another thread could append first; only affects concurrent use of same LM instance |
| A3 | `response.cache_hit` attribute is always present on cache hits | §5 cache detection | If LiteLLM/DSPy cache layer changes the attribute name, `getattr(..., False)` returns False (safe default, just misclassifies cache hits as non-hits) |

---

## Open Questions

1. **`_step_counter` vs `len(window)` for step_idx**
   - What we know: `SessionState` from Phase 1 has no `_step_counter` field; window is a `deque`.
   - What's unclear: Should the planner add `_step_counter: int = 0` to `SessionState`, or is
     `len(session.window)` sufficient? They are equivalent IF every window append corresponds to
     exactly one step.
   - Recommendation: Use `len(session.window)` inside the `_lock` to keep the dataclass unchanged.
     Add `_step_counter` only if out-of-window step tracking is needed (e.g., for step_idx to grow
     beyond `window_size`). For Phase 2, window-based index is sufficient.

2. **Concurrent same-LM-instance token reads**
   - What we know: `lm.history` is a list; `update_history` appends. If two threads share the same
     `dspy.LM` instance, `history[-1]` may return the other thread's entry.
   - What's unclear: Does the benchmark/test setup use a single shared LM or per-session LMs?
   - Recommendation: In tests, give each `TrajectoryTracker` its own `DummyLM` instance. Document
     that for concurrent sessions, callers should use separate `dspy.LM` instances to avoid
     history interleaving. This is advisory, not enforced by Phase 2.

3. **Async ReAct path (`aforward`)**
   - What we know: `ReAct.aforward` exists and uses `await module.acall(...)` which is decorated
     with `@with_callbacks` and calls `async_wrapper`. The callback hooks fire correctly in async
     paths.
   - What's unclear: CONTEXT.md does not explicitly require async support in Phase 2.
   - Recommendation: The sync path is the priority (success criteria reference a non-async `dspy.ReAct`
     run). The callback implementation above works for both sync and async since it uses no
     async-specific primitives. No extra work needed.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `dspy.Suggest` / `dspy.Assert` for validation capture | Exception arg on `*_end` callbacks | DSPy 3.x | Suggest/Assert removed; failures surface via `exception` in callbacks |
| `configure(callbacks=[...])` as the standard registration | `dspy.context(callbacks=...)` for scoped, non-destructive registration | DSPy 3.x settings refactor | `configure` is now owner-thread-locked and replaces; `context` is the safe multi-callback path |

---

## Sources

### Primary (HIGH confidence — installed source)

- `dspy 3.2.1` at `~/.local/lib/python3.14/site-packages/dspy/`
  - `utils/callback.py` — `BaseCallback`, `with_callbacks`, `ACTIVE_CALL_ID`, `_get_active_callbacks`
  - `dsp/utils/settings.py` — `Settings.context()`, `Settings.configure()`, `thread_local_overrides` ContextVar
  - `clients/base_lm.py` — `BaseLM.__call__`, `_process_lm_response`, history entry structure
  - `clients/lm.py` — `LM.forward`, `cache_hit` guard at line 196, `UsageTracker.add_usage` call
  - `clients/cache.py` — `Cache._prepare_cached_response`, `response.usage = {}`, `response.cache_hit = True`
  - `predict/react.py` — `ReAct.forward` loop, `self.react` (Predict), `self.extract` (ChainOfThought)
  - `predict/predict.py` — `Predict.__init__`, `self.signature = ensure_signature(signature)`
  - `signatures/signature.py` — `SignatureMeta.input_fields`, `output_fields`, `make_signature`, `signature_name="StringSignature"`
  - `utils/usage_tracker.py` — `UsageTracker`, `track_usage()` context manager

### Secondary (MEDIUM confidence — earlier verified research)

- `dev/research-dspy-routellm.md` (verified 2026-06-18) — callback hooks, A.3 usage tracking
- `.planning/research/PITFALLS.md` (verified 2026-06-18) — P1 through P7 all source-verified

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; all from Phase 1
- Architecture: HIGH — verified against installed ReAct source; callback fire sequence traced
- Token usage path: HIGH — verified `_process_lm_response` → history entry → `cache_hit` in cache layer
- Signature identity: HIGH — verified `SignatureMeta.input_fields/output_fields`, `make_signature` default name
- Session isolation: HIGH — ContextVar threading behavior is stdlib; caveat documented from source
- Pitfalls: HIGH — P1, P4, P6, P7 all re-verified against current source

**Research date:** 2026-06-18
**Valid until:** 2026-07-18 (DSPy 3.2.1 is pinned; stable until next major version)
