# tests/unit/test_contracts.py
# Nyquist gates for structural contracts (directory layout + data contract shapes).
# test_directory_structure: GREEN now (dirs exist from Task 2).
# TurnRecord/CostRecord/SessionState tests: GREEN now (state.py implemented in Task 2).
from __future__ import annotations

import dataclasses
import threading
from collections import deque
from pathlib import Path


def test_directory_structure() -> None:
    """
    Assert that the documented directory structure exists.
    LIB-01 success criterion 5.
    Status: GREEN from Task 2 (directories created).
    """
    root = Path(__file__).parent.parent.parent  # project root
    assert (root / "agent_router").is_dir(), "agent_router/ package dir missing"
    assert (root / "agent_router" / "routing").is_dir(), "agent_router/routing/ subpackage missing"
    assert (root / "tests" / "unit").is_dir(), "tests/unit/ dir missing"
    assert (root / "tests" / "integration").is_dir(), "tests/integration/ dir missing"
    assert (root / "tests" / "bench").is_dir(), "tests/bench/ dir missing"
    assert (root / "pyproject.toml").is_file(), "pyproject.toml missing"


def test_turn_record_is_frozen() -> None:
    """
    TurnRecord must be a frozen dataclass (D-03): immutable after construction.
    Status: GREEN from Task 2 (state.py implements frozen TurnRecord).
    """
    from agent_router.state import TurnRecord

    record = TurnRecord(
        call_id="test-call-1",
        step_idx=0,
        signature_name="ChainOfThought[question->answer]",
        tool_name=None,
        tool_args=None,
        input_token_count=100,
        output_token_count=50,
        output_text="The answer is 42.",
        cache_hit=False,
        exception=None,
    )
    # Must raise FrozenInstanceError on mutation attempt
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        record.step_idx = 99  # type: ignore[misc]


def test_turn_record_fields_present() -> None:
    """
    TurnRecord must carry the D-06 fields: signature identity, step index, token counts,
    output text, cache-hit flag, exception field.
    Status: GREEN from Task 2.
    """
    from agent_router.state import TurnRecord

    field_names = {f.name for f in dataclasses.fields(TurnRecord)}
    required_fields = {
        "call_id",
        "step_idx",
        "signature_name",
        "tool_name",
        "tool_args",
        "input_token_count",
        "output_token_count",
        "output_text",
        "cache_hit",
        "exception",
    }
    missing = required_fields - field_names
    assert not missing, f"TurnRecord is missing D-06 fields: {missing}"


def test_cost_record_is_frozen() -> None:
    """
    CostRecord must be frozen (D-03): immutable, separating billed vs cache-free cost.
    Status: GREEN from Task 2.
    """
    from agent_router.state import CostRecord

    record = CostRecord(
        call_id="test-call-1",
        model_used="openai/gpt-4o-mini",
        billed_cost=0.0003,
        input_tokens=100,
        output_tokens=50,
        is_cache_hit=False,
    )
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        record.billed_cost = 9.99  # type: ignore[misc]

    # Cache-hit: billed_cost is None (Pitfall P18)
    cache_record = CostRecord(
        call_id="test-call-2",
        model_used="openai/gpt-4o-mini",
        billed_cost=None,
        input_tokens=100,
        output_tokens=50,
        is_cache_hit=True,
    )
    assert cache_record.billed_cost is None


def test_session_state_is_mutable() -> None:
    """
    SessionState must be mutable (D-03): sliding window updated in place.
    Status: GREEN from Task 2.
    """
    from agent_router.state import SessionState

    state = SessionState(
        session_id="sess-001",
        window=deque(maxlen=10),
        current_threshold=0.11593,
        escalation_count=0,
        cost_log=[],
    )
    # Must be mutable — no FrozenInstanceError
    state.escalation_count = 1
    assert state.escalation_count == 1

    state.current_threshold = 0.0  # forced escalation
    assert state.current_threshold == 0.0

    # Must have a threading.Lock for concurrent sessions
    assert isinstance(state._lock, threading.Lock)


def test_session_state_fields_present() -> None:
    """
    SessionState must carry the D-06 fields: session_id, turn window, current_threshold,
    escalation_count.
    Status: GREEN from Task 2.
    """
    from agent_router.state import SessionState

    field_names = {f.name for f in dataclasses.fields(SessionState)}
    required_fields = {
        "session_id",
        "window",
        "current_threshold",
        "escalation_count",
        "cost_log",
    }
    missing = required_fields - field_names
    assert not missing, f"SessionState is missing D-06 fields: {missing}"


def test_turn_record_is_hashable_with_tool_args() -> None:
    """
    A frozen TurnRecord carrying a dict tool_args must remain hashable (WR-01):
    tool_args is excluded from the auto-generated __hash__/__eq__.
    """
    from agent_router.state import TurnRecord

    record = TurnRecord(
        call_id="c1",
        step_idx=0,
        signature_name="ReAct[q->a]",
        tool_name="search",
        tool_args={"query": "weather", "limit": 5},
        input_token_count=10,
        output_token_count=5,
        output_text="...",
        cache_hit=False,
        exception=None,
    )
    # Must not raise TypeError: unhashable type: 'dict'
    assert isinstance(hash(record), int)


def test_session_state_equality_ignores_lock() -> None:
    """
    Two value-identical SessionStates must compare equal — the per-instance Lock is
    excluded from __eq__ (CR-02).
    """
    from agent_router.state import SessionState

    def make() -> SessionState:
        return SessionState(
            session_id="s1",
            window=deque(maxlen=10),
            current_threshold=0.11593,
            escalation_count=0,
            cost_log=[],
        )

    assert make() == make()


def test_registry_lock_exists() -> None:
    """A module-level registry lock guards check-then-insert on the session registry (CR-01)."""
    from agent_router import state

    assert isinstance(state._REGISTRY_LOCK, threading.Lock)
