# Research: DSPy + RouteLLM APIs (verified 2026-06-18)

Verified against **installed source** (`dspy 3.2.1`, Python 3.14, `~/.local/lib/python3.14/site-packages/dspy`)
and **RouteLLM docs via ctx7** (`/lm-sys/routellm`). RouteLLM is NOT yet installed locally.

## A. DSPy — State Capture mechanism

### A.0 Scope assumption OUTDATED: `dspy.Suggest` / `dspy.Assert` are gone
- Top-level `dspy` exports no `Suggest`/`Assert` in 3.2.1. Replaced by `Refine` and `BestOfN`
  (`dspy.Refine`, `dspy.BestOfN`).
- Implication: the State Capture "intercept dspy.Suggest/dspy.Assert validation failures" requirement
  must be rewritten. Validation/retry failures now surface as **exceptions** caught by callbacks
  (see A.1), or via `Refine`'s internal reward-threshold loop.

### A.1 Strategy A — Callbacks (RECOMMENDED, official + stable)
- Registration: `dspy.settings.configure(callbacks=[MyCallback()])` (global) or per-LM
  `dspy.LM(model, callbacks=[...])`. Default `dspy.settings.callbacks == []`.
- Subclass `dspy.utils.BaseCallback` (`from dspy.utils import BaseCallback`). Hooks available:
  - `on_module_start(call_id, instance, inputs)` / `on_module_end(call_id, outputs, exception)`
  - `on_lm_start(call_id, instance, inputs)` / `on_lm_end(call_id, outputs, exception)`
  - `on_tool_start(call_id, instance, inputs)` / `on_tool_end(call_id, outputs, exception)`
  - `on_adapter_format_start/end`, `on_adapter_parse_start/end`, `on_evaluate_start/end`
- `call_id` ties start↔end of the same call. `exception` arg on every `*_end` = failure signal.

Capture coverage vs scope requirements:
| Scope requirement | How callbacks deliver it |
|---|---|
| active Signature class name | `on_module_start.instance.signature` (Predict/Module carries `.signature`) |
| current step index in loop | count `on_module_start`/`on_tool_start` per `call_id` session (ReAct = N inner `react` calls) |
| input tokens vs output length | `on_lm_start.inputs` (messages → count) + `on_lm_end.outputs`; or enable usage tracking (A.3) |
| success/failure signals | `exception` arg on `on_module_end`/`on_lm_end`/`on_tool_end` |
| tool-call flapping (block 2) | `on_tool_start.instance` (tool name) + `inputs` (params) — DIRECT feed, no parsing needed |

### A.2 Strategy B — wrapping `dspy.LM` (NOT recommended for capture)
- Possible (subclass `dspy.LM`, override `__call__`/`forward`), gives raw prompt/response/usage,
  but couples us to LM internals and misses module/tool structure that callbacks expose cleanly.
- **Verdict:** use callbacks for capture. Reserve a thin LM subclass ONLY for the routing/threshold
  target (see B.3), not for telemetry.

### A.3 Token usage
- DSPy has first-class usage tracking: `dspy.settings.configure(track_usage=True)`, then
  `prediction.get_lm_usage()`. Internals: `UsageTracker.add_usage()/get_total_tokens()`
  (`dspy/utils/usage_tracker.py`); LM pulls `results.usage` after each call (`clients/lm.py:196`).
- For per-step token deltas the tracker can read `on_lm_end` outputs, or count messages in
  `on_lm_start.inputs`. Exact usage-in-outputs plumbing = implementation detail to confirm at build.

### A.4 ReAct loop shape (for step index / flapping)
- `dspy.ReAct(signature, tools, max_iters=20)`; loop is `for idx in range(max_iters)` building a
  `trajectory` dict `thought_{idx}`, `tool_name_{idx}`, `tool_args_{idx}`, `observation_{idx}`
  (`predict/react.py`). Each iter calls an inner `Predict` → one `on_module_start` per step.

## B. RouteLLM — runtime threshold control

### B.0 KEY FINDING: threshold is ALREADY per-request — no header patch needed
- RouteLLM encodes router + threshold in the **`model` field**: `router-[NAME]-[THRESHOLD]`,
  e.g. `router-mf-0.11593`. Lower threshold → more strong-model (frontier) calls.
- Therefore the scope's "extend RouteLLM controller to accept `X-RouteLLM-Threshold` header" is
  **unnecessary / over-engineering**. To force frontier on the next call, send `model="router-mf-0.0"`.
  This also better honors the scope's own "No Custom Web Gateway" rule — we patch nothing in RouteLLM.

### B.1 Two usage modes
- **In-process Controller** (Python SDK):
  `controller.chat.completions.create(model="router-mf-0.11593", messages=[...])` (OpenAI-mirrored).
- **OpenAI-compatible server**:
  `python -m routellm.openai_server --routers mf --strong-model gpt-4-... --weak-model ...`
  → serves `http://0.0.0.0:6060/v1`. Standard OpenAI client with `base_url` + `model="router-mf-X"`.
- Threshold calibration: `python -m routellm.calibrate_threshold --routers mf --strong-model-pct 0.5`.
- LiteLLM handles the actual provider calls underneath (multi-provider plumbing).

## C. DSPy ↔ RouteLLM wiring

### C.1 Point DSPy at RouteLLM
- DSPy LM supports custom endpoints: `dspy.LM("openai/router-mf-0.11593",
  api_base="http://localhost:6060/v1", api_key="...")`.
- **Dynamic threshold (escalation):** DSPy's LM model string is fixed at construction. To vary
  threshold per call, use a thin `dspy.LM` subclass whose model string is rebuilt each call as
  `router-mf-{scoring_engine.current_threshold}`. This is the ONE place Strategy B is appropriate.

### C.2 Few-shot payload risk
- Scope warns of KeyError when DSPy appends few-shot demos through an OpenAI-compatible proxy.
  Not reproduced here (RouteLLM not installed). Flag as a build-time risk: validate that demos pass
  through the server unmutated. Likely low risk since RouteLLM forwards OpenAI-shaped payloads via
  LiteLLM, but must be tested.

## Net effect on the scope
1. Rewrite block-1 "Suggest/Assert" capture → exception-based capture via callbacks (+ optional Refine awareness).
2. Block-3 "X-RouteLLM-Threshold header" → DROP; threshold is per-request via model string.
3. Capture = callbacks (Strategy A); dynamic-threshold LM = thin LM subclass (Strategy B), narrowly scoped.
