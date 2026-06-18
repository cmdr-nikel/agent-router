# agent-router

## What This Is

A trajectory-monitoring router for DSPy agents that solves the "Routing Plateau" by bridging
DSPy's compile-time optimization with RouteLLM's runtime cost/quality routing. It silently
observes a running DSPy agent's execution trajectory (per-step telemetry, captured non-intrusively
via DSPy callbacks), scores that trajectory for pathologies (reasoning loops, tool-call flapping,
strict-format demands), and — when an anomaly is detected — dynamically forces RouteLLM to escalate
the next call from a cheap model to a frontier model to clear the block.

It is a production-ready Python library that developers add to their existing DSPy code without
changing their agent logic, while also serving as a research vehicle to validate the core
hypothesis: *trajectory-aware routing beats single-prompt embedding routing.*

## Core Value

When an agent gets stuck in a reasoning loop or tool-call flapping, the router detects it from
trajectory telemetry alone (no LLM judge) and automatically escalates to a frontier model that
clears the block — demonstrably, on a reproducible scenario.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

- ✓ **LIB-01** (pip-installable package, clean light public API, typed contracts, dir structure) — Phase 1
- ✓ **LIB-02** (config-driven weak→strong model pair via RouterConfig + env) — Phase 1

### Active

<!-- Current scope. Building toward these. v1 = full scope. -->

**Block 1 — State Capture Engine (non-intrusive DSPy integration)**
- [ ] Developer wraps existing DSPy calls in `with TrajectoryTracker(session_id=...):` without changing agent logic
- [ ] Tracker captures per step: active `Signature` class name, step index within the loop, input-token count vs expected output length
- [ ] Tracker captures success/failure signals via exceptions surfaced in DSPy callbacks (Suggest/Assert are gone in DSPy 3.x — failures now surface as exceptions)
- [ ] Capture is implemented via DSPy's official callback system (`BaseCallback` + `dspy.settings.configure(callbacks=[...])`), not by patching DSPy internals

**Block 2 — Dynamic Scoring Engine (the brain)**
- [ ] Maintains a sliding window of the last N turns per session
- [ ] Loop Velocity Profiler: detects when similar inputs produce repeating output embeddings across consecutive turns (stuck in a reasoning loop)
- [ ] Tool-Call Flapping Monitor: flags when the same tool is called with slightly varied params >2× without state change
- [ ] Structural Constraint Scanner: programmatic regex (not semantics) detects strict-format demands (JSON Schema, valid XML, compilable syntax) and bypasses standard routing profiles
- [ ] All scoring is based on mathematical telemetry / regex / post-generation tracking only — no pre-inference LLM judge

**Block 3 — RouteLLM Execution Layer**
- [ ] Dynamic threshold control: the router sets RouteLLM's threshold per-call via the model string `router-{name}-{threshold}` (no header patch needed — threshold is already per-request)
- [ ] Automated Escalation Protocol: on a flagged anomaly, the next call's threshold drops to 0.0, forcing RouteLLM past the cheap model straight to the frontier model
- [ ] Payload Shape Normalization: DSPy few-shot demos pass through RouteLLM (OpenAI-compatible) without dict mutation / KeyError
- [ ] Cost tracking: every routed call's cost/tokens are logged (cost-cap auto-stop deferred — see Out of Scope)

**Validation (research arm)**
- [ ] Synthetic loop bench: a toy DSPy agent + task on which the weak model reliably loops, used to prove escalation clears the block
- [ ] Real benchmark confirmation: reproduce the effect on a real task set (e.g. GSM8K / HotpotQA / code)

### Out of Scope

<!-- Explicit boundaries. -->

- **Custom web gateway / proxy / API-key management / rate-limiting** — RouteLLM + LiteLLM own 100% of the raw plumbing
- **Pre-inference LLM-as-judge** — all routing analysis must be telemetry/regex/post-generation; no extra LLM query before routing
- **Replacing DSPy optimizers (MIPROv2 etc.)** — DSPy compiles prompts; we compile runtime operational safety and cost
- **Patching RouteLLM's internal controller / `X-RouteLLM-Threshold` header** — unnecessary; threshold is already per-request via the model string (research finding, 2026-06-18)
- **Budget auto-stop / hard cost-cap** — deferred; v1 logs cost, an approximate cap is computed later from real numbers
- **Wrapping `dspy.LM` for telemetry capture** — callbacks are the non-intrusive path; an LM subclass is used ONLY for the dynamic-threshold routing target

## Context

- **Ecosystem:** DSPy `3.2.1` installed (Python 3.14, user-local at `~/.local/lib/python3.14/site-packages`). RouteLLM (`lm-sys/routellm`) NOT yet installed; it has an in-process `Controller` and an OpenAI-compatible server mode (`python -m routellm.openai_server`, default `:6060`), backed by LiteLLM for provider calls.
- **Prior research:** `dev/research-dspy-routellm.md` (verified 2026-06-18 against installed source + ctx7) documents: DSPy callback hooks (`on_module_*`, `on_lm_*`, `on_tool_*` with `call_id`, `instance`, `inputs`/`outputs`, `exception`), usage tracking (`track_usage` + `UsageTracker`), ReAct loop shape, and RouteLLM's per-request `router-mf-<threshold>` model-string mechanism.
- **Original scope:** captured in `./scope`. Two of its assumptions were corrected by research (Suggest/Assert removed; threshold header unnecessary).
- **Author background:** ML/DS engineer; runs related agent-optimization work (Stratum). Comfortable with DSPy, conda+uv stack.

## Constraints

- **Tech stack**: Python 3.14, DSPy 3.2.1, RouteLLM (lm-sys), LiteLLM — pin versions; verify APIs against installed source, not memory
- **Capture mechanism**: must be non-intrusive (DSPy callbacks); developer agent code stays unchanged
- **Routing**: must not patch RouteLLM/LiteLLM internals — use per-request model string + OpenAI-compatible interface
- **Models**: weak→strong pair is config-driven; default cheap API (e.g. gpt-4o-mini / haiku) → frontier API (Opus / GPT-5)
- **Budget**: frontier escalation = paid calls; v1 logs cost per call; approximate cost-cap computed later (machine is RAM-tight — keep experiments small)
- **Distribution**: production-ready, pip-installable library — clean public API surface

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Capture via DSPy callbacks (Strategy A), not LM wrapping | Official, stable, non-intrusive; hooks expose Signature, step index, tokens, exceptions, tool calls | — Pending |
| Dynamic threshold via `router-mf-<threshold>` model string, not a header patch | RouteLLM threshold is already per-request; avoids patching RouteLLM (honors "no custom gateway") | — Pending |
| Detect failures via callback `exception` arg | `dspy.Suggest`/`dspy.Assert` removed in DSPy 3.x; failures surface as exceptions | — Pending |
| Own git repo (`git init`), not the outer `/home/cmdr-nikel` repo | Clean separation for a production-ready repository | ✓ Good |
| v1 = full scope, all 3 blocks + all detectors | User wants the complete pipeline, validated, in the first iteration | — Pending |
| Validation: synthetic loop bench → real benchmark | Need a reproducible looping scenario to demonstrate loop-breaking before trusting real-task numbers | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-18 after Phase 1 (Foundation & Contracts) completion*
