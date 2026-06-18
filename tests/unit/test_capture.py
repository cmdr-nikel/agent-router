# tests/unit/test_capture.py
# Phase 2 Wave 0 — RED test stubs for CAP-01..CAP-07.
#
# These 7 tests are the executable acceptance contract for Phase 2.
# They MUST:
#   - Collect cleanly (0 errors on `pytest --collect-only`)
#   - Fail (RED) because TrajectoryTracker is unimplemented — not because of
#     ImportError / NameError / collection errors
#   - Be selectable by the -k keywords documented in 02-VALIDATION.md:
#       wrap | preserve_callbacks | signature_identity | overcount |
#       tokens | exception | isolation
#
# They will turn GREEN when Plans 02-02 (capture.py) and 02-03 (tracker wiring)
# land and implement TrajectoryCallback + the full TrajectoryTracker.__enter__/__exit__.
from __future__ import annotations

import pytest

import dspy

from agent_router.state import _SESSION_REGISTRY
from agent_router.tracker import TrajectoryTracker

# conftest.py provides: DummyLM, CacheHit, dummy_tool, dummy_lm, dummy_lm_factory,
# pre_existing_callback.  We import dummy_tool and CacheHit directly here for
# inline construction; fixtures are declared as test parameters.
from tests.conftest import CacheHit, DummyLM, dummy_tool


# ---------------------------------------------------------------------------
# CAP-01 — test_wrap
#
# Requirement: `with TrajectoryTracker(session_id=...):` wraps a real dspy.ReAct
# and the agent runs UNCHANGED inside the context, returning its normal prediction.
# Tracker must create a SessionState in _SESSION_REGISTRY under __enter__ and
# remove it under __exit__.
#
# RED reason with stub: __enter__ returns self without creating a SessionState;
# the assertions on _SESSION_REGISTRY fail.
# ---------------------------------------------------------------------------


def test_wrap(dummy_lm: DummyLM) -> None:
    """CAP-01: with TrajectoryTracker wraps agent without changing its result."""
    dspy.configure(lm=dummy_lm)
    react = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=10)

    session_id = "cap01-wrap"
    prediction = None

    with TrajectoryTracker(session_id=session_id) as tracker:
        # Inside __enter__, the session MUST be registered in _SESSION_REGISTRY.
        # RED: stub __enter__ returns self without creating any session.
        assert session_id in _SESSION_REGISTRY, (
            f"TrajectoryTracker.__enter__ must create a SessionState in _SESSION_REGISTRY "
            f"under '{session_id}'. Currently the stub __enter__ does nothing."
        )
        prediction = react(question="What is 2+2?")

    # Agent must complete and return a non-None prediction.
    assert prediction is not None, "ReAct inside TrajectoryTracker returned None"
    assert hasattr(prediction, "answer"), "Prediction missing 'answer' field"

    # After __exit__, the session must be REMOVED (cleanup prevents unbounded growth).
    assert session_id not in _SESSION_REGISTRY, (
        f"Session '{session_id}' was not cleaned up from _SESSION_REGISTRY after __exit__. "
        "TrajectoryTracker.__exit__ must call _SESSION_REGISTRY.pop(session_id, None)."
    )

    # The tracker must have captured at least one TurnRecord during the run.
    # Access via tracker._session (held even after __exit__ cleanup).
    # RED: stub tracker has no _session attribute → AttributeError.
    assert hasattr(tracker, "_session"), (
        "TrajectoryTracker must expose _session after __enter__ so tests can inspect records."
    )
    records = list(tracker._session.window)
    assert records, (
        "TrajectoryTracker captured zero TurnRecords. "
        "TrajectoryCallback.on_lm_end must append a TurnRecord per LM call."
    )


# ---------------------------------------------------------------------------
# CAP-02 — test_preserve_callbacks
#
# Requirement: TrajectoryTracker registers its callback via
# dspy.context(callbacks=existing + [tracker_cb]), NOT dspy.configure(), so
# pre-existing user callbacks (Langfuse, W&B, etc.) remain active during the run.
#
# RED reason with stub: the stub __enter__ does not register any callback, so
# no TurnRecords are captured.  We assert both that the pre-existing callback
# still fired AND that the tracker captured records — the latter fails.
# ---------------------------------------------------------------------------


def test_preserve_callbacks(dummy_lm: DummyLM, pre_existing_callback) -> None:
    """CAP-02: TrajectoryTracker does not clobber pre-existing dspy callbacks."""
    dspy.configure(lm=dummy_lm)
    react = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=10)

    session_id = "cap02-preserve"

    # Register the pre-existing callback BEFORE entering the tracker.
    # The implemented tracker must READ existing callbacks and APPEND to them via
    # dspy.context(callbacks=existing + [tracker_cb]), NOT dspy.configure() which
    # would replace all existing callbacks (D-01, Pitfall P4).
    existing_before = dspy.settings.get("callbacks", [])
    with dspy.context(callbacks=existing_before + [pre_existing_callback]):
        with TrajectoryTracker(session_id=session_id) as tracker:
            # RED: stub __enter__ never creates a session.
            assert session_id in _SESSION_REGISTRY, (
                f"TrajectoryTracker.__enter__ must create a SessionState in _SESSION_REGISTRY "
                f"under '{session_id}'."
            )
            react(question="Does my callback survive?")

    # Pre-existing callback must have fired at least once per LM call.
    # If the tracker used dspy.configure() (wrong), it would REPLACE pre_existing_callback
    # and this count would be 0.  If it used dspy.context() (correct), the count is > 0.
    assert pre_existing_callback.on_lm_end_count > 0, (
        "Pre-existing callback's on_lm_end was never fired. "
        "TrajectoryTracker must preserve existing callbacks via dspy.context(callbacks=existing + [cb])."
    )

    # After __exit__, session is cleaned up; tracker must have captured records.
    records = list(tracker._session.window)  # AttributeError with stub → RED
    assert records, (
        "TrajectoryTracker captured zero TurnRecords even though pre_existing_callback fired. "
        "TrajectoryCallback.on_lm_end must append a TurnRecord per LM call."
    )


# ---------------------------------------------------------------------------
# CAP-03 — test_signature_identity
#
# Requirement: No TurnRecord has signature_name == "StringSignature".
# Two different inline string signatures produce DISTINCT signature_name values
# via the derived key: f"StringSignature:{','.join(sorted(in_keys))}>{','.join(sorted(out_keys))}"
# (D-04, Pitfall P2).
#
# RED reason: stub captures no TurnRecords; session never created; first assert fails.
# ---------------------------------------------------------------------------


def test_signature_identity() -> None:
    """CAP-03: signature_name never 'StringSignature'; inline sigs produce distinct names."""
    # Two distinct inline signatures
    sig_a = "city -> weather"        # in: [city] out: [weather]
    sig_b = "city, date -> forecast"  # in: [city, date] out: [forecast]

    lm_a = DummyLM(responses=[
        {"next_thought": "checking", "next_tool_name": "finish", "next_tool_args": {}},
        {"reasoning": "sunny", "weather": "sunny"},
    ])
    lm_b = DummyLM(responses=[
        {"next_thought": "checking", "next_tool_name": "finish", "next_tool_args": {}},
        {"reasoning": "cloudy", "forecast": "cloudy"},
    ])

    session_id_a = "cap03-sig-a"
    session_id_b = "cap03-sig-b"

    dspy.configure(lm=lm_a)
    react_a = dspy.ReAct(sig_a, tools=[dummy_tool], max_iters=5)
    with TrajectoryTracker(session_id=session_id_a) as tracker_a:
        # RED: stub does not create session; assert below will fail.
        assert session_id_a in _SESSION_REGISTRY, (
            f"TrajectoryTracker.__enter__ must register session '{session_id_a}' "
            "in _SESSION_REGISTRY (stub __enter__ does not)."
        )
        react_a(question="weather in Paris?")

    dspy.configure(lm=lm_b)
    react_b = dspy.ReAct(sig_b, tools=[dummy_tool], max_iters=5)
    with TrajectoryTracker(session_id=session_id_b) as tracker_b:
        assert session_id_b in _SESSION_REGISTRY, (
            f"TrajectoryTracker.__enter__ must register session '{session_id_b}'."
        )
        react_b(question="forecast for Paris on Monday?")

    # After exit, retrieve captured records from the tracker's internal session.
    # (Implemented tracker exposes _session even after __exit__.)
    records_a = list(tracker_a._session.window)  # AttributeError with stub → RED
    records_b = list(tracker_b._session.window)  # AttributeError with stub → RED

    # No record may use the raw "StringSignature" class name.
    for r in records_a + records_b:
        assert r.signature_name != "StringSignature", (
            f"TurnRecord.signature_name must not be 'StringSignature'; got: {r.signature_name!r}. "
            "Inline sigs must use the derived key (D-04)."
        )

    # The two signatures must produce DISTINCT signature_name values.
    names_a = {r.signature_name for r in records_a}
    names_b = {r.signature_name for r in records_b}
    assert names_a != names_b, (
        f"Inline sigs '{sig_a}' and '{sig_b}' produced the same signature_name. "
        f"names_a={names_a}, names_b={names_b}"
    )


# ---------------------------------------------------------------------------
# CAP-04 — test_overcount
#
# Requirement: a 5-iteration dspy.ReAct produces EXACTLY 5 TurnRecords with
# step_idx == [0, 1, 2, 3, 4].  The trailing extract LM call (on_lm_end fires
# N+1 times total) must NOT generate a 6th record.
# (D-02, Pitfall P7, RESEARCH §Pattern 2)
#
# RED reason: stub captures 0 TurnRecords; assertion on window length fails.
# ---------------------------------------------------------------------------


def test_overcount() -> None:
    """CAP-04: 5-iter ReAct yields exactly 5 TurnRecords, step_idx [0..4]."""
    n_iters = 5
    lm = DummyLM(responses=[
        {"next_thought": f"step {i}", "next_tool_name": "dummy_tool" if i < n_iters - 1 else "finish",
         "next_tool_args": {} if i == n_iters - 1 else {"query": f"step-{i}"}}
        for i in range(n_iters)
    ] + [{"reasoning": "done", "answer": f"answer-{n_iters}"}])

    dspy.configure(lm=lm)
    react = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=n_iters + 2)

    session_id = "cap04-overcount"
    with TrajectoryTracker(session_id=session_id) as tracker:
        # RED: stub does not create session in __enter__.
        assert session_id in _SESSION_REGISTRY, (
            "TrajectoryTracker.__enter__ must register the session before the agent runs."
        )
        react(question="Count to 5")

    # After exit, the tracker._session must have the captured window.
    records = list(tracker._session.window)  # AttributeError with stub → RED

    # Exactly N records: no overcount (extract excluded), no undercount.
    assert len(records) == n_iters, (
        f"Expected exactly {n_iters} TurnRecords for a {n_iters}-iteration ReAct, "
        f"but got {len(records)}. "
        "The trailing extract LM call must be excluded via the _in_extract sentinel."
    )

    # step_idx must be [0, 1, 2, ..., n_iters-1] in order.
    actual_indices = [r.step_idx for r in records]
    expected_indices = list(range(n_iters))
    assert actual_indices == expected_indices, (
        f"step_idx values are {actual_indices}, expected {expected_indices}."
    )


# ---------------------------------------------------------------------------
# CAP-05 — test_tokens
#
# Requirement: every TurnRecord has non-zero input_token_count and
# output_token_count (from lm.history[-1]["usage"]); a scripted cache-hit step
# is recorded with cache_hit is True and distinct (zeroed) token counts.
# (D-03, RESEARCH §Pattern 3)
#
# RED reason: stub captures 0 TurnRecords; first assertion on records fails.
# ---------------------------------------------------------------------------


def test_tokens() -> None:
    """CAP-05: TurnRecords have non-zero token counts; cache hits flagged."""
    # 2 normal react steps + 1 cache-hit finish + extract
    lm = DummyLM(responses=[
        {"next_thought": "searching", "next_tool_name": "dummy_tool", "next_tool_args": {"query": "x"}},
        CacheHit({"next_thought": "cached finish", "next_tool_name": "finish", "next_tool_args": {}}),
        {"reasoning": "done", "answer": "42"},
    ])

    dspy.configure(lm=lm)
    react = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=10)

    session_id = "cap05-tokens"
    with TrajectoryTracker(session_id=session_id) as tracker:
        assert session_id in _SESSION_REGISTRY, (
            "TrajectoryTracker.__enter__ must create the session before agent runs."
        )
        react(question="Token counting test")

    records = list(tracker._session.window)  # AttributeError with stub → RED

    # There must be at least one TurnRecord.
    assert records, "No TurnRecords captured — tracker unimplemented."

    normal_records = [r for r in records if not r.cache_hit]
    cache_records = [r for r in records if r.cache_hit]

    # Every normal (non-cache-hit) record must have non-zero token counts.
    for r in normal_records:
        assert r.input_token_count > 0, (
            f"TurnRecord step {r.step_idx} has zero input_token_count. "
            "Read from lm.history[-1]['usage']['prompt_tokens'] in on_lm_end."
        )
        assert r.output_token_count > 0, (
            f"TurnRecord step {r.step_idx} has zero output_token_count. "
            "Read from lm.history[-1]['usage']['completion_tokens'] in on_lm_end."
        )

    # There must be at least one cache-hit record.
    assert cache_records, (
        "No cache-hit TurnRecord captured. "
        "The scripted CacheHit response must set record.cache_hit = True."
    )

    # Cache-hit records must have cache_hit = True (not silently zeroed out).
    for r in cache_records:
        assert r.cache_hit is True, f"TurnRecord step {r.step_idx}: cache_hit is not True."


# ---------------------------------------------------------------------------
# CAP-06 — test_exception
#
# Requirement: when DummyLM.forward raises, a TurnRecord is still appended with
# record.exception populated (not None), and the outputs=None path does not crash
# the callback.  The failed step must appear in the window.
# (D-06, RESEARCH §Pattern 6)
#
# RED reason: stub captures 0 TurnRecords; assertion on exception records fails.
# ---------------------------------------------------------------------------


def test_exception() -> None:
    """CAP-06: failed LM call produces a TurnRecord with exception field set."""
    # First react step succeeds; second LM call (extract) raises.
    # The callback must fire for both, capturing exception=RuntimeError on the 2nd.
    lm = DummyLM(responses=[
        {"next_thought": "done", "next_tool_name": "finish", "next_tool_args": {}},
        RuntimeError("Simulated LM failure for CAP-06"),
    ])

    dspy.configure(lm=lm)
    react = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=5)

    session_id = "cap06-exception"
    with TrajectoryTracker(session_id=session_id) as tracker:
        assert session_id in _SESSION_REGISTRY, (
            "TrajectoryTracker.__enter__ must create the session."
        )
        # The RuntimeError propagates from the extract step; wrap it so the test
        # body can still assert on the captured window.
        with pytest.raises(Exception):
            react(question="Will this crash?")

    records = list(tracker._session.window)  # AttributeError with stub → RED

    # At least one record must have exception != None.
    exception_records = [r for r in records if r.exception is not None]
    assert exception_records, (
        "No TurnRecord with exception captured. "
        "on_lm_end(exception=...) must produce a TurnRecord even when outputs is None."
    )

    # The exception record must carry the actual exception (not a string or None).
    for r in exception_records:
        assert isinstance(r.exception, Exception), (
            f"TurnRecord.exception must be an Exception instance, got {type(r.exception)}."
        )

    # The exception record must have outputs = None (i.e. output_text is None).
    for r in exception_records:
        assert r.output_text is None, (
            f"TurnRecord for failed step must have output_text=None (outputs=None path), "
            f"got {r.output_text!r}."
        )


# ---------------------------------------------------------------------------
# CAP-07 — test_isolation
#
# Requirement: two TrajectoryTracker instances with DIFFERENT session_ids (each
# with its own DummyLM) capture only their own TurnRecords — no step-count or
# window bleed. _SESSION_REGISTRY is empty after both context managers exit.
# (D-05, RESEARCH §Pattern 5, Open Question 2)
#
# RED reason: stub never creates sessions; tracker._session AttributeError → RED.
# ---------------------------------------------------------------------------


def test_isolation(dummy_lm_factory) -> None:
    """CAP-07: two concurrent/sequential sessions have isolated windows; registry empty after."""
    n_iters = 2  # 2 react steps each

    lm1 = dummy_lm_factory(n_iters=n_iters)
    lm2 = dummy_lm_factory(n_iters=n_iters)

    # Session 1
    session_id_1 = "cap07-session-1"
    dspy.configure(lm=lm1)
    react1 = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=10)
    with TrajectoryTracker(session_id=session_id_1) as tracker1:
        assert session_id_1 in _SESSION_REGISTRY, (
            "TrajectoryTracker.__enter__ must register session_id in _SESSION_REGISTRY."
        )
        react1(question="Session 1 question")
    # After exit, session_id_1 must be GONE from registry (cleanup in __exit__).
    assert session_id_1 not in _SESSION_REGISTRY, (
        "TrajectoryTracker.__exit__ must remove session from _SESSION_REGISTRY."
    )

    # Session 2
    session_id_2 = "cap07-session-2"
    dspy.configure(lm=lm2)
    react2 = dspy.ReAct("question -> answer", tools=[dummy_tool], max_iters=10)
    with TrajectoryTracker(session_id=session_id_2) as tracker2:
        assert session_id_2 in _SESSION_REGISTRY, (
            "TrajectoryTracker.__enter__ must register session_id_2 in _SESSION_REGISTRY."
        )
        react2(question="Session 2 question")
    assert session_id_2 not in _SESSION_REGISTRY, (
        "TrajectoryTracker.__exit__ must remove session_id_2 from _SESSION_REGISTRY."
    )

    # Both sessions' windows must be accessible via their tracker._session refs.
    window1 = list(tracker1._session.window)  # AttributeError with stub → RED
    window2 = list(tracker2._session.window)  # AttributeError with stub → RED

    # Each session must have exactly n_iters records (no overcount, no bleed).
    assert len(window1) == n_iters, (
        f"Session 1: expected {n_iters} TurnRecords, got {len(window1)}."
    )
    assert len(window2) == n_iters, (
        f"Session 2: expected {n_iters} TurnRecords, got {len(window2)}."
    )

    # step_idx in each window must be [0..n_iters-1] independently.
    assert [r.step_idx for r in window1] == list(range(n_iters)), (
        f"Session 1 step_idx values don't match expected: {[r.step_idx for r in window1]}"
    )
    assert [r.step_idx for r in window2] == list(range(n_iters)), (
        f"Session 2 step_idx values don't match expected: {[r.step_idx for r in window2]}"
    )

    # call_ids must not overlap between sessions (no bleed).
    call_ids_1 = {r.call_id for r in window1}
    call_ids_2 = {r.call_id for r in window2}
    overlap = call_ids_1 & call_ids_2
    assert not overlap, (
        f"Sessions share call_ids — window bleed detected: {overlap}"
    )

    # Registry must be empty after both contexts exit.
    assert _SESSION_REGISTRY == {}, (
        f"_SESSION_REGISTRY not empty after all TrackerContexts exited: "
        f"{list(_SESSION_REGISTRY.keys())}"
    )
