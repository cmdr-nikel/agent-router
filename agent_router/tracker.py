# agent_router/tracker.py
# Source: RESEARCH.md §"Verified: TrajectoryTracker stub (context manager shell)"
# Phase 2 implementation: wires TrajectoryCallback via dspy.context().
# Source-verified: dspy/dsp/utils/settings.py context() + ContextVar.reset (RESEARCH §Pattern 1)
from __future__ import annotations

from collections import deque
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

import dspy

from agent_router.capture import TrajectoryCallback
from agent_router.state import (
    SessionState,
    _REGISTRY_LOCK,
    _SESSION_REGISTRY,
)

# Default window size for captured TurnRecords.
_DEFAULT_WINDOW_SIZE: int = 50


class TrajectoryTracker:
    """
    Context manager that silently instruments any DSPy program to capture
    per-step telemetry into a SessionState.window.

    Usage:
        with TrajectoryTracker(session_id="my-session") as tracker:
            # your dspy agent code here
            agent(question="...")
        records = list(tracker._session.window)

    Callback registration: uses dspy.context(callbacks=existing + [cb]) — NOT
    dspy.configure() — to preserve pre-existing user callbacks (Langfuse, W&B,
    etc.) and to scope the change to this context only (D-01 / Pitfall P4).

    ContextVar threading caveat: see TrajectoryCallback docstring. Async agents
    work correctly; ThreadPoolExecutor-based parallel agents may not.

    Session registry: each TrajectoryTracker creates or looks up a SessionState in
    _SESSION_REGISTRY under _REGISTRY_LOCK (TOCTOU-safe, Pitfall CR-01). On
    __exit__, the session is removed to prevent unbounded registry growth.
    """

    def __init__(
        self,
        session_id: str,
        config: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self.config = config
        # Set in __enter__, retained after __exit__ for post-run inspection.
        self._session: SessionState | None = None
        self._callback: TrajectoryCallback | None = None
        self._ctx: AbstractContextManager[Any] | None = None

    def __enter__(self) -> "TrajectoryTracker":
        """Create or look up the SessionState, register the callback, enter dspy.context."""
        # 1. Create or look up session under registry lock (TOCTOU fix / Pitfall CR-01).
        window_size: int = (
            getattr(self.config, "window_size", _DEFAULT_WINDOW_SIZE)
            if self.config is not None
            else _DEFAULT_WINDOW_SIZE
        )
        with _REGISTRY_LOCK:
            if self.session_id not in _SESSION_REGISTRY:
                _SESSION_REGISTRY[self.session_id] = SessionState(
                    session_id=self.session_id,
                    window=deque(maxlen=window_size),
                    current_threshold=1.0,
                    escalation_count=0,
                    cost_log=[],
                )
            self._session = _SESSION_REGISTRY[self.session_id]

        # 2. Build callback bound to this session by direct object reference.
        self._callback = TrajectoryCallback(session=self._session)

        # 3. Register via dspy.context — preserves existing callbacks (D-01 / P4).
        #    dspy.context returns a contextmanager; we enter it manually so __exit__
        #    can restore the original callbacks via ContextVar.reset(token).
        existing = dspy.settings.get("callbacks", [])
        self._ctx = dspy.context(callbacks=existing + [self._callback])
        self._ctx.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Restore previous callbacks and remove session from registry."""
        # 1. Restore original callbacks via ContextVar.reset (atomic; handles exceptions).
        if self._ctx is not None:
            self._ctx.__exit__(exc_type, exc_val, exc_tb)
            self._ctx = None

        # 2. Remove session from registry to prevent unbounded growth.
        with _REGISTRY_LOCK:
            _SESSION_REGISTRY.pop(self.session_id, None)

        # Note: self._session is retained so callers can inspect records after exit.
