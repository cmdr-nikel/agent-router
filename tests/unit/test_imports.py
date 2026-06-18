# tests/unit/test_imports.py
# Nyquist gate: lazy public API — importing agent_router must not load heavy optional deps.
from __future__ import annotations

import sys


def test_public_api_import() -> None:
    """
    Importing TrajectoryTracker, DynamicRouteLM, RouterConfig from agent_router
    must NOT cause fastembed or routellm to appear in sys.modules.

    This test encodes the D-01 / LIB-01 contract: the core install stays light.
    """
    # Remove any cached agent_router modules to get a clean import
    modules_to_remove = [k for k in sys.modules if k.startswith("agent_router")]
    for mod in modules_to_remove:
        del sys.modules[mod]

    from agent_router import DynamicRouteLM, RouterConfig, TrajectoryTracker  # noqa: F401

    # Assert that heavy optional deps were NOT loaded
    assert "fastembed" not in sys.modules, (
        "fastembed was loaded at import time — must be deferred to first use inside "
        "TrajectoryTracker/LoopVelocityProfiler. Install: pip install agent-router[embed]"
    )
    assert "routellm" not in sys.modules, (
        "routellm was loaded at import time — must be deferred to first use inside "
        "DynamicRouteLM. Install: pip install agent-router[serve]"
    )


def test_public_api_in_dir() -> None:
    """PEP 562 __dir__ surfaces the lazily-exported names before first access (WR-03)."""
    import agent_router

    listed = dir(agent_router)
    assert "TrajectoryTracker" in listed
    assert "DynamicRouteLM" in listed
    assert "RouterConfig" in listed
