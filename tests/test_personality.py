from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from silas.context.personality import SilasPersonalityEngine
from silas.models.messages import ChannelMessage
from silas.models.personality import (
    AxisProfile,
    MoodState,
    PersonaEvent,
    PersonaPreset,
    PersonaState,
    VoiceConfig,
)
from silas.persistence.migrations import run_migrations
from silas.persistence.persona_store import SQLitePersonaStore


def _now() -> datetime:
    return datetime.now(UTC)


def _axis_profile(
    *,
    warmth: float = 0.5,
    assertiveness: float = 0.5,
    verbosity: float = 0.5,
    formality: float = 0.5,
    humor: float = 0.5,
    initiative: float = 0.5,
    certainty: float = 0.5,
) -> AxisProfile:
    return AxisProfile(
        warmth=warmth,
        assertiveness=assertiveness,
        verbosity=verbosity,
        formality=formality,
        humor=humor,
        initiative=initiative,
        certainty=certainty,
    )


def _mood(
    *,
    energy: float = 0.5,
    patience: float = 0.5,
    curiosity: float = 0.5,
    frustration: float = 0.5,
) -> MoodState:
    return MoodState(
        energy=energy,
        patience=patience,
        curiosity=curiosity,
        frustration=frustration,
    )


def _voice(tone: str = "steady") -> VoiceConfig:
    return VoiceConfig(
        tone=tone,
        quirks=["use concrete examples"],
        speech_patterns=["lead with action", "state assumptions"],
        anti_patterns=["vague filler", "unsupported certainty"],
    )


def _state(scope_id: str = "owner") -> PersonaState:
    return PersonaState(
        scope_id=scope_id,
        baseline_axes=_axis_profile(),
        mood=_mood(),
        active_preset="default",
        voice=_voice(),
        last_context="",
        updated_at=_now(),
    )


class TestPersonalityModels:
    def test_axis_profile_clamps(self) -> None:
        axes = AxisProfile(
            warmth=-0.5,
            assertiveness=1.7,
            verbosity=0.3,
            formality=2.2,
            humor=-1.0,
            initiative=0.8,
            certainty=10.0,
        )
        assert axes.warmth == 0.0
        assert axes.assertiveness == 1.0
        assert axes.verbosity == 0.3
        assert axes.formality == 1.0
        assert axes.humor == 0.0
        assert axes.initiative == 0.8
        assert axes.certainty == 1.0

    def test_mood_state_clamps(self) -> None:
        mood = MoodState(energy=-1.0, patience=2.0, curiosity=0.4, frustration=4.0)
        assert mood.energy == 0.0
        assert mood.patience == 1.0
        assert mood.curiosity == 0.4
        assert mood.frustration == 1.0

    def test_persona_state_serialization_roundtrip(self) -> None:
        src = PersonaState(
            scope_id="scope-x",
            baseline_axes=_axis_profile(warmth=0.2, assertiveness=0.8),
            mood=_mood(energy=0.7, frustration=0.2),
            active_preset="work",
            voice=_voice("professional"),
            last_context="code review",
            updated_at=datetime(2026, 1, 2, 10, 0, 0, tzinfo=timezone(timedelta(hours=-5))),
        )
        encoded = src.model_dump_json()
        loaded = PersonaState.model_validate_json(encoded)
        assert loaded.scope_id == "scope-x"
        assert loaded.active_preset == "work"
        assert loaded.voice.tone == "professional"
        assert loaded.last_context == "code review"
        assert loaded.updated_at.tzinfo is not None

    def test_persona_event_requires_timezone_aware_datetime(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            PersonaEvent(
                event_id="evt-1",
                scope_id="owner",
                event_type="feedback",
                trusted=True,
                source="runtime",
                created_at=datetime(2026, 1, 1, 12, 0, 0),
            )


@pytest.mark.asyncio
class TestSQLitePersonaStore:
    async def _store(self, tmp_path: Path) -> SQLitePersonaStore:
        db_path = tmp_path / "persona.db"
        await run_migrations(str(db_path))
        return SQLitePersonaStore(str(db_path))

    async def test_save_and_get_state(self, tmp_path: Path) -> None:
        store = await self._store(tmp_path)
        state = _state("scope-a")
        await store.save_state(state)

        loaded = await store.get_state("scope-a")
        assert loaded is not None
        assert loaded.scope_id == "scope-a"
        assert loaded.baseline_axes.warmth == state.baseline_axes.warmth
        assert loaded.mood.energy == state.mood.energy
        assert loaded.voice.tone == state.voice.tone

    async def test_get_state_returns_none_when_missing(self, tmp_path: Path) -> None:
        store = await self._store(tmp_path)
        assert await store.get_state("missing-scope") is None

    async def test_append_and_list_events(self, tmp_path: Path) -> None:
        store = await self._store(tmp_path)
        now = _now()
        await store.append_event(
            PersonaEvent(
                event_id="evt-1",
                scope_id="scope-b",
                event_type="compliment",
                trusted=True,
                delta_axes={"warmth": 0.1},
                source="owner_feedback",
                created_at=now - timedelta(minutes=2),
            )
        )
        await store.append_event(
            PersonaEvent(
                event_id="evt-2",
                scope_id="scope-b",
                event_type="blocked",
                trusted=False,
                delta_mood={"frustration": 0.2},
                source="runtime",
                created_at=now - timedelta(minutes=1),
            )
        )

        events = await store.list_events("scope-b", limit=10)
        assert [event.event_id for event in events] == ["evt-2", "evt-1"]

    async def test_append_event_duplicate_is_ignored(self, tmp_path: Path) -> None:
        store = await self._store(tmp_path)
        event = PersonaEvent(
            event_id="evt-dup",
            scope_id="scope-c",
            event_type="feedback",
            trusted=True,
            delta_axes={"warmth": 0.1},
            source="owner_feedback",
            created_at=_now(),
        )
        await store.append_event(event)
        await store.append_event(event)
        events = await store.list_events("scope-c", limit=10)
        assert len(events) == 1
        assert events[0].event_id == "evt-dup"


@dataclass(slots=True)
class InMemoryPersonaStore:
    states: dict[str, PersonaState] = field(default_factory=dict)
    events: list[PersonaEvent] = field(default_factory=list)

    async def get_state(self, scope_id: str) -> PersonaState | None:
        state = self.states.get(scope_id)
        return state.model_copy(deep=True) if state else None

    async def save_state(self, state: PersonaState) -> None:
        self.states[state.scope_id] = state.model_copy(deep=True)

    async def append_event(self, event: PersonaEvent) -> None:
        self.events.append(event.model_copy(deep=True))

    async def list_events(self, scope_id: str, limit: int = 100) -> list[PersonaEvent]:
        filtered = [event for event in self.events if event.scope_id == scope_id]
        filtered.sort(key=lambda item: (item.created_at, item.event_id), reverse=True)
        return [event.model_copy(deep=True) for event in filtered[:limit]]


@pytest.mark.asyncio
class TestSilasPersonalityEngine:
    def _presets(self) -> dict[str, PersonaPreset]:
        return {
            "default": PersonaPreset(
                name="default",
                axes=_axis_profile(),
                voice=VoiceConfig(
                    tone="balanced",
                    quirks=["explicit assumptions"],
                    speech_patterns=["state a plan before details"],
                    anti_patterns=["wordy preambles"],
                ),
            ),
            "review": PersonaPreset(
                name="review",
                axes=_axis_profile(assertiveness=0.75, formality=0.8, certainty=0.7),
                voice=VoiceConfig(
                    tone="analytical",
                    quirks=["point out risk first"],
                    speech_patterns=["findings first"],
                    anti_patterns=["soft conclusions"],
                ),
            ),
        }

    def _contexts(self) -> dict[str, dict[str, float]]:
        return {
            "default": {},
            "code review": {"assertiveness": 0.15, "verbosity": 0.1, "certainty": 0.1},
            "incident response": {"assertiveness": 0.2, "formality": 0.2, "warmth": -0.1},
        }

    async def test_detect_context_prefers_route_hint(self) -> None:
        store = InMemoryPersonaStore()
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        message = ChannelMessage(channel="web", sender_id="owner", text="please review this code")
        context = await engine.detect_context(message, route_hint="incident response")
        assert context == "incident response"

    async def test_detect_context_uses_keyword_matching(self) -> None:
        store = InMemoryPersonaStore()
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        message = ChannelMessage(
            channel="web",
            sender_id="owner",
            text="Can you do a code review on this patch?",
        )
        context = await engine.detect_context(message)
        assert context == "code review"

    async def test_detect_context_falls_back_to_default(self) -> None:
        store = InMemoryPersonaStore()
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        message = ChannelMessage(
            channel="web",
            sender_id="owner",
            text="hello there",
        )
        context = await engine.detect_context(message)
        assert context == "default"

    async def test_get_effective_axes_composes_baseline_and_context(self) -> None:
        store = InMemoryPersonaStore(
            states={
                "owner": PersonaState(
                    scope_id="owner",
                    baseline_axes=_axis_profile(warmth=0.4, assertiveness=0.6, verbosity=0.5),
                    mood=_mood(),
                    active_preset="default",
                    voice=_voice(),
                    last_context="",
                    updated_at=_now(),
                )
            }
        )
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        axes = await engine.get_effective_axes("owner", "code review")
        assert axes.warmth == pytest.approx(0.4)
        assert axes.assertiveness == pytest.approx(0.75)
        assert axes.verbosity == pytest.approx(0.6)
        assert axes.certainty == pytest.approx(0.6)

    async def test_get_effective_axes_includes_mood_delta(self) -> None:
        store = InMemoryPersonaStore(
            states={
                "owner": PersonaState(
                    scope_id="owner",
                    baseline_axes=_axis_profile(),
                    mood=_mood(energy=0.9, patience=0.6, curiosity=0.8, frustration=0.1),
                    active_preset="default",
                    voice=_voice(),
                    last_context="",
                    updated_at=_now(),
                )
            }
        )
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        axes = await engine.get_effective_axes("owner", "default")
        assert axes.initiative > 0.5
        assert axes.verbosity > 0.5
        assert axes.warmth > 0.5

    async def test_render_directives_includes_voice_and_updates_context(self) -> None:
        store = InMemoryPersonaStore()
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        directives = await engine.render_directives("owner", "code review")
        word_count = len(directives.split())

        assert "balanced" in directives
        assert "Voice quirks" in directives
        assert "Anti-patterns to avoid" in directives
        assert 150 <= word_count <= 500

        state = await store.get_state("owner")
        assert state is not None
        assert state.last_context == "code review"

    async def test_apply_event_trusted_updates_baseline_and_persists_event(self) -> None:
        store = InMemoryPersonaStore(
            states={"owner": _state("owner")},
        )
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        updated = await engine.apply_event(
            "owner",
            event_type="owner_feedback",
            trusted=True,
            source="owner",
            metadata={"delta_axes": {"assertiveness": 0.2}},
        )
        assert updated.baseline_axes.assertiveness == pytest.approx(0.7)
        assert len(store.events) == 1
        assert store.events[0].trusted is True

    async def test_apply_event_untrusted_updates_mood_not_baseline(self) -> None:
        state = _state("owner")
        store = InMemoryPersonaStore(states={"owner": state})
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        updated = await engine.apply_event(
            "owner",
            event_type="channel_feedback",
            trusted=False,
            source="channel",
            metadata={"delta_axes": {"assertiveness": 0.4}},
        )
        assert updated.baseline_axes.assertiveness == pytest.approx(
            state.baseline_axes.assertiveness
        )
        assert updated.mood.frustration > state.mood.frustration
        assert len(store.events) == 1
        assert store.events[0].trusted is False

    async def test_decay_moves_mood_toward_neutral(self) -> None:
        state = PersonaState(
            scope_id="owner",
            baseline_axes=_axis_profile(),
            mood=_mood(energy=0.9, patience=0.1, curiosity=0.7, frustration=0.8),
            active_preset="default",
            voice=_voice(),
            last_context="",
            updated_at=_now() - timedelta(minutes=5),
        )
        store = InMemoryPersonaStore(states={"owner": state})
        engine = SilasPersonalityEngine(
            store,
            self._presets(),
            self._contexts(),
            decay_rate=0.04,
        )
        decayed = await engine.decay("owner", _now())
        assert decayed.mood.energy == pytest.approx(0.7)
        assert decayed.mood.patience == pytest.approx(0.3)
        assert decayed.mood.curiosity == pytest.approx(0.5)
        assert decayed.mood.frustration == pytest.approx(0.6)

    async def test_set_preset_updates_baseline_and_voice(self) -> None:
        store = InMemoryPersonaStore(states={"owner": _state("owner")})
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        state = await engine.set_preset("owner", "review")
        assert state.active_preset == "review"
        assert state.baseline_axes.assertiveness == pytest.approx(0.75)
        assert state.voice.tone == "analytical"

    async def test_adjust_axes_trusted_persist_updates_baseline(self) -> None:
        store = InMemoryPersonaStore(states={"owner": _state("owner")})
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        state = await engine.adjust_axes(
            "owner",
            delta={"warmth": 0.2, "certainty": -0.1},
            trusted=True,
            persist_to_baseline=True,
        )
        assert state.baseline_axes.warmth == pytest.approx(0.7)
        assert state.baseline_axes.certainty == pytest.approx(0.4)
        assert state.mood.energy == pytest.approx(0.5)

    async def test_adjust_axes_untrusted_uses_drift_limit_on_mood(self) -> None:
        store = InMemoryPersonaStore(states={"owner": _state("owner")})
        engine = SilasPersonalityEngine(
            store,
            self._presets(),
            self._contexts(),
            baseline_drift_limit=0.05,
        )
        state = await engine.adjust_axes(
            "owner",
            delta={"assertiveness": 1.0},
            trusted=False,
            persist_to_baseline=False,
        )
        assert state.baseline_axes.assertiveness == pytest.approx(0.5)
        assert state.mood.energy == pytest.approx(0.5075)
        assert state.mood.patience == pytest.approx(0.4775)
        assert state.mood.frustration == pytest.approx(0.52)

    async def test_set_preset_raises_for_unknown_preset(self) -> None:
        store = InMemoryPersonaStore()
        engine = SilasPersonalityEngine(store, self._presets(), self._contexts())
        with pytest.raises(ValueError, match="unknown preset"):
            await engine.set_preset("owner", "nonexistent")


__all__: list[str] = []
