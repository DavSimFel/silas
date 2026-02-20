"""Core module — low-level infrastructure shared across all packages.

Context management, subscriptions, plan parsing, and approval flow have moved:
    from silas.context import LiveContextManager, ContextRegistry, TurnContext
    from silas.execution import MarkdownPlanParser, SilasVerificationRunner
    from silas.gates import ApprovalFlow

Stream is NOT imported here to avoid pulling in FastAPI at import time.
Import directly: from silas.core.stream import Stream
"""

from silas.core.key_manager import SilasKeyManager

__all__ = [
    "SilasKeyManager",
]
