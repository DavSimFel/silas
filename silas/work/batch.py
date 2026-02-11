from __future__ import annotations

import uuid

from silas.models.review import BatchActionDecision, BatchActionItem, BatchProposal


class BatchExecutor:
    def __init__(self, work_item_store: object, gate_runner: object | None = None) -> None:
        self._work_item_store = work_item_store
        self._gate_runner = gate_runner

    def execute_batch(
        self,
        proposal: BatchProposal,
        decision: BatchActionDecision,
    ) -> list[dict[str, object]]:
        if decision.verdict == "decline":
            return []

        selected_items = proposal.items
        if decision.verdict == "edit_selection":
            selected_ids = set(decision.selected_items)
            selected_items = [item for item in proposal.items if item.item_id in selected_ids]

        results: list[dict[str, object]] = []
        for item in selected_items:
            error: str | None = None
            success = True
            payload = {"item_id": item.item_id, "title": item.title, "actor": item.actor}
            try:
                if not self._is_allowed(proposal.action, payload):
                    raise PermissionError("blocked by gate runner")
                self._execute_item(proposal.action, payload)
            except Exception as exc:  # noqa: BLE001 - surfaced in result payload.
                success = False
                error = str(exc)

            results.append({"item_id": item.item_id, "success": success, "error": error})
        return results

    def create_batch_from_items(self, items: list[dict[str, object]], action: str) -> BatchProposal:
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

    def _is_allowed(self, action: str, payload: dict[str, object]) -> bool:
        if self._gate_runner is None:
            return True
        if hasattr(self._gate_runner, "allow"):
            return bool(self._gate_runner.allow(action, payload))  # type: ignore[attr-defined]
        if hasattr(self._gate_runner, "run"):
            result = self._gate_runner.run(action, payload)  # type: ignore[attr-defined]
            return _coerce_gate_result(result)
        if callable(self._gate_runner):
            result = self._gate_runner(action, payload)
            return _coerce_gate_result(result)
        return True

    def _execute_item(self, action: str, payload: dict[str, object]) -> None:
        if hasattr(self._work_item_store, "execute_action"):
            self._work_item_store.execute_action(action, payload)  # type: ignore[attr-defined]
            return
        if hasattr(self._work_item_store, "apply"):
            self._work_item_store.apply(action, payload)  # type: ignore[attr-defined]
            return
        if callable(self._work_item_store):
            self._work_item_store(action, payload)


def _coerce_gate_result(result: object) -> bool:
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        if "allowed" in result:
            return bool(result["allowed"])
        if "success" in result:
            return bool(result["success"])
    try:
        return bool(result.allowed)  # type: ignore[attr-defined]
    except AttributeError:
        pass
    return bool(result)


__all__ = ["BatchExecutor"]
