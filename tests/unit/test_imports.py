# tests/unit/test_imports.py
# Nyquist gate: lazy public API — importing agent_router must not load heavy optional deps.
from __future__ import annotations

import sys


def test_public_api_import() -> None:
    """
    Importing TrajectoryTracker, DynamicRouteLM, RouterConfig from agent_router
    must NOT cause fastembed or routellm to appear in sys.modules.

    This test encodes the D-01 / LIB-01 contract: the core install stays light.

    Run in a SUBPROCESS (clean interpreter): import-time behavior cannot be asserted
    reliably in-process once another test in the suite has already loaded fastembed
    (e.g. the loop-velocity / bench tests) — sys.modules is process-global.
    """
    import subprocess

    code = (
        "import sys\n"
        "from agent_router import TrajectoryTracker, DynamicRouteLM, RouterConfig\n"
        "assert 'fastembed' not in sys.modules, 'fastembed loaded at import time'\n"
        "assert 'routellm' not in sys.modules, 'routellm loaded at import time'\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, (
        "Public import pulled a heavy optional dep at import time (D-01/LIB-01):\n"
        f"{proc.stderr}"
    )


def test_public_api_in_dir() -> None:
    """PEP 562 __dir__ surfaces the lazily-exported names before first access (WR-03)."""
    import agent_router

    listed = dir(agent_router)
    assert "TrajectoryTracker" in listed
    assert "DynamicRouteLM" in listed
    assert "RouterConfig" in listed
