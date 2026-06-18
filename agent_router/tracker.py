# agent_router/tracker.py
# Source: RESEARCH.md §"Verified: TrajectoryTracker stub (context manager shell)"
from __future__ import annotations

from types import TracebackType
from typing import Any


class TrajectoryTracker:
    """
    Phase 1 stub — public API surface only.
    Implemented in Phase 2.

    Usage:
        with TrajectoryTracker(session_id="my-session") as tracker:
            # your dspy agent code here
            ...
    """

    def __init__(
        self,
        session_id: str,
        config: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self.config = config

    def __enter__(self) -> "TrajectoryTracker":
        # Phase 2: create SessionState, register TrajectoryCallback via dspy.context()
        # NOTE: must use dspy.context(callbacks=existing + [cb]) NOT dspy.configure()
        #       to avoid replacing existing user callbacks (Pitfall P4).
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Phase 2: deregister callback, flush cost log, remove from _SESSION_REGISTRY.
        # TODO: cleanup from _SESSION_REGISTRY on exit to prevent unbounded growth.
        pass
