# Pitfalls Research

**Domain:** DSPy trajectory-monitoring router (DSPy callbacks + embedding loop-detection + RouteLLM integration)
**Researched:** 2026-06-18
**Confidence:** HIGH — all DSPy pitfalls verified against installed source (dspy 3.2.1, Python 3.14). RouteLLM pitfalls verified against ctx7 docs + RouteLLM README. Embedding pitfalls from multi-source synthesis.

---

## Critical Pitfalls

### Pitfall 1: `on_lm_end` does NOT carry token usage — it receives processed text, not the raw response

**What goes wrong:**
You implement `on_lm_end` expecting `outputs` to contain token counts (prompt_tokens, completion_tokens). It does not. The callback receives the return value of `BaseLM.__call__`, which is the result of `_process_lm_response()` — a `list[str]` or `list[dict]` of decoded text. The raw `response` object with `.usage` was consumed internally before the callback fires.

Verified in source: `base_lm.py` lines 129–132:
```python
response = self.forward(prompt=prompt, messages=messages, **kwargs)
outputs = self._process_lm_response(response, prompt, messages, **kwargs)
return outputs  # ← this is what on_lm_end receives
```
The usage is stored in the history entry (line 109) and sent to `UsageTracker` (lines 196–197) — both happening inside `_process_lm_response`, before `outputs` is returned.

**Why it happens:**
The callback interface documents `outputs` as "the outputs of the LM's `__call__` method" — which is technically true, but `__call__` returns processed text, not the response envelope.

**How to avoid:**
Use `dspy.settings.configure(track_usage=True)` + a `UsageTracker` context manager (`dspy.utils.usage_tracker.track_usage()`) for aggregate token counts, OR read from `lm.history[-1]["usage"]` after each step in a separate hook, OR count tokens from `on_lm_start.inputs["messages"]` using a tokenizer (approximate). Do NOT expect usage data inside `on_lm_end.outputs`.

**Warning signs:**
`on_lm_end` handler that tries `outputs["usage"]` or `outputs.usage` raises `KeyError`/`AttributeError` immediately on first call. Silent wrong-zero readings if you default to 0 on exception.

**Phase to address:**
Block 1 (State Capture Engine) — verify token-collection strategy in the very first callback integration test before any scoring logic is layered on top.

---

### Pitfall 2: Signature `__name__` returns `"StringSignature"` for inline string signatures

**What goes wrong:**
The scope requires capturing "active Signature class name" per step. If the developer defines their DSPy agent using the inline string form (`dspy.Predict("question -> answer")`), then `instance.signature.__name__` in `on_module_start` returns the string `"StringSignature"` — not a meaningful module identity. Every Predict module in the whole agent trajectory looks identical.

Verified in live Python:
```
predict = dspy.Predict('question -> answer')
predict.signature.__name__  # → 'StringSignature'

class MyCustomSig(dspy.Signature): ...
predict2 = dspy.Predict(MyCustomSig)
predict2.signature.__name__  # → 'MyCustomSig'
```

**Why it happens:**
DSPy's `SignatureMeta` metaclass names dynamically-constructed signatures `"StringSignature"`. Only user-defined subclasses of `dspy.Signature` get a meaningful `__name__`. This is intentional — inline signatures are anonymous.

**How to avoid:**
Fall back to `instance.__class__.__name__` (the module type, e.g. `"Predict"`, `"ChainOfThought"`, `"ReAct"`) when `signature.__name__ == "StringSignature"`. Better: derive a stable step identity from the combination of `instance.__class__.__name__` + sorted field names from `signature.input_fields.keys()` + `signature.output_fields.keys()`. This is unique per distinct module even when inline signatures are used.

**Warning signs:**
All trajectory steps appear as `"StringSignature"` in telemetry logs. Scoring engine fails to distinguish steps from each other, making loop detection meaningless.

**Phase to address:**
Block 1 — define the step-identity hashing scheme before the scoring engine is built. The scoring engine takes step identity as input; if identity is garbage, scoring is garbage.

---

### Pitfall 3: `ACTIVE_CALL_ID` is a `ContextVar` — thread-spawned workers see `None`, breaking step-index tracking

**What goes wrong:**
DSPy propagates call nesting via `ACTIVE_CALL_ID` (a `contextvars.ContextVar`). In async code, each `asyncio` Task correctly inherits a copy. But in `threading.Thread` — confirmed empirically — a spawned thread sees `ACTIVE_CALL_ID = None`. If the user's agent runs DSPy calls from a thread pool (common with `dspy.Evaluate`, or any user code that parallelises steps with `ThreadPoolExecutor`), the tracker's step-index counter cannot distinguish nested calls from sibling calls.

The session-level step counter you maintain also becomes a race condition: two concurrent threads writing to the same session dict without a lock will corrupt the count.

**Why it happens:**
Python `ContextVar` copies context to `asyncio` Tasks automatically but NOT to `threading.Thread` by default (requires explicit `copy_context().run()`).

**How to avoid:**
(1) Use `threading.local()` or per-session `threading.Lock` for the step counter. (2) If you need cross-thread call-id correlation, propagate a `session_id` explicitly via the tracker's context manager, not via `ACTIVE_CALL_ID`. (3) Document that `TrajectoryTracker` is not safe to share across threads without locking; one tracker instance per thread or per coroutine.

**Warning signs:**
Step counters jump or reset unexpectedly during parallel evaluations. Two concurrent sessions bleed into each other's trajectory windows.

**Phase to address:**
Block 1, specifically the context manager design — before any concurrent usage patterns are exercised.

---

### Pitfall 4: `dspy.configure(callbacks=[cb])` REPLACES, not appends — silently drops the user's existing callbacks

**What goes wrong:**
Your library calls `dspy.configure(callbacks=[TrajectoryCallback()])` at `TrajectoryTracker.__enter__`. This replaces whatever `dspy.settings.callbacks` was before. The user may have already registered an observability callback (Langfuse, MLFLOW, custom logger). After your context manager exits, their callback is gone.

Verified live:
```python
dspy.configure(callbacks=[cb1])
dspy.configure(callbacks=[cb2])
# dspy.settings.callbacks is now [cb2] — cb1 is gone
```

**Why it happens:**
`dspy.configure()` is a simple settings update; there is no append-semantic.

**How to avoid:**
Inside `TrajectoryTracker.__enter__`, read the existing callbacks with `existing = dspy.settings.get("callbacks", [])`, then use `dspy.context(callbacks=existing + [self._callback])` as the context manager. This uses DSPy's `dspy.context()` (scoped override) instead of `dspy.configure()` (global replace), and restores the previous state on `__exit__` automatically.

**Warning signs:**
User's observability integrations (Langfuse, W&B) stop receiving events while your tracker is active. This is a silent breakage — no exception, just missing data.

**Phase to address:**
Block 1 — first integration test should assert that a pre-existing callback is still active inside the `TrajectoryTracker` context.

---

### Pitfall 5: Mutation of `inputs` in a callback corrupts the original call's data

**What goes wrong:**
`on_lm_start` (and `on_module_start`) receive `inputs` as the dict of keyword arguments passed to the wrapped function. This is the live dict, not a copy. If your callback stores it by reference and later mutates it (normalises keys, appends metadata fields), you mutate the data that DSPy itself is about to use for the LM call.

From DSPy observability docs: "When working with input or output data in callbacks, mutating them in-place can modify the original data passed to the program."

**Why it happens:**
`inspect.getcallargs()` in `_execute_start_callbacks` builds a dict — but the values are references to the original objects. Nested dicts (e.g., the `messages` list) are shared.

**How to avoid:**
Always `copy.deepcopy(inputs)` before storing or modifying. For hot-path callbacks where deepcopy is too slow, only extract the scalar fields you need (step index, tool name string, message length) and discard the rest.

**Warning signs:**
Non-deterministic downstream errors — LM receives malformed messages or wrong token counts — that disappear when callbacks are removed.

**Phase to address:**
Block 1 — codified as a rule in the callback base class docs; audited in first integration test.

---

### Pitfall 6: `on_lm_end` fires AFTER the LM response is received but BEFORE DSPy's cache is written — double-fire on retry

**What goes wrong:**
`dspy.LM` has `num_retries=3` by default with exponential backoff. On a transient network error, `forward()` retries inside LiteLLM before returning. The callback fires only once per `__call__` (after the successful retry), not once per attempt. However, if you are counting LM calls for step index and the LM raises an exception that propagates out (rate limit exhausted, context overflow), `on_lm_end` fires with `exception != None` and `outputs = None`. Your step counter must handle `outputs=None` without crashing.

There is also a subtler issue: DSPy's `request_cache` (disk/memory LRU) returns cached responses and sets `cache_hit=True`. The `usage_tracker.add_usage` check at line 196 skips cache hits, but your callback still fires. If your token counter is naive, cached responses inflate or deflate your per-step token estimates.

**Why it happens:**
The callback wraps `__call__`, not `forward`. Retries and caching are internal to `forward`.

**How to avoid:**
(1) Guard `on_lm_end` with `if outputs is None: handle_exception(...)`. (2) Do NOT count raw `on_lm_end` fires as "one LM call" without checking `exception`. (3) If token accuracy matters, read from `lm.history[-1]["usage"]` where cache-hit status is also stored, rather than estimating from callback firings.

**Warning signs:**
Step index skips or duplicates when the model is rate-limited. Token counts are consistently lower than billed amounts (cached calls counted as zero tokens).

**Phase to address:**
Block 1 — verified in integration tests under simulated retry conditions.

---

### Pitfall 7: ReAct inner `self.react` fires `on_module_start` — the ReAct module itself also fires it — leading to double step-counting

**What goes wrong:**
`ReAct` is a `Module` subclass, so its `__call__` is `@with_callbacks` decorated. Each iteration of the `for idx in range(max_iters)` loop calls `self.react(...)` (a `Predict` submodule) — that also fires `on_module_start`. You will see:
- `on_module_start` for the outer `ReAct` instance (once per agent invocation)
- `on_module_start` for the inner `self.react` Predict (once per iteration)
- `on_module_start` for the `self.extract` ChainOfThought (once at the end)
- `on_lm_start` / `on_lm_end` for each underlying LM call

If you count all `on_module_start` firings as "steps", you will over-count. The outer `ReAct` call is one agent episode, not one step.

**Why it happens:**
The callback system fires for every `dspy.Module` subclass, regardless of depth in the call tree. There is no built-in way to distinguish "outer agent call" from "inner step call".

**How to avoid:**
Use `call_id` nesting: `ACTIVE_CALL_ID` at the time `on_module_start` fires is the parent call's id. Track a call-id stack. A new `call_id` that is nested under the ReAct `call_id` is a step; the ReAct `call_id` itself is the episode. Alternatively, filter by `instance.__class__.__name__ == "Predict"` to count only atomic prediction steps.

**Warning signs:**
Step count is consistently 2–3x higher than the actual number of ReAct iterations, or the trajectory window triggers too early.

**Phase to address:**
Block 1 — define step-counting semantics before Block 2 window logic is written. Wrong step count → wrong Loop Velocity scoring.

---

## Embedding Loop-Detection Pitfalls

### Pitfall 8: Cosine similarity threshold requires per-domain calibration — 0.9 is too aggressive, 0.75 is too lenient

**What goes wrong:**
A fixed cosine similarity threshold for "these two outputs are a loop" will give opposite failure modes in different domains:
- Creative/generative tasks: outputs legitimately vary in phrasing while meaning is identical → threshold set to 0.9 misses the loop
- Factual Q&A or structured output tasks: outputs are nearly identical even when correct (same answer in the same format) → threshold set to 0.9 constantly false-flags healthy convergence

There is no universally correct threshold. Papers show optimal cosine thresholds ranging from 0.75 to 0.95 depending on task and embedding model.

**Why it happens:**
Embeddings cluster in high-dimensional space in domain-specific ways. The norm of cosine similarity varies by model family (ada-002, sentence-transformers/all-MiniLM, etc.) and by the vocabulary of the task.

**How to avoid:**
(1) Make the threshold a config parameter with a sensible default (0.85 is a reasonable starting point for general English tasks). (2) Calibrate on the synthetic loop bench (Pitfall 16) before tuning for real tasks. (3) Require at least N=2 consecutive high-similarity pairs before flagging (a window-based rather than point-based trigger). (4) Layer with a structural check: if the similarity is 0.85+ AND the step index has not advanced in 2+ turns, flag. Don't rely on similarity alone.

**Warning signs:**
Loop detector fires on every tool observation (observations are often short, similar strings). Loop detector never fires even when the agent visibly repeats thoughts.

**Phase to address:**
Block 2 (Dynamic Scoring Engine) — threshold must be configurable before Block 3 escalation logic is written. Hardcoded thresholds will require a code change to tune.

---

### Pitfall 9: Per-turn embedding inference adds 15–50ms per step on CPU — this is not negligible at `max_iters=20`

**What goes wrong:**
Embedding a single string with a local sentence-transformers model (e.g., `all-MiniLM-L6-v2`) takes ~5–15ms warm, but the first call (cold start, model load) can take 2–10 seconds. At 20 iterations, 15ms/call = 300ms additional latency per agent run. For production agents with sub-second SLA expectations, this is significant.

Additionally, if embeddings are computed synchronously inside `on_lm_end`, they block the callback and delay the next LM call from starting (in async agents).

**Why it happens:**
Local embedding models are loaded from disk on first use. Even warm, transformer inference on CPU involves non-trivial computation for token-level attention.

**How to avoid:**
(1) Warm the model at `TrajectoryTracker.__enter__` time, not on first step. (2) In async agents, offload embedding to a thread pool via `asyncio.run_in_executor` to avoid blocking the event loop. (3) Consider using a fixed-dimension hash (SimHash or random-projection sketch) for a fast approximate similarity check as a pre-filter, with full embeddings only when the sketch says "similar". (4) Cache embeddings for strings that appear more than once (legitimate for loop detection — a repeated string is exactly what you want to detect).

**Warning signs:**
First agent run is much slower than subsequent ones (cold start). Async agent throughput drops under embedding computation. Profiling shows >10% of wall time in embedding calls.

**Phase to address:**
Block 2 — embedding strategy must be designed for async-safety before the async agent path is exercised. This is a Phase 2 build-time decision, not a Phase 3 optimization.

---

### Pitfall 10: Legitimate retries on tool errors look identical to pathological loops

**What goes wrong:**
When a tool call fails with an error (`trajectory[f"observation_{idx}"] = "Execution error in search_tool: ..."`), a well-behaved agent should retry with a corrected query. The corrected query may be semantically very similar to the original. Your similarity-based loop detector will flag this as a loop and escalate prematurely, spending a frontier-model call on something the weak model would have resolved with one more retry.

From the ReAct source, tool execution errors produce: `f"Execution error in {pred.next_tool_name}: {_fmt_exc(err)}"` as the observation. The agent's next thought often begins the same way regardless of whether it's adapting or stuck.

**Why it happens:**
Similarity-based detection cannot distinguish "same approach, one more try" from "same approach, stuck forever". The trajectory context (observation content) is the differentiator, not the thought embedding alone.

**How to avoid:**
(1) Also embed the observation string, not just the thought/output. If the observation changed (even with similar input), the agent is not stuck. (2) Use a combined signal: high output similarity AND identical observation content (or a repeated error pattern). (3) The tool-flapping monitor (Block 2) is actually better suited for this case than Loop Velocity — detect same tool + similar args + 2x without state change. Use each detector for its designed pathology.

**Warning signs:**
Escalation happens on the second tool call of a fresh agent session. Escalation rate is high on tasks with unreliable external tools.

**Phase to address:**
Block 2 — define detector preconditions (minimum step index, required similarity window width) before integration testing on real tasks.

---

## RouteLLM Integration Pitfalls

### Pitfall 11: `dspy.LM.model` is set at construction — the thin LM subclass must rebuild the model string per-call

**What goes wrong:**
DSPy's `LM` sets `self.model = model` in `__init__` and uses it directly in `forward()` at line 186: `request=dict(model=self.model, messages=messages, ...)`. There is no per-call model override hook. If you attempt to change threshold by modifying `lm.model` at the time of escalation, you create a race condition in concurrent use: one thread's escalation overwrites another thread's normal-routing model string.

**Why it happens:**
The model string is instance state, not call state. This is consistent with DSPy's design (LM instances are configured up front), but it conflicts with the need for per-call threshold control.

**How to avoid:**
As noted in the research doc (C.1): use a thin `BaseLM` subclass that overrides `__call__` (or `forward`) to dynamically compute `self.model = f"openai/router-mf-{scoring_engine.current_threshold}"` at call time from the scoring engine's current read-only state. The scoring engine itself must be thread-safe (use a `threading.RLock` or atomic float for `current_threshold`). Never mutate a shared `dspy.LM` instance's `.model` attribute from multiple threads.

**Warning signs:**
Escalation occasionally routes to the wrong threshold under concurrent requests. Model string in LM history shows stale values that don't match the decision made by the scoring engine.

**Phase to address:**
Block 3 — design the thin LM subclass with thread-safety as a requirement, not an afterthought.

---

### Pitfall 12: RouteLLM's MF router was calibrated on GPT-4 / Mixtral — thresholds do not transfer to other model pairs

**What goes wrong:**
The MF (matrix factorization) router is trained on preference data from the GPT-4-1106-preview vs Mixtral-8x7B pair. When you swap to GPT-4o-mini (weak) vs Claude Opus or GPT-5 (strong), the router's internal score distribution changes because the relative difficulty of queries changes. A threshold of `0.11593` that routes 50% to strong on the original pair may route 80% to strong on your pair, inflating costs dramatically.

**Why it happens:**
The MF router's score is a learned latent representation of "query difficulty". Difficulty is relative to the capability gap between the model pair. A wider or different gap shifts the score distribution.

**How to avoid:**
Always run `python -m routellm.calibrate_threshold --routers mf --strong-model <your_strong> --weak-model <your_weak> --strong-model-pct 0.X --config config.yaml` with your actual model pair before the first production run. Store the calibrated threshold in config, not hardcoded. Re-calibrate when models change.

**Warning signs:**
Cost per agent run is far higher than expected (over-routing to frontier). Or loop detection never triggers because the "weak" model is actually good enough at the task (under-routing to frontier does not manifest as a test failure, it just means escalation doesn't help).

**Phase to address:**
Block 3 and Validation — calibration step must be part of the integration test setup, not an afterthought. The synthetic loop bench calibration run should be documented as a reproducible command.

---

### Pitfall 13: In-process Controller vs server mode have different failure modes

**What goes wrong:**
The in-process `Controller` loads router model weights into the same Python process as the agent. Under Python 3.14 (the target), GIL dynamics mean heavy embedding/router inference can block the event loop if you run async agents. Also, the Controller does not support all LiteLLM providers out of the box — some providers require environment variables or config YAML that the server mode exposes more cleanly.

The server mode (`python -m routellm.openai_server`) adds a network hop (~1ms local) but isolates the routing process. It also exposes a standard OpenAI-compatible endpoint, which is exactly what DSPy's `dspy.LM` with `api_base` expects.

**Why it happens:**
Two modes exist because different deployment contexts have different constraints. The server mode is more production-appropriate for the use case here (non-intrusive, separate concern).

**How to avoid:**
Default to server mode for the reference integration. The thin LM subclass points `api_base="http://localhost:6060/v1"`. Document server startup as a prerequisite in the library README. For testing, provide a mock server or stub that accepts the `router-mf-{threshold}` model string without actual routing.

**Warning signs:**
In-process Controller hangs on first import (weights loading). Router inference adds unexpected latency between LM calls in profiling.

**Phase to address:**
Block 3 — server vs in-process decision should be explicit in Block 3 design doc, with a mock/stub server for unit tests that do not require actual RouteLLM.

---

## The Few-Shot Demo KeyError Risk

### Pitfall 14: DSPy demos pass as pre-formatted `messages` through RouteLLM — extra non-standard fields in message dicts cause payload rejection

**What goes wrong:**
When DSPy compiles an optimised program with `BootstrapFewShot` or similar, each `Predict` module's `self.demos` list contains `Example` objects. The adapter serialises these into the `messages` list as role=user / role=assistant pairs. The `messages` list is then sent through RouteLLM's OpenAI-compatible endpoint, which forwards it via LiteLLM to the actual model.

The risk (flagged unresolved in research doc C.2): if DSPy's adapter attaches extra metadata to any message dict (e.g., an `_dspy_demo` flag or custom field), some strict-schema validators in LiteLLM's OpenAI provider path will reject the extra key with a `400 Unknown parameter` error or a `KeyError` during validation.

Verified LiteLLM issue pattern: `'litellm_params' passed to OpenAI API causing 'Unknown parameter' error` (GitHub issue #14901). The vector is: DSPy adapter produces a message dict, RouteLLM forwards it unchanged, LiteLLM passes it to provider, provider rejects unknown field.

**Why it happens:**
LiteLLM strips known-unknown parameters when it recognises the provider, but if RouteLLM forwards without stripping (it acts as a passthrough proxy), provider-side validation may be the first line of defense.

**How to avoid:**
(1) Write a pytest integration test: configure a compiled DSPy program with at least one demo, run it against the RouteLLM server (or a local mock), and assert the response is 200. (2) If failures occur, add a `PayloadNormalizer` step in the thin LM subclass's `forward()` that strips non-standard keys from every message dict before the request reaches RouteLLM. The normaliser only keeps `role`, `content`, `tool_calls`, `tool_call_id`, `name`. (3) Do NOT pre-emptively mutate the demo dict — operate on a copy.

**Warning signs:**
`400 Bad Request` errors only when the agent has compiled demos (not in baseline zero-shot mode). Error disappears when `Predict.demos = []`. LiteLLM logs show `Unknown parameter: _dspy_...`.

**Phase to address:**
Block 3 — the first end-to-end integration test must use a compiled program (with demos), not just a zero-shot baseline. Zero-shot tests pass; the bug hides until demos are used.

---

## Synthetic Loop Bench Pitfalls

### Pitfall 15: Weak models do not reliably loop — they sometimes fail gracefully instead

**What goes wrong:**
Building a "synthetic scenario where the weak model reliably loops" is harder than it sounds. Small models often quit early (emit `"finish"` or give a wrong answer) rather than looping. They may also hallucinate a plausible tool output rather than actually calling the tool again. The loop you need is: the model calls the same tool with nearly identical args, receives a useless observation, and calls the same tool again — reliably, across 5+ test cases.

From benchmark research (TIDE paper, 2025): "A majority of evaluated LLM agents exhibit high loop ratios... scaling from 4B to 30B reduced loop ratio from 15.8% to 1.0%." This means loops are a property of small models on hard tasks — but the specific task and tool design matter enormously.

**Why it happens:**
Loop behavior depends on the interplay between task difficulty, tool utility, and the model's instruction-following. A task that is slightly too easy will not loop. A task that is impossible will cause the model to hallucinate a completion rather than retry.

**How to avoid:**
Design the synthetic bench around a task with a well-defined correct tool path that a small model cannot complete in one step, but where the tool's responses look "almost helpful" (enough to keep the agent trying). The classic: a knowledge-lookup task where the search tool always returns a document that almost-but-not-quite contains the answer. The weak model loops on search variations; a frontier model rephrases the query or synthesises from partial evidence. Test at least 10 seeds. Measure loop rate, not just whether one run loops. Aim for >80% loop rate on the weak model across seeds.

**Warning signs:**
The weak model answers correctly on 3/5 test seeds (task is too easy). The weak model emits `finish` after 2 failed tool calls (gives up rather than loops). Loop rate is <50% — the bench is not reliable enough to demonstrate escalation value.

**Phase to address:**
Validation — define the synthetic bench task description before writing code. It is a research design problem, not an engineering problem. Block 2 (scoring engine) must be tuned on this bench, so it must exist and be reliable before Block 2 is declared done.

---

### Pitfall 16: DSPy's `cache=True` (default) causes the weak model to return the same cached response repeatedly — masking a real loop

**What goes wrong:**
DSPy's `dspy.LM` has `cache=True` by default. In the synthetic bench, when the weak model is called with an identical prompt in successive loop iterations, the cache returns the same response without an actual model call. This makes the loop look "clean" and deterministic — but it is artificial. The model never actually got stuck; it got cached. If your loop detection works on the cached-response bench, it may fail on real deployment where the model gets slightly different trajectories.

More critically: the trajectory text grows each iteration (appending new `thought_N`, `tool_name_N`, `observation_N` fields). So the input to `self.react` is different each step — the cache miss rate may be 100% anyway. But if you control inputs tightly in a bench scenario, accidental caching is possible.

**Why it happens:**
The cache key includes the full request dict (model + messages + kwargs). If the trajectory is constant across iterations (only possible in a carefully constructed test), you get a cache hit.

**How to avoid:**
For the synthetic bench, always set `cache=False` on the weak model LM: `dspy.LM("weak-model", cache=False)`. This ensures each iteration makes a real call (or a real mock call) and that loop behavior is natural, not cached. For integration tests of the loop detector itself, use `cache=False` to prevent spurious determinism.

**Warning signs:**
Bench runs complete suspiciously fast (sub-millisecond per loop iteration). Token counts are all zero (cache hits are not billed/tracked by `UsageTracker`). All loop iterations produce byte-identical outputs.

**Phase to address:**
Validation — a `cache=False` requirement must be documented in the synthetic bench setup.

---

## Cost-Safety Pitfalls

### Pitfall 17: Escalation is triggered by scoring engine state, not by human review — a miscalibrated detector causes runaway frontier spending

**What goes wrong:**
The scoring engine escalates (sets threshold to 0.0) on every flagged anomaly. If the Loop Velocity detector has a low threshold (aggressive), or if the Structural Constraint Scanner has a broad regex, nearly every call will be flagged. Each escalation sends the next call to the frontier model (GPT-5 / Opus). On a 20-iteration ReAct agent with aggressive detection, 10+ frontier calls per agent run at $0.015–$0.05 per call = $0.15–$0.50 per run. On a 1,000-run evaluation suite, that is $150–$500 from miscalibration.

**Why it happens:**
The scoring engine is easy to miscalibrate during development (set thresholds low to make tests pass), and the cost only appears in integration/evaluation runs where real API calls are made.

**How to avoid:**
(1) Log every escalation decision with its triggering signal (which detector, what score, what threshold was crossed). This makes miscalibration visible in a single evaluation run. (2) Add a configurable max-escalations-per-session cap (e.g., 3 escalations per agent episode) as a safety valve. This is NOT the budget auto-stop that was deferred out of scope — it is a per-session escalation rate limiter that prevents runaway spending even without a global budget cap. (3) During bench runs, pipe escalation events to stderr so the researcher sees them in real time. (4) Pre-compute estimated cost ceiling before starting any multi-run sweep: `n_runs × max_iters × escalation_rate × frontier_price_per_call`.

**Warning signs:**
Escalation events appear in every single trajectory during the test suite. Cost tracker shows frontier-model proportion far above the expected routing ratio. A single evaluation run costs more than expected.

**Phase to address:**
Block 2 (detector thresholds) AND Block 3 (escalation execution) — cost logging and the per-session escalation cap must be in place before any real-model evaluation runs are executed.

---

### Pitfall 18: `cost` field in DSPy's LM history is `None` on cache hits — your cost tracker silently under-reports

**What goes wrong:**
`BaseLM._process_lm_response()` stores `"cost": getattr(response, "_hidden_params", {}).get("response_cost")` in the history entry. LiteLLM computes `response_cost` from the provider's price table. On a cache hit, `response_cost` is `None` (LiteLLM sets it to `None` for cached responses). If your cost tracker sums the `cost` field naively, it will report zero for cached calls and produce a cumulative cost that is lower than actual spend.

The actual spend only occurs on the first (cache-miss) call. Subsequent identical calls are free. The risk is in the inverse: believing you've saved cost when the LM history shows `cost=None` everywhere, but you haven't checked whether those are cache hits or actual free calls.

**How to avoid:**
Track costs in two buckets: `billed_cost` (where `cost is not None`) and `estimated_free_calls` (where `cost is None AND cache_hit=True`). Log both. For the escalation tracker, only count billed escalation calls (where `cost is not None`) toward the cost cap. Use `getattr(response, "cache_hit", False)` to distinguish.

**Warning signs:**
Cost tracker shows $0.00 across an entire evaluation run. Either all calls were cached (bench is misconfigured — see Pitfall 16) or the cost field extraction is broken.

**Phase to address:**
Block 3 (cost tracking) — validated in the first real-model integration test.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Hardcode similarity threshold at 0.9 | Simpler config, works for MVP bench | False positives in generative tasks, false negatives in structured tasks; requires code change to tune | Never — make it a config parameter from day one |
| Use `dspy.configure(callbacks=[cb])` instead of `dspy.context()` | Simpler one-liner | Silently drops user's existing callbacks on every `__enter__` | Never — use `dspy.context()` |
| Skip the PayloadNormalizer step | Fewer files to write | KeyError surfaces only in compiled-program tests, which are run later; hard to trace back | Only acceptable if zero-shot-only is explicitly in scope |
| Rely on `dspy.LM.model` mutation for per-call threshold | Simple, no subclass needed | Race condition under concurrent calls; corrupts model string in history | Never — use the thin subclass |
| Fix `cache=True` during bench to make tests pass faster | Faster tests | Loop detector is validated on cached (non-looping) data; false confidence | Never in bench; acceptable in unit tests with mocked responses |
| Count all `on_module_start` events as steps | Simple counter | 3x overcounting due to outer ReAct + inner Predict + extract CoT | Never — filter by module type or use call-id depth |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| DSPy callbacks | Registering with `dspy.configure()` — replaces existing callbacks globally | Use `dspy.context(callbacks=existing + [new_cb])` for scoped, non-destructive registration |
| RouteLLM MF router | Using calibration threshold from README (0.11593, calibrated on GPT-4/Mixtral) as-is | Run `python -m routellm.calibrate_threshold` with the actual model pair before any evaluation |
| DSPy `dspy.LM` + RouteLLM server | Pointing `api_base` to RouteLLM server with `model="openai/router-mf-0.11"` — the `openai/` prefix is for LiteLLM, not RouteLLM | Use `model="openai/router-mf-0.11"` — LiteLLM strips the `openai/` prefix; RouteLLM sees `router-mf-0.11`. Verify model string format against installed RouteLLM server logs on first run |
| LiteLLM under RouteLLM | Assuming `extra_body` parameters pass through transparently | They do not always — `extra_body` handling is provider-specific; test with the actual provider you intend to use |
| DSPy `dspy.ReAct` + `max_iters` | Relying on `max_iters` as a reliable termination signal | ReAct also terminates on `ValueError` (invalid tool selection) and on `ContextWindowExceededError` after 3 truncation attempts; your tracker must handle these early-exit paths |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Synchronous embedding in `on_lm_end` | Each agent step adds 15–50ms; async agents block on embedding | Warm model at startup; use `run_in_executor` in async paths | At any agent run with >5 steps if latency SLA < 2s |
| No embedding model warm-up | First agent run takes 2–10s extra (model load) | Call `model.encode(["warm"])` at `TrajectoryTracker.__enter__` | On first instantiation in a new process |
| Storing full message dicts per step in the tracker | Memory grows O(n_steps × message_length); in a 20-step agent with long context, each snapshot is 10–50KB | Store only the embedding vector + scalar telemetry per step, not the full messages | At batch evaluation with 1,000+ runs — ~50GB if naively accumulated |
| Re-embedding the same observation string multiple times | A repeated observation (loop condition) triggers re-embedding each time | Cache `(string → embedding)` with a small LRU; repeated strings are the detection target | Not a correctness issue, but 2–4x unnecessary compute per detected loop |

---

## "Looks Done But Isn't" Checklist

- [ ] **Callback registration:** Verify that a pre-existing callback (e.g., a print-logger) is still active inside a `TrajectoryTracker` context — not silently dropped.
- [ ] **Signature identity:** Log `signature.__name__` for a DSPy agent that uses inline string signatures and confirm you do NOT see all steps labeled `"StringSignature"`.
- [ ] **Step counter:** Run a 5-iteration ReAct agent and confirm the step counter reports 5, not 10–15 (double-counting from module nesting).
- [ ] **Token usage:** Confirm that per-step token counts are non-zero and roughly consistent with the actual request size. If they are all 0, the collection path is wrong.
- [ ] **Few-shot demo roundtrip:** Run a DSPy program with at least one compiled demo through the RouteLLM server and assert 200 response, no KeyError.
- [ ] **Loop bench reliability:** Run the synthetic bench 10 times with different seeds and confirm the weak model loops on ≥8/10 seeds. If not, the task design is wrong.
- [ ] **Cache disabled in bench:** Confirm `cache=False` on the weak model LM in the synthetic bench, and that wall time per iteration is >0ms (not a cache hit).
- [ ] **Escalation logging:** Confirm that every escalation event is logged with the triggering detector name, score, and threshold.
- [ ] **Cost tracking:** After a real-model run, confirm the cost tracker reports a non-zero value that matches the provider's billing dashboard within ±10%.
- [ ] **Thread safety:** Run two concurrent sessions under `TrajectoryTracker` and confirm neither session's step count leaks into the other.

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| P1: `on_lm_end` has no usage data | Block 1 — State Capture | Integration test: assert per-step token delta > 0 using the chosen collection path |
| P2: `"StringSignature"` breaks step identity | Block 1 — State Capture | Unit test: assert all steps have non-`"StringSignature"` identity even for inline-defined agents |
| P3: ContextVar broken across threads | Block 1 — State Capture | Concurrent session test: two threads, assert no cross-contamination |
| P4: `configure()` replaces callbacks | Block 1 — State Capture | Test: pre-existing callback still fires inside `TrajectoryTracker` context |
| P5: Callback input mutation | Block 1 — State Capture | Code review + unit test: store only deep copies or scalar extractions |
| P6: Exception path in `on_lm_end` | Block 1 — State Capture | Inject a mock LM that raises to trigger the exception path |
| P7: ReAct double-counts module events | Block 1 — State Capture | 5-iter ReAct trace: assert step count == 5 |
| P8: Similarity threshold needs calibration | Block 2 — Scoring Engine | Calibrate on synthetic bench; expose threshold as config |
| P9: Embedding adds latency | Block 2 — Scoring Engine | Profile: embedding must be < 20ms per step in warm state |
| P10: Tool retries look like loops | Block 2 — Scoring Engine | Test: one tool error + retry does NOT trigger escalation |
| P11: LM.model race condition | Block 3 — Routing Layer | Concurrent call test: model strings in history match the decisions logged |
| P12: Threshold doesn't transfer across model pairs | Block 3 + Validation | Calibration command in integration test setup |
| P13: In-process vs server mode | Block 3 — Routing Layer | Use server mode as default; mock server in unit tests |
| P14: Few-shot demo KeyError | Block 3 — Routing Layer | End-to-end test with compiled demos through RouteLLM |
| P15: Weak model doesn't reliably loop | Validation — Synthetic Bench | ≥8/10 seeds loop; measure loop rate, not single-run observation |
| P16: Cache masks real loops in bench | Validation — Synthetic Bench | `cache=False` on weak model; verify non-trivial wall time |
| P17: Miscalibrated detector → runaway cost | Block 2 + Block 3 | Per-session escalation cap + escalation event logging before any real-model sweep |
| P18: Cost `None` on cache hits | Block 3 — Cost Tracking | Assert cost tracker produces non-zero value on a cache-miss run |

---

## Sources

- DSPy 3.2.1 installed source (verified 2026-06-18): `dspy/utils/callback.py`, `dspy/clients/base_lm.py`, `dspy/clients/lm.py`, `dspy/predict/react.py`, `dspy/predict/predict.py`, `dspy/signatures/signature.py`
- Live Python verification: `signature.__name__` behavior, `ContextVar` thread isolation, `dspy.configure()` replacement semantics
- RouteLLM ctx7 docs (`/lm-sys/routellm`): Controller initialization, server mode, threshold calibration commands
- DSPy observability docs (official): input mutation warning in callbacks
- LiteLLM GitHub issue #14901: `litellm_params` / unknown parameter in OpenAI provider
- TIDE paper (2025): loop ratio in small models
- Community forum: cosine similarity threshold tuning range 0.75–0.95

---
*Pitfalls research for: DSPy trajectory-monitoring router + RouteLLM integration*
*Researched: 2026-06-18*
