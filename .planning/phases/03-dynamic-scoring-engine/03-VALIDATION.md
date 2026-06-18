---
phase: 3
slug: dynamic-scoring-engine
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-18
---

# Phase 3 — Validation Strategy

> FREE phase: detectors run on constructed `SessionState` fixtures + a local fastembed
> embedder (bge-small). No network, no LLM calls.

## Test Infrastructure

| Property | Value |
|----------|-------|
| Framework | pytest 9.x |
| Quick run | `python -m pytest tests/unit/test_scoring.py -q` |
| Full suite | `python -m pytest -q` |
| Runtime | ~2-3s (embedder warm after first load) |

## Per-Requirement Verification Map

| Test (-k) | Req | Asserts |
|-----------|-----|---------|
| `structural` | SCORE-04 | JSON-Schema/XML input → kind=structural_constraint |
| `structural_fires` | SCORE-04 | structural override returns before the embedder loads (monkeypatched to explode) |
| `flapping` | SCORE-03 | same tool ≥3× with unchanged observation → kind=tool_flapping |
| `flapping_progress` | SCORE-03 | same tool 3× but changing observations → no flag |
| `loop` | SCORE-02 | 2 consecutive high-sim outputs + unchanged obs → kind=loop_velocity |
| `loop_false_positive` | SCORE-02/P10 | high-sim outputs but CHANGED observation → no flag |
| `config_threshold` | SCORE-04 | same window, different `loop_similarity_threshold` → different verdict |
| `cap` | SCORE-05 | after `max_escalations_per_session`, no more forced 0.0; each escalation logged w/ detector+score |
| `no_llm_judge` | SCORE-05 | scoring.py references no LM client (static source check) |
| `tool_capture` | D-05 | real ReAct under TrajectoryTracker(config) populates session.tool_events |

## Sign-Off
- [x] All detectors covered by automated tests
- [x] mypy --strict clean (8 files)
- [x] 31 unit tests pass; light import preserved
- [x] `nyquist_compliant: true`

**Approval:** approved 2026-06-18 (inline)
