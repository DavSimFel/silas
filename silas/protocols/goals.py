"""Goal protocol — removed.

Goal functionality is now part of the Topic model. There is no separate
GoalManager protocol; Topic persistence is handled by TopicRegistry.

This module raises ImportError at import time so that stale imports fail
loudly rather than silently importing an empty namespace.

Use instead:
  from silas.topics import Topic, TopicRegistry
  from silas.models.goals import Schedule, StandingApproval
"""

raise ImportError(
    "silas.protocols.goals has been removed. "
    "Goal functionality is now part of Topic — "
    "use `silas.topics.TopicRegistry` and `silas.topics.Topic` instead."
)
