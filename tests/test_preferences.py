from __future__ import annotations

import os

from silas.core.subscriptions import ContextSubscriptionManager
from silas.models.context import ContextSubscription, ContextZone
from silas.models.preferences import InferredPreference, PreferenceSignal
from silas.models.review import BatchActionDecision, BatchActionItem, BatchProposal
from silas.proactivity.preferences import PreferenceInferenceEngine
from silas.work.batch import BatchExecutor


def _signal(
    signal_id: str,
    scope_id: str = "owner",
    signal_type: str = "correction",
    context: str = "editing file",
) -> PreferenceSignal:
    return PreferenceSignal(
        signal_id=signal_id,
        scope_id=scope_id,
        signal_type=signal_type,  # type: ignore[arg-type]
        context=context,
    )


def _subscription(
    sub_id: str,
    target: str,
    *,
    active: bool = True,
    sub_type: str = "file",
) -> ContextSubscription:
    return ContextSubscription(
        sub_id=sub_id,
        sub_type=sub_type,
        target=target,
        zone=ContextZone.workspace,
        turn_created=1,
        content_hash="hash",
        active=active,
    )


class _BatchStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute_batch_action(self, action: str, payload: dict[str, object]) -> None:
        self.calls.append((action, payload))


def test_preference_signal_creation() -> None:
    signal = PreferenceSignal(
        signal_id="sig-1",
        scope_id="owner",
        signal_type="correction",
        context="while summarizing plan",
    )
    assert signal.signal_id == "sig-1"
    assert signal.metadata == {}
    assert signal.created_at.tzinfo is not None


def test_inferred_preference_confidence_clamping() -> None:
    high = InferredPreference(
        preference_id="pref-high",
        scope_id="owner",
        category="communication_style",
        description="prefers concise responses",
        confidence=1.4,
    )
    low = InferredPreference(
        preference_id="pref-low",
        scope_id="owner",
        category="communication_style",
        description="prefers concise responses",
        confidence=-0.2,
    )
    assert high.confidence == 1.0
    assert low.confidence == 0.0


def test_preference_inference_record_and_infer_three_signals() -> None:
    engine = PreferenceInferenceEngine(signal_store=[])
    for idx in range(3):
        engine.record_signal(
            _signal(f"sig-{idx}", signal_type="style_feedback", context="response tone")
        )

    inferred = engine.infer_preferences("owner")
    assert len(inferred) == 1
    assert inferred[0].confidence == 0.5
    assert len(inferred[0].supporting_signals) == 3


def test_preference_inference_ten_signals_confidence() -> None:
    engine = PreferenceInferenceEngine(signal_store=[])
    for idx in range(10):
        engine.record_signal(
            _signal(f"sig-{idx}", signal_type="correction", context="shell command style")
        )

    inferred = engine.infer_preferences("owner")
    assert len(inferred) == 1
    assert inferred[0].confidence == 0.9


def test_preference_inference_respects_min_signals_threshold() -> None:
    engine = PreferenceInferenceEngine(signal_store=[])
    for idx in range(4):
        engine.record_signal(
            _signal(f"sig-{idx}", signal_type="correction", context="task planning")
        )

    inferred = engine.infer_preferences("owner", min_signals=5)
    assert inferred == []


def test_preference_inference_get_and_clear_preferences() -> None:
    engine = PreferenceInferenceEngine(signal_store=[])
    for idx in range(3):
        engine.record_signal(_signal(f"sig-{idx}", signal_type="correction", context="code edits"))

    engine.infer_preferences("owner")
    current = engine.get_preferences("owner")
    assert len(current) == 1
    cleared = engine.clear_preferences("owner")
    assert cleared == 1
    assert engine.get_preferences("owner") == []


def test_context_subscription_manager_register_unregister(tmp_path) -> None:
    manager = ContextSubscriptionManager(subscriptions=[])
    file_path = tmp_path / "notes.txt"
    file_path.write_text("hello", encoding="utf-8")

    manager.register(_subscription("sub-1", str(file_path)))
    active = manager.get_active()
    assert len(active) == 1
    assert active[0].sub_id == "sub-1"
    assert manager.unregister("sub-1") is True
    assert manager.unregister("sub-1") is False


def test_context_subscription_manager_check_changes(tmp_path) -> None:
    file_path = tmp_path / "tracked.txt"
    file_path.write_text("v1", encoding="utf-8")
    manager = ContextSubscriptionManager(subscriptions=[_subscription("sub-1", str(file_path))])

    assert manager.check_changes() == []
    previous_stat = file_path.stat()
    file_path.write_text("v2", encoding="utf-8")
    os.utime(file_path, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns + 1_000_000_000))

    changed = manager.check_changes()
    assert len(changed) == 1
    assert changed[0]["subscription_id"] == "sub-1"
    assert changed[0]["target"] == str(file_path)
    assert manager.check_changes() == []


def test_context_subscription_manager_materialize_file_content(tmp_path) -> None:
    file_path = tmp_path / "materialize.txt"
    file_path.write_text("file content", encoding="utf-8")
    manager = ContextSubscriptionManager(subscriptions=[_subscription("sub-1", str(file_path))])

    assert manager.materialize("sub-1") == "file content"


def test_context_subscription_manager_prune_expired() -> None:
    active = _subscription("active", "/tmp/active.txt", active=True)
    expired = _subscription("expired", "/tmp/expired.txt", active=False)
    manager = ContextSubscriptionManager(subscriptions=[active, expired])

    removed = manager.prune_expired()
    assert removed == 1
    assert [sub.sub_id for sub in manager.get_active()] == ["active"]


def test_batch_proposal_creation() -> None:
    executor = BatchExecutor(work_item_store=_BatchStore())
    proposal = executor.create_batch_from_items(
        [
            {"item_id": "i1", "title": "Title 1", "actor": "alice"},
            {"item_id": "i2", "title": "Title 2", "actor": "bob"},
        ],
        action="archive",
    )

    assert proposal.action == "archive"
    assert len(proposal.items) == 2
    assert proposal.items[0].item_id == "i1"


def test_batch_executor_approve_flow() -> None:
    store = _BatchStore()
    executor = BatchExecutor(work_item_store=store)
    proposal = BatchProposal(
        proposal_id="p1",
        action="archive",
        items=[
            BatchActionItem(item_id="i1", title="First", actor="alice"),
            BatchActionItem(item_id="i2", title="Second", actor="bob"),
        ],
    )
    decision = BatchActionDecision(proposal_id="p1", verdict="approve")

    results = executor.execute_batch(proposal, decision)
    assert len(results) == 2
    assert all(result["success"] is True for result in results)
    assert [call[1]["item_id"] for call in store.calls] == ["i1", "i2"]


def test_batch_executor_decline_flow() -> None:
    store = _BatchStore()
    executor = BatchExecutor(work_item_store=store)
    proposal = BatchProposal(
        proposal_id="p1",
        action="archive",
        items=[BatchActionItem(item_id="i1", title="First", actor="alice")],
    )
    decision = BatchActionDecision(proposal_id="p1", verdict="decline")

    results = executor.execute_batch(proposal, decision)
    assert results == []
    assert store.calls == []


def test_batch_executor_edit_selection_flow() -> None:
    store = _BatchStore()
    executor = BatchExecutor(work_item_store=store)
    proposal = BatchProposal(
        proposal_id="p1",
        action="archive",
        items=[
            BatchActionItem(item_id="i1", title="First", actor="alice"),
            BatchActionItem(item_id="i2", title="Second", actor="bob"),
        ],
    )
    decision = BatchActionDecision(
        proposal_id="p1",
        verdict="edit_selection",
        selected_items=["i2"],
    )

    results = executor.execute_batch(proposal, decision)
    assert len(results) == 1
    assert results[0]["item_id"] == "i2"
    assert store.calls == [("archive", {"item_id": "i2", "title": "Second", "actor": "bob"})]


def test_batch_action_item_serialization() -> None:
    item = BatchActionItem(item_id="i1", title="Review ticket", actor="alice")
    payload = item.model_dump()

    assert payload["item_id"] == "i1"
    assert payload["title"] == "Review ticket"
    assert payload["actor"] == "alice"
    # New spec fields have defaults
    assert payload["reason"] == ""
    assert payload["confidence"] == 1.0
