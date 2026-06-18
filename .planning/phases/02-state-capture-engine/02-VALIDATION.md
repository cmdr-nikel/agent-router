---
phase: 2
slug: state-capture-engine
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-18
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x (+ pytest-asyncio 1.4, pytest-mock 3.15) — already installed |
| **Config file** | `pyproject.toml` [tool.pytest.ini_options] (from Phase 1) |
| **Quick run command** | `python -m pytest tests/unit -q` |
| **Full suite command** | `python -m pytest -q` |
| **Estimated runtime** | ~6 seconds (mock/DummyLM, no network) |

---

## Sampling Rate

- **After every task commit:** `python -m pytest tests/unit -q`
- **After every plan wave:** `python -m pytest -q`
- **Before verify:** full suite green + `mypy --strict agent_router/` clean + light-import preserved
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Req | Test Type | Automated Command | Secure Behavior | Status |
|---------|-----|-----------|-------------------|-----------------|--------|
| cap-01 | CAP-01 | unit | `pytest tests/unit/test_capture.py -k wrap -q` (ReAct runs unchanged inside `with TrajectoryTracker`) | N/A | ⬜ |
| cap-02 | CAP-02 | unit | `pytest tests/unit/test_capture.py -k preserve_callbacks -q` (pre-existing callback still fires) | N/A | ⬜ |
| cap-03 | CAP-03 | unit | `pytest tests/unit/test_capture.py -k signature_identity -q` (no `StringSignature`; inline sigs distinguished) | N/A | ⬜ |
| cap-04 | CAP-04 | unit | `pytest tests/unit/test_capture.py -k overcount -q` (5-iter ReAct → exactly 5 TurnRecords) | N/A | ⬜ |
| cap-05 | CAP-05 | unit | `pytest tests/unit/test_capture.py -k tokens -q` (non-zero in/out tokens; cache_hit flag distinct) | N/A | ⬜ |
| cap-06 | CAP-06 | unit | `pytest tests/unit/test_capture.py -k exception -q` (failed step → TurnRecord w/ exception, outputs=None handled) | N/A | ⬜ |
| cap-07 | CAP-07 | unit | `pytest tests/unit/test_capture.py -k isolation -q` (2 concurrent sessions, no bleed) | N/A | ⬜ |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_capture.py` — 7 test stubs mapping 1:1 to CAP-01..CAP-07
- [ ] A `DummyLM` test double (or dspy's built-in dummy) that drives a real `dspy.ReAct` for a fixed number of iterations without network — separate instance per session for isolation tests
- [ ] `tests/conftest.py` fixtures for a ReAct-with-tools harness if needed

*pytest already installed (Phase 1); no new framework.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| — | — | All Phase 2 behaviors are automatable with a mock LM | — |

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers the test double + stubs
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
