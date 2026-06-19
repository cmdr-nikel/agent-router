# agent_router/scoring.py
# Dynamic Scoring Engine (Block 2). Reads SessionState.window + tool_events, flags
# trajectory pathologies, and applies routing decisions (escalate / de-escalate).
#
# Detectors in priority order (cheap → expensive):
#   1. StructuralConstraintScanner  — regex on input prompt (free)
#   2. ExceptionRateDetector        — exception count in recent window (free)
#   3. HedgingDensityDetector       — regex on latest output (free)
#   4. StepOverrunDetector          — step count vs estimated complexity (free)
#   5. TokenBurnAccelerationDetector — token-per-step trend (free)
#   6. ToolCallFlappingMonitor      — same-tool / same-observation counter (free)
#   7. SemanticVelocityDetector     — avg cosine distance across window (embeddings)
#   8. LoopVelocityProfiler         — consecutive-pair cosine (embeddings, kept for compat)
#
# Routing actions (stored on ScoringResult):
#   anomaly=True                 → escalate the next single call (threshold → 0.0)
#   anomaly=True, escalate_session=True → escalate ALL remaining calls in session
#   anomaly=False, de_escalate=True     → trajectory recovered; reset threshold to default
#
# Light-import discipline: fastembed is imported LAZILY inside embedding-based detectors
# so that `import agent_router` never loads onnxruntime.
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
    kind: str | None = None       # detector-specific label
    score: float = 0.0            # numeric magnitude (interpretation depends on kind)
    detector: str | None = None   # class/function name that produced the result
    # When True the scoring engine will set session.escalate_session so ALL remaining
    # calls in the session route through the strong model (not just the next one).
    escalate_session: bool = False
    # When True (anomaly must be False) the trajectory has recovered after an escalation;
    # score_and_apply resets current_threshold to default_threshold.
    de_escalate: bool = False


# ---------------------------------------------------------------------------
# Shared embedding infrastructure
# ---------------------------------------------------------------------------


def _get_embedding(call_id: str, text: str, cache: dict[str, Any]) -> Any:
    """Look up or compute the embedding for *text*, storing it in the given instance cache.

    The cache is keyed by call_id and is owned by the ScoringEngine instance so it is
    isolated per session — no cross-session collisions (H1 fix).
    """
    if call_id not in cache:
        cache[call_id] = _Embedder.encode(text)
    return cache[call_id]


def _cosine(a: Any, b: Any) -> float:
    import numpy as np

    denom = float(np.linalg.norm(a)) * float(np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


class _Embedder:
    """Lazy fastembed singleton (warmed once). Loaded only when an embedding detector runs."""

    _model: Any = None

    @classmethod
    def encode(cls, text: str) -> Any:
        if cls._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "Embedding-based detectors need fastembed. "
                    "Install it with: pip install agent-router[embed]"
                ) from exc
            cls._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        return next(iter(cls._model.embed([text])))


def _observation_changed(session: SessionState, n_steps: int) -> bool:
    """True if the last two available tool observations differ (agent made progress)."""
    events = session.tool_events
    if len(events) < 2:
        return False
    return (events[-1].observation or "") != (events[-2].observation or "")


# ---------------------------------------------------------------------------
# 1. Structural Constraint Scanner (SCORE-04) — regex, runs first
# ---------------------------------------------------------------------------

_STRUCTURAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bJSON\s*Schema\b",
        r'"\$schema"',
        r'"type"\s*:\s*"(object|array|string|number|boolean)"',
        r"<\?xml\b",
        r"</[A-Za-z][\w:-]*>",
        r"\bvalid\s+XML\b",
        r"```(json|xml|python|sql|yaml|toml|[a-z+]{2,})\b",
        r"\bmust\s+(compile|be valid)\b",
        r"\bexecutable\b.*\b(syntax|code)\b",
    )
)


def detect_structural(input_text: str) -> ScoringResult:
    """Flag strict-format demands in the input prompt (no LM, no embeddings)."""
    for pat in _STRUCTURAL_PATTERNS:
        if pat.search(input_text):
            return ScoringResult(
                anomaly=True,
                kind="structural_constraint",
                score=1.0,
                detector="StructuralConstraintScanner",
            )
    return ScoringResult(anomaly=False)


# ---------------------------------------------------------------------------
# 2. Exception Rate Detector — fraction of recent steps that raised
# ---------------------------------------------------------------------------


def detect_exception_rate(session: SessionState, config: RouterConfig) -> ScoringResult:
    """Flag sessions where too many recent LM calls are failing."""
    window = list(session.window)
    if len(window) < 2:
        return ScoringResult(anomaly=False)
    recent = window[-config.exception_rate_window :]
    failed = sum(1 for r in recent if r.exception is not None)
    rate = failed / len(recent)
    if rate >= config.exception_rate_threshold:
        return ScoringResult(
            anomaly=True,
            kind="exception_rate",
            score=rate,
            detector="ExceptionRateDetector",
        )
    return ScoringResult(anomaly=False, score=rate)


# ---------------------------------------------------------------------------
# 3. Hedging Density Detector — uncertainty markers in the latest output
# ---------------------------------------------------------------------------

# L1 fix: patterns are tightened to reduce false positives in normal CoT text.
# Removed over-broad phrases ("I think", "possibly", "it seems") that commonly appear
# in non-hedging reasoning contexts.  Remaining patterns require explicit inability or
# uncertainty markers; "I cannot determine" was merged into "I cannot" to eliminate the
# overlap that caused double-counting.
#
# Detection counts distinct non-overlapping match SPANS across all patterns rather than
# the number of pattern hits (which double-counted when patterns overlap on the same text).
_HEDGING_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bI(?:'m| am) not sure\b",
        r"\bI don'?t know\b",
        r"\bI(?:'m| am) unable\b",
        r"\bunable to\b",
        r"\bI cannot\b",        # covers "I cannot determine", "I cannot answer", etc.
        r"\bI(?:'m| am) not able\b",
        r"\bI(?:'m| am) unsure\b",
        r"\bI(?:'m| am) having trouble\b",
        r"\bI(?:'m| am) confused\b",
    )
)


def _count_distinct_hedging_spans(text: str) -> int:
    """Count distinct non-overlapping match spans across all _HEDGING_PATTERNS.

    Collecting all spans first and then de-overlapping prevents one multi-word phrase
    from incrementing the count more than once (L1 fix: no double-counting).
    """
    spans: list[tuple[int, int]] = []
    for pat in _HEDGING_PATTERNS:
        for m in pat.finditer(text):
            spans.append((m.start(), m.end()))
    # Sort by start; greedily collect non-overlapping spans.
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            # Overlaps with the previous span — extend it but do not add a new count.
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return len(merged)


def detect_hedging(session: SessionState, config: RouterConfig) -> ScoringResult:
    """Flag outputs where the model is expressing significant uncertainty."""
    window = list(session.window)
    if not window:
        return ScoringResult(anomaly=False)
    latest = window[-1]
    if not latest.output_text:
        return ScoringResult(anomaly=False)
    matches = _count_distinct_hedging_spans(latest.output_text)
    if matches >= config.hedging_min_matches:
        return ScoringResult(
            anomaly=True,
            kind="hedging_density",
            score=float(matches),
            detector="HedgingDensityDetector",
        )
    return ScoringResult(anomaly=False, score=float(matches))


# ---------------------------------------------------------------------------
# 4. Step Overrun Detector — actual steps >> expected from input complexity
# ---------------------------------------------------------------------------


def _estimate_complexity(input_text: str) -> int:
    """Rough upper-bound on expected steps from surface features of the input.

    Counts sentences, bullet points, and question marks as proxies for
    sub-tasks.  Clamped to [1, 15] so edge cases don't produce absurd numbers.
    """
    if not input_text.strip():
        return 1
    sentences = len(re.findall(r"[.!?]+", input_text))
    bullets = len(re.findall(r"(?m)^[\s]*[-*•]\s", input_text))
    questions = input_text.count("?")
    raw = max(1, sentences + bullets + questions)
    return min(raw, 15)


def detect_step_overrun(
    session: SessionState, config: RouterConfig, input_text: str
) -> ScoringResult:
    """Flag when the agent is taking far more steps than the task suggests it needs.

    M1 fixes:
    - Non-sticky: does not set escalate_session=True so de-escalation can still fire.
    - Counts steps-since-last-progress (last observation change) rather than the
      absolute window length, which was monotonically increasing and would permanently
      block the SemanticVelocityDetector's de-escalation path.
    """
    window = list(session.window)
    if not window or not input_text.strip():
        return ScoringResult(anomaly=False)

    # Find the index of the last observation that differed from its predecessor.
    events = session.tool_events
    last_progress_idx = 0  # index into window (0 = no progress observed yet)
    for i in range(1, len(events)):
        if (events[i].observation or "") != (events[i - 1].observation or ""):
            # Map tool-event index to window position (parallel by construction).
            last_progress_idx = min(i, len(window) - 1)

    # Steps taken since the last observation change (1-based so it's non-zero at step 1).
    actual = len(window) - last_progress_idx
    expected = _estimate_complexity(input_text)
    ratio = actual / max(expected, 1)
    if ratio >= config.step_overrun_factor:
        return ScoringResult(
            anomaly=True,
            kind="step_overrun",
            score=ratio,
            detector="StepOverrunDetector",
            # NOT escalate_session — non-sticky so de-escalation can still clear the block.
        )
    return ScoringResult(anomaly=False, score=ratio)


# ---------------------------------------------------------------------------
# 5. Token Burn Acceleration Detector — per-step token spend is rising
# ---------------------------------------------------------------------------


def detect_burn_acceleration(session: SessionState, config: RouterConfig) -> ScoringResult:
    """Flag when output tokens-per-step in the second half of the window is accelerating
    relative to the first half — a sign the agent is generating more text per step
    without making progress (elaboration / confusion spiral).

    M2 fix: uses output_token_count only (not input + output).  In ReAct the input
    grows every step because history is re-sent, so summing input+output would trigger
    false positives for any long-but-healthy agent.
    """
    window = list(session.window)
    if len(window) < config.burn_window_min_steps:
        return ScoringResult(anomaly=False)
    tokens = [r.output_token_count for r in window]
    mid = len(tokens) // 2
    first_avg = sum(tokens[:mid]) / mid if mid > 0 else 0.0
    second_avg = sum(tokens[mid:]) / (len(tokens) - mid)
    if first_avg == 0.0:
        return ScoringResult(anomaly=False)
    ratio = second_avg / first_avg
    if ratio >= config.burn_acceleration_factor:
        return ScoringResult(
            anomaly=True,
            kind="token_burn_acceleration",
            score=ratio,
            detector="TokenBurnAccelerationDetector",
            escalate_session=True,
        )
    return ScoringResult(anomaly=False, score=ratio)


# ---------------------------------------------------------------------------
# 6. Tool-Call Flapping Monitor (SCORE-03)
# ---------------------------------------------------------------------------


def detect_flapping(session: SessionState, config: RouterConfig) -> ScoringResult:
    """Flag the same tool called >= flapping_min_repeats with unchanged observation."""
    events = [e for e in session.tool_events if e.tool_name != "finish"]
    if len(events) < config.flapping_min_repeats:
        return ScoringResult(anomaly=False)
    counts: dict[str, int] = {}
    obs_by_tool: dict[str, set[str]] = {}
    for e in events:
        counts[e.tool_name] = counts.get(e.tool_name, 0) + 1
        obs_by_tool.setdefault(e.tool_name, set()).add(e.observation or "")
    for tool_name, n in counts.items():
        if n >= config.flapping_min_repeats and len(obs_by_tool[tool_name]) <= 1:
            return ScoringResult(
                anomaly=True,
                kind="tool_flapping",
                score=float(n) / max(len(events), 1),
                detector="ToolCallFlappingMonitor",
            )
    return ScoringResult(anomaly=False)


# ---------------------------------------------------------------------------
# 7. Semantic Velocity Detector — window-wide rate of output change
# ---------------------------------------------------------------------------


class SemanticVelocityDetector:
    """Measures the average cosine distance between consecutive step outputs
    across the entire window (not just the last two steps).

    Low average distance = the agent is producing nearly-identical reasoning
    on each step without progressing.  Also emits a de-escalation signal when
    velocity recovers strongly after a previous escalation.

    *cache* is an instance-level dict (owned by the parent ScoringEngine) that
    maps call_id → embedding vector so texts are embedded at most once per session
    and isolated across sessions (H1 fix).
    """

    def __init__(self, cache: dict[str, Any]) -> None:
        self._cache = cache

    def detect(self, session: SessionState, config: RouterConfig) -> ScoringResult:
        window = [r for r in session.window if r.output_text]
        if len(window) < config.velocity_min_window:
            return ScoringResult(anomaly=False)

        distances: list[float] = []
        for i in range(1, len(window)):
            prev = window[i - 1]
            curr = window[i]
            if prev.output_text is None or curr.output_text is None:
                raise ValueError(
                    f"output_text must not be None for call_ids "
                    f"{prev.call_id!r} / {curr.call_id!r} "
                    "(guarded by list comprehension above — this should never happen)"
                )
            sim = _cosine(
                _get_embedding(prev.call_id, prev.output_text, self._cache),
                _get_embedding(curr.call_id, curr.output_text, self._cache),
            )
            distances.append(1.0 - sim)

        if not distances:
            return ScoringResult(anomaly=False)

        avg_velocity = sum(distances) / len(distances)

        # De-escalation: use only the most-recent K consecutive pairs so that
        # early stuck steps do not drag the recovery signal down (M3 fix).
        k = config.de_escalation_recent_k
        recent_distances = distances[-k:] if k < len(distances) else distances
        recent_velocity = sum(recent_distances) / len(recent_distances)

        if (
            config.de_escalation_enabled
            and session.escalation_count > 0
            and recent_velocity
            >= config.semantic_velocity_threshold * config.de_escalation_velocity_multiplier
        ):
            return ScoringResult(anomaly=False, score=avg_velocity, de_escalate=True)

        # Low velocity AND no observation change → agent is stuck across the whole window.
        if avg_velocity < config.semantic_velocity_threshold:
            if not _observation_changed(session, len(window)):
                return ScoringResult(
                    anomaly=True,
                    kind="low_semantic_velocity",
                    score=avg_velocity,
                    detector="SemanticVelocityDetector",
                    escalate_session=True,
                )

        return ScoringResult(anomaly=False, score=avg_velocity)


# ---------------------------------------------------------------------------
# 8. Loop Velocity Profiler (SCORE-02) — kept for backward compatibility
# ---------------------------------------------------------------------------


class LoopVelocityProfiler:
    """Detects repeating outputs across consecutive turns with no observation change.

    Kept as the final embedding-based check so that existing unit tests (which
    exercise it directly and check for 'detector=LoopVelocityProfiler' in logs)
    continue to pass.  For new signal work, prefer SemanticVelocityDetector which
    evaluates the whole window rather than just the last two steps.

    *cache* is an instance-level dict shared with SemanticVelocityDetector so each
    text is embedded at most once per session (H1 fix).
    """

    def __init__(self, cache: dict[str, Any]) -> None:
        self._cache = cache

    def detect(self, session: SessionState, config: RouterConfig) -> ScoringResult:
        window = list(session.window)
        if len(window) < 2:
            return ScoringResult(anomaly=False)
        prev, curr = window[-2], window[-1]
        if not prev.output_text or not curr.output_text:
            return ScoringResult(anomaly=False)
        sim = _cosine(
            _get_embedding(prev.call_id, prev.output_text, self._cache),
            _get_embedding(curr.call_id, curr.output_text, self._cache),
        )
        if sim < config.loop_similarity_threshold:
            return ScoringResult(anomaly=False, score=sim)
        if _observation_changed(session, len(window)):
            return ScoringResult(anomaly=False, score=sim)
        return ScoringResult(
            anomaly=True,
            kind="loop_velocity",
            score=sim,
            detector="LoopVelocityProfiler",
        )


# ---------------------------------------------------------------------------
# Context Window Pressure Detector
# ---------------------------------------------------------------------------


def detect_context_pressure(session: SessionState, config: RouterConfig) -> ScoringResult:
    """Flag when the latest step's input token count approaches the context window limit.

    In DSPy agents each LM call re-sends the full conversation history, so
    input_token_count at step N is a reliable proxy for how full the context
    window is.  A model approaching its context limit degrades rapidly — better
    to escalate to a model with a larger effective window before that happens.
    """
    window = list(session.window)
    if not window:
        return ScoringResult(anomaly=False)
    latest_input = window[-1].input_token_count
    pressure = latest_input / config.context_window_limit
    if pressure >= config.context_pressure_threshold:
        return ScoringResult(
            anomaly=True,
            kind="context_pressure",
            score=pressure,
            detector="ContextWindowPressureDetector",
            escalate_session=True,
        )
    return ScoringResult(anomaly=False, score=pressure)


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------


class ScoringEngine:
    """Runs all detectors in priority order and applies the routing decision.

    Detector order (cheap → expensive, session-wide anomalies before per-step):
      structural → exception_rate → hedging → step_overrun → burn_acceleration
      → context_pressure → flapping → semantic_velocity → loop_velocity

    Routing decisions applied by score_and_apply():
      • anomaly + escalate_session  → set session.escalate_session = True (all future calls)
      • anomaly only                → set session.current_threshold = 0.0 (next call only)
      • de_escalate                 → reset threshold + escalate_session flag
    """

    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        # Per-instance embedding cache: call_id → vector.  Shared by both embedding-based
        # detectors so each text is embedded at most once per session, and GC'd together with
        # this ScoringEngine (which is created per-session in tracker.py:94).  Isolates across
        # test runs and concurrent sessions — no module-global state (H1 fix).
        self._embedding_cache: dict[str, Any] = {}
        self._semantic_velocity = SemanticVelocityDetector(self._embedding_cache)
        self._loop = LoopVelocityProfiler(self._embedding_cache)

    def score(self, session: SessionState, input_text: str = "") -> ScoringResult:
        # 1. Structural (regex on input prompt)
        r = detect_structural(input_text)
        if r.anomaly:
            return r

        # 2. Exception rate (cheap counter)
        r = detect_exception_rate(session, self.config)
        if r.anomaly:
            return r

        # 3. Hedging density (regex on latest output)
        r = detect_hedging(session, self.config)
        if r.anomaly:
            return r

        # 4. Step overrun (arithmetic)
        r = detect_step_overrun(session, self.config, input_text)
        if r.anomaly:
            return r

        # 5. Token burn acceleration (arithmetic)
        r = detect_burn_acceleration(session, self.config)
        if r.anomaly:
            return r

        # 6. Context window pressure (arithmetic)
        r = detect_context_pressure(session, self.config)
        if r.anomaly:
            return r

        # 7. Tool-call flapping (counter)
        r = detect_flapping(session, self.config)
        if r.anomaly:
            return r

        # 8. Semantic velocity (embeddings, window-wide) — may also emit de_escalate
        r = self._semantic_velocity.detect(session, self.config)
        if r.anomaly or r.de_escalate:
            return r

        # 9. Loop velocity (embeddings, consecutive pair) — backward-compat fallback
        return self._loop.detect(session, self.config)

    def score_and_apply(self, session: SessionState, input_text: str = "") -> ScoringResult:
        """Score, then apply the routing decision under the session lock."""
        result = self.score(session, input_text)

        with session._lock:
            if result.de_escalate and session.escalation_count > 0:
                session.current_threshold = self.config.default_threshold
                session.escalate_session = False
                logger.info(
                    "de_escalation session=%s detector=%s score=%.4f "
                    "threshold_reset=%.5f",
                    session.session_id,
                    result.detector,
                    result.score,
                    self.config.default_threshold,
                )
            elif result.anomaly:
                if session.escalation_count < self.config.max_escalations_per_session:
                    session.current_threshold = 0.0
                    if result.escalate_session:
                        session.escalate_session = True
                    session.escalation_count += 1
                    logger.info(
                        "escalation session=%s detector=%s kind=%s score=%.4f "
                        "count=%d escalate_session=%s",
                        session.session_id,
                        result.detector,
                        result.kind,
                        result.score,
                        session.escalation_count,
                        result.escalate_session,
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
