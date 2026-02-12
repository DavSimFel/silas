from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from silas.models.preferences import InferredPreference, PreferenceSignal


class PreferenceInferenceEngine:
    def __init__(self, signal_store: list[PreferenceSignal] | object | None = None) -> None:
        self._signal_store: list[PreferenceSignal] | object = (
            signal_store if signal_store is not None else []
        )
        self._preferences_by_scope: dict[str, list[InferredPreference]] = {}

    def record_signal(self, signal: PreferenceSignal) -> None:
        if isinstance(self._signal_store, list):
            self._signal_store.append(signal)
            return
        if hasattr(self._signal_store, "append"):
            self._signal_store.append(signal)  # type: ignore[attr-defined]
            return
        if hasattr(self._signal_store, "record_signal"):
            self._signal_store.record_signal(signal)  # type: ignore[attr-defined]
            return
        raise TypeError("signal_store must support append() or record_signal()")

    def infer_preferences(self, scope_id: str, min_signals: int = 3) -> list[InferredPreference]:
        if min_signals < 1:
            min_signals = 1

        grouped: dict[tuple[str, str, str], list[PreferenceSignal]] = {}
        for signal in self._signals_for_scope(scope_id):
            category = _category_for_signal(signal)
            context_key = signal.context.strip().lower()
            key = (category, signal.signal_type, context_key)
            grouped.setdefault(key, []).append(signal)

        inferred: list[InferredPreference] = []
        now = datetime.now(UTC)
        for (category, signal_type, _context_key), signals in sorted(grouped.items()):
            if len(signals) < min_signals:
                continue

            sample = signals[0]
            description = (
                f"Repeated {signal_type} feedback in context '{sample.context}' "
                f"({len(signals)} signals)"
            )
            inferred.append(
                InferredPreference(
                    preference_id=f"pref:{scope_id}:{uuid.uuid4().hex}",
                    scope_id=scope_id,
                    category=category,
                    description=description,
                    confidence=_confidence_for_signal_count(len(signals)),
                    supporting_signals=[signal.signal_id for signal in signals],
                    created_at=now,
                    updated_at=now,
                )
            )

        self._preferences_by_scope[scope_id] = inferred
        return [preference.model_copy(deep=True) for preference in inferred]

    def get_preferences(self, scope_id: str) -> list[InferredPreference]:
        return [
            preference.model_copy(deep=True)
            for preference in self._preferences_by_scope.get(scope_id, [])
        ]

    def clear_preferences(self, scope_id: str) -> int:
        existing = self._preferences_by_scope.pop(scope_id, [])
        return len(existing)

    def _signals_for_scope(self, scope_id: str) -> list[PreferenceSignal]:
        if isinstance(self._signal_store, list):
            return [signal for signal in self._signal_store if signal.scope_id == scope_id]

        if hasattr(self._signal_store, "list_signals"):
            listed = self._signal_store.list_signals(scope_id)  # type: ignore[attr-defined]
            return [signal for signal in listed if signal.scope_id == scope_id]

        if isinstance(self._signal_store, Iterable):
            return [signal for signal in self._signal_store if signal.scope_id == scope_id]

        raise TypeError("signal_store must be iterable or expose list_signals(scope_id)")


def _category_for_signal(signal: PreferenceSignal) -> str:
    context = signal.context.lower()
    if any(token in context for token in ("tool", "terminal", "shell", "command", "file", "path")):
        return "tool_usage"
    if signal.signal_type in {"style_feedback", "praise"}:
        return "communication_style"
    return "task_approach"


def _confidence_for_signal_count(count: int) -> float:
    if count >= 10:
        return 0.9
    if count >= 5:
        return 0.7
    if count >= 3:
        return 0.5
    return 0.3


__all__ = ["PreferenceInferenceEngine"]
