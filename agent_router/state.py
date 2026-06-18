# agent_router/state.py
# Source: Python stdlib docs — dataclasses.dataclass(frozen=True) [VERIFIED: Python 3.14.5]
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TurnRecord:
    """Immutable record of a single LM call, captured by TrajectoryTracker (Phase 2)."""

    call_id: str
    step_idx: int
    signature_name: str  # class.__name__ + sorted(input_fields + output_fields)
    tool_name: str | None
    tool_args: dict | None  # type: ignore[type-arg]
    input_token_count: int
    output_token_count: int
    output_text: str | None  # raw LM output; NO output_embedding here (lazy in Phase 3)
    cache_hit: bool
    exception: Exception | None  # from on_lm_end(exception=...) (Pitfall P6)


@dataclass(frozen=True)
class CostRecord:
    """Immutable cost record per routed LM call."""

    call_id: str
    model_used: str  # resolved model name (weak or strong)
    billed_cost: float | None  # None on cache hit (Pitfall P18)
    input_tokens: int
    output_tokens: int
    is_cache_hit: bool


@dataclass
class SessionState:
    """Mutable per-session state: sliding window + routing parameters."""

    session_id: str
    window: deque  # type: ignore[type-arg]  # deque[TurnRecord], maxlen=config.window_size
    current_threshold: float
    escalation_count: int
    cost_log: list  # type: ignore[type-arg]  # list[CostRecord]
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


# Module-level registry: keyed by session_id.
# TODO: TrajectoryTracker.__exit__ must delete entries to prevent unbounded growth (Phase 2).
_SESSION_REGISTRY: dict[str, SessionState] = {}
