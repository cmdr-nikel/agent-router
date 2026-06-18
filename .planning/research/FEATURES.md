# Feature Research

**Domain:** Trajectory-monitoring router library bridging DSPy and RouteLLM
**Researched:** 2026-06-18
**Confidence:** HIGH (all table stakes and differentiators cross-verified against installed DSPy 3.2.1
source, ctx7-verified RouteLLM API, and ecosystem research; anti-features anchored to explicit
scope boundaries)

---

## Feature Landscape

### Table Stakes (Users Expect These)

Any library calling itself a "monitoring router for DSPy agents" must have these or it is not
credible. Absence = immediate rejection by the target audience (ML engineers running DSPy in prod).

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Non-intrusive capture via context manager | The core UX promise: `with TrajectoryTracker(session_id=...):` wraps existing code unchanged. Any capture mechanism requiring agent-code edits defeats the purpose. | LOW | Implemented as a Python context manager that registers/deregisters DSPy callbacks on enter/exit. Thin surface area — good. |
| Per-step Signature capture | A monitoring tool that can't identify which reasoning step is executing is blind. Users expect to know "stuck at VerifyAnswer, step 7". | LOW | `on_module_start.instance.signature` gives this for free via DSPy 3.2.1 callbacks. |
| Step index tracking within the agent loop | Without step index, loop detection is impossible and logs are unordered noise. | LOW | Count `on_module_start` / `on_tool_start` events per `call_id` session. ReAct exposes this cleanly (one `on_module_start` per iteration). |
| Exception / failure signal capture | The monitoring tool must distinguish success from failure on every step. Silent failure = missed escalation trigger. | LOW | `exception` arg on every `*_end` callback hook in DSPy 3.2.1. Replaces the removed `Suggest`/`Assert` intercept. |
| Input-token count per step | Token monitoring is a baseline expectation in any LLM production tool. Establishes cost baseline and is required for token-spike detection. | LOW | `on_lm_start.inputs` (count messages) or `on_lm_end.outputs` via DSPy's `UsageTracker`. Exact plumbing to confirm at build. |
| Sliding window state (last N turns per session) | Loop detection algorithms require sequential history. A stateless observer can detect nothing. | LOW | An in-memory dict keyed by `session_id` holding a fixed-size deque of turn records. Standard pattern; no exotic deps. |
| Session isolation | Multiple concurrent agents must not cross-contaminate each other's telemetry. | LOW | `session_id` parameter on `TrajectoryTracker`. Thread-local or session-keyed state dict. |
| RouteLLM escalation on anomaly flag | The final actuator — without it, detection has no consequence and the tool is just a logger. | MEDIUM | Drop threshold to 0.0 via `router-mf-0.0` model string on the next call. Requires the dynamic-threshold LM subclass (B.3 from research). |
| Per-call cost logging | Any production routing tool logs what it spends. Without this, users cannot justify using the library or tune thresholds. | LOW | Log model name, token counts, call cost per `on_lm_end`. Can derive cost from token count + known per-token rates for the configured model pair. |
| Clean public API (pip-installable, typed) | A "production-ready library" that requires manual sys.path manipulation or has no type hints is unprofessional. | LOW | Standard `pyproject.toml` packaging. Add `py.typed` marker and type all public symbols. |
| Payload shape normalization for few-shot demos | DSPy appends few-shot demos as extra `messages` entries. If the OpenAI-compatible proxy (RouteLLM server) rejects this shape, every production DSPy user hits a KeyError. | MEDIUM | Validate that demo dicts pass through RouteLLM/LiteLLM unmutated. If not, add a pre-flight normalizer. Must be tested at build; currently flagged LOW risk (see research C.2). |

---

### Differentiators (Competitive Advantage)

These are what no existing tool (Langfuse, MLflow, Portkey, LiteLLM) provides. They constitute the
core hypothesis: *trajectory-aware routing beats single-prompt embedding routing.* Build them well
— they are the reason the library exists.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Loop Velocity Profiler (embedding similarity across turns) | Detects reasoning loops purely from trajectory telemetry — no LLM judge, no human in the loop. Existing observability tools log loops retroactively; this one breaks them prospectively. | HIGH | Compare output embeddings across consecutive turns within the sliding window. Need a lightweight embedder (e.g. `sentence-transformers` all-MiniLM-L6-v2 or similar small model). Similarity threshold is a tunable hyperparameter. Core algorithmic innovation of the library. |
| Tool-Call Flapping Monitor | Detects the specific "same tool, varied params, no state change" pathology that embedding routers miss entirely (they see only the prompt, not tool-call structure). | MEDIUM | Direct feed from `on_tool_start.instance` (tool name) + `inputs` (params) — no parsing needed. Flag when same tool name repeats >2x within the sliding window without intervening state change. Requires lightweight state-change heuristic (e.g. observation delta). |
| Structural Constraint Scanner | Detects format-demanding prompts (JSON Schema, valid XML, compilable syntax) via regex, bypassing routing profiles entirely. A cheap model that can't produce valid JSON on first try wastes multiple retries; escalating immediately is strictly cheaper. | MEDIUM | Programmatic regex rules — no LLM involved. Pattern library for common format demands. Produces a binary flag that overrides the scoring engine's probabilistic output. |
| Dynamic per-call threshold control (not static routing) | Every other routing tool (including RouteLLM's basic usage) picks a fixed threshold at startup. This library adjusts the threshold per-call based on trajectory state — escalating exactly when needed, not blanket-routing hard calls to frontier. | MEDIUM | Thin `dspy.LM` subclass that rebuilds model string as `router-mf-{scoring_engine.current_threshold}` on every call. Confirmed feasible from research (B.3). |
| Trajectory-aware escalation (not prompt-embedding routing) | RouteLLM's MF router embeds a single prompt and picks a model. This library embeds *sequences of outputs across turns* and picks escalation based on behavioral trajectory. Distinct capability, not replication. | HIGH | Depends on Loop Velocity Profiler. The scoring engine aggregates all detector signals (loop velocity + flapping + structural) into an escalation decision. The key unit is the session trajectory, not the individual prompt. |
| Automated Escalation Protocol (self-clearing loops) | When a loop is detected, the library automatically routes the next call to a frontier model that clears the block — no human intervention, no manual restart. Demonstrable on a reproducible synthetic scenario. | MEDIUM | Ties together: anomaly detection → threshold override → frontier call → monitoring resumed at normal threshold. State machine with 3 states: normal / anomaly-detected / escalated. |
| Synthetic loop bench (research validation) | Provides a reproducible, self-contained scenario where a weak model reliably loops, allowing automated regression testing of the escalation logic. Competitors have no equivalent because they don't need to prove their routing works on a loop — they route by cost/quality, not by trajectory pathology. | MEDIUM | A toy DSPy agent + task where gpt-4o-mini (or similar) provably loops. Benchmarked against frontier escalation. Serves as the project's flagship demo and CI regression suite. |
| Real benchmark confirmation (GSM8K / HotpotQA / code) | Demonstrates the effect is not a toy artifact but holds on standard research benchmarks. Positions the library as credible for an ML/research audience. | HIGH | Reproduces the escalation-clears-block effect on a real task set. Requires careful experimental design: same task, weak model loops, escalated frontier clears. Time-consuming but non-negotiable for research credibility. |

---

### Anti-Features (Out of Scope — Deliberately NOT Building)

These are features that seem natural extensions but are explicitly excluded. Documenting them
prevents scope creep during implementation and answers inevitable "why don't you just add X" questions.

| Feature | Why Requested | Why Excluded | What We Do Instead |
|---------|---------------|--------------|-------------------|
| Custom web gateway / API proxy | "Put all LLM traffic through one place for full control." Seems powerful. | RouteLLM + LiteLLM already do this. Building our own duplicates battle-tested infrastructure, adds maintenance burden, and violates the "no custom gateway" boundary explicitly set in scope. Our job is trajectory logic, not plumbing. | Use RouteLLM's OpenAI-compatible server (`python -m routellm.openai_server`) or in-process Controller. Zero plumbing code. |
| Pre-inference LLM-as-judge | "Use a fast LLM to assess query difficulty before routing." RouteLLM's own research explores this; it feels like the natural complement to our scoring engine. | Adds latency and cost before every single call. Our thesis is that trajectory telemetry + regex is sufficient — if we reach for an LLM judge, we are admitting the thesis is wrong. Also violates explicit scope boundary. | All routing analysis is mathematical telemetry, programmatic regex, or post-generation tracking only. This is faster and cheaper. |
| Replacing DSPy optimizers (MIPROv2 etc.) | "You're already observing the agent — why not use that data to retrain the prompts?" | DSPy's optimizer pipeline is a separate, complex system optimizing compile-time prompt weights. We operate at runtime. Conflating the two scopes would make the library impossible to scope and impossible to test. | DSPy continues to compile prompts. We compile runtime safety and cost. They compose, not compete. |
| Patching RouteLLM internals / X-RouteLLM-Threshold header | Original scope assumed a header patch was needed. Feels like the "clean" API. | Research confirmed (2026-06-18) threshold is already per-request via the model string `router-mf-{threshold}`. Patching RouteLLM internals would couple us to its internals across versions. | Use `router-mf-{threshold}` model string. Zero RouteLLM internals touched. |
| Hard budget auto-stop / real-time cost cap | "Stop spending when you hit $X." Safety feature, feels essential. | v1 must establish real cost-per-escalation numbers first before setting a meaningful cap. Premature cap = either too conservative (kills legitimate escalations) or too loose (provides false safety). Also adds state machine complexity that risks interfering with the escalation protocol. | v1 logs cost per call. Approximate cap is computed post-v1 from real data. Cost tracking (table stakes) provides the data needed to calibrate a cap. |
| Wrapping `dspy.LM` for telemetry capture (Strategy B for capture) | "Subclassing LM gives you raw prompt/response access." True — and seems straightforward. | LM wrapping couples to LM internals and misses module/tool structure that callbacks expose cleanly. Research (A.2) confirms callbacks are strictly superior for capture. | Use DSPy callbacks (Strategy A) for all capture. Reserve the LM subclass pattern narrowly for the dynamic-threshold routing target only (Strategy B), not telemetry. |
| Multi-provider API key management / rate limiting | "You're already in the call path — handle keys and limits." | RouteLLM + LiteLLM own this entirely. Adding key management would require building an API gateway and maintaining provider credential security — out of scope by design. | LiteLLM handles all provider calls underneath RouteLLM. Configuration is delegated to the user's existing LiteLLM/RouteLLM setup. |

---

## Feature Dependencies

```
[Session isolation]
    └──required-by──> [Sliding window state]
                          └──required-by──> [Loop Velocity Profiler]
                          └──required-by──> [Tool-Call Flapping Monitor]
                          └──required-by──> [Automated Escalation Protocol]

[Non-intrusive context manager]
    └──required-by──> [Per-step Signature capture]
    └──required-by──> [Step index tracking]
    └──required-by──> [Exception / failure signal capture]
    └──required-by──> [Input-token count per step]
    └──required-by──> [Tool-Call Flapping Monitor] (direct feed from on_tool_start)

[Loop Velocity Profiler]
    └──feeds──> [Trajectory-aware escalation]

[Tool-Call Flapping Monitor]
    └──feeds──> [Trajectory-aware escalation]

[Structural Constraint Scanner]
    └──feeds──> [Trajectory-aware escalation] (override path, bypasses normal scoring)

[Trajectory-aware escalation]
    └──requires──> [Dynamic per-call threshold control]
    └──executes──> [Automated Escalation Protocol]

[Per-call cost logging]
    └──feeds──> [Synthetic loop bench] (cost-per-escalation measurement)
    └──feeds──> [Real benchmark confirmation]

[Payload shape normalization]
    └──guards──> [Automated Escalation Protocol] (ensures the escalated call doesn't KeyError)

[Synthetic loop bench]
    └──validates──> [Automated Escalation Protocol]
    └──precedes──> [Real benchmark confirmation]
```

### Dependency Notes

- **Sliding window state requires session isolation:** Without per-session keying, turn histories
  bleed across concurrent agents. This is the foundational data structure everything else reads from.

- **All detectors require the context manager (capture):** No capture = no telemetry = no
  detection. The DSPy callback system (`BaseCallback` + `dspy.settings.configure`) is the single
  point of integration that all detector inputs flow through.

- **Structural Constraint Scanner is an override path:** It does not feed into the probabilistic
  scoring that Loop Velocity + Flapping use. Instead it produces a binary "bypass everything,
  escalate immediately" signal. This means it must be evaluated before the normal scoring path in
  the escalation protocol.

- **Payload shape normalization guards the escalation path:** If normalization fails, the escalated
  call (the most expensive call in the system) crashes with a KeyError. It must be verified at build
  time (integration test against RouteLLM server) before the escalation protocol is considered
  complete.

- **Synthetic loop bench precedes real benchmarks:** The synthetic bench is a controlled environment
  that proves the escalation mechanism works in isolation. If it fails there, real benchmark results
  are uninterpretable. Build synthetic bench first.

- **Dynamic per-call threshold control requires a thin LM subclass:** This is the one place
  Strategy B (LM wrapping) is appropriate — not for capture, but for dynamically rebuilding the
  model string as `router-mf-{scoring_engine.current_threshold}` on every call. Scope has approved
  this narrow usage.

---

## MVP Definition

### Launch With (v1)

v1 = full scope (all three blocks + all detectors + validation). Per PROJECT.md: "User wants the
complete pipeline, validated, in the first iteration."

**Block 1 — State Capture Engine:**
- [ ] Context manager (`with TrajectoryTracker(session_id=...):`) — zero agent-code changes
- [ ] Per-step capture: Signature name, step index, input tokens, exception/failure signal
- [ ] Tool-call capture: tool name + params via `on_tool_start` hooks
- [ ] Session-isolated sliding window state (last N turns, configurable)

**Block 2 — Dynamic Scoring Engine:**
- [ ] Loop Velocity Profiler (embedding similarity across consecutive turns)
- [ ] Tool-Call Flapping Monitor (same tool, varied params, no state change)
- [ ] Structural Constraint Scanner (regex, format-demanding prompts, override path)
- [ ] Scoring engine aggregates detector signals into escalation decision

**Block 3 — RouteLLM Execution Layer:**
- [ ] Dynamic per-call threshold control via `router-mf-{threshold}` model string
- [ ] Automated Escalation Protocol (anomaly → threshold 0.0 → frontier call → resume normal)
- [ ] Payload shape normalization (few-shot demos pass through unmutated)
- [ ] Per-call cost logging (model, tokens, estimated cost)

**Validation:**
- [ ] Synthetic loop bench (toy agent + task where weak model reliably loops; proves escalation clears block)
- [ ] Real benchmark confirmation (GSM8K / HotpotQA / code; reproduces trajectory-aware routing advantage)

### Add After Validation (v1.x)

- [ ] Budget auto-stop / hard cost cap — trigger: real escalation cost data from v1 logs establishes meaningful cap value
- [ ] OpenTelemetry / Langfuse trace export — trigger: user demand for integration with their existing observability stack
- [ ] Configurable detector weights (tune how much each detector contributes to escalation score) — trigger: real-task benchmark shows one detector dominates and others add noise

### Future Consideration (v2+)

- [ ] Async / streaming support for the callback hooks — defer until there is evidence of async DSPy usage that breaks the sync-first implementation
- [ ] Multi-session aggregate analytics (fleet-level loop rate, model comparison) — defer until v1 is validated in production; adds storage/aggregation complexity
- [ ] Calibrated threshold recommendation tool (analogous to RouteLLM's `calibrate_threshold`) — defer; requires real usage data to calibrate against

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Non-intrusive context manager | HIGH | LOW | P1 |
| Per-step Signature + step-index capture | HIGH | LOW | P1 |
| Exception / failure signal capture | HIGH | LOW | P1 |
| Session-isolated sliding window | HIGH | LOW | P1 |
| Loop Velocity Profiler | HIGH | HIGH | P1 |
| Automated Escalation Protocol | HIGH | MEDIUM | P1 |
| Dynamic per-call threshold control | HIGH | MEDIUM | P1 |
| Tool-Call Flapping Monitor | HIGH | MEDIUM | P1 |
| Structural Constraint Scanner | MEDIUM | MEDIUM | P1 |
| Payload shape normalization | HIGH | MEDIUM | P1 |
| Per-call cost logging | MEDIUM | LOW | P1 |
| Synthetic loop bench | HIGH | MEDIUM | P1 |
| Real benchmark confirmation | HIGH | HIGH | P1 |
| Input-token count per step | MEDIUM | LOW | P1 |
| Budget auto-stop / cost cap | MEDIUM | MEDIUM | P2 |
| OpenTelemetry / Langfuse export | LOW | MEDIUM | P2 |
| Configurable detector weights | MEDIUM | LOW | P2 |
| Async / streaming callback support | LOW | HIGH | P3 |
| Fleet-level aggregate analytics | LOW | HIGH | P3 |

**Priority key:**
- P1: Must have for v1 launch
- P2: Add after validation (v1.x)
- P3: Future (v2+)

---

## Competitor Feature Analysis

This is not a consumer product competing on a feature matrix — it is a niche research/production
library filling a specific gap. Direct competitors do not exist. The closest adjacents are general
observability platforms and RouteLLM itself.

| Feature | Langfuse / MLflow / Phoenix | RouteLLM (standalone) | agent-router (this project) |
|---------|----------------------------|-----------------------|-----------------------------|
| DSPy callback integration | Via OpenInference/OpenTelemetry auto-instrumentation | N/A | Direct `BaseCallback` subclass — tighter integration, exposes Signature + tool structure natively |
| Loop detection | None (retroactive logging only) | None | Loop Velocity Profiler (prospective) |
| Tool-call flapping detection | None | None | Tool-Call Flapping Monitor |
| Format-constraint bypass | None | None | Structural Constraint Scanner |
| Dynamic per-call threshold routing | N/A | Fixed threshold at startup | Per-call via model string, driven by trajectory state |
| Trajectory-aware escalation | None | Prompt-embedding routing (single call context) | Sequence of turn embeddings (multi-turn trajectory) |
| Cost logging | Yes (Langfuse, MLflow) | Basic (via LiteLLM) | Per-call logging tied to escalation events |
| Non-intrusive integration | Varies (some require agent code changes) | Requires pointing LM to server | Single context manager, zero agent changes |
| Synthetic validation bench | None | MMLU / MT-Bench (held-out evals) | Toy looping scenario proves mechanism prospectively |

**Key gap this project fills:** Prospective loop-breaking via trajectory-aware dynamic routing.
Observability tools detect failure retroactively; RouteLLM routes by single-prompt complexity.
Neither addresses the "agent stuck in a loop, needs a frontier call to break out" scenario
automatically and in real-time.

---

## Sources

- PROJECT.md (active requirements, out-of-scope boundaries, key decisions)
- `./scope` (original engineering scope document)
- `dev/research-dspy-routellm.md` (verified 2026-06-18: DSPy 3.2.1 callback API, RouteLLM
  per-request threshold, ReAct loop shape, usage tracking)
- [Agent Observability: How to Monitor and Evaluate LLM Agents in Production](https://www.langchain.com/blog/production-monitoring)
- [8 LLM Observability Tools to Monitor & Eval AI Agents](https://www.langchain.com/resources/llm-observability-tools)
- [Top 5 LLM Routing Techniques](https://www.getmaxim.ai/articles/top-5-llm-routing-techniques/)
- [Dynamic LLM Routing: Tools and Frameworks](https://latitude.so/blog/dynamic-llm-routing-tools-and-frameworks)
- [RouteLLM: An Open-Source Framework for Cost-Effective LLM Routing](https://www.lmsys.org/blog/2024-07-01-routellm/)
- [Why Heuristic Detectors Beat LLMs at Finding Agent Failures](https://dev.to/tuomo_pisama/why-heuristic-detectors-beat-llms-at-finding-agent-failures-2dba)
- [How to Prevent AI Agent Reasoning Loops from Wasting Tokens](https://dev.to/aws/how-to-prevent-ai-agent-reasoning-loops-from-wasting-tokens-2652)
- [MLflow DSPy Tracing Integration](https://mlflow.org/docs/latest/genai/tracing/integrations/listing/dspy/)

---
*Feature research for: trajectory-monitoring router (DSPy + RouteLLM)*
*Researched: 2026-06-18*
