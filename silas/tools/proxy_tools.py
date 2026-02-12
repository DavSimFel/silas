"""Proxy-specific pydantic-ai tool functions.

These tools are only registered on the proxy agent. They support
the proxy's routing decision by enabling context inspection and
user communication before producing a RouteDecision.
"""

from __future__ import annotations

from pydantic_ai import RunContext

from silas.tools.common import AgentDeps


async def tell_user(ctx: RunContext[AgentDeps], message: str) -> str:
    """Send an interim message to the user.

    Used by the proxy to acknowledge receipt or provide status updates
    before completing its routing decision. The runtime delivers this
    via the active transport (WebSocket/API).

    Why a tool not just response text: allows the proxy to send multiple
    interim messages during a multi-step tool loop before producing its
    final RouteDecision.
    """
    # Why no actual send here: the runtime intercepts tell_user calls
    # from the tool loop and routes them to the user transport. In this
    # layer we just confirm the intent was recorded.
    return f"Message sent to user: {message[:100]}"


async def context_inspect(ctx: RunContext[AgentDeps], query: str) -> str:
    """Inspect conversation context for routing decisions.

    Allows the proxy to examine recent context state (conversation history,
    active scope, pending operations) before deciding how to route.

    Why separate from memory_search: context_inspect looks at the current
    session's live state, while memory_search queries persisted long-term memory.
    """
    # Why stub implementation: the context manager integration comes in a
    # later work item. For now, returns a placeholder so the tool is
    # registered and agents can be tested with it.
    return f"Context inspection for '{query}': no active context manager configured."


__all__ = [
    "context_inspect",
    "tell_user",
]
