from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BatchActionItem(BaseModel):
    item_id: str
    title: str
    actor: str


class BatchProposal(BaseModel):
    proposal_id: str
    action: str
    items: list[BatchActionItem]
    reasoning: str = ""


class BatchActionDecision(BaseModel):
    proposal_id: str
    verdict: Literal["approve", "decline", "edit_selection"]
    selected_items: list[str] = Field(default_factory=list)


__all__ = ["BatchActionItem", "BatchProposal", "BatchActionDecision"]
