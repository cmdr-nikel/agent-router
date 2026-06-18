# Project Research Summary

**Project:** agent-router
**Domain:** Python library -- trajectory-monitoring router bridging DSPy compile-time optimization and RouteLLM runtime cost/quality routing
**Researched:** 2026-06-18
**Confidence:** HIGH

## Executive Summary

agent-router is a niche production library that solves a specific gap: existing observability platforms (Langfuse, MLflow, Phoenix) detect loops retroactively through logging, and RouteLLM routes by single-prompt embedding complexity -- neither addresses the "agent stuck in a loop, escalate to frontier to break out" scenario prospectively and automatically. The project bridges DSPy's callback hook system with RouteLLM's per-request threshold mechanism, inserting trajectory-aware routing logic between them without touching agent code. All four research tracks agree that the library is feasible with installed dependencies (DSPy 3.2.1 + LiteLLM + openai SDK), requires only two new installs (RouteLLM and fastembed), and follows a forced linear build order that cannot be resequenced.

The recommended approach is a three-block architecture (State Capture -> Scoring Engine -> RouteLLM Execution), validated by two bench phases (synthetic loop bench -> real benchmarks). The critical implementation path is: DSPy BaseCallback subclass for all telemetry capture; dspy.context() (not dspy.configure()) for scoped callback registration; lazy embeddings via fastembed 0.8.0 on ONNX runtime (no torch, no CUDA -- the machine has 16 GiB with ~1 GiB stolen by APU); and a thin dspy.LM subclass that rebuilds the router-mf-{threshold} model string per call. The threshold override mechanism is already built into RouteLLM: sending model="router-mf-0.0" routes 100% to the strong model, no header patching required.

The three highest-leverage risks all surface in Block 1 and must be resolved before any scoring logic is written: (1) on_lm_end does not carry token usage -- the callback receives processed text, not the response envelope, requiring a UsageTracker or lm.history read path; (2) dspy.configure(callbacks=[cb]) replaces existing callbacks -- use dspy.context() with the existing list prepended instead; (3) ReAct fires on_module_start for the outer episode AND for each inner Predict step -- naive counting triple-counts steps, breaking the Loop Velocity Profiler's window logic. All three are verifiable with a single integration test before Block 2 is started.

## Key Findings

### Recommended Stack

All core dependencies except RouteLLM and fastembed are already installed. The two net-new installs are deliberately chosen to be lightweight: fastembed 0.8.0 brings 6 packages (onnxruntime + small helpers) vs sentence-transformers which would pull torch 2.12.1 + the full CUDA stack -- a multi-GB install that would swap-thrash the 16 GiB APU box. RouteLLM 0.2.0 (the only release with full server + eval extras) is pip-installable with the [serve] extra and delegates all provider calls to the already-installed LiteLLM 1.83.7. The build toolchain (hatchling + hatch 1.17.0 with uv as installer) is the current standard for pip-installable Python libraries.

**Core technologies:**
- Python 3.14.5 (installed): Runtime -- no ABI friction; all key packages ship cp314 wheels
- DSPy 3.2.1 (installed): Agent framework being monitored; BaseCallback / dspy.settings.configure are the capture hooks -- verified in source at dspy/utils/callback.py
- RouteLLM 0.2.0 (not yet installed): Runtime router; router-mf-{threshold} model-string mechanism is the per-call threshold signal -- no patching required
- LiteLLM 1.83.7 (installed): Multi-provider backend underneath RouteLLM -- already present as DSPy dependency; transparent
- fastembed 0.8.0 (not yet installed): Local CPU embeddings for Loop Velocity Profiler -- ONNX backend, no torch, 6 transitive packages; default model BAAI/bge-small-en-v1.5 (22 MB)
- pydantic 2.12.5 (installed): Typed config models (RouterConfig, ScoringConfig) for public API validation
- hatchling + hatch 1.17.0: Build backend for pip-installable library; [tool.hatch.envs.default] installer = "uv" documented

**Do NOT use:**
- sentence-transformers: pulls torch + CUDA stack -- verified in dry-run to be prohibitive on this machine
- dspy.Suggest / dspy.Assert: removed in DSPy 3.x -- failures surface via callback exception arg
- dspy.configure(callbacks=[cb]) for scoped registration: replaces all existing callbacks globally

### Expected Features

The library's v1 scope is full scope: all three blocks plus both validation phases. There is no deferred MVP -- the user explicitly requires the complete validated pipeline in the first iteration.

**Must have (table stakes):**
- Non-intrusive context manager (with TrajectoryTracker(session_id=...):) -- zero agent-code changes
- Per-step Signature capture, step index tracking, exception/failure signal capture
- Session-isolated sliding window state (per-session_id deque, configurable depth)
- RouteLLM escalation on anomaly flag -- without this, detection has no consequence
- Payload shape normalization for few-shot demos -- prevents KeyError on compiled DSPy programs
- Per-call cost logging (model, tokens, estimated cost)
- Clean pip-installable public API with type hints

**Should have (differentiators -- these are the entire value proposition):**
- Loop Velocity Profiler: cosine similarity across consecutive turn output embeddings -- prospective loop detection, no LLM judge
- Tool-Call Flapping Monitor: same tool + varied params + no state change -- addresses what embedding routers miss
- Structural Constraint Scanner: regex-based format demand detection (JSON Schema, XML, compilable syntax) -- binary override signal, bypasses probabilistic scoring
- Dynamic per-call threshold control via router-mf-{threshold} model string rebuild -- not static routing
- Trajectory-aware escalation (multi-turn sequence, not single-prompt embedding)
- Automated Escalation Protocol: anomaly -> threshold 0.0 -> frontier call -> resume normal
- Synthetic loop bench: reproducible scenario proving escalation works; weak model loops >=8/10 seeds
- Real benchmark confirmation (GSM8K / HotpotQA / code): research credibility

**Defer to v1.x or v2+:**
- Budget auto-stop / hard cost cap -- requires real escalation cost data from v1 logs first
- OpenTelemetry / Langfuse trace export -- requires user demand signal
- Configurable detector weights -- requires real-task data showing which detector dominates
- Async / streaming callback support -- defer until async DSPy usage is confirmed
- Fleet-level aggregate analytics -- requires v1 production data

### Architecture Approach

The system is three blocks sharing a single SessionState object per session. TrajectoryCallback (Block 1) observes DSPy events and writes TurnRecord structs to the session's sliding window deque. After each module-end, ScoringEngine (Block 2) reads the window, runs three detectors, and writes current_threshold back to the session. DynamicRouteLM (Block 3) reads current_threshold before each LM call and rebuilds the router-mf-{t} model string. The only shared mutable state is SessionState; the only cross-block communication channel is that single object. The design is single-process, in-memory for v1 -- no IPC, no external store.

**Major components:**
1. TrajectoryTracker (context manager) -- lifecycle owner; registers/deregisters callback via dspy.context(); allocates and cleans up SessionState; wires all three blocks together
2. TrajectoryCallback (BaseCallback subclass) -- observes DSPy hooks; constructs TurnRecord per step; pushes to SessionState.window; triggers ScoringEngine.score() in on_module_end
3. SessionState / TurnRecord / session registry -- single shared mutable object per session; deque(maxlen=N) window; current_threshold float; escalation_flag bool; cost_log list
4. ScoringEngine -- orchestrates LoopVelocityProfiler, FlappingMonitor, StructuralConstraintScanner; writes current_threshold and escalation_flag to session on each turn
5. LoopVelocityProfiler -- lazy embedding similarity over last-K outputs; uses fastembed; caches embeddings on TurnRecord after first computation
6. FlappingMonitor -- counts same tool name in window; flags when args vary but observation content unchanged
7. StructuralConstraintScanner -- regex over input text; binary override path that bypasses probabilistic scoring
8. DynamicRouteLM (thin dspy.LM subclass) -- reads current_threshold from session registry by session_id; rebuilds router-mf-{t} model string per call; appends CostRecord to session
9. PayloadNormalizer -- strips non-standard fields from message dicts before RouteLLM forward; guards the escalated call from KeyError on DSPy few-shot demos

### Critical Pitfalls

The research identified 18 pitfalls across 4 categories. The ones that change implementation decisions (not just testing strategy) are:

1. **on_lm_end does NOT carry token usage** -- the callback receives processed text (list[str]), not the response envelope; outputs["usage"] raises KeyError immediately. Use dspy.settings.configure(track_usage=True) with UsageTracker, or read lm.history[-1]["usage"] after each step. Must be resolved in Block 1 before any token-based scoring is layered on top.

2. **dspy.configure(callbacks=[cb]) REPLACES, not appends** -- silently drops the user's existing Langfuse/MLflow callbacks. Use dspy.context(callbacks=existing + [self._callback]) inside __enter__ to get scoped, non-destructive registration. Non-negotiable for a library that runs inside user code.

3. **ReAct overcounts on_module_start events** -- the outer ReAct instance fires once, each inner Predict step fires once, and the final ChainOfThought extract fires once. Naive step counting gives 3x the actual iteration count, breaking the Loop Velocity Profiler's window trigger. Filter by call_id depth or by instance.__class__.__name__ == "Predict" to count only atomic steps.

4. **Inline signatures produce "StringSignature" for __name__** -- any agent using dspy.Predict("question -> answer") makes all steps look identical. Fall back to instance.__class__.__name__ + sorted field names as the step identity key. Must be defined before the scoring engine is built.

5. **dspy.LM.model mutation is a race condition** -- setting lm.model at escalation time from one thread overwrites another thread's model string. DynamicRouteLM must rebuild the model string inside its own __call__ / forward override from a thread-safe read of session.current_threshold -- never mutate a shared instance's .model attribute.

6. **Few-shot demo KeyError at RouteLLM boundary** -- DSPy's compiled demos may carry non-standard fields that some LiteLLM providers reject with 400 Unknown parameter. PayloadNormalizer strips all keys except role, content, tool_calls, tool_call_id, name. The first end-to-end integration test must use a compiled program with demos.

7. **Synthetic bench requires cache=False** -- DSPy's cache=True default means identical prompts return cached responses in sub-millisecond time, making the loop bench look reliable when it is actually a cache artifact. dspy.LM("weak-model", cache=False) is mandatory on the bench model.

8. **Per-session escalation cap required before any real-model sweep** -- a miscalibrated Loop Velocity threshold routes every call to frontier. At $0.015-$0.05 per frontier call, a 1,000-run eval sweep can burn $500 before anyone notices. Add a configurable max_escalations_per_session cap in Block 3 before any real-model evaluation runs.

## Implications for Roadmap

The build order is strictly forced by data dependencies: capture must exist before scoring can consume data; scoring must exist before routing can read its signal; both must be complete before the bench can validate the full pipeline. No block can be partially built and handed off -- the unit test at the end of each block is the gate for the next.

### Phase 1: State Capture Engine (Block 1)

**Rationale:** Every other block depends on telemetry data flowing into SessionState. No scoring, no routing, no bench is possible without it. Contains the highest density of critical pitfalls that change implementation decisions if discovered late.

**Delivers:** TrajectoryTracker context manager; TrajectoryCallback (BaseCallback subclass); SessionState / TurnRecord / session registry; dspy.context() scoped registration; token collection strategy resolved; step identity scheme defined.

**Addresses:** Non-intrusive context manager; per-step Signature capture; step index tracking; exception capture; session isolation; sliding window state.

**Must resolve before exiting Phase 1:**
- P1: on_lm_end usage absence -- verify token collection path in first integration test
- P2: "StringSignature" -- define step identity hash before scoring engine is built
- P3: ContextVar thread isolation -- document threading contract; use threading.Lock on window writes
- P4: configure() vs context() -- first integration test asserts pre-existing callback survives
- P5: Callback input mutation -- deep-copy or scalar extraction rule codified in callback base
- P6: Exception path in on_lm_end -- inject mock LM that raises; assert no crash
- P7: ReAct double-counting -- 5-iter ReAct trace asserts step count == 5, not 10-15

**Research flag:** Standard patterns -- no additional research phase needed.

### Phase 2: Dynamic Scoring Engine (Block 2)

**Rationale:** Requires the Phase 1 SessionState and TurnRecord structs as input. The three detectors are independent of each other and can be built in parallel within this phase, but all require the window data structure from Phase 1.

**Delivers:** ScoringEngine; LoopVelocityProfiler (fastembed, lazy embeddings, configurable cosine threshold); FlappingMonitor (tool-call repetition counter); StructuralConstraintScanner (regex library for format demands); RouterConfig (all thresholds as config, not hardcoded); per-session escalation cap wired in.

**Addresses:** Loop Velocity Profiler; Tool-Call Flapping Monitor; Structural Constraint Scanner; trajectory-aware escalation (scoring side); per-session escalation rate limiter.

**Must resolve before exiting Phase 2:**
- P8: Similarity threshold -- expose as config; calibrate on synthetic bench
- P9: Embedding latency -- warm fastembed at __enter__; lazy compute inside analyzer; measure <20ms warm
- P10: Tool retry vs loop false positive -- combined signal (similarity + unchanged observation); minimum step window before trigger
- P17: Runaway escalation cost -- per-session max_escalations_per_session cap + escalation event logging before any real-model sweep

**Research flag:** Standard patterns for FlappingMonitor and StructuralConstraintScanner. Loop Velocity threshold is empirical (suggested 0.85), calibrate on Phase 4 bench.

### Phase 3: RouteLLM Execution Layer (Block 3)

**Rationale:** Requires session.current_threshold from Phase 2 to be written before DynamicRouteLM can read it. Also requires RouteLLM server to be running -- install and server-start is a Phase 3 prerequisite.

**Delivers:** DynamicRouteLM (thin dspy.LM subclass, per-call model string rebuild); PayloadNormalizer (few-shot demo guard); per-call cost logging; RouteLLM server integration tested end-to-end; calibration command documented.

**Addresses:** Dynamic per-call threshold control; Automated Escalation Protocol (execution side); payload shape normalization; per-call cost logging.

**Must resolve before exiting Phase 3:**
- P11: dspy.LM.model race -- DynamicRouteLM.__call__ reads threshold from session at call time, not from instance state
- P12: MF router threshold calibration -- run calibrate_threshold with actual model pair; store result in config
- P13: Server vs in-process -- default to server mode; mock server stub for unit tests
- P14: Few-shot KeyError -- first end-to-end integration test uses compiled program with demos
- P18: cost=None on cache hits -- track billed_cost and cache_hit_count separately

**Research flag:** RouteLLM model string format and server mode are verified. PayloadNormalizer scope must be confirmed empirically on first compiled-program integration test -- cannot be fully specified from research alone.

### Phase 4: Validation

**Rationale:** All three blocks must be complete before validation is meaningful. Synthetic bench comes first because it is the controlled proof of concept; if the escalation mechanism fails there, real benchmark numbers are uninterpretable.

**Delivers:** bench/synthetic_loop_bench.py (reproducible toy agent; weak model loops >=8/10 seeds; escalation clears block demonstrated); real benchmark results (GSM8K / HotpotQA / code) with cost logging; core hypothesis validated or refuted.

**Must resolve before bench is trusted:**
- P15: Weak model doesn't reliably loop -- design task around "almost helpful" tool responses; test >=10 seeds; target >80% loop rate
- P16: Cache masks loops -- cache=False on weak model LM; verify non-trivial wall time per iteration

**Research flag:** Synthetic bench task design is a research design problem. Needs experimentation (TIDE paper reference: 15.8% loop rate at 4B parameters on hard tasks). Design the task before writing bench code. Calibrate Loop Velocity threshold on this bench before running the real benchmark sweep.

### Phase Ordering Rationale

- Block 1 must precede Block 2: ScoringEngine reads TurnRecord objects from SessionState.window; without Block 1, the window is empty and no detector can fire.
- Block 2 must precede Block 3: DynamicRouteLM reads session.current_threshold that ScoringEngine writes; without Block 2, the threshold never changes from the default.
- Block 3 must precede Validation: the escalation protocol spans Blocks 2 and 3; both must be integrated before any end-to-end bench run is valid.
- Synthetic bench precedes real benchmark: it is the controlled proof of concept. Discovering the escalation is broken on GSM8K is far more expensive to debug than on a 20-step toy agent.
- All pitfalls in Phase 1 are blocking -- they produce wrong data that flows forward and corrupts scoring. Phase 2 pitfalls affect detection quality. Phase 3 pitfalls affect cost and reliability. Phase 4 pitfalls affect research validity.

### Research Flags

Phases needing experimentation during planning:
- **Phase 4 (Synthetic bench design):** Task design is empirical -- not all tasks produce reliable weak-model loops. Allocate time before writing bench code.
- **Phase 3 (PayloadNormalizer scope):** Which extra fields DSPy attaches to message dicts must be confirmed empirically on first compiled-program integration test.
- **Phase 2 (Loop Velocity threshold):** Optimal cosine similarity threshold is domain-specific (research range 0.75-0.95). Treat 0.85 as a hypothesis and calibrate on Phase 4 synthetic bench.

Phases with standard patterns (skip research-phase):
- **Phase 1 (State Capture Engine):** DSPy BaseCallback API fully documented and verified against installed source. dspy.context() scoped callback registration verified live. No unknowns remain.
- **Phase 3 (DynamicRouteLM / model string mechanism):** RouteLLM router-mf-{threshold} format verified via ctx7. dspy.LM subclassing pattern verified in source. No additional research required.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All key packages dry-run verified; fastembed vs sentence-transformers decision based on actual dependency chain inspection; version compatibility confirmed |
| Features | HIGH | Table stakes derived from PROJECT.md active requirements; differentiators anchored to the unique gap RouteLLM + observability tools leave open |
| Architecture | HIGH | Derived from verified DSPy 3.2.1 installed source (callback.py, base_lm.py, react.py); RouteLLM model-string mechanism verified via ctx7; data flow traced through actual source lines |
| Pitfalls | HIGH | All DSPy pitfalls verified against installed source with live Python or source-line citations; RouteLLM pitfalls from ctx7 + README |

**Overall confidence:** HIGH

### Gaps to Address

- **Token usage collection path (P1):** Three candidate approaches (UsageTracker, lm.history[-1]["usage"], message-count estimation) -- prescribe which is most reliable per-step empirically in first Block 1 integration test. Recommendation: attempt lm.history[-1]["usage"] first; fall back to UsageTracker if history is stale in callback context.

- **PayloadNormalizer scope (P14):** Cannot enumerate which specific extra fields DSPy attaches to message dicts without running a compiled program end-to-end against RouteLLM server. Write PayloadNormalizer after the first failure is observed. Whitelist approach (role, content, tool_calls, tool_call_id, name) is the safe default.

- **RouteLLM MF router threshold calibration (P12):** The README threshold (0.11593) is calibrated on GPT-4-1106-preview vs Mixtral-8x7B. Any different model pair requires running calibrate_threshold before the first real-model bench run. Document as a required setup step.

- **Synthetic bench task design (P15):** Research constrains the design space but cannot prescribe the exact task. Design and measure empirically -- target >80% loop rate on weak model across >=10 seeds. Design the task before writing bench code.

## Sources

### Primary (HIGH confidence -- verified against installed source or official docs)

- DSPy 3.2.1 installed source at ~/.local/lib/python3.14/site-packages/dspy/ -- callback.py, base_lm.py, lm.py, react.py, usage_tracker.py, signature.py; all pitfall claims verified with source line numbers
- /lm-sys/routellm (ctx7, score 65.5) -- server launch command, router-mf-{threshold} model string format, in-process Controller API, calibration command
- /qdrant/fastembed (ctx7, score 75.59) -- BAAI/bge-small-en-v1.5 CPU model, ONNX backend, cosine similarity pattern
- /pypa/hatch (ctx7, score 89) -- hatchling build backend config, uv installer option
- /websites/pytest-asyncio_readthedocs_io_en_stable (ctx7, score 87.42) -- asyncio_mode = "auto", Python 3.14 support in 1.4.0
- PyPI dry-runs (verified 2026-06-18): fastembed 0.8.0 vs sentence-transformers 5.6.0 dependency chain; routellm 0.2.0 resolution
- dev/research-dspy-routellm.md -- prior verified research that all four research files build on

### Secondary (MEDIUM confidence -- multiple sources agree)

- DSPy observability docs (official) -- callback input mutation warning; track_usage pattern
- LiteLLM GitHub issue #14901 -- litellm_params / unknown parameter in OpenAI provider path
- TIDE paper (2025) -- loop ratio benchmarks in small models (4B: 15.8%, 30B: 1.0%)
- Community synthesis -- cosine similarity threshold calibration range 0.75-0.95

### Tertiary (inferred / needs validation)

- Optimal Loop Velocity threshold (0.85 recommended default) -- informed by domain range from literature; must be calibrated on synthetic bench
- Synthetic bench task design pattern ("almost helpful tool responses") -- inferred from TIDE paper findings; requires empirical validation

---
*Research completed: 2026-06-18*
*Ready for roadmap: yes*
