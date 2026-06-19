# agent_router/config.py
# Source: https://github.com/pydantic/pydantic-settings (ctx7, 2026-06-18)
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RouterConfig(BaseSettings):
    """
    Configuration for the trajectory-aware router.

    All fields can be overridden via environment variables with the
    `AGENT_ROUTER_` prefix (e.g. AGENT_ROUTER_WEAK_MODEL).
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_ROUTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Window and routing thresholds
    window_size: int = Field(default=10, ge=1, le=100)
    default_threshold: float = Field(default=0.11593, ge=0.0, le=1.0)
    loop_similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_escalations_per_session: int = Field(default=3, ge=1)
    # Scoring detector thresholds (Phase 3). All detector knobs live here so behavior
    # changes require no code edit (SCORE-04).
    flapping_min_repeats: int = Field(default=3, ge=2)

    # --- Token burn acceleration detector ---
    # Requires at least this many steps before comparing first-half vs second-half burn.
    burn_window_min_steps: int = Field(default=4, ge=2)
    # Flag when second-half avg tokens-per-step exceeds first-half avg by this factor.
    burn_acceleration_factor: float = Field(default=2.0, ge=1.0)

    # --- Exception rate detector ---
    # Look at this many most-recent steps when computing the exception rate.
    exception_rate_window: int = Field(default=5, ge=2)
    # Flag when the fraction of failed steps in the window meets or exceeds this value.
    exception_rate_threshold: float = Field(default=0.4, ge=0.0, le=1.0)

    # --- Hedging density detector ---
    # Flag when the latest output contains at least this many distinct hedging phrases.
    hedging_min_matches: int = Field(default=3, ge=1)

    # --- Step overrun detector ---
    # Flag when actual step count / estimated complexity >= this factor.
    step_overrun_factor: float = Field(default=3.0, ge=1.0)

    # --- Semantic velocity detector ---
    # Minimum steps required before computing window-wide velocity.
    velocity_min_window: int = Field(default=3, ge=2)
    # Flag (and escalate session) when avg cosine-distance between consecutive steps
    # falls below this value AND no observation change is detected.
    semantic_velocity_threshold: float = Field(default=0.15, ge=0.0, le=1.0)

    # --- Context window pressure detector ---
    # Assumed max context window size (tokens) for the weak model.
    context_window_limit: int = Field(default=128_000, ge=1_000)
    # Flag when the latest step's input_token_count / context_window_limit >= this.
    context_pressure_threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    # --- De-escalation ---
    # When True, allow the scoring engine to reset current_threshold back to
    # default_threshold after the strong model clears a block (semantic velocity recovers).
    de_escalation_enabled: bool = True
    # Velocity must exceed semantic_velocity_threshold * this multiplier to trigger
    # de-escalation (requires a clear recovery signal, not just marginal improvement).
    de_escalation_velocity_multiplier: float = Field(default=2.0, ge=1.0)
    # M3: judge recovery on the most-recent K consecutive pairs, not the whole window.
    # Env-override: AGENT_ROUTER_DE_ESCALATION_RECENT_K
    de_escalation_recent_k: int = Field(default=3, ge=1)

    # Model pair — override via AGENT_ROUTER_WEAK_MODEL / AGENT_ROUTER_STRONG_MODEL
    weak_model: str = "openai/gpt-4o-mini"
    strong_model: str = "openai/gpt-4o"
