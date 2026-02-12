from __future__ import annotations

from pydantic import BaseModel, Field


class ScorerGroup(BaseModel):
    reason: str
    block_ids: list[str] = Field(default_factory=list)


class ScorerOutput(BaseModel):
    keep_groups: list[ScorerGroup] = Field(default_factory=list)
    evict_groups: list[ScorerGroup] = Field(default_factory=list)


__all__ = ["ScorerGroup", "ScorerOutput"]
