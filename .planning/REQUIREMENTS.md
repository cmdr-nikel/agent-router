# Requirements: agent-router

**Defined:** 2026-06-18
**Core Value:** When an agent gets stuck in a reasoning loop or tool-call flapping, the router detects it from trajectory telemetry alone (no LLM judge) and automatically escalates to a frontier model that clears the block — demonstrably, on a reproducible scenario.

## v1 Requirements

Requirements for initial release. v1 = the full scope (3 blocks + all detectors + validation). Each maps to roadmap phases.

### State Capture Engine (Block 1)

- [ ] **CAP-01**: Developer wraps existing DSPy calls in `with TrajectoryTracker(session_id=...):` without changing any agent logic
- [ ] **CAP-02**: Tracker registers via DSPy's callback system without clobbering pre-existing callbacks (uses `dspy.context(callbacks=...)`, not a `configure` replace)
- [ ] **CAP-03**: For each step, the tracker records the active Signature identity derived from class name + sorted field names (correctly distinguishes inline `StringSignature` instances)
- [ ] **CAP-04**: For each step, the tracker records the correct step index within a loop (ReAct outer/inner/extract calls do not cause overcounting)
- [ ] **CAP-05**: For each step, the tracker records input token count and output length via a verified usage path (handles cache hits where cost is absent)
- [ ] **CAP-06**: The tracker records success/failure per step via the callback `exception` argument (handles `outputs=None` on exception)
- [ ] **CAP-07**: Telemetry is isolated per `session_id`, so concurrent agent runs do not collide

### Dynamic Scoring Engine (Block 2)

- [ ] **SCORE-01**: The engine maintains a sliding window of the last N turns per session
- [ ] **SCORE-02**: Loop Velocity Profiler flags when similar inputs produce repeating output embeddings across consecutive turns; it also embeds the observation so a changed observation is treated as progress, not a loop
- [ ] **SCORE-03**: Tool-Call Flapping Monitor flags when the same tool is called with slightly varied params more than twice without a state change
- [ ] **SCORE-04**: Structural Constraint Scanner uses programmatic regex (not semantics) to detect strict-format demands (JSON Schema, valid XML, compilable syntax) and bypasses standard routing; this override path is evaluated before the probabilistic scoring
- [ ] **SCORE-05**: All scoring is based on mathematical telemetry, regex, or post-generation tracking only — no pre-inference LLM judge

### RouteLLM Execution Layer (Block 3)

- [ ] **ROUTE-01**: The router sets RouteLLM's threshold per call by composing the model string `router-{name}-{threshold}` (no controller patch / header)
- [ ] **ROUTE-02**: On a flagged anomaly, the next call's threshold drops to 0.0, forcing RouteLLM past the cheap model straight to the frontier model
- [ ] **ROUTE-03**: The dynamic-threshold LM is a thin, thread-safe `dspy.LM` subclass that computes the model string at call time from scoring state (no mutation of shared `LM.model`)
- [ ] **ROUTE-04**: DSPy few-shot demos pass through RouteLLM (OpenAI-compatible) without payload mutation / KeyError
- [ ] **ROUTE-05**: A per-session escalation cap limits runaway frontier spend; every escalation event is logged with its triggering signal
- [ ] **ROUTE-06**: Every routed call's cost and tokens are logged, tracking billed vs cache-free calls separately

### Validation (research arm)

- [ ] **VAL-01**: A synthetic loop bench provides a toy DSPy agent + task on which the weak model reliably loops (≥80% loop rate across seeds, `cache=False` on the weak model)
- [ ] **VAL-02**: The bench demonstrates the full chain end-to-end: anomaly detected → threshold 0.0 → frontier escalation → block cleared
- [ ] **VAL-03**: The effect is confirmed on a real benchmark (e.g. GSM8K / HotpotQA / code)

### Library & Packaging

- [ ] **LIB-01**: The project is pip-installable (hatchling build) with a clean, documented public API surface
- [ ] **LIB-02**: The weak→strong model pair is config-driven (default cheap API → frontier API)
- [ ] **LIB-03**: A pytest suite covers capture, scoring, and routing, using a mock RouteLLM server for unit tests

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Cost Safety

- **COST-01**: Hard budget cap with auto-stop, computed from real cost numbers gathered in v1
- **COST-02**: Configurable cost-cap thresholds per session / per run

### Performance

- **PERF-01**: Background-thread scoring so embedding computation never blocks the agent's hot path
- **PERF-02**: Zero-dependency loop-detection fallback (hash fingerprint) for environments that cannot install an embedder

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Custom web gateway / proxy / API-key mgmt / rate-limiting | RouteLLM + LiteLLM own 100% of the raw plumbing |
| Pre-inference LLM-as-judge | All routing analysis must be telemetry/regex/post-generation |
| Replacing DSPy optimizers (MIPROv2 etc.) | DSPy compiles prompts; we compile runtime safety + cost |
| Patching RouteLLM controller / `X-RouteLLM-Threshold` header | Unnecessary — threshold is already per-request via the model string |
| Wrapping `dspy.LM` for telemetry capture | Callbacks are the non-intrusive path; LM subclass is only the routing target |
| Budget auto-stop / hard cost-cap (v1) | Deferred to v2 (COST-01); v1 logs cost only |

## Traceability

Which phases cover which requirements. Populated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| (to be filled by roadmapper) | — | Pending |

**Coverage:**
- v1 requirements: 21 total
- Mapped to phases: 0 (pending roadmap)
- Unmapped: 21 ⚠️

---
*Requirements defined: 2026-06-18*
*Last updated: 2026-06-18 after initial definition*
