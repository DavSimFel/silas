"""Taint propagation tracker for tool call chains.

Taint flows *upward* through execution: if any input or intermediate tool
introduces a higher taint level, all downstream outputs inherit that level.
This prevents external-sourced data from being silently treated as owner-trusted.

Uses ``contextvars`` so concurrent asyncio tasks each get isolated taint state —
critical because the Stream processes multiple connections concurrently.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import ClassVar

from silas.models.messages import TaintLevel

# Numeric ordering lets us use max() for lattice-join propagation.
_TAINT_ORDER: dict[TaintLevel, int] = {
    TaintLevel.owner: 0,
    TaintLevel.auth: 1,
    TaintLevel.external: 2,
}

# Reverse lookup for converting back from int to TaintLevel.
_ORDER_TO_TAINT: dict[int, TaintLevel] = {v: k for k, v in _TAINT_ORDER.items()}


def _max_taint(a: TaintLevel, b: TaintLevel) -> TaintLevel:
    """Return the higher (less trusted) of two taint levels."""
    return _ORDER_TO_TAINT[max(_TAINT_ORDER[a], _TAINT_ORDER[b])]


# Module-level ContextVar so each async task gets its own TaintTracker state
# without explicit threading.  The Token returned by .set() is used for reset.
_current_taint: ContextVar[TaintLevel] = ContextVar("_current_taint", default=TaintLevel.owner)


class TaintTracker:
    """Per-execution-context taint propagation tracker.

    Tracks the high-water-mark taint level across a chain of tool calls
    within a single turn.  Thread-safe via ``contextvars``.

    Typical lifecycle::

        tracker = TaintTracker()
        tracker.reset()                        # start of turn
        tracker.on_tool_input(signed.taint)    # record inbound message taint
        out_taint = tracker.on_tool_output("web_search")  # propagated taint
        item.taint = out_taint                 # stamp on ContextItem / MemoryItem
    """

    # Dynamic registry for skill-declared tool taints.  Checked before
    # the hardcoded sets below, allowing skills to override defaults.
    _dynamic_tool_taints: ClassVar[dict[str, TaintLevel]] = {}

    # Tools with known taint ceilings.  Anything not listed defaults to owner
    # (i.e. internal tools don't escalate taint on their own).
    EXTERNAL_TOOLS: ClassVar[frozenset[str]] = frozenset(
        {
            "web_search",
            "web_fetch",
            "web_browse",
            "http_request",
            "api_call",
            "email_send",
            "email_read",
        }
    )

    AUTH_TOOLS: ClassVar[frozenset[str]] = frozenset(
        {
            "calendar_read",
            "calendar_write",
            "sharepoint_read",
            "sharepoint_write",
            "notion_read",
            "notion_write",
        }
    )

    def on_tool_input(self, taint: TaintLevel) -> None:
        """Record the taint of data flowing *into* the current execution.

        Called when a message or prior tool result is consumed.  Ratchets
        the context taint upward if the input is less trusted than what
        we've seen so far.
        """
        current = _current_taint.get()
        _current_taint.set(_max_taint(current, taint))

    def on_tool_output(self, tool_name: str) -> TaintLevel:
        """Compute propagated taint for a tool's output.

        Combines the tool's inherent taint ceiling with the accumulated
        input taint — the result is always >= both, ensuring taint never
        drops silently.
        """
        tool_taint = self._tool_taint(tool_name)
        current = _current_taint.get()
        propagated = _max_taint(current, tool_taint)
        # Ratchet context to propagated level so subsequent tools inherit it
        _current_taint.set(propagated)
        return propagated

    def get_current_taint(self) -> TaintLevel:
        """Return the current high-water-mark taint for this execution context."""
        return _current_taint.get()

    def reset(self) -> None:
        """Reset taint to baseline (owner) at the start of a new turn.

        Must be called per-turn to prevent cross-turn taint leakage.
        """
        _current_taint.set(TaintLevel.owner)

    @classmethod
    def add_tool_taint(cls, tool_name: str, taint: TaintLevel) -> None:
        """Register a dynamic taint level for a tool (e.g. from a loaded skill)."""
        cls._dynamic_tool_taints[tool_name] = taint

    def _tool_taint(self, tool_name: str) -> TaintLevel:
        """Determine a tool's inherent taint ceiling based on its category.

        Checks the dynamic registry first (populated by skill loading),
        then falls back to the hardcoded category sets.
        """
        dynamic = self._dynamic_tool_taints.get(tool_name)
        if dynamic is not None:
            return dynamic
        if tool_name in self.EXTERNAL_TOOLS:
            return TaintLevel.external
        if tool_name in self.AUTH_TOOLS:
            return TaintLevel.auth
        # Internal tools (memory, context, planning) don't escalate taint
        return TaintLevel.owner


__all__ = ["TaintTracker"]
