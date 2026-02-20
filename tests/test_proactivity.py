from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from silas.core.stream import Stream
from silas.models.messages import ChannelMessage
from silas.models.proactivity import Suggestion, SuggestionProposal
from silas.models.work import WorkItemResult, WorkItemStatus
from silas.topics.calibrator import SimpleAutonomyCalibrator
from silas.topics.suggestions import SimpleSuggestionEngine

from tests.fakes import FakeAutonomyCalibrator, FakeSuggestionEngine, InMemoryChannel


def _msg(text: str, sender_id: str = "owner") -> ChannelMessage:
    return ChannelMessage(
        channel="web",
        sender_id=sender_id,
        text=text,
        timestamp=datetime.now(UTC),
    )


def _proposal(
    *,
    suggestion_id: str,
    text: str,
    confidence: float,
    source: str = "idle_heartbeat",
    category: str = "next_step",
) -> SuggestionProposal:
    now = datetime.now(UTC)
    return SuggestionProposal(
        id=suggestion_id,
        text=text,
        confidence=confidence,
        source=source,
        category=category,
        action_hint="Do it",
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    )


def test_suggestion_confidence_range() -> None:
    with pytest.raises(ValidationError):
        Suggestion(
            id="s1",
            text="Try this",
            confidence=-0.01,
            source="idle_heartbeat",
            category="next_step",
        )
    with pytest.raises(ValidationError):
        Suggestion(
            id="s2",
            text="Try this",
            confidence=1.01,
            source="idle_heartbeat",
            category="next_step",
        )


def test_suggestion_proposal_expiry_validation() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        SuggestionProposal(
            id="sp1",
            text="Follow up",
            confidence=0.7,
            source="idle_heartbeat",
            category="next_step",
            action_hint="Do it",
            created_at=now,
            expires_at=now,
        )


@pytest.mark.asyncio
async def test_simple_suggestion_engine_idle_filters_expired_and_handled() -> None:
    engine = SimpleSuggestionEngine(cooldown=timedelta(minutes=30))
    now = datetime.now(UTC)
    active = SuggestionProposal(
        id="active",
        text="active",
        confidence=0.7,
        source="idle_heartbeat",
        category="next_step",
        action_hint="Act",
        created_at=now - timedelta(minutes=10),
        expires_at=now + timedelta(minutes=10),
    )
    also_active = SuggestionProposal(
        id="also-active",
        text="also active",
        confidence=0.6,
        source="idle_heartbeat",
        category="next_step",
        action_hint="Act",
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=10),
    )
    expired = SuggestionProposal(
        id="expired",
        text="expired",
        confidence=0.6,
        source="idle_heartbeat",
        category="next_step",
        action_hint="Act",
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(minutes=1),
    )

    engine.queue_idle("owner", active)
    engine.queue_idle("owner", also_active)
    engine.queue_idle("owner", expired)
    await engine.mark_handled("owner", "active", "dismissed")

    generated = await engine.generate_idle("owner", now)
    assert [suggestion.id for suggestion in generated] == ["also-active"]


@pytest.mark.asyncio
async def test_simple_suggestion_engine_generate_post_execution() -> None:
    engine = SimpleSuggestionEngine()
    result = WorkItemResult(
        work_item_id="w1",
        status=WorkItemStatus.done,
        summary="done",
        next_steps=["Write tests", "Ship patch"],
    )

    generated = await engine.generate_post_execution("owner", result)
    assert len(generated) == 2
    assert all(suggestion.source == "post_execution" for suggestion in generated)
    assert all(suggestion.confidence == 0.75 for suggestion in generated)


@pytest.mark.asyncio
async def test_simple_autonomy_calibrator_emits_widen_and_tighten() -> None:
    now = datetime.now(UTC)

    widen_calibrator = SimpleAutonomyCalibrator(
        window_size=6,
        min_sample_size=5,
        widen_threshold=0.2,
        tighten_threshold=0.6,
    )
    for _ in range(5):
        await widen_calibrator.record_outcome("owner", "direct", "approved")
    widen = await widen_calibrator.evaluate("owner", now)
    assert widen
    assert widen[0]["direction"] == "widen"

    tighten_calibrator = SimpleAutonomyCalibrator(
        window_size=6,
        min_sample_size=5,
        widen_threshold=0.1,
        tighten_threshold=0.4,
    )
    for _ in range(3):
        await tighten_calibrator.record_outcome("owner", "direct", "declined")
    for _ in range(2):
        await tighten_calibrator.record_outcome("owner", "direct", "approved")
    tighten = await tighten_calibrator.evaluate("owner", now)
    assert tighten
    assert tighten[0]["direction"] == "tighten"


@pytest.mark.asyncio
async def test_simple_autonomy_calibrator_apply_approved_keeps_threshold() -> None:
    calibrator = SimpleAutonomyCalibrator(
        window_size=8,
        min_sample_size=5,
        widen_threshold=0.2,
        tighten_threshold=0.8,
        threshold_step=0.1,
    )
    for _ in range(5):
        await calibrator.record_outcome("owner", "direct", "approved")

    proposal = (await calibrator.evaluate("owner", datetime.now(UTC)))[0]
    result = await calibrator.apply(proposal, "approved")

    metrics = calibrator.get_metrics("owner")
    assert result["decision"] == "approved"
    assert result["rolled_back"] is False
    assert metrics["families"]["direct"]["threshold"] == 0.6


@pytest.mark.asyncio
async def test_simple_autonomy_calibrator_apply_rejected_rolls_back_threshold() -> None:
    calibrator = SimpleAutonomyCalibrator(
        window_size=8,
        min_sample_size=5,
        widen_threshold=0.2,
        tighten_threshold=0.8,
        threshold_step=0.1,
    )
    for _ in range(5):
        await calibrator.record_outcome("owner", "direct", "approved")

    proposal = (await calibrator.evaluate("owner", datetime.now(UTC)))[0]
    result = await calibrator.apply(proposal, "rejected")

    metrics = calibrator.get_metrics("owner")
    assert result["decision"] == "rejected"
    assert result["rolled_back"] is True
    assert metrics["families"]["direct"]["threshold"] == 0.5


@pytest.mark.asyncio
async def test_simple_autonomy_calibrator_apply_rejects_unknown_decision() -> None:
    calibrator = SimpleAutonomyCalibrator()
    proposal = {"scope_id": "owner", "action_family": "direct"}

    with pytest.raises(ValueError, match="approved"):
        await calibrator.apply(proposal, "later")


@pytest.mark.asyncio
async def test_stream_high_confidence_suggestion_prepended(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    suggestion = _proposal(
        suggestion_id="s-high",
        text="You can also ask me to create a checklist.",
        confidence=0.91,
    )
    engine = FakeSuggestionEngine(idle_by_scope={"owner": [suggestion]})
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        suggestion_engine=engine,
    )

    result = await stream._process_turn(_msg("hello"))

    assert result.startswith("Suggestion: You can also ask me to create a checklist.")
    assert channel.outgoing[0]["text"] == result
    assert channel.suggestion_cards == []


@pytest.mark.asyncio
async def test_stream_low_confidence_suggestion_sent_to_side_panel(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    low = _proposal(
        suggestion_id="s-low",
        text="Review open tasks",
        confidence=0.40,
    )
    engine = FakeSuggestionEngine(idle_by_scope={"owner": [low]})
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        suggestion_engine=engine,
    )

    result = await stream._process_turn(_msg("hello"))

    assert result == "echo: hello"
    assert len(channel.suggestion_cards) == 1
    assert channel.suggestion_cards[0]["suggestion"] == low


@pytest.mark.asyncio
async def test_stream_records_autonomy_outcome(
    channel: InMemoryChannel,
    turn_context,
) -> None:
    calibrator = FakeAutonomyCalibrator()
    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        autonomy_calibrator=calibrator,
    )

    await stream._process_turn(_msg("hello"))

    assert calibrator.record_calls == [("owner", "direct", "approved")]
