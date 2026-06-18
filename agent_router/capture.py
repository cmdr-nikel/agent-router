# agent_router/capture.py
# Source: dspy 3.2.1 callback system — verified against installed source
# (dspy/utils/callback.py, dspy/clients/base_lm.py, dspy/clients/cache.py,
#  dspy/predict/react.py, dspy/signatures/signature.py)
#
# Security note: TurnRecord.output_text stores raw LM output which may contain
# prompt-derived text. Downstream logging / PII handling is the caller's
# responsibility. This library records what DSPy produces; no sanitisation is
# applied (T-02-05, accepted).
from __future__ import annotations

from typing import Any

from dspy.utils.callback import BaseCallback

from agent_router.state import SessionState, TurnRecord


def _derive_signature_name(sig: Any) -> str:
    """Stable identity for any DSPy Signature class, including inline StringSignature.

    For named Signature subclasses (e.g. class MyPipeline(dspy.Signature):) this
    returns the class __name__ unchanged.

    For inline string signatures (created via dspy.Signature.from_str / make_signature),
    DSPy always names them "StringSignature".  Two different inline signatures would
    be indistinguishable by name alone, so we derive a stable key from their sorted
    field names:

        StringSignature:<sorted-input-fields>><sorted-output-fields>

    Example:
        "city -> weather"        ->  "StringSignature:city>weather"
        "city, date -> forecast" ->  "StringSignature:city,date>forecast"

    Args:
        sig: A DSPy Signature class (not an instance).

    Returns:
        A non-empty string that uniquely identifies the signature shape.
    """
    name: str = getattr(sig, "__name__", "unknown")
    if name != "StringSignature":
        return name
    # Inline signature: build from sorted field names (D-04 / CAP-03).
    in_keys = sorted(getattr(sig, "input_fields", {}).keys())
    out_keys = sorted(getattr(sig, "output_fields", {}).keys())
    return f"StringSignature:{','.join(in_keys)}>{','.join(out_keys)}"


class TrajectoryCallback(BaseCallback):  # type: ignore[misc]
    """DSPy callback that captures one TurnRecord per non-extract LM call.

    Bound to a single SessionState by direct object reference — the isolation
    primitive for concurrent sessions. Never share a TrajectoryCallback instance
    across TrajectoryTracker instances.

    ReAct overcount (CAP-04):
        A dspy.ReAct with N iterations fires on_lm_end N+1 times: N react steps
        plus one trailing extract call from the ChainOfThought module. We detect
        the extract module via on_module_start (ChainOfThought isinstance check)
        and track its call_id in _extract_ids for the duration of that module call.
        on_lm_end skips record creation for successful LM calls while _extract_ids
        is non-empty. (A failed extract is still recorded — see CAP-06 / WR-04.)

        Assumption A1: the extract module is always a ChainOfThought and the
        react-step modules are always Predict instances. If the user's agent uses
        ChainOfThought inside tools or custom sub-modules, the sentinel may
        over-exclude records from those sub-modules. Adjust to a more specific
        heuristic (e.g. trajectory field detection) if needed.

    ContextVar threading caveat:
        This callback is registered via dspy.context(callbacks=...) which uses a
        ContextVar for scope-local override storage. ContextVar values ARE copied
        to child asyncio Tasks but are NOT inherited by threading.Thread instances
        spawned directly. For ThreadPoolExecutor-based parallel agents, the callback
        will not fire in child threads. Use copy_context().run() when spawning
        threads inside a TrajectoryTracker context, or avoid thread-based
        parallelism there. Async (asyncio) is unaffected.
    """

    def __init__(self, session: SessionState) -> None:
        self._session: SessionState = session
        # call_id → BaseLM instance; populated in on_lm_start, consumed in on_lm_end.
        # Bounded by session lifetime (DoS mitigation T-02-04: pop on on_lm_end).
        self._pending_lm: dict[str, Any] = {}
        # Last seen Predict signature; safe for single-threaded sequential DSPy modules.
        self._active_signature: str = "unknown"
        # Set of active ChainOfThought (extract) module call_ids (CAP-04). A set, not a
        # single id, so a nested ChainOfThought does not clear the sentinel early when
        # its inner on_module_end fires (Pitfall WR-01). While the set is non-empty,
        # on_lm_end suppresses record creation for successful LM calls.
        self._extract_ids: set[str] = set()

    def on_module_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
        """Capture signature identity and detect the extract ChainOfThought.

        Signature capture: on_module_start fires before on_lm_start for the same
        module call (verified: callback.py sync_wrapper). Storing _active_signature
        here is correct for single-threaded sequential agents (RESEARCH Pitfall NEW).

        Extract detection: the extract module in dspy.ReAct is always a
        ChainOfThought (see dspy/predict/react.py self.extract). We set the
        _in_extract sentinel on ANY ChainOfThought module_start so on_lm_end can
        skip its LM call (Assumption A1 above).
        """
        sig = getattr(instance, "signature", None)
        if sig is not None:
            self._active_signature = _derive_signature_name(sig)

        # Detect extract ChainOfThought — guard ImportError for minimal-dep envs.
        try:
            from dspy.predict.chain_of_thought import ChainOfThought  # noqa: PLC0415

            if isinstance(instance, ChainOfThought):
                self._extract_ids.add(call_id)
        except ImportError:
            pass

    def on_module_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: Exception | None = None,
    ) -> None:
        """Clear this call's extract sentinel once the ChainOfThought module completes."""
        self._extract_ids.discard(call_id)

    def on_lm_start(
        self,
        call_id: str,
        instance: Any,
        inputs: dict[str, Any],
    ) -> None:
        """Store the LM instance keyed by call_id for retrieval in on_lm_end.

        on_lm_end does not receive the LM instance, only outputs/exception.
        We capture it here so on_lm_end can read lm.history[-1]["usage"]
        for token counts (Pitfall P1: do NOT read tokens from outputs).
        """
        # Never store the inputs dict by reference (T-02-03 / Pitfall P5).
        # We do not record the raw input dict; we only store the LM instance.
        self._pending_lm[call_id] = instance

    def on_lm_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: Exception | None = None,
    ) -> None:
        """Create and append one TurnRecord per non-extract LM call.

        This is the single point of TurnRecord creation for Phase 2 telemetry.

        Token usage: lm.history[-1]["usage"] is the definitive source (written by
        BaseLM._process_lm_response before this callback fires). NEVER derive token
        counts from `outputs` — it is a list of decoded text strings with no usage
        data (Pitfall P1 / RESEARCH §Pattern 3).

        Cache hits: the DSPy cache layer sets response.usage = {} and
        response.cache_hit = True (dspy/clients/cache.py _prepare_cached_response).
        We detect this via getattr(response, "cache_hit", False) and flag it
        distinctly rather than silently treating it as a zero-token normal call.

        Exception path: on_lm_end fires in a finally block even when the LM call
        raises (RESEARCH §Pattern 6). outputs is None on exception; we guard
        before accessing it and still append a TurnRecord with exception set.
        """
        lm = self._pending_lm.pop(call_id, None)

        # Skip if we have no lm ref (shouldn't happen).
        if lm is None:
            return

        # Skip SUCCESSFUL extract calls (the ChainOfThought trailing summary step).
        # Exception: if the extract LM call itself raised, still capture the record
        # so that failed steps are always visible in the window (CAP-06). A failed
        # extract therefore yields N+1 records — see TrajectoryTracker docstring (WR-04).
        if self._extract_ids and exception is None:
            return

        # Read usage from history ONLY on the success path. On exception,
        # _process_lm_response never ran, so lm.history[-1] is the PREVIOUS successful
        # call's entry — reading it would attribute another step's tokens to this failed
        # record (Pitfall CR-01). Use an empty entry instead → 0 tokens, no cache flag.
        entry: dict[str, Any] = lm.history[-1] if (exception is None and lm.history) else {}
        usage: dict[str, Any] = entry.get("usage", {}) or {}
        response: Any = entry.get("response") if entry else None
        is_cache_hit: bool = bool(getattr(response, "cache_hit", False))

        # Extract output text safely — outputs is None when an exception occurred.
        output_text: str | None = None
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, str):
                output_text = first
            elif isinstance(first, dict):
                output_text = first.get("text")

        with self._session._lock:
            step_idx = len(self._session.window)
            record = TurnRecord(
                call_id=call_id,
                step_idx=step_idx,
                signature_name=self._active_signature,
                tool_name=None,
                tool_args=None,
                input_token_count=int(usage.get("prompt_tokens", 0)),
                output_token_count=int(usage.get("completion_tokens", 0)),
                output_text=output_text,
                cache_hit=is_cache_hit,
                exception=exception,
            )
            self._session.window.append(record)
