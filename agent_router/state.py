# agent_router/state.py
# Source: Python stdlib docs — dataclasses.dataclass(frozen=True) [VERIFIED: Python 3.14.5]
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TurnRecord:
    """Immutable record of a single LM call, captured by TrajectoryTracker (Phase 2)."""

    call_id: str
    step_idx: int
    signature_name: str  # class.__name__ + sorted(input_fields + output_fields)
    tool_name: str | None
    # Excluded from eq/hash so the frozen auto-__hash__ doesn't choke on an unhashable
    # dict (Pitfall WR-01). The flapping monitor (Phase 2) compares tool_args explicitly,
    # not via TurnRecord equality.
    tool_args: dict[str, Any] | None = field(compare=False, hash=False)
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


@dataclass(frozen=True)
class ToolEvent:
    """Immutable record of one tool call + its observation (Phase 3, gap D-05).

    Captured via on_tool_start/on_tool_end (dspy.Tool is @with_callbacks). Lives in a
    parallel list rather than on TurnRecord because the tool runs AFTER the step's
    (frozen) TurnRecord is created. The scoring engine reads these for flapping
    detection and for the loop-velocity observation-change false-positive gate (P10).
    """

    call_id: str
    tool_name: str
    # Excluded from eq/hash (frozen auto-__hash__ can't hash a dict — same as TurnRecord WR-01).
    tool_args: dict[str, Any] | None = field(compare=False, hash=False)
    observation: str | None = None
    exception: Exception | None = None


@dataclass
class SessionState:
    """Mutable per-session state: sliding window + routing parameters."""

    session_id: str
    window: deque  # type: ignore[type-arg]  # deque[TurnRecord], maxlen=config.window_size
    current_threshold: float
    escalation_count: int
    cost_log: list  # type: ignore[type-arg]  # list[CostRecord]
    # Parallel to window: one ToolEvent per tool call (Phase 3 / gap D-05).
    tool_events: list[ToolEvent] = field(default_factory=list)
    # compare=False/hash=False: each Lock is a unique object, so including it would make
    # two value-identical SessionStates never compare equal (Pitfall CR-02).
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False, hash=False
    )


# Module-level registry: keyed by session_id.
# TODO: TrajectoryTracker.__exit__ must delete entries to prevent unbounded growth (Phase 2).
_SESSION_REGISTRY: dict[str, SessionState] = {}

# Guards the check-then-insert on _SESSION_REGISTRY (Pitfall CR-01: TOCTOU race).
# Phase 2's TrajectoryTracker.__enter__ MUST hold this around registry mutation:
#     with _REGISTRY_LOCK:
#         if session_id not in _SESSION_REGISTRY: _SESSION_REGISTRY[session_id] = ...
_REGISTRY_LOCK: threading.Lock = threading.Lock()
