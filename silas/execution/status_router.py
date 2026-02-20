"""Status event routing to UI surfaces.

Maps execution status values to the UI surfaces that should receive
notifications, per specs/agent-loop-architecture.md §6.3.

Why a standalone module: status routing is a pure function with no
dependencies. Keeping it separate makes it trivially testable and
prevents consumers.py from growing a responsibility it shouldn't own.
"""

from __future__ import annotations

# Why a dict instead of match/case: easier to test exhaustiveness and
# extend without modifying control flow. The function wraps it for a
# clean API with a sensible default.
_STATUS_SURFACES: dict[str, tuple[str, ...]] = {
    "running": ("activity",),
    "done": ("stream", "activity"),
    "failed": ("stream", "activity"),
    "stuck": ("stream", "activity"),
    "blocked": ("stream", "activity"),
    "verification_failed": ("stream", "activity"),
}


def route_to_surface(status: str) -> tuple[str, ...]:
    """Determine which UI surfaces receive a status event.

    Why tuple return: failure statuses dual-emit to both Stream (user
    notification) and Activity (audit timeline). Running goes only to
    Activity since the user doesn't need to see routine progress ticks.

    Returns surface names as strings — the caller resolves these to
    actual UI objects. This keeps the routing logic free of UI deps.
    """
    return _STATUS_SURFACES.get(status, ("stream", "activity"))


__all__ = ["route_to_surface"]
