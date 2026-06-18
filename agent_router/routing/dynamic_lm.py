# agent_router/routing/dynamic_lm.py
# Source: dspy BaseLM docs (installed source ~/.local/lib/python3.14/site-packages/dspy/clients/base_lm.py)
# Note: routellm is NOT imported at module load — it is under the [serve] optional extra.
from __future__ import annotations

from typing import Any

import dspy  # type: ignore[import-untyped]


class DynamicRouteLM(dspy.BaseLM):
    """
    Phase 1 stub — public API surface only.
    Implemented in Phase 4.

    Wraps a RouteLLM server endpoint, dynamically adjusting the routing
    threshold per call based on the trajectory scorer's output.
    """

    def __init__(
        self,
        session_id: str,
        router: str = "mf",
        routellm_base: str = "http://localhost:6060/v1",
        api_key: str = "",
        **kwargs: Any,
    ) -> None:
        # Build initial model string; will be rebuilt per-call in Phase 4
        # Format: openai/router-{router_name}-{threshold}
        model = f"openai/router-{router}-0.11593"
        super().__init__(model=model, **kwargs)
        self.session_id = session_id
        self.router = router
        self.routellm_base = routellm_base

    def forward(self, prompt: str | None = None, messages: list | None = None, **kwargs: Any) -> Any:
        raise NotImplementedError("DynamicRouteLM.forward implemented in Phase 4")
