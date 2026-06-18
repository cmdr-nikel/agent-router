<!-- GSD:project-start source:PROJECT.md -->
## Project

**agent-router**

A trajectory-monitoring router for DSPy agents that solves the "Routing Plateau" by bridging
DSPy's compile-time optimization with RouteLLM's runtime cost/quality routing. It silently
observes a running DSPy agent's execution trajectory (per-step telemetry, captured non-intrusively
via DSPy callbacks), scores that trajectory for pathologies (reasoning loops, tool-call flapping,
strict-format demands), and — when an anomaly is detected — dynamically forces RouteLLM to escalate
the next call from a cheap model to a frontier model to clear the block.

It is a production-ready Python library that developers add to their existing DSPy code without
changing their agent logic, while also serving as a research vehicle to validate the core
hypothesis: *trajectory-aware routing beats single-prompt embedding routing.*

**Core Value:** When an agent gets stuck in a reasoning loop or tool-call flapping, the router detects it from
trajectory telemetry alone (no LLM judge) and automatically escalates to a frontier model that
clears the block — demonstrably, on a reproducible scenario.

### Constraints

- **Tech stack**: Python 3.14, DSPy 3.2.1, RouteLLM (lm-sys), LiteLLM — pin versions; verify APIs against installed source, not memory
- **Capture mechanism**: must be non-intrusive (DSPy callbacks); developer agent code stays unchanged
- **Routing**: must not patch RouteLLM/LiteLLM internals — use per-request model string + OpenAI-compatible interface
- **Models**: weak→strong pair is config-driven; default cheap API (e.g. gpt-4o-mini / haiku) → frontier API (Opus / GPT-5)
- **Budget**: frontier escalation = paid calls; v1 logs cost per call; approximate cost-cap computed later (machine is RAM-tight — keep experiments small)
- **Distribution**: production-ready, pip-installable library — clean public API surface
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.14.5 (installed) | Runtime | Already on machine; sentence-transformers 5.6.0 and routellm 0.2.0 both ship cp314 wheels — no ABI friction |
| DSPy | 3.2.1 (installed) | Agent framework being monitored; `BaseCallback` / `dspy.settings.configure` are the capture hooks | Already installed and verified against source; `BaseCallback` lives at `dspy/utils/callback.py` |
| RouteLLM | 0.2.0 (latest on PyPI) | Runtime LLM router; exposes `python -m routellm.openai_server` on `:6060` and in-process `Controller` | Only release with full server + eval extras; `router-mf-{threshold}` model-string mechanism verified in ctx7 |
| LiteLLM | 1.83.7 (installed) | Multi-provider backend underneath RouteLLM | Already installed as DSPy dependency; RouteLLM delegates all provider calls to it; no separate install needed |
| openai (SDK) | 2.30.0 (installed) | OpenAI-compatible client for talking to the RouteLLM server | Already installed; `openai.OpenAI(base_url="http://localhost:6060/v1")` is the standard RouteLLM client pattern |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| fastembed | 0.8.0 (latest) | Local CPU embeddings for Loop Velocity Profiler — cosine similarity between consecutive turn outputs | Use instead of sentence-transformers; pulls only onnxruntime (no torch, no CUDA) — critical on 16 GiB RAM-tight box; verified dry-run: 6 new packages vs ~15 + CUDA stack |
| onnxruntime | 1.27.0 (pulled by fastembed) | ONNX inference backend for embedding model; no GPU required | Automatic transitive dep of fastembed; no direct pin needed |
| numpy | 2.4.4 (installed) | Cosine similarity computation, sliding-window arrays | Already installed; use `np.dot` + `np.linalg.norm` for cosine distance — no scipy needed |
| pydantic | 2.12.5 (installed) | Typed config models (`RouterConfig`, `ScoringConfig`) for the library's public API | Already installed as DSPy/RouteLLM dependency; use for config validation, not for internal state |
| hatch | 1.17.0 (latest) | Build backend (`hatchling`) + project manager for the pip-installable library | Standard pyproject.toml build; integrates natively with uv as installer; `[tool.hatch.envs.default] installer = "uv"` documented and verified |
### Development Tools
| Tool | Purpose | Notes |
|------|---------|-------|
| uv | 0.11.14 (installed) | Fast dependency resolution and venv creation | Set as hatch env installer via `[tool.hatch.envs.default] installer = "uv"`; use `uv pip install -e ".[dev]"` for editable installs in conda env |
| pytest | 9.1.0 (latest) | Test runner | Current stable; verified Python 3.14 support |
| pytest-asyncio | 1.4.0 (latest) | Async test support for DSPy callback hooks (which fire in async contexts) | 1.x is a major release; verified: adds Python 3.14 preliminary support (changelog 2025-05-26); configure with `asyncio_mode = "auto"` in `pyproject.toml` to avoid per-test decorator clutter |
| pytest-mock | latest (`~3.x`) | Mock DSPy LM calls in unit tests without live API calls | Standard; use for testing scoring engine in isolation |
| hatchling | (bundled with hatch) | Build backend declared in `[build-system]` | Replaces setuptools; dynamic version read from `__version__` or VCS tag |
## Installation
# Install build tooling (if not present)
# Install RouteLLM with server + eval extras (not yet installed)
# Install embedding library (no torch pulled in)
# Install dev/test tools
# Install the library itself in editable mode (once pyproject.toml exists)
### Running the RouteLLM local server
# Export provider keys
# Start server (default port 6060)
# Calibrate threshold (optional, to hit a target strong-model call rate)
# Client points at it with per-request threshold in model string
# Normal routing: model="router-mf-0.11593"
# Force frontier:  model="router-mf-0.0"
### pyproject.toml skeleton
## Alternatives Considered
| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| fastembed 0.8.0 | sentence-transformers 5.6.0 | Only if you need training/fine-tuning of the embedding model, or need a model not in fastembed's library; sentence-transformers pulls torch 2.12.1 + full CUDA stack (~2–4 GB install) — prohibitive on 16 GiB machine |
| fastembed 0.8.0 | OpenAI `text-embedding-3-small` API | Only if RAM is tighter than expected and you can't afford onnxruntime; adds latency + cost per loop-detection call; defeats the "no extra LLM calls" principle — avoid |
| hatchling build backend | setuptools | setuptools if the project must support Python < 3.8 or extremely legacy CI; no reason here — hatch 1.17.0 with uv is the current standard |
| pytest-asyncio 1.4.0 | anyio | anyio if the codebase adopts anyio primitives throughout; overkill here — DSPy callbacks are asyncio-native and pytest-asyncio covers the test surface cleanly |
| RouteLLM server mode | RouteLLM in-process Controller | In-process Controller if you want to embed routing inside the same process without running a server; valid option for integration tests; server mode is recommended for production separation of concerns |
| uv (as installer) | pip directly | pip if deploying to an environment without uv; the pyproject.toml is pip-compatible — uv is only the dev-time accelerator |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| sentence-transformers for embeddings | Pulls torch 2.12.1 + CUDA toolkit (nvidia_cublas, nvidia_cudnn, etc.) verified in dry-run — multi-GB install that will swap-thrash on a 16 GiB APU box with ~1 GiB stolen by GPU | fastembed 0.8.0: only onnxruntime + 5 small packages, verified dry-run |
| `dspy.Suggest` / `dspy.Assert` interception | Removed in DSPy 3.x (verified against installed 3.2.1 source); replaced by `dspy.Refine`/`dspy.BestOfN`; failures now surface as exceptions via callback `exception` arg | `on_module_end(exception=...)` / `on_lm_end(exception=...)` callback hooks |
| Patching RouteLLM internals or `X-RouteLLM-Threshold` header | Unnecessary; threshold is already per-request via the `router-mf-{threshold}` model string (verified in ctx7 docs + README); patching breaks upgrade compatibility | `model="router-mf-0.0"` in the OpenAI-compatible request |
| Wrapping `dspy.LM` for telemetry capture (Strategy B for capture) | Couples library to LM internals; misses Signature-level and tool-level structure that callbacks expose directly (verified in research doc A.2) | `BaseCallback` subclass registered via `dspy.settings.configure(callbacks=[...])` — use Strategy B ONLY for the dynamic-threshold routing LM |
| `setuptools` as build backend | Legacy; hatchling + hatch 1.17.0 is the current pip-installable library standard with uv integration | `hatchling` in `[build-system]` |
| `pip install -e .` (bare pip) for dev | Slower than uv; no lock file; fine for one-off but inconsistent across devs | `uv pip install -e ".[dev]"` |
| pytest-asyncio 0.x | Deprecated configuration style (`@pytest.mark.asyncio` required everywhere); 1.x adds Python 3.14 support | pytest-asyncio 1.4.0 with `asyncio_mode = "auto"` |
| `routellm[eval]` extras in production dep | Pulls `datasets==5.0.0` + `pandarallel` + `matplotlib` — 100+ MB of data-science tooling irrelevant to production routing | Use `routellm[serve]` in `[project.dependencies]`; put `routellm[serve,eval]` in dev/bench extras only |
## Stack Patterns by Variant
- Add `routellm[eval]` to a `[project.optional-dependencies] bench` extra
- Keep it out of the default install — users of the library do not need eval datasets
- Use `python -m routellm.openai_server` with `--weak-model ollama_chat/llama3` (local Ollama) for zero API cost
- Calibrate with `python -m routellm.calibrate_threshold` before benchmarking thresholds
- Use RouteLLM in-process `Controller` for isolation tests
- Mock the `Controller.chat.completions.create` call with `pytest-mock` for pure scoring-engine tests
- Pre-download model to a fixture cache directory (`~/.cache/fastembed`) and mount it in CI
- Default model to use: `BAAI/bge-small-en-v1.5` — 22 MB ONNX, verified in fastembed docs as CPU default
## Version Compatibility
| Package | Compatible With | Notes |
|---------|-----------------|-------|
| dspy 3.2.1 | Python 3.14.5 | Installed and working; `BaseCallback` at `dspy/utils/callback.py` verified in source |
| routellm 0.2.0 | litellm 1.83.7, openai 2.30.0 | dry-run confirms all deps already satisfied; no conflicts |
| fastembed 0.8.0 | onnxruntime 1.27.0, numpy 2.4.4 | dry-run: 6 new packages total; no torch conflict |
| sentence-transformers 5.6.0 | torch 2.12.1 | NOT recommended — confirmed via dry-run: pulls CUDA stack |
| pytest-asyncio 1.4.0 | pytest 9.1.0, Python 3.14 | Changelog explicitly adds Python 3.14 preliminary support in 1.0.0 (2025-05-26); `asyncio_mode="auto"` required in pyproject.toml |
| hatchling (hatch 1.17.0) | uv 0.11.14 | `[tool.hatch.envs.default] installer = "uv"` documented; no conflict |
## Sources
- `/lm-sys/routellm` (ctx7, score 65.5) — server launch command, model string format `router-mf-{threshold}`, in-process Controller API, `pip install "routellm[serve,eval]"` syntax
- `/websites/sbert_net` (ctx7, score 87.32) — CPU quantization, `all-MiniLM-L6-v2` usage pattern
- `/qdrant/fastembed` (ctx7, score 75.59) — `BAAI/bge-small-en-v1.5` CPU model, cosine similarity pattern, ONNX backend
- `/pypa/hatch` (ctx7, score 89) — `hatchling` build backend config, uv installer option, optional-dependencies pattern
- `/websites/pytest-asyncio_readthedocs_io_en_stable` (ctx7, score 87.42) — `asyncio_mode = "auto"` config, Python 3.14 support added in 1.0.0
- Installed source: `dspy 3.2.1` at `~/.local/lib/python3.14/site-packages/dspy/utils/callback.py` — `BaseCallback` class confirmed
- PyPI dry-runs (verified 2026-06-18): fastembed 0.8.0 vs sentence-transformers 5.6.0 dependency chain comparison; routellm 0.2.0 dep resolution
- `dev/research-dspy-routellm.md` (verified 2026-06-18) — DSPy callback hooks, usage tracking, RouteLLM threshold mechanism; not duplicated here
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
