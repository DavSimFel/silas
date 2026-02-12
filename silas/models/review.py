"""Batch review, decision, and draft review models (§3.11)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# --- Batch review ---


class BatchActionItem(BaseModel):
    item_id: str
    title: str
    actor: str
    occurred_at: datetime | None = None
    reason: str = ""
    confidence: float = 1.0


class BatchProposal(BaseModel):
    batch_id: str = ""
    goal_id: str = ""
    action: str = ""
    items: list[BatchActionItem] = Field(default_factory=list)
    reason_summary: str = ""
    confidence_min: float = 0.0
    created_at: datetime | None = None
    # Legacy compat
    proposal_id: str = ""
    reasoning: str = ""


class BatchActionVerdict(StrEnum):
    approve = "approve"
    decline = "decline"
    edit_selection = "edit_selection"


class BatchActionDecision(BaseModel):
    verdict: BatchActionVerdict | Literal["approve", "decline", "edit_selection"] = (
        BatchActionVerdict.decline
    )
    selected_item_ids: list[str] = Field(default_factory=list)
    # Legacy compat
    proposal_id: str = ""
    selected_items: list[str] = Field(default_factory=list)


# --- Decision cards ---


class DecisionOption(BaseModel):
    label: str
    value: str
    approval_tier: Literal["tap"] = "tap"


class DecisionResult(BaseModel):
    selected_value: str | None = None
    freetext: str | None = None
    approved: bool = False


__all__ = [
    "BatchActionDecision",
    "BatchActionItem",
    "BatchActionVerdict",
    "BatchProposal",
    "DecisionOption",
    "DecisionResult",
]
