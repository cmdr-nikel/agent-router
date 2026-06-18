# tests/unit/test_imports.py
# Nyquist gate: lazy public API — importing agent_router must not load heavy optional deps.
# RED until Plan 04 wires the lazy API surface correctly.
from __future__ import annotations

import sys


def test_public_api_import() -> None:
    """
    Importing TrajectoryTracker, DynamicRouteLM, RouterConfig from agent_router
    must NOT cause fastembed or routellm to appear in sys.modules.

    This test encodes the D-01 / LIB-01 contract: the core install stays light.
    Status: RED until Plan 04 implements the full lazy import surface.
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
