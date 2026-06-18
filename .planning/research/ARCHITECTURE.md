# Architecture Research

**Domain:** Trajectory-monitoring router library (DSPy + RouteLLM bridge)
**Researched:** 2026-06-18
**Confidence:** HIGH — derived from verified installed source (dspy 3.2.1) and ctx7-verified RouteLLM docs

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                          USER SPACE                              │
│   with TrajectoryTracker(session_id="abc") as tracker:           │
│       agent = dspy.ReAct(sig, tools)                             │
│       result = agent(question=...)   ← unchanged agent code      │
└──────────────────────────────┬───────────────────────────────────┘
                               │  context manager __enter__:
                               │  registers TrajectoryCallback into
                               │  dspy.settings.callbacks[]
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  BLOCK 1 — STATE CAPTURE ENGINE                                  │
│                                                                  │
│  TrajectoryCallback (BaseCallback subclass)                      │
│    on_module_start  → step index, Signature class name           │
│    on_lm_start      → token count (messages→count or usage)      │
│    on_tool_start    → tool name, params (flap feed)              │
│    on_module_end \                                               │
│    on_lm_end       } → exception arg → failure signal           │
│    on_tool_end    /                                              │
│                                                                  │
│  TrajectoryCallback writes TurnRecord structs into               │
│  the shared SessionState (identified by session_id)              │
└──────────────────────────────┬───────────────────────────────────┘
                               │  push TurnRecord on each *_end
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  SHARED SESSION STATE  (in-process, thread-safe dict or obj)     │
│                                                                  │
│  SessionState {                                                  │
│    session_id: str                                               │
│    window: deque[TurnRecord]  ← max N turns (sliding)            │
│    current_threshold: float   ← read by DynamicRouteLM           │
│    escalation_flag: bool      ← set by ScoringEngine             │
│    cost_log: list[CostRecord] ← appended on each routed call     │
│  }                                                               │
│                                                                  │
│  TurnRecord {                                                    │
│    call_id: str, step_idx: int                                   │
│    signature_name: str                                           │
│    tool_name: str | None, tool_args: dict | None                 │
│    input_token_count: int, output_token_count: int               │
│    output_embedding: np.ndarray | None  (lazy, computed once)    │
│    exception: Exception | None                                   │
│  }                                                               │
└──────────────────────────────┬───────────────────────────────────┘
                               │  read window on each *_end
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  BLOCK 2 — DYNAMIC SCORING ENGINE                                │
│                                                                  │
│  ScoringEngine.score(session: SessionState) → ScoringResult      │
│    ├── LoopVelocityProfiler                                      │
│    │     cosine similarity of last-K output embeddings           │
│    │     flag if similarity > threshold and idx keeps climbing   │
│    ├── FlappingMonitor                                           │
│    │     count same tool_name in window; flag if args vary       │
│    │     but state unchanged (no new observation content)        │
│    └── StructuralConstraintScanner                               │
│          regex over input text in window                         │
│          flag if JSON Schema / valid XML / compiler syntax found  │
│                                                                  │
│  ScoringResult { anomaly: bool, kind: str, confidence: float }   │
│                                                                  │
│  When anomaly=True:                                              │
│    session.escalation_flag = True                                │
│    session.current_threshold = 0.0                               │
│  Else:                                                           │
│    session.current_threshold = config.default_threshold          │
│    (optionally: restore from 0.0 after M clean turns)            │
└──────────────────────────────┬───────────────────────────────────┘
                               │  session.current_threshold read
                               │  before each LM call
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  BLOCK 3 — ROUTELLM EXECUTION LAYER                              │
│                                                                  │
│  DynamicRouteLM (thin dspy.LM subclass)                          │
│    __call__ / forward:                                           │
│      1. read session.current_threshold                           │
│      2. build model string: f"router-mf-{threshold}"             │
│      3. delegate to parent dspy.LM with that model string        │
│         → RouteLLM server at localhost:6060/v1 resolves router   │
│         → threshold=0.0 → 100% strong model; normal → mix        │
│      4. append CostRecord to session.cost_log                    │
│                                                                  │
│  PayloadNormalizer  (thin layer, called before delegation)       │
│    strip or repack DSPy few-shot demos if they cause KeyError    │
│    in the RouteLLM/LiteLLM OpenAI-compatible forward path        │
└──────────────────────────────────────────────────────────────────┘
```

---

## Component Responsibilities

| Component | Owns | Does NOT own |
|-----------|------|--------------|
| `TrajectoryCallback` | Listening to DSPy hook events; constructing TurnRecords; pushing to SessionState.window | Scoring logic, routing decisions |
| `TrajectoryTracker` (ctx mgr) | Registering / deregistering callback in `dspy.settings.callbacks`; creating and cleaning up SessionState | Running the agent, embedding computation |
| `SessionState` | Single shared mutable object per session: window deque, threshold, escalation flag, cost log | Writing its own fields — only ScoringEngine and DynamicRouteLM write threshold/flag |
| `ScoringEngine` | Consuming window; running three detectors; writing `current_threshold` and `escalation_flag` | Knowing anything about RouteLLM model strings or DSPy internals |
| `LoopVelocityProfiler` | Embedding similarity over last-K outputs | Calling the embedding model at push time (lazy — only computed when window analysis runs) |
| `FlappingMonitor` | Tool-call repetition counting in the window | Token counting, signature tracking |
| `StructuralConstraintScanner` | Regex over input text | Any LLM query |
| `DynamicRouteLM` | Reading `current_threshold` from session; building `router-mf-{t}` model string per call; appending cost log | Scoring, telemetry collection |
| `PayloadNormalizer` | Ensuring few-shot demos do not mutate payload shape when forwarded to RouteLLM | Routing logic |

---

## Data Flow

### Per-turn data flow (inside a `with TrajectoryTracker():` block)

```
DSPy agent executes one ReAct step
   │
   ├─ on_module_start(call_id, instance, inputs)
   │     → TurnRecord created, step_idx assigned, signature_name extracted
   │
   ├─ on_lm_start(call_id, instance, inputs)
   │     → input_token_count computed (message list length / usage hook)
   │
   ├─ on_tool_start(call_id, instance, inputs)
   │     → TurnRecord.tool_name, tool_args captured
   │
   ├─── LM call executes (inside RouteLLM / DynamicRouteLM) ─────────┐
   │                                                                  │
   │    DynamicRouteLM.__call__                                       │
   │      1. read session.current_threshold                           │
   │      2. model = f"router-mf-{threshold}"                         │
   │      3. PayloadNormalizer.normalize(messages)                    │
   │      4. parent dspy.LM forward → RouteLLM server → LiteLLM      │
   │      5. append CostRecord(tokens, model_used, cost_estimate)     │
   │                                                                  │
   └──────────────────────────────────────────────────────────────────┘
   │
   ├─ on_lm_end(call_id, outputs, exception)
   │     → output_token_count, exception captured; TurnRecord completed
   │
   ├─ on_tool_end(call_id, outputs, exception)
   │     → observation content; feeds FlappingMonitor state-change check
   │
   └─ on_module_end(call_id, outputs, exception)
         → TurnRecord pushed to SessionState.window (capped to N)
         → ScoringEngine.score(session) called synchronously
               → update session.current_threshold + escalation_flag
         → next LM call will read updated threshold
```

### Escalation signal path

```
ScoringEngine detects anomaly
   │
   └─ session.escalation_flag = True
      session.current_threshold = 0.0
         │
         └─ next DynamicRouteLM.__call__
               reads threshold=0.0
               sends model="router-mf-0.0"
               RouteLLM routes 100% to strong model
               → block cleared
               → after M clean turns: threshold restored to default
```

### Threshold lifecycle

```
default (e.g. 0.11)  →  ScoringEngine: anomaly detected  →  0.0 (force escalation)
                                                                │
                     ←  ScoringEngine: M clean turns elapsed  ←┘
```

---

## Shared Session / State Object Design

The `SessionState` is the only object shared between all three blocks. It must be:

- **Keyed by `session_id`**: a module-level registry `dict[str, SessionState]` (or `threading.local` if multi-session in one thread is needed)
- **Thread-safe writes**: `threading.Lock` on `window.append` and threshold writes; the sliding deque uses `collections.deque(maxlen=N)`
- **Lightweight**: no database, no IPC — all in-process for v1

```python
# Conceptual structure (not final API)
@dataclass
class TurnRecord:
    call_id: str
    step_idx: int
    signature_name: str
    tool_name: str | None
    tool_args: dict | None
    input_token_count: int
    output_token_count: int
    output_text: str | None        # raw, for embedding + regex
    output_embedding: np.ndarray | None  # lazy
    exception: Exception | None

@dataclass
class SessionState:
    session_id: str
    window: deque                  # deque(maxlen=config.window_size)
    current_threshold: float       # written by ScoringEngine, read by DynamicRouteLM
    escalation_flag: bool
    cost_log: list                 # list[CostRecord]
    _lock: threading.Lock
```

---

## Public API Surface

These are the two developer-facing interfaces. Everything else is internal.

### 1. `TrajectoryTracker` context manager

```python
from agent_router import TrajectoryTracker

with TrajectoryTracker(
    session_id="run-001",          # required: isolates per agent run
    window_size=10,                # sliding window depth (default: 10)
    default_threshold=0.11,        # RouteLLM default threshold
    embedding_model="all-MiniLM-L6-v2",  # for loop velocity (local, fast)
    config=RouterConfig(...),      # optional: override detector params
) as tracker:
    agent = dspy.ReAct(MySignature, tools=[...])
    result = agent(question="...")
    # tracker.cost_summary() available after block exits
```

- `__enter__`: creates `SessionState`, registers `TrajectoryCallback` via `dspy.settings.configure(callbacks=[...])`
- `__exit__`: deregisters callback, optionally writes cost log

### 2. `DynamicRouteLM` — the RouteLLM routing target

```python
from agent_router import DynamicRouteLM

lm = DynamicRouteLM(
    session_id="run-001",          # must match TrajectoryTracker session_id
    router="mf",                   # RouteLLM router name
    routellm_base="http://localhost:6060/v1",
    api_key="...",
    strong_model="gpt-4o",         # for cost-log labeling only
    weak_model="gpt-4o-mini",
)
dspy.configure(lm=lm)
```

`DynamicRouteLM` is a `dspy.LM` subclass. Its only behavioral difference from stock `dspy.LM` is that it rebuilds the model string from `session.current_threshold` before each call. The `session_id` lookup into the shared registry is how it reads the current threshold set by the scoring engine.

---

## Recommended Project Structure

```
agent_router/
├── __init__.py                    # exports: TrajectoryTracker, DynamicRouteLM, RouterConfig
├── tracker.py                     # TrajectoryTracker context manager
├── callback.py                    # TrajectoryCallback (BaseCallback subclass)
├── state.py                       # SessionState, TurnRecord, CostRecord, session registry
├── scoring/
│   ├── __init__.py                # ScoringEngine, ScoringResult
│   ├── loop_velocity.py           # LoopVelocityProfiler (cosine sim on output embeddings)
│   ├── flapping.py                # FlappingMonitor (tool-call repetition in window)
│   └── structural.py              # StructuralConstraintScanner (regex)
├── routing/
│   ├── __init__.py
│   ├── dynamic_lm.py              # DynamicRouteLM (dspy.LM subclass)
│   └── payload.py                 # PayloadNormalizer (few-shot demo guard)
└── config.py                      # RouterConfig dataclass (thresholds, window size, etc.)

tests/
├── unit/
│   ├── test_callback.py
│   ├── test_loop_velocity.py
│   ├── test_flapping.py
│   └── test_structural.py
├── integration/
│   └── test_tracker_dspy.py       # real dspy.ReAct + mock LM
└── bench/
    └── synthetic_loop_bench.py    # toy agent that reliably loops; validates escalation
```

---

## Architectural Patterns

### Pattern 1: Observer via Official Hook (non-intrusive capture)

**What:** `TrajectoryCallback` subclasses `dspy.utils.BaseCallback` and is registered in `dspy.settings.callbacks`. The agent code is never touched.

**When to use:** Always — this is the mandated approach per PROJECT.md (Strategy A).

**Trade-offs:** Pure and stable; relies on DSPy maintaining the callback API (which it does in 3.x). Cannot intercept calls made outside DSPy's own dispatch path (irrelevant here).

**Key invariant:** `__enter__` prepends to `dspy.settings.callbacks`; `__exit__` removes only our instance, leaving any pre-existing callbacks untouched.

### Pattern 2: Synchronous Scoring on Window Push

**What:** `ScoringEngine.score(session)` is called synchronously inside `on_module_end` (after each ReAct step completes). It reads the current window deque and updates `current_threshold` before the next LM call can be dispatched.

**When to use:** v1 — latency of scoring is dominated by embedding inference (local MiniLM, ~5ms), not a bottleneck vs LLM call latency.

**Trade-offs:** Simplicity (no threads, no queues). If embedding inference becomes slow at large window sizes, move to a background thread with a lock; the `SessionState._lock` design already supports this upgrade path.

### Pattern 3: Model-String-as-Signal for per-call threshold

**What:** `DynamicRouteLM` encodes the threshold into the RouteLLM model string `router-mf-{threshold}` on every call. RouteLLM parses this string itself — no patching needed.

**When to use:** Always — this is the verified mechanism (research finding 2026-06-18, B.0 in research doc).

**Trade-offs:** Threshold granularity limited to float precision in the model string (irrelevant in practice). The strong advantage is zero coupling to RouteLLM internals.

---

## Anti-Patterns

### Anti-Pattern 1: Wrapping `dspy.LM` for telemetry capture

**What people do:** Subclass `dspy.LM`, override `__call__`, intercept all messages there.

**Why it's wrong:** Misses module-level and tool-level structure (Signature names, step indices, tool-call params). The callback system exposes all of that cleanly; `__call__` only sees raw message lists.

**Do this instead:** Use callbacks for all telemetry. Reserve `dspy.LM` subclassing exclusively for `DynamicRouteLM` (threshold signal), not capture.

### Anti-Pattern 2: Patching RouteLLM internals for per-request threshold

**What people do:** Monkey-patch RouteLLM's `Controller`, add `X-RouteLLM-Threshold` header handling.

**Why it's wrong:** Unnecessary. RouteLLM already parses the threshold from the model string per request. Patching internals creates a maintenance dependency on RouteLLM's private API.

**Do this instead:** Rebuild `model="router-mf-{threshold}"` in `DynamicRouteLM.__call__` before each call.

### Anti-Pattern 3: Embedding at push time (eager)

**What people do:** Compute output embeddings inside `on_lm_end` for every turn.

**Why it's wrong:** Adds ~5ms to the synchronous callback path on every call, even when the window isn't full and `LoopVelocityProfiler` hasn't been triggered.

**Do this instead:** Store `output_text` in `TurnRecord`; compute `output_embedding` lazily inside `LoopVelocityProfiler.analyze()` only when the window contains enough turns. Cache the embedding on the TurnRecord after first computation.

### Anti-Pattern 4: Global singleton SessionState

**What people do:** One module-level `SessionState` shared across all tracker instances.

**Why it's wrong:** Multiple concurrent sessions (e.g., parallel agent evaluations) corrupt each other's windows and thresholds.

**Do this instead:** A `dict[str, SessionState]` registry keyed by `session_id`. `TrajectoryTracker.__enter__` allocates, `__exit__` cleans up (or moves to a completed archive).

---

## Build Order and Dependencies

The dependency graph is strictly linear: capture must exist before scoring can consume data; scoring must exist before routing can read its signal.

```
Phase 1 — State Capture (Block 1)
  ├── state.py: TurnRecord, SessionState, registry
  ├── callback.py: TrajectoryCallback (BaseCallback subclass)
  ├── tracker.py: TrajectoryTracker context manager
  └── validate: unit test with real dspy.ReAct + mock LM
        confirms callbacks fire, TurnRecords populate window

Phase 2 — Scoring Engine (Block 2)
  ├── config.py: RouterConfig (window size, detector thresholds)
  ├── scoring/structural.py: StructuralConstraintScanner (pure regex, no deps)
  ├── scoring/flapping.py: FlappingMonitor (dict/counter, no deps)
  ├── scoring/loop_velocity.py: LoopVelocityProfiler (sentence-transformers dep)
  ├── scoring/__init__.py: ScoringEngine (orchestrates three detectors)
  └── validate: synthetic loop bench (toy ReAct that reliably loops)
        confirms: anomaly=True fired, current_threshold set to 0.0

Phase 3 — Routing Layer (Block 3)
  ├── routing/payload.py: PayloadNormalizer (few-shot demo guard)
  ├── routing/dynamic_lm.py: DynamicRouteLM (reads session threshold)
  └── validate: end-to-end with real RouteLLM server
        confirms: threshold=0.0 → strong model selected
        confirms: few-shot demos pass without KeyError

Phase 4 — Validation (research arm)
  ├── bench/synthetic_loop_bench.py: toy looping agent, reproducible
  └── real benchmark (GSM8K / HotpotQA / code) with cost logging
```

**Key dependency constraints:**
- `DynamicRouteLM` must be configured as `dspy.configure(lm=...)` BEFORE `TrajectoryTracker.__enter__` is called, because the callback fires on the same LM that's already registered.
- The session registry (`dict[str, SessionState]`) must be importable by both `callback.py` and `routing/dynamic_lm.py` without circular imports — place it in `state.py` as the single source of truth.
- Embedding model (sentence-transformers) is a heavy dep — make it optional at import; `LoopVelocityProfiler` raises a clear error if not installed rather than failing silently.

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| RouteLLM server | OpenAI-compatible HTTP at `localhost:6060/v1`; `dspy.LM("openai/router-mf-{t}", api_base=...)` | Server must be running before `DynamicRouteLM` calls. In-process `Controller` mode is a valid alternative for testing. |
| LiteLLM (inside RouteLLM) | Transparent — RouteLLM calls LiteLLM internally | No direct integration needed from agent-router side. |
| sentence-transformers | Local import inside `LoopVelocityProfiler` | `all-MiniLM-L6-v2` — fast, CPU-friendly, ~80MB. Optional dependency. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `TrajectoryCallback` → `SessionState` | Direct write to `session.window` (locked) | Callback holds a reference to SessionState, not to ScoringEngine |
| `TrajectoryCallback` → `ScoringEngine` | Direct call `engine.score(session)` in `on_module_end` | ScoringEngine is injected into the callback at construction; callback does not import it globally |
| `ScoringEngine` → `SessionState` | Writes `current_threshold` and `escalation_flag` | ScoringEngine does not know about DynamicRouteLM |
| `DynamicRouteLM` → `SessionState` | Reads `current_threshold` by `session_id` lookup in registry | DynamicRouteLM does not know about ScoringEngine |
| `TrajectoryTracker` → all | Factory and lifecycle owner; wires callback + scoring engine + session at `__enter__` | Only place that has visibility into all three blocks simultaneously |

---

## Scaling Considerations

This system is designed for single-process, in-session use. Scaling is not a v1 concern, but the design decisions that matter later:

| Scale | Architecture note |
|-------|-------------------|
| Single agent run (v1) | In-process dict registry, synchronous scoring — correct and sufficient |
| Parallel eval sweeps (e.g. Stratum hangars) | `session_id` isolation already handles this; one `SessionState` per session, no shared mutable state between runs |
| Multi-process agent farms | `SessionState` is in-process only; would need an external store (Redis) for cross-process session sharing — out of scope for v1 |

---

## Sources

- DSPy 3.2.1 installed source: `~/.local/lib/python3.14/site-packages/dspy` (verified 2026-06-18)
  - `dspy/utils/callback.py` — `BaseCallback`, hook signatures, `call_id` contract
  - `dspy/predict/react.py` — ReAct loop shape, `trajectory` dict, `on_module_start` per step
  - `dspy/clients/lm.py:196` — usage tracking plumbing
  - `dspy/utils/usage_tracker.py` — `UsageTracker.add_usage()` / `get_total_tokens()`
- RouteLLM docs via ctx7 `/lm-sys/routellm` (verified 2026-06-18)
  - `router-[NAME]-[THRESHOLD]` model-string mechanism — per-request, no header needed
  - In-process `Controller` and `openai_server` modes
  - `calibrate_threshold` tool
- `dev/research-dspy-routellm.md` — consolidated research findings (2026-06-18)
- `./scope` — original functional scope document
- `.planning/PROJECT.md` — canonical requirements and constraints

---
*Architecture research for: agent-router (trajectory-monitoring DSPy + RouteLLM bridge)*
*Researched: 2026-06-18*
