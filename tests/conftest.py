from __future__ import annotations

import pytest
from silas.core.turn_context import TurnContext
from silas.models.agents import RouteDecision

from tests.fakes import (
    FakeModel,
    InMemoryAuditLog,
    InMemoryChannel,
    InMemoryContextManager,
    InMemoryMemoryStore,
)


@pytest.fixture(autouse=True)
def configure_route_profiles() -> None:
    RouteDecision.configure_profiles({"conversation", "coding", "research", "support", "planning"})


@pytest.fixture
def context_manager() -> InMemoryContextManager:
    return InMemoryContextManager()


@pytest.fixture
def memory_store() -> InMemoryMemoryStore:
    return InMemoryMemoryStore()


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def test_model() -> FakeModel:
    return FakeModel()


@pytest.fixture
def channel() -> InMemoryChannel:
    return InMemoryChannel()


@pytest.fixture
def turn_context(
    context_manager: InMemoryContextManager,
    memory_store: InMemoryMemoryStore,
    audit_log: InMemoryAuditLog,
    test_model: FakeModel,
) -> TurnContext:
    return TurnContext(
        scope_id="owner",
        context_manager=context_manager,
        memory_store=memory_store,
        proxy=test_model,
        audit=audit_log,
    )
