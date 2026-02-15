from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from silas.models.messages import TaintLevel


@dataclass
class ContextItem:
    """Unified context item for the new ContextRegistry."""

    item_id: str
    content: str
    source: str  # "memory:episodic:abc", "file:config.yaml#42-58", "personality"
    role: Literal["system", "assistant", "user"]
    last_modified: datetime
    token_count: int
    taint: TaintLevel = TaintLevel.owner
    ttl_seconds: float | None = None
    eviction_priority: float = 0.5  # 0=evict first, 1=never evict
    source_tag: str = ""  # "memory", "topic", "personality", "file", "plan", "skill"
    turn_created: int | None = None
    cache_key: str | None = None
    tags: set[str] = field(default_factory=set)
