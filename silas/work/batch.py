"""BatchExecutor — executes approved batch action proposals.

Handles gate-checked batch operations against work item stores,
with proper protocol typing instead of duck-typed `object` params.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from silas.models.review import BatchActionDecision, BatchActionItem, BatchProposal

logger = logging.getLogger(__name__)


@runtime_checkable
class BatchGateRunner(Protocol):
    """Protocol for gate runners that can check batch action permissions."""

    def check_batch_action(self, action: str, payload: Mapping[str, object]) -> bool: ...


@runtime_checkable
class BatchWorkItemStore(Protocol):
    """Protocol for stores that can execute batch actions."""

    def execute_batch_action(self, action: str, payload: Mapping[str, object]) -> None: ...


class BatchExecutor:
    """Executes approved batch proposals against a work item store with optional gate checks."""

    def __init__(
        self,
        work_item_store: BatchWorkItemStore,
        gate_runner: BatchGateRunner | None = None,
    ) -> None:
        self._work_item_store = work_item_store
        self._gate_runner = gate_runner

    def execute_batch(
        self,
        proposal: BatchProposal,
        decision: BatchActionDecision,
    ) -> list[dict[str, object]]:
        """Execute a batch proposal filtered by the user's decision."""
        if decision.verdict == "decline":
            return []

        selected_items = proposal.items
        if decision.verdict == "edit_selection":
            selected_ids = set(decision.selected_items)
            selected_items = [item for item in proposal.items if item.item_id in selected_ids]

        results: list[dict[str, object]] = []
        for item in selected_items:
            result = self._execute_single_item(proposal.action, item)
            results.append(result)
        return results

    def create_batch_from_items(self, items: list[dict[str, object]], action: str) -> BatchProposal:
        """Build a BatchProposal from raw item dicts."""
        batch_items: list[BatchActionItem] = []
        for item in items:
            item_id = str(item.get("item_id") or item.get("id") or uuid.uuid4().hex)
            title = str(item.get("title", "Untitled"))
            actor = str(item.get("actor", "unknown"))
            batch_items.append(BatchActionItem(item_id=item_id, title=title, actor=actor))

        return BatchProposal(
            proposal_id=f"proposal:{uuid.uuid4().hex}",
            action=action,
            items=batch_items,
            reasoning="",
        )

    def _execute_single_item(
        self,
        action: str,
        item: BatchActionItem,
    ) -> dict[str, object]:
        """Execute one batch item with gate check, returning success/error dict."""
        payload = {"item_id": item.item_id, "title": item.title, "actor": item.actor}
        try:
            if not self._is_allowed(action, payload):
                raise PermissionError("blocked by gate runner")
            self._work_item_store.execute_batch_action(action, payload)
        except (PermissionError, ValueError, RuntimeError, OSError) as exc:
            logger.warning("Batch item %s failed: %s", item.item_id, exc)
            return {"item_id": item.item_id, "success": False, "error": str(exc)}

        return {"item_id": item.item_id, "success": True, "error": None}

    def _is_allowed(self, action: str, payload: Mapping[str, object]) -> bool:
        """Check gate runner permission. No gate runner = always allowed."""
        if self._gate_runner is None:
            return True
        return self._gate_runner.check_batch_action(action, payload)


__all__ = ["BatchExecutor", "BatchGateRunner", "BatchWorkItemStore"]
