# agent_router/scoring.py
# Dynamic Scoring Engine (Block 2). Reads SessionState.window + tool_events, flags
# reasoning loops / tool-call flapping / structural-constraint demands, and applies the
# per-session escalation cap. Telemetry/regex/embedding only — NO LM judge (SCORE-05).
#
# Light-import discipline: fastembed is imported LAZILY inside the loop profiler so that
# `import agent_router` (and scoring of structural/flapping cases) never loads onnxruntime.
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_router.config import RouterConfig
    from agent_router.state import SessionState

logger = logging.getLogger("agent_router.scoring")


@dataclass(frozen=True)
class ScoringResult:
    """Outcome of scoring one session window after a step."""

    anomaly: bool
    kind: str | None = None  # structural_constraint | tool_flapping | loop_velocity
    score: float = 0.0
    detector: str | None = None


# --- Structural Constraint Scanner (SCORE-04): regex only, runs first ---------------

# Tunable in one place. Heuristic: matches demands for strict machine-readable formats.
_STRUCTURAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bJSON\s*Schema\b",
        r'"\$schema"',
        r'"type"\s*:\s*"(object|array|string|number|boolean)"',
        r"<\?xml\b",
        r"</[A-Za-z][\w:-]*>",  # closing XML/HTML tag
        r"\bvalid\s+XML\b",
        r"```(json|xml|python|sql|yaml|toml|[a-z+]{2,})\b",  # fenced code w/ a language
        r"\bmust\s+(compile|be valid)\b",
        r"\bexecutable\b.*\b(syntax|code)\b",
    )
)


def detect_structural(input_text: str) -> ScoringResult:
    """Flag strict-format demands in the input prompt. No LM, no embeddings (SCORE-04)."""
    for pat in _STRUCTURAL_PATTERNS:
        if pat.search(input_text):
            return ScoringResult(
                anomaly=True,
                kind="structural_constraint",
                score=1.0,
                detector="StructuralConstraintScanner",
            )
    return ScoringResult(anomaly=False)


# --- Tool-Call Flapping Monitor (SCORE-03) ------------------------------------------


def detect_flapping(session: SessionState, config: RouterConfig) -> ScoringResult:
    """Flag the same tool called >= flapping_min_repeats with unchanged observation."""
    events = [e for e in session.tool_events if e.tool_name != "finish"]
    if len(events) < config.flapping_min_repeats:
        return ScoringResult(anomaly=False)

    # Group consecutive same-tool calls and check observation stagnation.
    counts: dict[str, int] = {}
    obs_by_tool: dict[str, set[str]] = {}
    for e in events:
        counts[e.tool_name] = counts.get(e.tool_name, 0) + 1
        obs_by_tool.setdefault(e.tool_name, set()).add(e.observation or "")

    for tool_name, n in counts.items():
        # >= min_repeats calls AND observations never changed (all identical).
        if n >= config.flapping_min_repeats and len(obs_by_tool[tool_name]) <= 1:
            return ScoringResult(
                anomaly=True,
                kind="tool_flapping",
                score=float(n) / max(len(events), 1),
                detector="ToolCallFlappingMonitor",
            )
    return ScoringResult(anomaly=False)


# --- Loop Velocity Profiler (SCORE-02, P8/P9/P10) -----------------------------------


def _cosine(a: Any, b: Any) -> float:
    import numpy as np

    denom = float(np.linalg.norm(a)) * float(np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


class _Embedder:
    """Lazy fastembed singleton (warmed once). Loaded only when the loop profiler runs."""

    _model: Any = None

    @classmethod
    def encode(cls, text: str) -> Any:
        if cls._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - exercised in minimal envs
                raise ImportError(
                    "LoopVelocityProfiler needs the embedder. "
                    "Install it with: pip install agent-router[embed]"
                ) from exc
            cls._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        return next(iter(cls._model.embed([text])))


class LoopVelocityProfiler:
    """Detects repeating outputs across consecutive turns with no observation change.

    Embeddings are cached by call_id to avoid recomputing. P10 false-positive gate:
    a changed observation means the agent is making progress -> no flag.
    """

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def _emb(self, call_id: str, text: str) -> Any:
        if call_id not in self._cache:
            self._cache[call_id] = _Embedder.encode(text)
        return self._cache[call_id]

    def detect(self, session: SessionState, config: RouterConfig) -> ScoringResult:
        # Alignment caveat (live path): a step's TurnRecord is appended in on_lm_end,
        # but its tool observation arrives slightly later (on_tool_end), so when scoring
        # runs the newest observation can lag the newest output by one step. The
        # observation-change gate therefore compares the two most-recent AVAILABLE
        # observations as a proxy. On aligned fixtures (the unit contract) this is exact;
        # the live one-step skew is a Phase-5 calibration item, not a correctness bug.
        window = list(session.window)
        if len(window) < 2:
            return ScoringResult(anomaly=False)

        prev, curr = window[-2], window[-1]
        if not prev.output_text or not curr.output_text:
            return ScoringResult(anomaly=False)

        sim = _cosine(
            self._emb(prev.call_id, prev.output_text),
            self._emb(curr.call_id, curr.output_text),
        )
        if sim < config.loop_similarity_threshold:
            return ScoringResult(anomaly=False, score=sim)

        # Output is highly similar — only a loop if the observation did NOT change (P10).
        if _observation_changed(session, len(window)):
            return ScoringResult(anomaly=False, score=sim)

        return ScoringResult(
            anomaly=True,
            kind="loop_velocity",
            score=sim,
            detector="LoopVelocityProfiler",
        )


def _observation_changed(session: SessionState, n_steps: int) -> bool:
    """True if the last two steps' observations differ (agent made progress)."""
    events = session.tool_events
    if len(events) < 2:
        return False  # no observation evidence -> treat as unchanged (conservative)
    return (events[-1].observation or "") != (events[-2].observation or "")


# --- Engine -------------------------------------------------------------------------


class ScoringEngine:
    """Runs detectors in priority order and applies the escalation cap.

    Order (SCORE-04): structural override (regex) -> flapping (counters) ->
    loop velocity (embeddings, most expensive).
    """

    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self._loop = LoopVelocityProfiler()

    def score(self, session: SessionState, input_text: str = "") -> ScoringResult:
        structural = detect_structural(input_text)
        if structural.anomaly:
            return structural
        flapping = detect_flapping(session, self.config)
        if flapping.anomaly:
            return flapping
        return self._loop.detect(session, self.config)

    def score_and_apply(self, session: SessionState, input_text: str = "") -> ScoringResult:
        """Score, then apply the escalation decision + cap under the session lock (SCORE-05)."""
        result = self.score(session, input_text)
        if not result.anomaly:
            return result
        with session._lock:
            if session.escalation_count < self.config.max_escalations_per_session:
                session.current_threshold = 0.0
                session.escalation_count += 1
                logger.info(
                    "escalation session=%s detector=%s kind=%s score=%.4f count=%d",
                    session.session_id,
                    result.detector,
                    result.kind,
                    result.score,
                    session.escalation_count,
                )
            else:
                logger.warning(
                    "escalation_cap_reached session=%s detector=%s kind=%s score=%.4f "
                    "(not forcing threshold=0.0)",
                    session.session_id,
                    result.detector,
                    result.kind,
                    result.score,
                )
        return result
