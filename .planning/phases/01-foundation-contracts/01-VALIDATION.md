---
phase: 1
slug: foundation-contracts
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-18
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x (+ pytest-asyncio 1.4, pytest-mock 3.15) |
| **Config file** | `pyproject.toml` [tool.pytest.ini_options] — Wave 0 creates |
| **Quick run command** | `python -m pytest tests/unit -q` |
| **Full suite command** | `python -m pytest -q` |
| **Estimated runtime** | ~5 seconds (Phase 1 is contracts only, no network) |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/unit -q`
- **After every plan wave:** Run `python -m pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green + `pip install -e .` succeeds + `mypy --strict agent_router/state.py agent_router/config.py` clean
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 1-pkg | 01 | 0 | LIB-01 | — | N/A | install | `pip install -e .` exits 0 | ❌ W0 | ⬜ pending |
| 1-imp | 01 | 1 | LIB-01 | — | Public import pulls no heavy deps | unit | `python -c "import sys; from agent_router import TrajectoryTracker, DynamicRouteLM, RouterConfig; assert 'fastembed' not in sys.modules and 'routellm' not in sys.modules"` | ❌ W0 | ⬜ pending |
| 1-ctr | 01 | 1 | LIB-01 | — | Contracts typed | unit+type | `mypy --strict agent_router/state.py agent_router/config.py` exits 0 | ❌ W0 | ⬜ pending |
| 1-cfg | 01 | 1 | LIB-02 | — | Config fields + env override | unit | `python -m pytest tests/unit/test_config.py -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Install dev/build deps not yet present: `hatchling`, `hatch`, `pydantic-settings`, `mypy`, `pytest`, `pytest-asyncio`, `pytest-mock` (fastembed 0.8.0 + routellm 0.2.0 already installed)
- [ ] `tests/conftest.py` — shared fixtures
- [ ] `tests/unit/test_config.py` — stubs for LIB-02 (RouterConfig fields + env override)
- [ ] `tests/unit/test_imports.py` — stub for LIB-01 (lazy public import)
- [ ] `tests/unit/test_contracts.py` — stub for SessionState/TurnRecord/CostRecord shape

*pytest is the framework; Wave 0 installs it and writes config + stubs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| — | — | All Phase 1 behaviors have automated verification | — |

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
