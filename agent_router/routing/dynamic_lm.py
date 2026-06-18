# agent_router/routing/dynamic_lm.py
# Block 3 — RouteLLM Execution Layer. A thin dspy.LM subclass that, on EACH call, reads the
# session's current_threshold (set by the scoring engine) and routes via the RouteLLM model
# string `router-{name}-{threshold}`. No RouteLLM patching: threshold is per-request via the
# model field (research finding 2026-06-18). routellm itself is never imported here (it pulls
# torch); we talk to an OpenAI-compatible RouteLLM server over HTTP via dspy.LM/litellm.
from __future__ import annotations

import threading
from typing import Any

import dspy

from agent_router.state import CostRecord, _REGISTRY_LOCK, _SESSION_REGISTRY

# Keys an OpenAI-compatible chat message may carry. Few-shot demos / adapters can attach extra
# keys that some servers reject (ROUTE-04 / Pitfall P14); we strip to this allow-list.
_ALLOWED_MESSAGE_KEYS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id"})


class DynamicRouteLM(dspy.LM):  # type: ignore[misc]
    """LM that rebuilds its RouteLLM model string per call from the session threshold.

    Bound to a session_id; at call time it looks up the live SessionState in the registry and
    composes `openai/router-{router}-{current_threshold}`. When the scoring engine has forced
    `current_threshold = 0.0`, RouteLLM routes 100% to the strong model (escalation, ROUTE-02).

    Thread-safety (Pitfall P11 / ROUTE-03): dspy.LM.forward reads `self.model`, so the model
    string is set and used under a per-instance lock — concurrent calls to the SAME instance
    serialize; different sessions use different instances and never share `self.model`.
    """

    def __init__(
        self,
        session_id: str,
        router: str = "mf",
        api_base: str = "http://localhost:6060/v1",
        api_key: str = "",
        default_threshold: float = 0.11593,
        **kwargs: Any,
    ) -> None:
        self.session_id = session_id
        self.router = router
        self.default_threshold = default_threshold
        self._model_lock = threading.Lock()
        super().__init__(
            model=self._model_string(default_threshold),
            api_base=api_base,
            api_key=api_key or "sk-no-auth",
            model_type="chat",
            cache=False,  # routing decision is per-call; caching would mask escalation
            **kwargs,
        )

    def _model_string(self, threshold: float) -> str:
        return f"openai/router-{self.router}-{threshold}"

    def _current_threshold(self) -> float:
        session = _SESSION_REGISTRY.get(self.session_id)
        return session.current_threshold if session is not None else self.default_threshold

    @staticmethod
    def _normalize_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        """Strip messages to OpenAI-standard keys so few-shot demos don't trip the server (ROUTE-04)."""
        if not messages:
            return messages
        return [{k: v for k, v in m.items() if k in _ALLOWED_MESSAGE_KEYS} for m in messages]

    def forward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        threshold = self._current_threshold()
        model_string = self._model_string(threshold)
        messages = self._normalize_messages(messages)
        with self._model_lock:
            self.model = model_string
            results = super().forward(prompt=prompt, messages=messages, **kwargs)
        self._log_cost(model_string, results)
        return results

    def _log_cost(self, model_string: str, results: Any) -> None:
        """Append a CostRecord to the session (ROUTE-06): tokens always, billed vs cache split."""
        session = _SESSION_REGISTRY.get(self.session_id)
        if session is None:
            return
        usage = dict(getattr(results, "usage", {}) or {})
        is_cache_hit = bool(getattr(results, "cache_hit", False))
        billed: float | None = None
        if not is_cache_hit:
            try:
                import litellm

                billed = float(litellm.completion_cost(completion_response=results))
            except Exception:
                billed = None  # unknown pricing (e.g. mock/local model) — tokens still logged
        record = CostRecord(
            call_id=model_string,
            model_used=model_string,
            billed_cost=billed,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            is_cache_hit=is_cache_hit,
        )
        with _REGISTRY_LOCK:
            session.cost_log.append(record)
