"""Deterministic personality engine for runtime style shaping."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from silas.models.messages import ChannelMessage
from silas.models.personality import (
    AxisProfile,
    MoodState,
    PersonaEvent,
    PersonaPreset,
    PersonaState,
    VoiceConfig,
)
from silas.persistence.persona_store import SQLitePersonaStore as PersonaStore

_AXES: tuple[str, ...] = (
    "warmth",
    "assertiveness",
    "verbosity",
    "formality",
    "humor",
    "initiative",
    "certainty",
)
_MOOD_FIELDS: tuple[str, ...] = ("energy", "patience", "curiosity", "frustration")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().replace("_", " ").replace("-", " ").split())


def _coerce_delta_map(raw: object) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        try:
            result[key] = float(str(value))
        except (TypeError, ValueError):
            continue
    return result


def _axis_profile_to_dict(axes: AxisProfile) -> dict[str, float]:
    return {axis: float(getattr(axes, axis)) for axis in _AXES}


def _mood_to_dict(mood: MoodState) -> dict[str, float]:
    return {field: float(getattr(mood, field)) for field in _MOOD_FIELDS}


def _axis_dict_to_profile(values: Mapping[str, float]) -> AxisProfile:
    payload = {axis: _clamp01(float(values.get(axis, 0.5))) for axis in _AXES}
    return AxisProfile.model_validate(payload)


def _dict_to_mood(values: Mapping[str, float]) -> MoodState:
    payload = {field: _clamp01(float(values.get(field, 0.5))) for field in _MOOD_FIELDS}
    return MoodState.model_validate(payload)


def _default_axes() -> AxisProfile:
    return AxisProfile(
        warmth=0.5,
        assertiveness=0.5,
        verbosity=0.5,
        formality=0.5,
        humor=0.5,
        initiative=0.5,
        certainty=0.5,
    )


def _default_mood() -> MoodState:
    return MoodState(energy=0.5, patience=0.5, curiosity=0.5, frustration=0.5)


def _default_voice() -> VoiceConfig:
    return VoiceConfig(tone="neutral", quirks=[], speech_patterns=[], anti_patterns=[])


def _mood_delta_to_axes(mood: MoodState) -> dict[str, float]:
    energy = mood.energy - 0.5
    patience = mood.patience - 0.5
    curiosity = mood.curiosity - 0.5
    frustration = mood.frustration - 0.5
    return {
        "warmth": 0.25 * patience - 0.30 * frustration,
        "assertiveness": 0.20 * energy + 0.35 * frustration - 0.15 * patience,
        "verbosity": 0.30 * energy + 0.25 * curiosity,
        "formality": -0.15 * energy + 0.10 * patience,
        "humor": 0.20 * energy + 0.20 * curiosity - 0.25 * frustration,
        "initiative": 0.35 * energy + 0.35 * curiosity,
        "certainty": 0.20 * energy + 0.15 * patience - 0.25 * frustration,
    }


def _axes_delta_to_mood_delta(delta: Mapping[str, float]) -> dict[str, float]:
    warmth = float(delta.get("warmth", 0.0))
    assertiveness = float(delta.get("assertiveness", 0.0))
    verbosity = float(delta.get("verbosity", 0.0))
    humor = float(delta.get("humor", 0.0))
    initiative = float(delta.get("initiative", 0.0))
    certainty = float(delta.get("certainty", 0.0))

    return {
        "energy": 0.45 * initiative + 0.20 * verbosity + 0.15 * assertiveness,
        "patience": 0.40 * warmth - 0.45 * assertiveness + 0.15 * certainty,
        "curiosity": 0.55 * initiative + 0.25 * humor,
        "frustration": 0.40 * assertiveness - 0.35 * warmth - 0.20 * certainty,
    }


def _describe_axis(low: str, mid: str, high: str, value: float) -> str:
    if value < 0.33:
        return low
    if value > 0.67:
        return high
    return mid


class SilasPersonalityEngine:
    def __init__(
        self,
        store: PersonaStore,
        presets: dict[str, PersonaPreset],
        context_registry: dict[str, dict[str, float]],
        decay_rate: float = 0.1,
        baseline_drift_limit: float = 0.05,
    ) -> None:
        self._store = store
        self._presets = presets
        self._context_registry = context_registry
        self._decay_rate = decay_rate
        self._baseline_drift_limit = baseline_drift_limit

    async def detect_context(self, message: ChannelMessage, route_hint: str | None = None) -> str:
        if route_hint:
            normalized_hint = _normalize(route_hint)
            for key in self._context_registry:
                normalized_key = _normalize(key)
                if normalized_hint == normalized_key or normalized_hint in normalized_key:
                    return key

        normalized_text = _normalize(message.text)
        for key in self._context_registry:
            normalized_key = _normalize(key)
            if not normalized_key:
                continue
            if normalized_key in normalized_text:
                return key
            words = normalized_key.split()
            if words and all(word in normalized_text for word in words):
                return key

        if "default" in self._context_registry:
            return "default"
        if self._context_registry:
            return next(iter(self._context_registry))
        return "default"

    async def get_effective_axes(self, scope_id: str, context_key: str) -> AxisProfile:
        state = await self._ensure_state(scope_id)
        baseline = _axis_profile_to_dict(state.baseline_axes)
        context_delta = _coerce_delta_map(self._context_registry.get(context_key, {}))
        mood_delta = _mood_delta_to_axes(state.mood)
        return _axis_dict_to_profile(
            {
                axis: _clamp01(
                    baseline[axis]
                    + float(context_delta.get(axis, 0.0))
                    + float(mood_delta.get(axis, 0.0))
                )
                for axis in _AXES
            }
        )

    async def render_directives(self, scope_id: str, context_key: str) -> str:
        state = await self._ensure_state(scope_id)
        effective = await self.get_effective_axes(scope_id, context_key)

        warmth_line = _describe_axis(
            "Keep empathy understated and rely on crisp factual framing.",
            "Balance empathy with objectivity so tone stays steady and practical.",
            "Lead with warmth and explicit support before moving into execution details.",
            effective.warmth,
        )
        assertive_line = _describe_axis(
            "Offer options gently and avoid forceful language unless risk requires it.",
            "Be direct about tradeoffs while leaving room for user preference.",
            "Use confident recommendations and clear next actions with minimal hedging.",
            effective.assertiveness,
        )
        verbose_line = _describe_axis(
            "Keep responses compact, with only the most decision-relevant details.",
            "Use medium detail: brief rationale, concrete steps, and explicit assumptions.",
            "Provide rich detail, alternatives, and explicit reasoning paths when useful.",
            effective.verbosity,
        )
        formal_line = _describe_axis(
            "Use a conversational style that stays plain, grounded, and efficient.",
            "Use professional wording with approachable phrasing and clear structure.",
            "Maintain high professional polish and precise language throughout.",
            effective.formality,
        )
        humor_line = _describe_axis(
            "Avoid jokes and keep language dry unless the user explicitly asks for levity.",
            "Allow light, subtle levity only when it does not dilute clarity.",
            "Use gentle playfulness sparingly to reduce friction while staying useful.",
            effective.humor,
        )
        initiative_line = _describe_axis(
            "Stay mostly reactive; avoid extra work unless directly requested.",
            "Propose focused next steps when uncertainty or risk appears.",
            "Take proactive ownership: suggest checks, safeguards, and follow-through actions.",
            effective.initiative,
        )
        certainty_line = _describe_axis(
            "Use careful language with explicit uncertainty and verification cues.",
            "Use moderate confidence and call out assumptions that affect outcomes.",
            "State conclusions clearly and decisively when evidence is strong.",
            effective.certainty,
        )

        quirks = ", ".join(state.voice.quirks) if state.voice.quirks else "none"
        speech_patterns = (
            ", ".join(state.voice.speech_patterns) if state.voice.speech_patterns else "none"
        )
        anti_patterns = (
            ", ".join(state.voice.anti_patterns) if state.voice.anti_patterns else "none"
        )

        directives = "\n".join(
            [
                f"You are Silas. Use a {state.voice.tone} voice and keep behavior deterministic for this turn.",
                "Honor system policy, approval controls, and safety constraints before any style preference.",
                "Prioritize technical correctness, concrete actions, and transparent assumptions over rhetoric.",
                f"{warmth_line}",
                f"{assertive_line}",
                f"{verbose_line}",
                f"{formal_line}",
                f"{humor_line}",
                f"{initiative_line}",
                f"{certainty_line}",
                "When context is incomplete, ask one focused clarifying question or state the best assumption.",
                "When giving recommendations, include practical tradeoffs and likely failure modes.",
                "When executing tasks, narrate progress in concise checkpoints with clear completion state.",
                "When blocked, state the blocker, attempted path, and the smallest next unblock step.",
                "Prefer explicit file paths, commands, and decision criteria over abstract language.",
                "Avoid repetitive filler, avoid self-referential commentary, and avoid overconfident claims.",
                f"Voice quirks to preserve when natural: {quirks}.",
                f"Preferred speech patterns: {speech_patterns}.",
                f"Anti-patterns to avoid: {anti_patterns}.",
                "Keep outputs readable for operators: short sections, stable wording, and actionable next moves.",
            ]
        )

        now = _utc_now()
        updated_state = state.model_copy(
            update={"last_context": context_key, "updated_at": now},
            deep=True,
        )
        await self._store.save_state(updated_state)
        return directives

    async def apply_event(
        self,
        scope_id: str,
        event_type: str,
        trusted: bool,
        source: str,
        metadata: dict[str, object] | None = None,
    ) -> PersonaState:
        state = await self._ensure_state(scope_id)
        metadata = metadata or {}
        delta_axes = _coerce_delta_map(metadata.get("delta_axes"))
        delta_mood = _coerce_delta_map(metadata.get("delta_mood"))

        baseline = _axis_profile_to_dict(state.baseline_axes)
        mood = _mood_to_dict(state.mood)

        if trusted:
            for axis, delta_value in delta_axes.items():
                if axis not in baseline:
                    continue
                baseline[axis] = _clamp01(baseline[axis] + delta_value)
        else:
            for mood_key, mood_delta in _axes_delta_to_mood_delta(delta_axes).items():
                mood[mood_key] = _clamp01(mood[mood_key] + mood_delta)

        for mood_key, mood_delta in delta_mood.items():
            if mood_key not in mood:
                continue
            mood[mood_key] = _clamp01(mood[mood_key] + mood_delta)

        now = _utc_now()
        next_state = state.model_copy(
            update={
                "baseline_axes": _axis_dict_to_profile(baseline),
                "mood": _dict_to_mood(mood),
                "updated_at": now,
            },
            deep=True,
        )

        created_at = metadata.get("created_at")
        if not isinstance(created_at, datetime):
            created_at = now
        elif created_at.tzinfo is None or created_at.tzinfo.utcoffset(created_at) is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)

        event_id = metadata.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            event_id = f"persona:{scope_id}:{uuid.uuid4().hex}"

        event = PersonaEvent(
            event_id=event_id,
            scope_id=scope_id,
            event_type=event_type,
            trusted=trusted,
            delta_axes=delta_axes,
            delta_mood=delta_mood,
            source=source,
            created_at=created_at,
        )
        await self._store.append_event(event)
        await self._store.save_state(next_state)
        return next_state

    async def decay(self, scope_id: str, now: datetime) -> PersonaState:
        state = await self._ensure_state(scope_id)

        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise ValueError("now must be timezone-aware")
        now_utc = now.astimezone(UTC)
        elapsed_minutes = max((now_utc - state.updated_at).total_seconds() / 60.0, 0.0)
        step = self._decay_rate * elapsed_minutes

        mood = _mood_to_dict(state.mood)
        for field in _MOOD_FIELDS:
            current = mood[field]
            if current > 0.5:
                mood[field] = _clamp01(max(0.5, current - step))
            elif current < 0.5:
                mood[field] = _clamp01(min(0.5, current + step))
            else:
                mood[field] = current

        next_state = state.model_copy(
            update={"mood": _dict_to_mood(mood), "updated_at": now_utc},
            deep=True,
        )
        await self._store.save_state(next_state)
        return next_state

    async def set_preset(self, scope_id: str, preset_name: str) -> PersonaState:
        preset = self._presets.get(preset_name)
        if preset is None:
            raise ValueError(f"unknown preset: {preset_name}")

        state = await self._ensure_state(scope_id)
        now = _utc_now()
        next_state = state.model_copy(
            update={
                "baseline_axes": preset.axes.model_copy(deep=True),
                "voice": preset.voice.model_copy(deep=True),
                "active_preset": preset.name,
                "updated_at": now,
            },
            deep=True,
        )
        await self._store.save_state(next_state)
        return next_state

    async def adjust_axes(
        self,
        scope_id: str,
        delta: dict[str, float],
        trusted: bool,
        persist_to_baseline: bool = False,
    ) -> PersonaState:
        state = await self._ensure_state(scope_id)
        baseline = _axis_profile_to_dict(state.baseline_axes)
        mood = _mood_to_dict(state.mood)

        bounded_delta: dict[str, float] = {}
        for axis, axis_delta in delta.items():
            if axis not in baseline:
                continue
            if trusted:
                bounded_delta[axis] = float(axis_delta)
            else:
                bounded_delta[axis] = max(
                    -self._baseline_drift_limit,
                    min(self._baseline_drift_limit, float(axis_delta)),
                )

        if persist_to_baseline and trusted:
            for axis, axis_delta in bounded_delta.items():
                baseline[axis] = _clamp01(baseline[axis] + axis_delta)
        else:
            mood_delta = _axes_delta_to_mood_delta(bounded_delta)
            for field, field_delta in mood_delta.items():
                mood[field] = _clamp01(mood[field] + field_delta)

        now = _utc_now()
        next_state = state.model_copy(
            update={
                "baseline_axes": _axis_dict_to_profile(baseline),
                "mood": _dict_to_mood(mood),
                "updated_at": now,
            },
            deep=True,
        )
        await self._store.save_state(next_state)
        return next_state

    async def _ensure_state(self, scope_id: str) -> PersonaState:
        state = await self._store.get_state(scope_id)
        if state is not None:
            return state

        if "default" in self._presets:
            preset_name = "default"
            preset = self._presets["default"]
        elif self._presets:
            preset_name, preset = next(iter(self._presets.items()))
        else:
            preset_name = "default"
            preset = PersonaPreset(name="default", axes=_default_axes(), voice=_default_voice())

        created = PersonaState(
            scope_id=scope_id,
            baseline_axes=preset.axes.model_copy(deep=True),
            mood=_default_mood(),
            active_preset=preset_name,
            voice=preset.voice.model_copy(deep=True),
            last_context="",
            updated_at=_utc_now(),
        )
        await self._store.save_state(created)
        return created


__all__ = ["SilasPersonalityEngine"]
