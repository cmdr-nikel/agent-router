# tests/conftest.py
# Shared fixtures for the agent-router test suite.
# Phase 1: empty shell.
# Phase 2 (02-01): DummyLM test double + ReAct harness fixtures for CAP-01..CAP-07.
#
# Light-import discipline enforced: no fastembed, no routellm imports at module level.
# DummyLM subclasses dspy.clients.base_lm.BaseLM via dspy.utils.DummyLM (already a BaseLM
# subclass) — verified against dspy 3.2.1 installed source.
from __future__ import annotations

from typing import Any

import pytest

import dspy
from dspy.utils.callback import BaseCallback

# Re-export the sentinel type so test modules can annotate parameters.
# (Not imported as a side-effect of conftest — only if tests import it explicitly.)


# ---------------------------------------------------------------------------
# CacheHit sentinel
# ---------------------------------------------------------------------------


class CacheHit:
    """
    Sentinel wrapper for a dict response spec.

    When DummyLM.forward encounters a CacheHit item, it formats the text
    normally (using the parent DummyLM's formatter so the adapter can parse
    it), but sets response.usage = {} and response.cache_hit = True — mimicking
    what Cache._prepare_cached_response does in the real DSPy cache layer
    (verified: dspy/clients/cache.py lines 149-155).

    Usage:
        DummyLM([CacheHit({'next_thought': 'done', 'next_tool_name': 'finish',
                           'next_tool_args': {}})])
    """

    def __init__(self, fields: dict[str, Any]) -> None:
        self.fields = fields


# ---------------------------------------------------------------------------
# DummyLM — network-free, scripted, non-zero usage.
# ---------------------------------------------------------------------------


class DummyLM(dspy.utils.DummyLM):
    """
    Network-free LM test double for Phase 2 unit tests.

    Extends dspy.utils.DummyLM (which is already a BaseLM subclass, verified
    against dspy 3.2.1) to add:

    1. **Non-zero token usage** — every normal response carries
       prompt_tokens=10, completion_tokens=5, so lm.history[-1]["usage"] is
       non-empty (CAP-05 requirement; built-in DummyLM returns 0s).

    2. **Cache-hit simulation** — wrap a response dict in `CacheHit(...)`.
       The LM still formats the text correctly (so the adapter can parse it)
       but sets response.usage = {} and response.cache_hit = True, replicating
       the Cache._prepare_cached_response behaviour.

    3. **Exception injection** — put an Exception *instance* in the responses
       list; forward() raises it on that call, letting on_lm_end fire with
       exception=... (CAP-06 requirement).

    Construction:
        DummyLM(responses=[
            # Normal dict response (N react steps):
            {'next_thought': 'step 0', 'next_tool_name': 'lookup',
             'next_tool_args': {'query': 'test'}},
            ...
            {'next_thought': 'done', 'next_tool_name': 'finish',
             'next_tool_args': {}},
            # Cache-hit response:
            CacheHit({'next_thought': 'cached', 'next_tool_name': 'finish',
                      'next_tool_args': {}}),
            # Error response:
            RuntimeError('LM exploded'),
            # Extract step (ChainOfThought expects reasoning + output fields):
            {'reasoning': 'all done', 'answer': '42'},
        ])

    Isolation:
        Never share a DummyLM instance between test sessions.  Each call
        consumes from the internal iterator.  Use the dummy_lm_factory
        fixture to get a fresh instance per session (RESEARCH Open Question 2).

    Thread safety:
        Not thread-safe — do not share across threads.  For CAP-07 concurrent
        session tests, use one DummyLM per TrajectoryTracker.
    """

    def __init__(self, responses: list[dict[str, Any] | CacheHit | Exception]) -> None:
        # Build the parent's dict list (used for text formatting).
        # For Exception entries, we supply a placeholder that will never be
        # returned (forward() raises before the parent formatter is called).
        parent_dicts: list[dict[str, Any]] = []
        for item in responses:
            if isinstance(item, dict):
                parent_dicts.append(item)
            elif isinstance(item, CacheHit):
                parent_dicts.append(item.fields)
            else:
                # Exception: the placeholder is never used by the parent
                # formatter because forward() raises before reaching it.
                parent_dicts.append({"answer": "_exception_placeholder_"})
        super().__init__(parent_dicts)
        self._raw_responses: list[dict[str, Any] | CacheHit | Exception] = responses
        self._call_idx: int = 0

    def forward(  # type: ignore[override]
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        if self._call_idx >= len(self._raw_responses):
            # Exhausted — parent returns the last formatted answer; add non-zero usage.
            resp = super().forward(prompt=prompt, messages=messages, **kwargs)
            self._add_usage(resp)
            self._call_idx += 1
            return resp

        item = self._raw_responses[self._call_idx]
        self._call_idx += 1

        # Exception path (CAP-06). Advance the parent formatter's internal pointer
        # over this slot's placeholder first so a later successful call still maps to
        # the correct scripted response (Pitfall WR-02), then raise.
        if isinstance(item, Exception):
            try:
                super().forward(prompt=prompt, messages=messages, **kwargs)
            except Exception:
                pass
            raise item

        # Format the text via the parent (uses the adapter's format_field_with_value
        # so the chat/JSON adapter can parse it back into typed fields).
        resp = super().forward(prompt=prompt, messages=messages, **kwargs)

        if isinstance(item, CacheHit):
            # Mimic Cache._prepare_cached_response: clear usage, set cache_hit.
            resp.usage = {}
            resp.cache_hit = True
        else:
            # Normal response: non-zero token counts.
            self._add_usage(resp)

        return resp

    @staticmethod
    def _add_usage(resp: Any) -> None:
        """Replace zero usage with non-zero counts on the response dotdict."""
        from dspy.dsp.utils.utils import dotdict

        resp.usage = dotdict(prompt_tokens=10, completion_tokens=5, total_tokens=15)


# ---------------------------------------------------------------------------
# Shared tool for ReAct harness
# ---------------------------------------------------------------------------


def dummy_tool(query: str = "") -> str:
    """Deterministic dummy tool — returns a fixed string without network access."""
    return f"result for: {query}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_react_responses(n_iters: int) -> list[dict[str, Any]]:
    """
    Build the scripted dict-response list for a dspy.ReAct with exactly n_iters
    react steps followed by a finish call, then one extract step.

    Total LM calls: n_iters (react) + 1 (extract) = n_iters + 1.
    """
    react_steps: list[dict[str, Any]] = []
    for i in range(n_iters):
        tool = "finish" if i == n_iters - 1 else "dummy_tool"
        args: dict[str, Any] = {} if tool == "finish" else {"query": f"step-{i}"}
        react_steps.append(
            {
                "next_thought": f"Step {i} reasoning",
                "next_tool_name": tool,
                "next_tool_args": args,
            }
        )
    # Extract step: ChainOfThought appends a 'reasoning' field.
    extract_step = {"reasoning": "Reached the final answer", "answer": f"answer-after-{n_iters}-steps"}
    return react_steps + [extract_step]


@pytest.fixture
def dummy_lm() -> DummyLM:
    """
    Fresh DummyLM scripted for 3 react iterations + 1 extract (4 total LM calls).
    Single-use: do not share across test sessions.
    """
    return DummyLM(responses=_make_react_responses(n_iters=3))


@pytest.fixture
def dummy_lm_factory():
    """
    Factory fixture: callable that returns a fresh DummyLM per call.

    Used by CAP-07 isolation test so each TrajectoryTracker session gets its
    own independent DummyLM instance — prevents lm.history interleaving when
    two sessions run sequentially (RESEARCH Open Question 2).

    Usage:
        def test_isolation(dummy_lm_factory):
            lm1 = dummy_lm_factory(n_iters=2)
            lm2 = dummy_lm_factory(n_iters=2)
    """

    def _make(n_iters: int = 2) -> DummyLM:
        return DummyLM(responses=_make_react_responses(n_iters=n_iters))

    return _make


class _CountingCallback(BaseCallback):
    """
    A minimal pre-existing callback for CAP-02 preservation tests.
    Records how many times on_lm_end fires so the test can assert > 0.
    """

    def __init__(self) -> None:
        self.on_lm_end_count: int = 0

    def on_lm_end(
        self,
        call_id: str,
        outputs: Any | None,
        exception: Exception | None = None,
    ) -> None:
        self.on_lm_end_count += 1


@pytest.fixture
def pre_existing_callback() -> _CountingCallback:
    """
    A fresh pre-existing BaseCallback for CAP-02 preservation tests.

    Register via dspy.context(callbacks=[pre_existing_callback]) BEFORE
    entering TrajectoryTracker, then assert .on_lm_end_count > 0 after
    the tracked run — confirming TrajectoryTracker did not clobber it.
    """
    return _CountingCallback()
