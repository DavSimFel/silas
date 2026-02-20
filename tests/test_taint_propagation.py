"""Tests for taint propagation through tool call chains (INV-05)."""

from __future__ import annotations

from contextvars import copy_context

from silas.models.messages import TaintLevel
from silas.gates.taint import TaintTracker


class TestTaintEscalation:
    """Verify taint ratchets upward through tool chains."""

    def test_owner_input_external_tool_produces_external(self) -> None:
        """External tool taints output even when input is owner-trusted."""
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_input(TaintLevel.owner)
        result = tracker.on_tool_output("web_search")
        assert result == TaintLevel.external

    def test_owner_input_owner_tool_stays_owner(self) -> None:
        """Internal tools don't escalate taint when input is clean."""
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_input(TaintLevel.owner)
        result = tracker.on_tool_output("memory_store")
        assert result == TaintLevel.owner

    def test_auth_plus_external_yields_external(self) -> None:
        """Max propagation: auth input + external tool = external."""
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_input(TaintLevel.auth)
        result = tracker.on_tool_output("web_fetch")
        assert result == TaintLevel.external

    def test_external_input_stays_external_with_owner_tool(self) -> None:
        """Once tainted external, even internal tools can't lower it."""
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_input(TaintLevel.external)
        result = tracker.on_tool_output("memory_store")
        assert result == TaintLevel.external

    def test_auth_input_auth_tool_stays_auth(self) -> None:
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_input(TaintLevel.auth)
        result = tracker.on_tool_output("notion_read")
        assert result == TaintLevel.auth

    def test_owner_input_auth_tool_yields_auth(self) -> None:
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_input(TaintLevel.owner)
        result = tracker.on_tool_output("calendar_read")
        assert result == TaintLevel.auth

    def test_chained_tools_accumulate_taint(self) -> None:
        """Multi-step chain: owner -> auth tool -> external tool = external."""
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_input(TaintLevel.owner)
        mid = tracker.on_tool_output("notion_read")
        assert mid == TaintLevel.auth
        final = tracker.on_tool_output("web_search")
        assert final == TaintLevel.external


class TestTaintReset:
    """Verify reset isolates turns from each other."""

    def test_reset_clears_taint(self) -> None:
        tracker = TaintTracker()
        tracker.on_tool_input(TaintLevel.external)
        assert tracker.get_current_taint() == TaintLevel.external
        tracker.reset()
        assert tracker.get_current_taint() == TaintLevel.owner

    def test_reset_between_turns(self) -> None:
        """Simulates two turns — taint from turn 1 must not leak into turn 2."""
        tracker = TaintTracker()

        # Turn 1: external taint
        tracker.reset()
        tracker.on_tool_input(TaintLevel.external)
        assert tracker.get_current_taint() == TaintLevel.external

        # Turn 2: clean start
        tracker.reset()
        assert tracker.get_current_taint() == TaintLevel.owner
        result = tracker.on_tool_output("memory_store")
        assert result == TaintLevel.owner


class TestConcurrentContextIsolation:
    """Verify contextvars isolation across concurrent execution contexts."""

    def test_separate_contexts_isolated(self) -> None:
        """Two copy_context() runs must not see each other's taint state.

        Uses contextvars.copy_context() which is how asyncio.create_task()
        isolates state in production — each task inherits a snapshot of the
        parent context at creation time, then mutates independently.
        """
        results: dict[str, TaintLevel] = {}

        def run_a() -> None:
            tracker = TaintTracker()
            tracker.reset()
            tracker.on_tool_input(TaintLevel.external)
            results["a"] = tracker.get_current_taint()

        def run_b() -> None:
            tracker = TaintTracker()
            tracker.reset()
            tracker.on_tool_input(TaintLevel.owner)
            results["b"] = tracker.get_current_taint()

        ctx_a = copy_context()
        ctx_b = copy_context()

        ctx_a.run(run_a)
        ctx_b.run(run_b)

        assert results["a"] == TaintLevel.external
        assert results["b"] == TaintLevel.owner

    def test_child_context_does_not_leak_to_parent(self) -> None:
        """Taint set in a child context must not propagate back to parent."""
        tracker = TaintTracker()
        tracker.reset()
        assert tracker.get_current_taint() == TaintLevel.owner

        def child() -> None:
            t = TaintTracker()
            t.on_tool_input(TaintLevel.external)

        ctx = copy_context()
        ctx.run(child)

        # Parent context should be unaffected
        assert tracker.get_current_taint() == TaintLevel.owner


class TestGetCurrentTaint:
    """Verify get_current_taint reflects accumulated state."""

    def test_default_is_owner(self) -> None:
        tracker = TaintTracker()
        tracker.reset()
        assert tracker.get_current_taint() == TaintLevel.owner

    def test_reflects_input(self) -> None:
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_input(TaintLevel.auth)
        assert tracker.get_current_taint() == TaintLevel.auth

    def test_reflects_tool_output_escalation(self) -> None:
        tracker = TaintTracker()
        tracker.reset()
        tracker.on_tool_output("web_search")
        assert tracker.get_current_taint() == TaintLevel.external
