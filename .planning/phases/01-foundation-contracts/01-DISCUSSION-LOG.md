# Phase 1: Foundation & Contracts - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-18
**Phase:** 1-Foundation & Contracts
**Areas discussed:** Dependency layout, Python floor, Contract mutability, Configuration source, Operating mode

---

## Dependency layout (embedder)

| Option | Description | Selected |
|--------|-------------|----------|
| Optional extra + lazy import | `agent-router[embed]`, lazy fastembed import with clear error | ✓ |
| Hard core dependency | fastembed always installed | |

**User's choice:** Recommended (optional extra). User delegated to Claude's recommended option.
**Notes:** Enables future zero-dep fallback (PERF-02); satisfies "import without heavy deps" criterion.

---

## Python floor

| Option | Description | Selected |
|--------|-------------|----------|
| 3.10+ | Broad support for a production library | ✓ |
| 3.14-only | Newest syntax, matches dev box only | |

**User's choice:** Recommended (3.10+).
**Notes:** 3.14-only would exclude most users; still test on 3.14 (dev box).

---

## Contract mutability

| Option | Description | Selected |
|--------|-------------|----------|
| frozen TurnRecord + mutable SessionState | Append-only telemetry, in-place window | ✓ |
| All mutable | Simpler but less safe under concurrency | |

**User's choice:** Recommended (frozen records, mutable state).
**Notes:** Safer for concurrent sessions (CAP-07).

---

## Configuration source

| Option | Description | Selected |
|--------|-------------|----------|
| RouterConfig + env vars | pydantic config + env for models/keys (12-factor) | ✓ |
| Programmatic only | Config only via code | |

**User's choice:** Recommended (RouterConfig + env).
**Notes:** Library configured without code edits; secrets out of source.

---

## Operating mode (cross-phase)

| Option | Description | Selected |
|--------|-------------|----------|
| Phases 1-3 auto, hard stop before 4 | Autonomous on free phases, gate before paid frontier calls | ✓ |
| Pause after every phase | Manual go after each, even free phases | |
| Full autonomous (no stop) | Run 1-5 with no gate | |

**User's choice:** Phases 1-3 autonomous, hard stop before Phase 4.
**Notes:** User delegated full GSD trajectory (discuss→research→plan→check→execute→verify), choosing
recommended options, reviewing reports at phase boundaries. Money gate is non-negotiable: no paid
frontier calls (Phases 4-5) without explicit go + budget.

## Claude's Discretion

- Module split inside `agent_router/`, pydantic v2 specifics, exact lazy-import shim — left to
  planner/executor provided the public API surface and locked contract fields hold.

## Deferred Ideas

- Zero-dependency hash-fingerprint loop-detection fallback → v2 (PERF-02).
- Hard budget cap / auto-stop → v2 (COST-01).
