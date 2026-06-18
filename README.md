# agent-router

A **trajectory-monitoring router for DSPy agents** that solves the "Routing Plateau" by bridging
DSPy (compile-time prompt optimization) and RouteLLM (runtime cost/quality routing).

It silently observes a running DSPy agent's execution trajectory — per-step telemetry captured
**non-intrusively via DSPy callbacks** — scores that trajectory for pathologies (reasoning loops,
tool-call flapping, strict-format demands), and when an anomaly is detected, **dynamically forces
RouteLLM to escalate the next call from a cheap model to a frontier model** to clear the block.

> **Core hypothesis:** trajectory-aware routing beats single-prompt embedding routing — the router
> detects a stuck agent from telemetry alone (no LLM judge) and escalates automatically.

## Status

| Phase | What | State |
|-------|------|-------|
| 1 — Foundation & Contracts | package, typed contracts, config, lazy public API | ✅ done |
| 2 — State Capture Engine | `TrajectoryTracker` + callback, per-step telemetry | ✅ done |
| 3 — Dynamic Scoring Engine | structural / flapping / loop-velocity detectors + escalation cap | ✅ done |
| 4 — RouteLLM Execution Layer | `DynamicRouteLM` (per-call threshold → model string) | ✅ code + mock demo |
| 5 — Integration & Validation | synthetic loop bench + mock-server integration | ✅ free harness; ⏳ real-model run |

**Quality:** `mypy --strict` clean · **39 tests** (35 unit + 4 integration) · `import agent_router`
stays light (the embedder/RouteLLM are not loaded at import time).

**What's left = "Tier B" (needs an `OPENAI_API_KEY` + a small budget cap):** confirm on real models
that a weak model genuinely loops (≥80% of seeds) and that escalation genuinely clears it (VAL-01/02),
then a real benchmark with cost/quality numbers (VAL-03). Everything to date is proven for free with
a mock backend.

## Install

```bash
# Python 3.10+ (developed on 3.14)
pip install -e .                 # core (light: no torch, no onnxruntime)
pip install -e ".[embed]"        # + fastembed (BAAI/bge-small) for the loop-velocity detector
pip install -e ".[serve]"        # + RouteLLM server (pulls torch ~532 MB) — only for Tier B
```

## Quickstart

```bash
# See the loop-breaking causal chain end-to-end ($0, no key — mock backend):
python dev/demo_loop_break.py

# Synthetic loop bench (mock backend): loop rate + escalation clear rate across seeds:
python bench/synthetic_loop_bench.py --seeds 20

# Full test suite:
python -m pytest tests/ -q
mypy --strict agent_router/
```

The demo prints the chain: weak routing → identical outputs detected as a loop → `current_threshold`
forced to `0.0` → next call routes via `router-mf-0.0` (the strong model).

## How a developer uses the library

```python
import dspy
from agent_router import TrajectoryTracker, RouterConfig

dspy.configure(lm=...)                       # your normal DSPy setup
agent = dspy.ReAct("question -> answer", tools=[...])

with TrajectoryTracker(session_id="abc", config=RouterConfig()):
    result = agent(question="...")           # agent code is UNCHANGED
```

Capture is non-intrusive (DSPy callbacks). With a `config`, the tracker also scores each step and
applies escalation; without one it is capture-only.

## Architecture (three blocks, one coupling point)

```
DSPy agent ──(callbacks)──▶ TrajectoryCallback ──writes──▶ SessionState.window / .tool_events
                                                                  │ (the only shared contract)
                                            ScoringEngine ──reads window, writes──▶ current_threshold
                                                                  │
                              DynamicRouteLM ──reads threshold──▶ "router-mf-{threshold}" ──▶ RouteLLM
```

- **Block 1 — Capture** (`agent_router/capture.py`, `tracker.py`): `BaseCallback` captures one
  `TurnRecord` per LM step (handles ReAct's extract overcount, token usage from `lm.history`,
  signature identity incl. inline `StringSignature`, exceptions) + `ToolEvent`s via `on_tool_*`.
- **Block 2 — Scoring** (`agent_router/scoring.py`): `StructuralConstraintScanner` (regex override,
  runs first) → `ToolCallFlappingMonitor` → `LoopVelocityProfiler` (fastembed cosine + a
  changed-observation false-positive gate). Per-session escalation cap + logging. **No LLM judge.**
- **Block 3 — Routing** (`agent_router/routing/dynamic_lm.py`): `DynamicRouteLM` rebuilds the
  RouteLLM model string per call from the live threshold (thread-safe), normalizes few-shot payloads,
  logs cost. RouteLLM threshold is per-request via the model string — no controller patching.

All thresholds live in `RouterConfig` (env-overridable via `AGENT_ROUTER_*`).

## Layout

```
agent_router/        # the library (state.py, config.py, capture.py, tracker.py, scoring.py, routing/)
tests/unit/          # per-block unit tests
tests/integration/   # mock RouteLLM server + full-pipeline tests
bench/               # synthetic_loop_bench.py (mock now, --real for Tier B)
dev/                 # demo_loop_break.py + verified API research (research-dspy-routellm.md)
.planning/           # GSD planning: PROJECT.md, ROADMAP.md, REQUIREMENTS.md, per-phase docs
scope                # the original project scope
```

## Continuing the work (Tier B)

1. `pip install -e ".[serve,embed,bench]"`
2. Provide a key (kept out of git via `.gitignore`):
   `echo 'OPENAI_API_KEY=sk-...' >> .env`
3. Launch a RouteLLM server (weak/strong pair) and point `DynamicRouteLM(api_base=...)` at it; or
   wire the model pair via `RouterConfig` (`AGENT_ROUTER_WEAK_MODEL` / `AGENT_ROUTER_STRONG_MODEL`).
4. Run the bench with real models: `python bench/synthetic_loop_bench.py --real` (mind the budget).
5. See `.planning/phases/05-*/` for the VAL-01/02/03 acceptance criteria.

Detailed, source-verified API notes live in `dev/research-dspy-routellm.md` and the per-phase
`.planning/phases/*/` research files.
