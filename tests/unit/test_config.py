# tests/unit/test_config.py
# Nyquist gates for RouterConfig (LIB-02): fields, defaults, env override.
# Status: GREEN from Task 2 (config.py implements RouterConfig with all D-05 fields).
from __future__ import annotations

import os


def test_router_config_fields() -> None:
    """
    RouterConfig must expose the 6 D-05 fields with correct defaults.
    Status: GREEN from Task 2.
    """
    from agent_router.config import RouterConfig

    cfg = RouterConfig()

    # Verify all 6 required fields (D-05) exist with defaults
    assert hasattr(cfg, "window_size"), "RouterConfig missing window_size"
    assert hasattr(cfg, "default_threshold"), "RouterConfig missing default_threshold"
    assert hasattr(cfg, "loop_similarity_threshold"), "RouterConfig missing loop_similarity_threshold"
    assert hasattr(cfg, "max_escalations_per_session"), "RouterConfig missing max_escalations_per_session"
    assert hasattr(cfg, "weak_model"), "RouterConfig missing weak_model"
    assert hasattr(cfg, "strong_model"), "RouterConfig missing strong_model"

    # Verify default values
    assert cfg.window_size == 10
    assert cfg.default_threshold == 0.11593
    assert cfg.loop_similarity_threshold == 0.85
    assert cfg.max_escalations_per_session == 3
    assert cfg.weak_model == "openai/gpt-4o-mini"
    assert cfg.strong_model == "openai/gpt-4o"


def test_router_config_env() -> None:
    """
    AGENT_ROUTER_WEAK_MODEL env var must override RouterConfig.weak_model.
    Exercises pydantic-settings env binding (D-04).
    Status: GREEN from Task 2.
    """
    from agent_router.config import RouterConfig

    env_key = "AGENT_ROUTER_WEAK_MODEL"
    test_value = "openai/gpt-4o-mini-2024-07-18"

    original = os.environ.get(env_key)
    try:
        os.environ[env_key] = test_value
        cfg = RouterConfig()
        assert cfg.weak_model == test_value, (
            f"RouterConfig.weak_model expected {test_value!r} from env, got {cfg.weak_model!r}"
        )
    finally:
        # Restore original env state
        if original is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = original


def test_router_config_field_validation() -> None:
    """
    RouterConfig must reject invalid field values (pydantic Field constraints).
    Status: GREEN from Task 2.
    """
    import pytest
    from pydantic import ValidationError

    from agent_router.config import RouterConfig

    # window_size must be ge=1
    with pytest.raises(ValidationError):
        RouterConfig(window_size=0)

    # default_threshold must be ge=0.0 le=1.0
    with pytest.raises(ValidationError):
        RouterConfig(default_threshold=1.5)

    with pytest.raises(ValidationError):
        RouterConfig(default_threshold=-0.1)
