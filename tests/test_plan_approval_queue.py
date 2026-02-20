"""Tests for #218: plan/approval flow wired into queue path.

Verifies that ProxyConsumer._handle_plan_result():
- checks standing approvals before requesting manual approval
- auto-dispatches execution_request when standing approval covers the plan
- sends manual approval request when no standing approval exists
- emits plan_approval message on decline
- handles empty/invalid plan markdown gracefully
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from silas.models.approval import (
    ApprovalDecision,
    ApprovalScope,
    ApprovalToken,
    ApprovalVerdict,
)
from silas.models.work import WorkItem
from silas.execution.consumers import ProxyConsumer
from silas.execution.router import QueueRouter
from silas.execution.queue_store import DurableQueueStore
from silas.execution.queue_types import QueueMessage

# ── Helpers ──────────────────────────────────────────────────────────

VALID_PLAN_MD = (
    "---\nid: wi-218\ntitle: Deploy service\ntype: task\n---\nRun deploy script on staging."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _make_standing_token(work_item: WorkItem) -> ApprovalToken:
    now = _utc_now()
    return ApprovalToken(
        token_id=f"standing:{work_item.id}",
        plan_hash=work_item.plan_hash(),
        work_item_id=work_item.id,
        scope=ApprovalScope.standing,
        verdict=ApprovalVerdict.approved,
        signature=b"test-sig",
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
        nonce=f"nonce:{work_item.id}",
        conditions={"spawn_policy_hash": work_item.plan_hash()},
    )


# ── Mock Agents / Channels ──────────────────────────────────────────


@dataclass
class _MockProxyOutput:
    route: str = "direct"
    reason: str = "mock"
    response: object = None
    message: str = ""


@dataclass
class _MockProxyResult:
    output: _MockProxyOutput


class _MockProxyAgent:
    async def run(self, prompt: str, deps: object | None = None) -> _MockProxyResult:
        return _MockProxyResult(output=_MockProxyOutput())


class _ApprovingChannel:
    """Channel that always approves manual approval requests."""

    def __init__(self) -> None:
        self.approval_requests: list[WorkItem] = []

    async def send_approval_request(
        self, recipient_id: str, work_item: WorkItem
    ) -> ApprovalDecision:
        self.approval_requests.append(work_item)
        return ApprovalDecision(verdict=ApprovalVerdict.approved)


class _DecliningChannel:
    """Channel that always declines manual approval requests."""

    def __init__(self) -> None:
        self.approval_requests: list[WorkItem] = []

    async def send_approval_request(
        self, recipient_id: str, work_item: WorkItem
    ) -> ApprovalDecision:
        self.approval_requests.append(work_item)
        return ApprovalDecision(verdict=ApprovalVerdict.declined)


class _NoApprovalChannel:
    """Channel with no send_approval_request method."""


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def store() -> DurableQueueStore:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        s = DurableQueueStore(tmp.name)
    await s.initialize()
    return s


@pytest.fixture
def router(store: DurableQueueStore) -> QueueRouter:
    return QueueRouter(store)


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_standing_approval_auto_dispatches(
    store: DurableQueueStore, router: QueueRouter
) -> None:
    """When a standing approval covers the work item, execution_request is
    produced without manual approval."""

    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        standing_approval_resolver=_make_standing_token,
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-standing",
        payload={"plan_markdown": VALID_PLAN_MD, "metadata": {}},
    )

    result = await consumer._handle_plan_result(msg)

    assert result is not None
    assert result.message_kind == "execution_request"
    assert result.payload["work_item_id"] == "wi-218"
    assert result.payload["title"] == "Deploy service"


@pytest.mark.asyncio
async def test_manual_approval_fallback(store: DurableQueueStore, router: QueueRouter) -> None:
    """When no standing approval exists, manual approval via channel is used."""

    channel = _ApprovingChannel()
    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        channel=channel,
        standing_approval_resolver=None,  # no standing approvals
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-manual",
        payload={"plan_markdown": VALID_PLAN_MD, "metadata": {}},
    )

    result = await consumer._handle_plan_result(msg)

    assert result is not None
    assert result.message_kind == "execution_request"
    assert len(channel.approval_requests) == 1
    assert channel.approval_requests[0].id == "wi-218"


@pytest.mark.asyncio
async def test_declined_approval_emits_plan_approval(
    store: DurableQueueStore, router: QueueRouter
) -> None:
    """When manual approval is declined, a plan_approval message with
    declined verdict is returned."""

    channel = _DecliningChannel()
    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        channel=channel,
        standing_approval_resolver=None,
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-declined",
        payload={"plan_markdown": VALID_PLAN_MD, "metadata": {}},
    )

    result = await consumer._handle_plan_result(msg)

    assert result is not None
    assert result.message_kind == "plan_approval"
    assert result.payload["verdict"] == "declined"
    assert result.payload["work_item_id"] == "wi-218"


@pytest.mark.asyncio
async def test_no_channel_no_standing_returns_decline(
    store: DurableQueueStore, router: QueueRouter
) -> None:
    """When neither standing approval nor channel is available, decline."""

    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        channel=None,
        standing_approval_resolver=None,
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-none",
        payload={"plan_markdown": VALID_PLAN_MD, "metadata": {}},
    )

    result = await consumer._handle_plan_result(msg)

    assert result is not None
    assert result.message_kind == "plan_approval"
    assert result.payload["verdict"] == "declined"


@pytest.mark.asyncio
async def test_empty_plan_markdown_returns_none(
    store: DurableQueueStore, router: QueueRouter
) -> None:
    """Empty plan_markdown is silently dropped."""

    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        standing_approval_resolver=_make_standing_token,
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-empty",
        payload={"plan_markdown": "", "metadata": {}},
    )

    result = await consumer._handle_plan_result(msg)
    assert result is None


@pytest.mark.asyncio
async def test_invalid_plan_markdown_returns_none(
    store: DurableQueueStore, router: QueueRouter
) -> None:
    """Invalid plan markdown (no front matter) is silently dropped."""

    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        standing_approval_resolver=_make_standing_token,
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-invalid",
        payload={"plan_markdown": "no front matter here", "metadata": {}},
    )

    result = await consumer._handle_plan_result(msg)
    assert result is None


@pytest.mark.asyncio
async def test_channel_without_approval_method_declines(
    store: DurableQueueStore, router: QueueRouter
) -> None:
    """Channel that lacks send_approval_request results in decline."""

    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        channel=_NoApprovalChannel(),
        standing_approval_resolver=None,
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-no-method",
        payload={"plan_markdown": VALID_PLAN_MD, "metadata": {}},
    )

    result = await consumer._handle_plan_result(msg)

    assert result is not None
    assert result.message_kind == "plan_approval"
    assert result.payload["verdict"] == "declined"


@pytest.mark.asyncio
async def test_standing_approval_skips_channel(
    store: DurableQueueStore, router: QueueRouter
) -> None:
    """When standing approval resolves, channel is never consulted."""

    channel = _ApprovingChannel()
    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        channel=channel,
        standing_approval_resolver=_make_standing_token,
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-skip",
        payload={"plan_markdown": VALID_PLAN_MD, "metadata": {}},
    )

    result = await consumer._handle_plan_result(msg)

    assert result is not None
    assert result.message_kind == "execution_request"
    # Channel should NOT have been called
    assert len(channel.approval_requests) == 0


@pytest.mark.asyncio
async def test_executor_tool_allowlist_propagated(
    store: DurableQueueStore, router: QueueRouter
) -> None:
    """Executor tool allowlist from metadata is carried to execution_request."""

    consumer = ProxyConsumer(
        store,
        router,
        _MockProxyAgent(),
        standing_approval_resolver=_make_standing_token,
    )

    msg = QueueMessage(
        message_kind="plan_result",
        sender="planner",
        trace_id="trace-218-allowlist",
        payload={
            "plan_markdown": VALID_PLAN_MD,
            "metadata": {"executor_tool_allowlist": ["tool_a", "tool_b"]},
        },
    )

    result = await consumer._handle_plan_result(msg)

    assert result is not None
    assert result.tool_allowlist == ["tool_a", "tool_b"]
