"""PreferenceInferenceEngine — infers user preferences from behavioral signals.

Groups repeated signals by category and context, producing InferredPreference
objects when enough supporting evidence accumulates.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from silas.models.preferences import InferredPreference, PreferenceSignal

logger = logging.getLogger(__name__)


@runtime_checkable
class SignalStore(Protocol):
    """Protocol for signal persistence backends."""

    def append(self, signal: PreferenceSignal) -> None: ...

    def list_signals(self, scope_id: str) -> list[PreferenceSignal]: ...


class InMemorySignalStore:
    """Simple in-memory signal store for testing and default usage."""

    def __init__(self) -> None:
        self._signals: list[PreferenceSignal] = []

    def append(self, signal: PreferenceSignal) -> None:
        self._signals.append(signal)

    def list_signals(self, scope_id: str) -> list[PreferenceSignal]:
        return [s for s in self._signals if s.scope_id == scope_id]


class PreferenceInferenceEngine:
    """Infers user preferences from accumulated behavioral signals.

    Args:
        signal_store: Backend for signal persistence. Defaults to in-memory.
    """

    def __init__(self, signal_store: SignalStore | None = None) -> None:
        self._signal_store: SignalStore = signal_store or InMemorySignalStore()
        self._preferences_by_scope: dict[str, list[InferredPreference]] = {}

    def record_signal(self, signal: PreferenceSignal) -> None:
        """Persist a new preference signal."""
        self._signal_store.append(signal)

    def infer_preferences(self, scope_id: str, min_signals: int = 3) -> list[InferredPreference]:
        """Analyze signals for a scope and produce preference objects.

        Groups signals by (category, signal_type, context) and creates a
        preference for each group exceeding min_signals threshold.
        """
        if min_signals < 1:
            min_signals = 1

        grouped: dict[tuple[str, str, str], list[PreferenceSignal]] = {}
        for signal in self._signal_store.list_signals(scope_id):
            category = _category_for_signal(signal)
            context_key = signal.context.strip().lower()
            key = (category, signal.signal_type, context_key)
            grouped.setdefault(key, []).append(signal)

        inferred: list[InferredPreference] = []
        now = datetime.now(UTC)
        for (_category, signal_type, _context_key), signals in sorted(grouped.items()):
            if len(signals) < min_signals:
                continue

            category = _category_for_signal(signals[0])
            description = (
                f"Repeated {signal_type} feedback in context '{signals[0].context}' "
                f"({len(signals)} signals)"
            )
            inferred.append(
                InferredPreference(
                    preference_id=f"pref:{scope_id}:{uuid.uuid4().hex}",
                    scope_id=scope_id,
                    category=category,
                    description=description,
                    confidence=_confidence_for_signal_count(len(signals)),
                    supporting_signals=[s.signal_id for s in signals],
                    created_at=now,
                    updated_at=now,
                )
            )

        self._preferences_by_scope[scope_id] = inferred
        return [p.model_copy(deep=True) for p in inferred]

    def get_preferences(self, scope_id: str) -> list[InferredPreference]:
        """Return previously inferred preferences for a scope."""
        return [
            p.model_copy(deep=True)
            for p in self._preferences_by_scope.get(scope_id, [])
        ]

    def clear_preferences(self, scope_id: str) -> int:
        """Clear cached preferences for a scope. Returns count removed."""
        existing = self._preferences_by_scope.pop(scope_id, [])
        return len(existing)


def _category_for_signal(signal: PreferenceSignal) -> str:
    """Classify a signal into a preference category based on context keywords."""
    context = signal.context.lower()
    if any(token in context for token in ("tool", "terminal", "shell", "command", "file", "path")):
        return "tool_usage"
    if signal.signal_type in {"style_feedback", "praise"}:
        return "communication_style"
    return "task_approach"


def _confidence_for_signal_count(count: int) -> float:
    """Map signal count to confidence score. More signals = higher confidence."""
    if count >= 10:
        return 0.9
    if count >= 5:
        return 0.7
    if count >= 3:
        return 0.5
    return 0.3


__all__ = ["InMemorySignalStore", "PreferenceInferenceEngine", "SignalStore"]
