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

    # Model pair — override via AGENT_ROUTER_WEAK_MODEL / AGENT_ROUTER_STRONG_MODEL
    weak_model: str = "openai/gpt-4o-mini"
    strong_model: str = "openai/gpt-4o"
