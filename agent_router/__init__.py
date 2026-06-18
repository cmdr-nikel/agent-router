# Source: PEP 562 (https://peps.python.org/pep-0562/) — module-level __getattr__ since Python 3.7
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["TrajectoryTracker", "DynamicRouteLM", "RouterConfig"]

# RouterConfig is always safe to import eagerly: pydantic-settings is a core dep.
# TrajectoryTracker and DynamicRouteLM are deferred via PEP 562 __getattr__ so that
# merely doing `import agent_router` never loads fastembed or routellm.
from agent_router.config import RouterConfig  # noqa: E402

_LAZY_MAP: dict[str, str] = {
    "TrajectoryTracker": "agent_router.tracker",
    "DynamicRouteLM": "agent_router.routing.dynamic_lm",
}


def __getattr__(name: str) -> object:
    if name in _LAZY_MAP:
        import importlib

        module = importlib.import_module(_LAZY_MAP[name])
        val = getattr(module, name)
        globals()[name] = val  # cache to avoid repeated __getattr__ calls
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # PEP 562: surface the lazily-exported names so dir()/IDE completion/help()
    # see TrajectoryTracker and DynamicRouteLM before first access (Pitfall WR-03).
    return sorted(set(globals()) | set(_LAZY_MAP))
