"""Shared custom tools used across multiple agents.

Tools are plain async functions registered on pydantic-ai agents via
dependency injection. Why functions not classes: pydantic-ai registers
tools as callables with RunContext for deps access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic_ai import RunContext


@runtime_checkable
class MemorySearchProvider(Protocol):
    """Protocol for memory search implementations.

    Why a protocol: decouples tools from concrete memory store, enabling
    test doubles without import-time dependencies on persistence layer.
    """

    async def search(self, query: str, max_results: int = 5) -> list[str]:
        """Return matching memory entries as formatted strings."""
        ...


@runtime_checkable
class WebSearchProvider(Protocol):
    """Protocol for web search implementations.

    Why a protocol: web search may be backed by different providers
    (Brave, SerpAPI, mock) depending on config and test context.
    """

    async def search(self, query: str, max_results: int = 3) -> list[str]:
        """Return search results as formatted strings."""
        ...


@dataclass
class AgentDeps:
    """Dependency container injected into agent tool functions via RunContext.

    Holds references to services that tools need. Passed as the `deps`
    argument when running a pydantic-ai Agent.

    Why a dataclass not a dict: type safety for tool implementations,
    and IDE autocomplete for contributors.

    Satisfies pydantic-ai-backend's ConsoleDeps protocol via the `backend`
    property, so console tools (read_file, ls, grep, etc.) can access the
    filesystem backend through the same deps object.
    """

    workspace_path: Path
    memory_retriever: MemorySearchProvider | None = None
    web_search_provider: WebSearchProvider | None = None
    # Why import as string: avoids circular import with queue module.
    # At runtime, this is a QueueRouter instance.
    queue_router: object | None = None
    config: dict[str, object] = field(default_factory=dict)
    # Why optional: backend is set by toolset builders when console tools
    # are included. Tools that don't need filesystem access skip this.
    _backend: object | None = field(default=None, repr=False)

    @property
    def backend(self) -> object:
        """Expose the filesystem backend for pydantic-ai-backend console tools.

        The ConsoleDeps protocol requires a `backend` property. This bridges
        our AgentDeps with the console toolset's expectations.
        """
        if self._backend is None:
            # Why lazy import: avoids import-time dependency on pydantic_ai_backends
            # for callers that don't use console tools.
            from pydantic_ai_backends import LocalBackend

            self._backend = LocalBackend(root_dir=str(self.workspace_path))
        return self._backend


async def memory_search(
    ctx: RunContext[AgentDeps],
    query: str,
    max_results: int = 5,
) -> str:
    """Search agent memory for relevant context.

    Used by proxy and planner to retrieve prior conversations, decisions,
    and learned patterns before making routing/planning decisions.

    Returns formatted results or a message indicating no retriever is configured.
    """
    retriever = ctx.deps.memory_retriever
    if retriever is None:
        return "Memory search unavailable: no retriever configured."
    results = await retriever.search(query, max_results=max_results)
    if not results:
        return f"No memory results found for: {query}"
    # Why numbered list: gives the LLM structured references it can cite.
    formatted = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(results))
    return f"Memory search results for '{query}':\n{formatted}"


async def web_search(
    ctx: RunContext[AgentDeps],
    query: str,
    max_results: int = 3,
) -> str:
    """Search the web for current information.

    Used by proxy (factual lookups) and executor (research mode).

    Returns formatted results or a message indicating no provider is configured.
    """
    provider = ctx.deps.web_search_provider
    if provider is None:
        return "Web search unavailable: no provider configured."
    results = await provider.search(query, max_results=max_results)
    if not results:
        return f"No web results found for: {query}"
    formatted = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(results))
    return f"Web search results for '{query}':\n{formatted}"


__all__ = [
    "AgentDeps",
    "MemorySearchProvider",
    "WebSearchProvider",
    "memory_search",
    "web_search",
]
