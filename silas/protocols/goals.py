"""Goal protocol — removed.

Goal functionality is now part of the Topic model. There is no separate
GoalManager protocol; Topic persistence is handled by TopicRegistry.

This file is kept as an empty shim so that any stale imports fail loudly
rather than silently (they will get an ImportError on the removed names).
"""

__all__: list[str] = []
