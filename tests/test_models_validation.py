from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from silas.models.agents import (
    AgentResponse,
    InteractionMode,
    InteractionRegister,
    MemoryQuery,
    MemoryQueryStrategy,
    RouteDecision,
)
from silas.models.approval import ApprovalScope, ApprovalToken, ApprovalVerdict
from silas.models.context import ContextProfile
from silas.models.work import Expectation


def test_agent_response_memory_query_limit() -> None:
    queries = [
        MemoryQuery(strategy=MemoryQueryStrategy.keyword, query=f"q{i}")
        for i in range(4)
    ]
    with pytest.raises(ValidationError):
        AgentResponse(message="hello", memory_queries=queries)


def test_context_profile_sum_validator() -> None:
    with pytest.raises(ValidationError):
        ContextProfile(
            name="too-big",
            chronicle_pct=0.5,
            memory_pct=0.2,
            workspace_pct=0.2,
        )


def test_expectation_mutual_exclusivity() -> None:
    with pytest.raises(ValidationError):
        Expectation(contains="ok", regex="ok")


def test_route_decision_requires_direct_response() -> None:
    with pytest.raises(ValidationError):
        RouteDecision(
            route="direct",
            reason="test",
            response=None,
            interaction_register=InteractionRegister.status,
            interaction_mode=InteractionMode.default_and_offer,
            context_profile="conversation",
        )


def test_approval_token_base64_roundtrip() -> None:
    token = ApprovalToken(
        token_id="tok-1",
        plan_hash="abc123",
        work_item_id="item-1",
        scope=ApprovalScope.full_plan,
        verdict=ApprovalVerdict.approved,
        signature=b"bytes-signature",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        nonce="n-1",
    )
    dumped = token.model_dump(mode="json")
    assert isinstance(dumped["signature"], str)
    loaded = ApprovalToken.model_validate(dumped)
    assert loaded.signature == b"bytes-signature"
