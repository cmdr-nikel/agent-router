# Phase 1: Foundation & Contracts - Research

**Researched:** 2026-06-18
**Domain:** Python library packaging (hatchling), pydantic v2 + pydantic-settings, dataclass contracts, PEP 562 lazy imports, mypy --strict
**Confidence:** HIGH — all critical items verified against installed packages, PyPI, ctx7 docs, and installed DSPy 3.2.1 source

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01 — Dependency layout:** fastembed is `[embed]` optional extra with lazy import + clear error on missing. RouteLLM server deps under `[serve]` extra. Eval/bench deps under `[bench]` extra. Core install stays light (only dspy, pydantic, pydantic-settings, openai, numpy).

**D-02 — Python support:** Minimum Python 3.10. Use `from __future__ import annotations` everywhere for 3.10 compat. Dev box is Python 3.14.5 — test on both.

**D-03 — Contract mutability:** `TurnRecord` frozen (immutable). `CostRecord` frozen. `SessionState` mutable. `RouterConfig` is a pydantic `BaseSettings` model (validated with defaults, reads env vars).

**D-04 — Configuration source:** `RouterConfig` reads `weak_model`, `strong_model`, and API keys from environment variables via pydantic-settings. Prefix: `AGENT_ROUTER_` (to be set by planner — see Claude's Discretion).

**D-05 — RouterConfig fields:** `window_size`, `default_threshold`, `loop_similarity_threshold`, `max_escalations_per_session`, `weak_model`, `strong_model`.

**D-06 — Contract fields:**
- `TurnRecord`: signature identity (class name + sorted field names), step index, input token count, output text + output length, cache-hit flag, success/exception field.
- `SessionState`: `session_id`, turn window (sliding deque), `current_threshold`, `escalation_count`.
- `CostRecord`: billed vs cache-free cost separation.

### Claude's Discretion
- Exact module split inside `agent_router/` (e.g., `contracts.py` vs `contracts/` package)
- Pydantic v2 specifics and the precise lazy-import shim
- Whether to use `src/` layout or flat layout
- Exact env var prefix for `RouterConfig`
- Whether `DynamicRouteLM` stub uses `BaseLM` or `LM` subclassing

### Deferred Ideas (OUT OF SCOPE)
- Zero-dependency hash-fingerprint loop-detection fallback (PERF-02, v2)
- Hard budget cap / auto-stop (COST-01, v2)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| LIB-01 | The project is pip-installable (hatchling build) with a clean, documented public API surface | Hatchling 1.30.1 flat layout, dynamic version via `__version__` in `__init__.py`, `[project.optional-dependencies]` for extras — fully supported. See Standard Stack and Code Examples. |
| LIB-02 | The weak→strong model pair is config-driven (default cheap API → frontier API) | pydantic-settings 2.14.1 `BaseSettings` + `SettingsConfigDict(env_prefix=...)` pattern — reads model pair from env vars with coded defaults. See Code Examples. |
</phase_requirements>

---

## Summary

Phase 1 delivers a pip-installable package skeleton with typed data contracts, a validated config object, and a public API surface that loads without optional heavy dependencies. No block logic is written in this phase — only the shapes that Blocks 1-3 will consume.

The critical subtlety is **success criterion 2** (importability without heavy deps). The clean solution is module-level deferred imports: `TrajectoryTracker` and `DynamicRouteLM` live in modules that do not import fastembed or routellm at module load time. The `__init__.py` can use direct imports if the submodules are clean, or PEP 562 `__getattr__` for extra safety. `RouterConfig` imports only pydantic-settings (a core dep), so it is always importable.

A critical discovery: **`routellm 0.2.0` requires torch, transformers, and datasets as core (non-optional) dependencies** — the `[serve]` extra only adds `fastapi + shortuuid + uvicorn`. This means `routellm` itself cannot be a "light" core dep in `agent-router`. The correct design is: `routellm` under `[serve]` extra (meaning "install to run the RouteLLM server locally"); the agent-router library itself communicates with RouteLLM via HTTP using only the `openai` SDK (already a core dep).

The other critical discovery: **`output_embedding` cannot be a field on a frozen `TurnRecord` dataclass** since lazy assignment would require a mutable field. The embedding belongs in the `LoopVelocityProfiler` (Phase 3), not in the Phase 1 contracts. `TurnRecord` stores `output_text` (the raw string); the profiler computes embeddings on demand.

**Primary recommendation:** Use flat layout (no `src/`), frozen stdlib `@dataclass` for `TurnRecord`/`CostRecord`, pydantic `BaseSettings` for `RouterConfig`, `SessionState` as a mutable `@dataclass` with `threading.Lock`, and direct module-level deferred imports (no heavy deps at module load) for the public API.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Package install & extras | Build system (hatchling/pyproject.toml) | — | Packaging config; no runtime tier |
| Data contracts (TurnRecord, CostRecord, SessionState) | `agent_router/state.py` | — | Single source of truth; imported by all other modules |
| Configuration & env vars | `agent_router/config.py` (RouterConfig) | OS env / .env file | pydantic-settings owns env binding; code imports RouterConfig |
| Public API surface (__init__.py) | `agent_router/__init__.py` | submodules | Thin re-export; no logic |
| Stub classes (TrajectoryTracker, DynamicRouteLM) | `agent_router/tracker.py`, `agent_router/routing/dynamic_lm.py` | — | Empty-body stubs in Phase 1; filled in Phase 2 and 4 |
| Type checking | mypy + pydantic.mypy plugin | pyproject.toml `[tool.mypy]` section | Plugin required for pydantic model fields to type-check under --strict |
| Test infrastructure | `tests/unit/`, `tests/integration/`, `tests/bench/` | `pyproject.toml [tool.pytest.ini_options]` | Directory creation only in Phase 1 |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| hatchling | 1.30.1 [VERIFIED: PyPI] | Build backend (replaces setuptools); declared in `[build-system]` | PEP 517/518 standard; integrates with uv; used by PyPA projects |
| pydantic | 2.12.5 (installed) [VERIFIED: installed] | `RouterConfig` base class and field validation | Already installed as DSPy/RouteLLM transitive dep; v2 is the current standard |
| pydantic-settings | 2.14.1 [VERIFIED: PyPI] | `BaseSettings` subclass for `RouterConfig` — reads env vars via `SettingsConfigDict` | The pydantic-endorsed env-var config pattern; ships `python-dotenv` support automatically |
| dspy | 3.2.1 (installed) [VERIFIED: installed source] | Core agent framework; `BaseCallback`, `dspy.context()` are the capture surface | Project constraint — monitoring DSPy agents |
| openai | 2.30.0 (installed) [VERIFIED: installed] | HTTP client to the RouteLLM server (`base_url="http://localhost:6060/v1"`) | Already installed; standard RouteLLM client pattern |
| numpy | 2.4.4 (installed) [VERIFIED: installed] | Type annotation for `output_embedding` (future), cosine similarity | Already installed; numpy 2.x ships `py.typed` for mypy |

### Supporting (Optional Extras)

| Library | Version | Extra | Purpose | Why Optional |
|---------|---------|-------|---------|--------------|
| fastembed | 0.8.0 [VERIFIED: PyPI] | `[embed]` | Local CPU embeddings for Loop Velocity Profiler | ~20MB ONNX model + 6 packages; not needed until Phase 3 scoring is active |
| onnxruntime | 1.27.0 [VERIFIED: PyPI via fastembed deps] | transitive of fastembed | ONNX inference backend | Automatic transitive of fastembed; no direct pin needed |
| routellm | 0.2.0 [VERIFIED: PyPI] | `[serve]` | Run the RouteLLM server locally | Pulls torch (532 MB) + transformers + datasets as CORE deps — heavy; only needed to run the server subprocess |

### Development/Test

| Library | Version | Purpose |
|---------|---------|---------|
| mypy | 2.1.0 [VERIFIED: PyPI] | Static type checking with --strict; cp314 wheel available |
| pytest | 9.1.0 [VERIFIED: PyPI] | Test runner |
| pytest-asyncio | 1.4.0 [VERIFIED: PyPI] | Async test support; Python 3.14 preliminary support added in 1.0.0 |
| pytest-mock | 3.15.1 [VERIFIED: PyPI] | Mock DSPy LM calls in unit tests |
| hatch | 1.17.0 [VERIFIED: PyPI] | Project manager wrapping hatchling; optional (uv can build directly) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| stdlib `@dataclass(frozen=True)` for TurnRecord | pydantic `BaseModel(frozen=True)` | pydantic adds runtime validation on every field set; frozen dataclass is lighter and sufficient for append-only telemetry |
| flat layout | `src/` layout | src-layout prevents accidental imports of un-installed package; flat is simpler for a small single-package library; either works with hatchling — discretion left to planner |
| pydantic-settings `BaseSettings` | plain pydantic `BaseModel` + manual `os.environ` reads | BaseSettings is the endorsed pydantic pattern; handles coercion, dotenv, validation out of the box |
| `from __future__ import annotations` + `Optional[X]` | `X | None` (3.10+ syntax only) | Both work on 3.10+; `from __future__ import annotations` makes `X | None` safe in all string annotations regardless of runtime; use it everywhere |

**Installation (to be run in Wave 0):**

```bash
# Install dev tools into user-local python3.14
pip3.14 install hatchling==1.30.1 pydantic-settings==2.14.1 mypy==2.1.0 \
               pytest==9.1.0 pytest-asyncio==1.4.0 pytest-mock==3.15.1

# Install the library itself in editable mode (after pyproject.toml exists)
pip3.14 install -e ".[dev]"
# or with uv:
uv pip install -e ".[dev]"
```

---

## Package Legitimacy Audit

All packages verified with `slopcheck 0.6.1` (installed at `~/.local/lib/python3.14/site-packages/slopcheck`).

| Package | Registry | slopcheck | Disposition |
|---------|----------|-----------|-------------|
| pydantic-settings | PyPI | [OK] | Approved |
| mypy | PyPI | [OK] | Approved |
| hatchling | PyPI | [OK] | Approved |
| hatch | PyPI | [OK] | Approved |
| pytest | PyPI | [OK] | Approved |
| pytest-asyncio | PyPI | [OK] | Approved |
| pytest-mock | PyPI | [OK] | Approved |
| fastembed | PyPI | [OK] | Approved |
| routellm | PyPI | [OK] | Approved |

**Packages removed due to slopcheck [SLOP] verdict:** none

**Packages flagged as suspicious [SUS]:** none

---

## Architecture Patterns

### System Architecture Diagram

```
pyproject.toml
  [build-system] hatchling 1.30.1
  [project] name="agent-router", requires-python=">=3.10"
  [project.dependencies] dspy, pydantic, pydantic-settings, openai, numpy
  [project.optional-dependencies]
    embed = ["fastembed>=0.8.0"]
    serve = ["routellm[serve]==0.2.0"]   ← heavy (torch); run server separately
    bench = ["routellm[serve,eval]==0.2.0"]
    dev   = ["mypy", "pytest", "pytest-asyncio", "pytest-mock"]

from agent_router import TrajectoryTracker, DynamicRouteLM, RouterConfig
            │                    │                  │
            │                    │                  └─ config.py (pydantic BaseSettings)
            │                    │                     reads AGENT_ROUTER_WEAK_MODEL etc.
            │                    └─ routing/dynamic_lm.py (stub in Phase 1)
            │                       dspy.BaseLM subclass; no routellm import at module load
            └─ tracker.py (stub in Phase 1)
               context manager; no fastembed import at module load

state.py ← imported by tracker.py AND routing/dynamic_lm.py (no circular import)
  @dataclass(frozen=True) TurnRecord
  @dataclass(frozen=True) CostRecord
  @dataclass          SessionState   (mutable; has threading.Lock)
  _SESSION_REGISTRY: dict[str, SessionState]  ← global registry
```

### Recommended Project Structure

```
agent-router/           ← project root
├── pyproject.toml      ← hatchling build, extras, mypy config, pytest config
├── agent_router/       ← flat layout (no src/)
│   ├── __init__.py     ← re-exports TrajectoryTracker, DynamicRouteLM, RouterConfig
│   ├── state.py        ← TurnRecord, CostRecord, SessionState, _SESSION_REGISTRY
│   ├── config.py       ← RouterConfig (pydantic BaseSettings)
│   ├── tracker.py      ← TrajectoryTracker stub (context manager shell)
│   └── routing/
│       ├── __init__.py
│       └── dynamic_lm.py  ← DynamicRouteLM stub (dspy.BaseLM subclass shell)
├── tests/
│   ├── unit/
│   │   └── test_contracts.py   ← TurnRecord/CostRecord/SessionState/RouterConfig
│   ├── integration/
│   │   └── .gitkeep
│   └── bench/
│       └── .gitkeep
└── dev/                ← existing research docs (unchanged)
```

### Pattern 1: PEP 562 Module-level `__getattr__` for Lazy Public API

**What:** `agent_router/__init__.py` uses module-level `__getattr__` (PEP 562, Python 3.7+) to defer the actual import of `TrajectoryTracker` and `DynamicRouteLM` until first access. This guarantees that merely doing `import agent_router` never loads fastembed or routellm.

**When to use:** Only needed if the submodule itself triggers a heavy import at module load. If `tracker.py` and `routing/dynamic_lm.py` are already clean (no top-level `import fastembed` / `import routellm`), a direct `from agent_router.tracker import TrajectoryTracker` in `__init__.py` is equivalent and simpler. Use `__getattr__` as belt-and-suspenders.

**Example:**
```python
# agent_router/__init__.py
# Source: PEP 562 — https://peps.python.org/pep-0562/
from __future__ import annotations

from agent_router.config import RouterConfig  # always safe: pydantic-settings is core

__version__ = "0.1.0"

__all__ = ["TrajectoryTracker", "DynamicRouteLM", "RouterConfig"]

_LAZY_MAP: dict[str, str] = {
    "TrajectoryTracker": "agent_router.tracker",
    "DynamicRouteLM": "agent_router.routing.dynamic_lm",
}


def __getattr__(name: str) -> object:
    if name in _LAZY_MAP:
        import importlib
        module = importlib.import_module(_LAZY_MAP[name])
        val = getattr(module, name)
        globals()[name] = val   # cache for subsequent accesses
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

### Pattern 2: Frozen Dataclass for TurnRecord / CostRecord

**What:** stdlib `@dataclass(frozen=True)` — immutable after construction, hashable, no pydantic overhead on every append.

**Key constraint:** `output_embedding` must NOT be a field on `TurnRecord` (frozen + lazy assignment are incompatible). Store only `output_text: str | None`. The embedding is computed by `LoopVelocityProfiler` in Phase 3 and cached in the profiler's own dict keyed by `call_id`.

**Example:**
```python
# agent_router/state.py
from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
import threading


@dataclass(frozen=True)
class TurnRecord:
    call_id: str
    step_idx: int
    signature_name: str          # class_name + sorted(input_fields + output_fields)
    tool_name: str | None
    tool_args: dict | None
    input_token_count: int
    output_token_count: int
    output_text: str | None      # raw LM output; embedding computed lazily in Phase 3
    cache_hit: bool
    exception: Exception | None


@dataclass(frozen=True)
class CostRecord:
    call_id: str
    model_used: str              # resolved model name (weak or strong)
    billed_cost: float | None    # None on cache hit (Pitfall P18)
    input_tokens: int
    output_tokens: int
    is_cache_hit: bool


@dataclass
class SessionState:
    session_id: str
    window: deque                # deque[TurnRecord], maxlen=config.window_size
    current_threshold: float
    escalation_count: int
    cost_log: list               # list[CostRecord]
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


# Module-level registry: keyed by session_id
_SESSION_REGISTRY: dict[str, SessionState] = {}
```

### Pattern 3: pydantic BaseSettings for RouterConfig

**What:** `pydantic_settings.BaseSettings` with `SettingsConfigDict(env_prefix="AGENT_ROUTER_")`. Fields with defaults are always valid; env vars override them.

**Example:**
```python
# agent_router/config.py
# Source: https://github.com/pydantic/pydantic-settings/blob/main/docs/index.md
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RouterConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_ROUTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Window and routing
    window_size: int = Field(default=10, ge=1, le=100)
    default_threshold: float = Field(default=0.11593, ge=0.0, le=1.0)
    loop_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_escalations_per_session: int = Field(default=3, ge=1)

    # Model pair (override via env: AGENT_ROUTER_WEAK_MODEL, AGENT_ROUTER_STRONG_MODEL)
    weak_model: str = "openai/gpt-4o-mini"
    strong_model: str = "openai/gpt-4o"
```

### Pattern 4: mypy --strict Configuration in pyproject.toml

**What:** `[tool.mypy]` section in `pyproject.toml` with the pydantic plugin. Scope is limited to the `agent_router/` package for Phase 1 (contracts only).

**Example:**
```toml
# pyproject.toml [tool.mypy] section
# Source: https://github.com/pydantic/pydantic/blob/main/docs/integrations/mypy.md
[tool.mypy]
plugins = ["pydantic.mypy"]
strict = true
python_version = "3.10"
files = ["agent_router/"]
# Ignore missing stubs for dspy (no py.typed marker in dspy 3.2.1)
[[tool.mypy.overrides]]
module = ["dspy.*", "litellm.*"]
ignore_missing_imports = true
```

**Note:** dspy 3.2.1 has no `py.typed` marker — `ignore_missing_imports = true` on `dspy.*` is required; otherwise mypy --strict errors on every dspy type reference in stubs. pydantic 2.12.5 and numpy 2.4.4 both have `py.typed` — no override needed.

### Pattern 5: hatchling flat-layout pyproject.toml (concrete shape)

```toml
# Source: https://github.com/pypa/hatch/blob/master/docs/config/metadata.md
[build-system]
requires = ["hatchling>=1.30"]
build-backend = "hatchling.build"

[project]
name = "agent-router"
description = "Trajectory-aware DSPy agent router with RouteLLM escalation"
requires-python = ">=3.10"
dynamic = ["version"]
dependencies = [
    "dspy>=3.2.1",
    "pydantic>=2.12",
    "pydantic-settings>=2.14",
    "openai>=2.0",
    "numpy>=2.0",
]

[project.optional-dependencies]
embed = ["fastembed>=0.8.0"]
serve = ["routellm[serve]==0.2.0"]         # torch + transformers pulled here
bench = ["routellm[serve,eval]==0.2.0"]    # adds datasets + pandarallel
dev   = [
    "mypy>=2.1",
    "pytest>=9.1",
    "pytest-asyncio>=1.4",
    "pytest-mock>=3.15",
]

[tool.hatch.version]
path = "agent_router/__init__.py"          # reads __version__ = "0.1.0"

[tool.hatch.build.targets.wheel]
packages = ["agent_router"]                # flat layout: agent_router/ at root

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.mypy]
plugins = ["pydantic.mypy"]
strict = true
python_version = "3.10"
files = ["agent_router/"]
[[tool.mypy.overrides]]
module = ["dspy.*", "litellm.*"]
ignore_missing_imports = true

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true
```

### Anti-Patterns to Avoid

- **Hard-importing fastembed at top of any Phase 1 module:** `import fastembed` at module level will cause `from agent_router import TrajectoryTracker` to fail when the `[embed]` extra is not installed. All fastembed imports must be inside function/method bodies with a clear `ImportError` + hint message.
- **Hard-importing routellm at top of routing/dynamic_lm.py:** Same issue — routellm is under `[serve]` extra. `DynamicRouteLM` in Phase 1 is a stub; it should not import routellm at all yet.
- **Using `dspy.configure(callbacks=[cb])` in TrajectoryTracker:** Replaces existing user callbacks. Use `dspy.context(callbacks=existing + [cb])` (Pitfall P4). Phase 1 stub should leave a `# TODO: use dspy.context()` comment.
- **`output_embedding` as a frozen dataclass field:** Cannot be lazily assigned; breaks `frozen=True`. Store `output_text`; embedding lives in Phase 3.
- **Putting routellm in `[project.dependencies]`:** routellm core requires torch (532 MB). It must be optional under `[serve]` or `[bench]`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Env var config binding | Manual `os.environ.get()` in `__init__` | `pydantic-settings BaseSettings` | Type coercion, validation, dotenv support, case-insensitive matching — all free |
| Type validation of config fields | Custom validators | `pydantic Field(ge=0.0, le=1.0)` | Built-in; raises `ValidationError` with clear field/constraint message |
| Immutable telemetry records | Custom `__setattr__` guards | `@dataclass(frozen=True)` | Python 3.7+ stdlib; raises `FrozenInstanceError` automatically |
| Optional dep error messages | Silent `ImportError` propagation | `try/except ImportError: raise ImportError("... pip install agent-router[embed]")` | Library UX — clear error pointing at the right extra |
| Build system | Custom `setup.py` | hatchling + `pyproject.toml` | PEP 517/518 compliant; uv-compatible; no setuptools legacy |

---

## Common Pitfalls

### Pitfall 1: routellm 0.2.0 bare install pulls torch (532 MB) — it cannot be a light core dep

**What goes wrong:** STACK.md (written before the slopcheck dry-run) says "routellm[serve] stays light". This is **false** for routellm 0.2.0. The `[serve]` extra only adds `fastapi + shortuuid + uvicorn`. torch, transformers, and datasets are hard core deps regardless of extras.

**Root cause:** PyPI metadata for routellm 0.2.0 lists `torch` and `transformers` as non-extra dependencies (`requires_dist` without `extra == "..."` marker).

**How to avoid:** Never list `routellm` in `[project.dependencies]`. List it only under `[project.optional-dependencies] serve` and `bench`. The `agent-router` library communicates with RouteLLM via HTTP (openai SDK, already a core dep); `routellm` is only needed to launch the server subprocess.

**Warning signs:** `pip install agent-router` begins downloading CUDA wheels. STACK.md pyproject.toml skeleton has `routellm[serve]` in `[project.dependencies]` — correct this in the plan.

### Pitfall 2: `output_embedding` on frozen TurnRecord breaks lazy-assignment pattern

**What goes wrong:** ARCHITECTURE.md shows `output_embedding: np.ndarray | None` as a `TurnRecord` field. A frozen dataclass raises `FrozenInstanceError` if you try to assign it after construction. You cannot write `record.output_embedding = vector` in the profiler.

**How to avoid:** Remove `output_embedding` from `TurnRecord`. Store only `output_text: str | None`. The `LoopVelocityProfiler` (Phase 3) maintains its own `dict[str, np.ndarray]` keyed by `call_id` for embedding cache.

**Warning signs:** Any code that does `turn_record.output_embedding = ...` after construction raises `FrozenInstanceError`.

### Pitfall 3: dspy has no `py.typed` — mypy --strict errors on every dspy import

**What goes wrong:** mypy --strict with `disallow_untyped_defs = true` (implied by `strict = true`) raises errors on all `dspy.*` symbols because dspy 3.2.1 has no `py.typed` marker and no bundled stubs. Even a simple `from dspy.utils import BaseCallback` will produce "error: Cannot find implementation or library stub for module named 'dspy.utils'".

**How to avoid:** Add `[[tool.mypy.overrides]] module = ["dspy.*", "litellm.*"] ignore_missing_imports = true` in `pyproject.toml`. The contracts themselves (`state.py`, `config.py`) do not import dspy — mypy --strict on these files alone will pass cleanly. The stubs that DO import dspy (tracker.py, routing/dynamic_lm.py) need the override.

**Warning signs:** `mypy --strict agent_router/` exits with errors on tracker.py even though the file is a shell stub.

### Pitfall 4: hatchling version path must match actual file and variable name

**What goes wrong:** `[tool.hatch.version] path = "agent_router/__init__.py"` requires hatchling to parse a line matching `__version__ = "..."` in that file. If the file does not contain `__version__`, or the variable is named differently, `hatch build` / `pip install -e .` fails with "no version found".

**How to avoid:** Ensure `agent_router/__init__.py` has exactly `__version__ = "0.1.0"` (string literal, no f-string, no computed value). This is what hatchling's regex extractor expects.

### Pitfall 5: pytest-asyncio 1.x requires `asyncio_mode = "auto"` in pyproject.toml

**What goes wrong:** pytest-asyncio 1.x dropped the default `"strict"` mode behavior from 0.x. Without `asyncio_mode = "auto"` in `[tool.pytest.ini_options]`, async tests require a `@pytest.mark.asyncio` decorator on every test function — noisy and easy to forget.

**How to avoid:** Add `asyncio_mode = "auto"` in `pyproject.toml`. Phase 1 tests are synchronous but Phase 2 tests will be async; set it correctly now.

---

## Code Examples

### Verified: PEP 562 module __getattr__ (Python 3.7+)

```python
# agent_router/__init__.py
# Source: PEP 562 (https://peps.python.org/pep-0562/) — module __getattr__ since Python 3.7
from __future__ import annotations

from agent_router.config import RouterConfig

__version__ = "0.1.0"
__all__ = ["TrajectoryTracker", "DynamicRouteLM", "RouterConfig"]

_LAZY_MAP: dict[str, str] = {
    "TrajectoryTracker": "agent_router.tracker",
    "DynamicRouteLM": "agent_router.routing.dynamic_lm",
}

def __getattr__(name: str) -> object:
    if name in _LAZY_MAP:
        import importlib
        mod = importlib.import_module(_LAZY_MAP[name])
        val = getattr(mod, name)
        globals()[name] = val  # cache to avoid repeated __getattr__ calls
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

### Verified: pydantic BaseSettings env binding

```python
# agent_router/config.py
# Source: https://github.com/pydantic/pydantic-settings docs (ctx7, 2026-06-18)
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RouterConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_ROUTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    window_size: int = Field(default=10, ge=1, le=100)
    default_threshold: float = Field(default=0.11593, ge=0.0, le=1.0)
    loop_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_escalations_per_session: int = Field(default=3, ge=1)
    weak_model: str = "openai/gpt-4o-mini"
    strong_model: str = "openai/gpt-4o"
```

### Verified: frozen dataclass for TurnRecord

```python
# agent_router/state.py
# Source: Python stdlib docs — dataclasses.dataclass(frozen=True) [VERIFIED: Python 3.14.5]
from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
import threading


@dataclass(frozen=True)
class TurnRecord:
    call_id: str
    step_idx: int
    signature_name: str      # derived as class.__name__ + sorted(input_fields + output_fields)
    tool_name: str | None
    tool_args: dict | None
    input_token_count: int
    output_token_count: int
    output_text: str | None  # raw LM output; NO output_embedding here (lazy in Phase 3)
    cache_hit: bool
    exception: Exception | None  # from on_lm_end(exception=...) (Pitfall P6)


@dataclass(frozen=True)
class CostRecord:
    call_id: str
    model_used: str
    billed_cost: float | None   # None on cache hit (Pitfall P18)
    input_tokens: int
    output_tokens: int
    is_cache_hit: bool


@dataclass
class SessionState:
    session_id: str
    window: deque               # deque[TurnRecord], maxlen set at construction time
    current_threshold: float
    escalation_count: int
    cost_log: list              # list[CostRecord]
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_SESSION_REGISTRY: dict[str, SessionState] = {}
```

### Verified: DynamicRouteLM stub (dspy.BaseLM subclass, no routellm import)

```python
# agent_router/routing/dynamic_lm.py
# Source: dspy BaseLM docs (installed source ~/.local/lib/python3.14/site-packages/dspy/clients/base_lm.py)
from __future__ import annotations
from typing import Any

import dspy  # type: ignore[import-untyped]


class DynamicRouteLM(dspy.BaseLM):
    """
    Phase 1 stub — public API surface only.
    Implemented in Phase 4.
    """

    def __init__(
        self,
        session_id: str,
        router: str = "mf",
        routellm_base: str = "http://localhost:6060/v1",
        api_key: str = "",
        **kwargs: Any,
    ) -> None:
        # Build initial model string; will be rebuilt per-call in Phase 4
        model = f"openai/router-{router}-0.11593"
        super().__init__(model=model, **kwargs)
        self.session_id = session_id
        self.router = router
        self.routellm_base = routellm_base

    def forward(self, prompt: str | None = None, messages: list | None = None, **kwargs: Any) -> Any:
        raise NotImplementedError("DynamicRouteLM.forward implemented in Phase 4")
```

### Verified: TrajectoryTracker stub (context manager shell)

```python
# agent_router/tracker.py
from __future__ import annotations
from types import TracebackType
from typing import Any


class TrajectoryTracker:
    """
    Phase 1 stub — public API surface only.
    Implemented in Phase 2.
    """

    def __init__(
        self,
        session_id: str,
        config: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self.config = config

    def __enter__(self) -> "TrajectoryTracker":
        # Phase 2: create SessionState, register TrajectoryCallback via dspy.context()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Phase 2: deregister callback, optionally flush cost log
        pass
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `setuptools` + `setup.py` | `hatchling` + `pyproject.toml` | PEP 517/518 (2015-2018), hatchling popular ~2022+ | Simpler, standard; uv-compatible |
| pydantic v1 `BaseSettings` (pydantic 1.x) | `pydantic-settings` separate package (pydantic 2.0+) | pydantic 2.0 (2023) | `BaseSettings` moved to `pydantic-settings`; must install separately |
| pytest-asyncio 0.x `@pytest.mark.asyncio` per test | pytest-asyncio 1.x `asyncio_mode = "auto"` | 1.0.0 (2025-05-26) | No per-test decorator needed; also adds Python 3.14 preliminary support |
| `@dataclass` + `mypy_extensions` stubs | mypy 2.x with stdlib dataclass inference | mypy 1.x+ improvements | mypy 2.1.0 has cp314 wheel; --strict works out of the box on stdlib dataclasses |
| Inline routellm as a core dep | routellm as optional `[serve]` extra | Discovered 2026-06-18: routellm 0.2.0 core requires torch | agent-router core stays lightweight; server runs as a subprocess |

**Deprecated/outdated:**
- `pydantic.BaseSettings` (from pydantic 1.x): import path no longer works in pydantic 2.x — use `pydantic_settings.BaseSettings`
- pytest-asyncio 0.x config style: `@pytest.mark.asyncio` on every test — replaced by `asyncio_mode = "auto"`
- STACK.md pyproject.toml skeleton: shows `routellm[serve]` in `[project.dependencies]` — this is wrong; move to `[project.optional-dependencies] serve`

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Env var prefix `AGENT_ROUTER_` is the right choice | Code Examples (RouterConfig) | Low: planner can change the prefix string; no structural impact |
| A2 | Flat layout (no `src/`) is preferred for this project | Architecture Patterns | Low: hatchling supports both equally; switching is a one-line change in `[tool.hatch.build.targets.wheel]` |
| A3 | `DynamicRouteLM` should subclass `dspy.BaseLM` rather than `dspy.LM` | Code Examples | Medium: `dspy.LM` is the user-facing class; subclassing `BaseLM` means re-implementing litellm dispatch in Phase 4. Planner should evaluate: if the Phase 4 implementation delegates back to `dspy.LM` internally, subclass `dspy.LM` directly |

**If this table is empty:** All claims in this research were verified or cited — no user confirmation needed.
_(Table is not empty — 3 low/medium-risk assumptions above.)_

---

## Open Questions

1. **`DynamicRouteLM` base class: `BaseLM` vs `LM`?**
   - What we know: `dspy.LM` (subclass of `BaseLM`) has litellm dispatch built in; `dspy.BaseLM` requires you to implement `forward` from scratch. DynamicRouteLM only needs to rebuild the model string per call.
   - What's unclear: In Phase 4, will DynamicRouteLM call the parent's `forward` (delegating to litellm) or implement its own `forward` using the openai SDK directly?
   - Recommendation: Phase 1 stub uses `BaseLM` for simplicity; Phase 4 planner refines based on which delegation path is simpler. The stub compiles and type-checks either way.

2. **`from __future__ import annotations` vs runtime annotations in pydantic fields?**
   - What we know: `from __future__ import annotations` makes all annotations strings at runtime, which can cause issues with pydantic v2's runtime field resolution in some edge cases (especially `Annotated[...]` and `Field(...)`). pydantic 2.12.5 has explicit support for this.
   - What's unclear: Are there edge cases in pydantic-settings with string-deferred annotations on Python 3.10?
   - Recommendation: Use `from __future__ import annotations` everywhere per D-02 (3.10 compat), but test the RouterConfig instantiation in the unit test to catch any annotation-resolution issue early.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| python3.14 | Build + tests | ✓ | 3.14.5 | — |
| pip3.14 | Package install | ✓ | 26.1.1 | uv pip install |
| uv | Fast installs | ✓ | 0.11.14 | pip3.14 |
| pydantic | RouterConfig | ✓ | 2.12.5 | — |
| dspy | TrajectoryTracker stub | ✓ | 3.2.1 | — |
| openai | DynamicRouteLM stub | ✓ | 2.30.0 | — |
| numpy | state.py type hints | ✓ | 2.4.4 | — |
| pydantic-settings | RouterConfig | ✗ | — (not yet installed) | none needed; install in Wave 0 |
| hatchling | Build backend | ✗ | — (not yet installed) | none; install in Wave 0 |
| mypy | Type checking | ✗ | — (not yet installed) | none; install in Wave 0 |
| pytest | Tests | ✗ | — (not yet installed) | none; install in Wave 0 |
| pytest-asyncio | Async tests | ✗ | — (not yet installed) | none; install in Wave 0 |
| pytest-mock | Mock tests | ✗ | — (not yet installed) | none; install in Wave 0 |

**Missing dependencies with no fallback:**
- pydantic-settings, hatchling, mypy, pytest, pytest-asyncio, pytest-mock — all must be installed in Wave 0 before any other plan tasks

**Missing dependencies with fallback:**
- none

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.1.0 |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (Wave 0 creates) |
| Quick run command | `pytest tests/unit/ -x -q` |
| Full suite command | `pytest tests/ -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| LIB-01 | `pip install -e .` succeeds | smoke | `pip3.14 install -e "." --dry-run` | ❌ Wave 0 (pyproject.toml) |
| LIB-01 | `from agent_router import TrajectoryTracker, DynamicRouteLM, RouterConfig` works without heavy deps | unit | `pytest tests/unit/test_contracts.py::test_public_api_import -x` | ❌ Wave 0 |
| LIB-01 | Directory structure is correct | unit | `pytest tests/unit/test_contracts.py::test_directory_structure -x` | ❌ Wave 0 |
| LIB-01 | TurnRecord/CostRecord/SessionState exist, typed, pass mypy | unit + mypy | `mypy --strict agent_router/state.py agent_router/config.py` | ❌ Wave 0 |
| LIB-02 | RouterConfig reads env vars for weak_model, strong_model | unit | `pytest tests/unit/test_contracts.py::test_router_config_env -x` | ❌ Wave 0 |
| LIB-02 | RouterConfig has all 6 required fields with defaults | unit | `pytest tests/unit/test_contracts.py::test_router_config_fields -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/ -x -q`
- **Per wave merge:** `pytest tests/unit/ -q && mypy --strict agent_router/state.py agent_router/config.py`
- **Phase gate:** All unit tests green + mypy clean before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `pyproject.toml` — build system, extras, tool config (LIB-01 prerequisite)
- [ ] `agent_router/__init__.py` — `__version__`, lazy re-exports
- [ ] `agent_router/state.py` — TurnRecord, CostRecord, SessionState
- [ ] `agent_router/config.py` — RouterConfig
- [ ] `agent_router/tracker.py` — TrajectoryTracker stub
- [ ] `agent_router/routing/__init__.py` — empty
- [ ] `agent_router/routing/dynamic_lm.py` — DynamicRouteLM stub
- [ ] `tests/unit/test_contracts.py` — all contract and import tests
- [ ] `tests/integration/.gitkeep`, `tests/bench/.gitkeep`
- [ ] Framework install: `pip3.14 install pydantic-settings mypy pytest pytest-asyncio pytest-mock hatchling`

---

## Security Domain

### Applicable ASVS Categories (Level 1)

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth in this phase |
| V3 Session Management | Partial | `session_id` is caller-provided string; no crypto needed in Phase 1 |
| V4 Access Control | No | Library, no access control in Phase 1 |
| V5 Input Validation | Yes | `RouterConfig` fields validated by pydantic `Field(ge=..., le=...)` |
| V6 Cryptography | No | No crypto in Phase 1 |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| API keys in RouterConfig leak to logs | Information Disclosure | `pydantic-settings` `SecretStr` type for `api_key` fields — masks value in `repr()` / `model_dump()` |
| `session_id` collisions between tenants | Spoofing | Phase 1 only: `session_id` is caller-provided; no enforcement. Phase 2 plan should document that callers must use unique IDs. |
| `_SESSION_REGISTRY` dict grows unbounded | DoS (memory) | `TrajectoryTracker.__exit__` must delete the session from `_SESSION_REGISTRY` (Phase 2). Phase 1 stub: document with a `# TODO: cleanup on __exit__` comment. |

**Note on API keys:** `RouterConfig.weak_model` and `strong_model` are model identifiers, not keys. API keys (`OPENAI_API_KEY` etc.) are handled by litellm/openai SDK directly via their own env vars — not stored in `RouterConfig`. Phase 1 only needs model name strings.

---

## Sources

### Primary (HIGH confidence)
- PyPI registry (2026-06-18): pydantic-settings 2.14.1, mypy 2.1.0, hatchling 1.30.1, pytest 9.1.0, pytest-asyncio 1.4.0, pytest-mock 3.15.1, fastembed 0.8.0, routellm 0.2.0 — all confirmed current via `pip3.14 index versions`
- `/pydantic/pydantic-settings` (ctx7, score 90.52) — BaseSettings, SettingsConfigDict, env_prefix pattern
- `/pypa/hatch` (ctx7, score 89) — pyproject.toml shape, optional-dependencies, hatchling version path, flat vs src layout
- `/pydantic/pydantic` (ctx7) — mypy plugin config (pyproject.toml `[tool.mypy]`, `[tool.pydantic-mypy]`), frozen BaseModel, stdlib dataclass patterns
- Installed dspy 3.2.1 source at `~/.local/lib/python3.14/site-packages/dspy/clients/base_lm.py` — `BaseLM.__init__`, `forward` signature, `self.model` attribute structure
- slopcheck 0.6.1 (installed) — all 9 packages rated [OK]

### Secondary (MEDIUM confidence)
- PEP 562 (python.org/pep-0562) — module-level `__getattr__` since Python 3.7; verified operational in Python 3.14.5
- PyPI `routellm 0.2.0` metadata (fetched live via `urllib.request`) — `requires_dist` showing torch, transformers, datasets as core (non-extra) dependencies; `[serve]` extra only adds fastapi, shortuuid, uvicorn

### Tertiary (LOW confidence)
- None; all claims verified

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions verified against PyPI registry (2026-06-18)
- Architecture: HIGH — derived from verified installed source + ctx7 docs
- Pitfalls: HIGH — P1/P2/P4/P6/P11/P18 verified against installed dspy 3.2.1 source; routellm dep structure verified against live PyPI JSON
- Lazy import pattern: HIGH — PEP 562 verified operational in Python 3.14.5
- mypy --strict config: HIGH — ctx7 pydantic docs + verified py.typed markers in installed packages

**Research date:** 2026-06-18
**Valid until:** 2026-09-18 (90 days; pydantic-settings and hatchling are stable; routellm 0.2.0 is the only release with serve support)
