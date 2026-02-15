"""End-to-end integration tests for the plan → approve → execute → verify pipeline.

These tests wire together real components (not fakes) wherever possible,
using fakes only for LLM calls and external I/O.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest
from silas.core.context_manager import LiveContextManager
from silas.core.plan_parser import MarkdownPlanParser
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.verification_runner import SilasVerificationRunner
from silas.execution.sandbox import SubprocessSandboxManager

# OutputGateRunner removed — using unified SilasGateRunner (PR #70)
from silas.gates.runner import SilasGateRunner
from silas.models.context import ContextItem, ContextZone, TokenBudget
from silas.models.gates import Gate, GateTrigger, GateType
from silas.models.messages import TaintLevel
from silas.models.work import Expectation, VerificationCheck, WorkItem, WorkItemStatus, WorkItemType

from tests.fakes import InMemoryWorkItemStore
from tests.stubs import InMemoryAuditLog

# --- Helpers ---


def _make_work_item(
    title: str = "Test task",
    body: str = "Do the thing",
    verify: list[VerificationCheck] | None = None,
    **kwargs,
) -> WorkItem:
    defaults = {
        "id": "wi-e2e-1",
        "title": title,
        "body": body,
        "type": WorkItemType.task,
        "created_at": datetime.now(UTC),
    }
    if verify is not None:
        defaults["verify"] = verify
    defaults.update(kwargs)
    return WorkItem(**defaults)


# --- Plan Parser Integration ---


class TestPlanParserIntegration:
    """Test that plan parser produces valid WorkItems from markdown."""

    def test_parse_simple_task(self) -> None:
        parser = MarkdownPlanParser()
        markdown = textwrap.dedent("""\
            ---
            id: task-001
            title: Create a greeting script
            type: task
            ---
            Write a Python script that prints "Hello, World!"
        """)
        work_item = parser.parse(markdown)
        assert work_item.id == "task-001"
        assert work_item.title == "Create a greeting script"
        assert work_item.type == WorkItemType.task
        assert "Hello, World!" in work_item.body

    def test_parse_task_with_verification(self) -> None:
        parser = MarkdownPlanParser()
        markdown = textwrap.dedent("""\
            ---
            id: task-002
            title: Verified task
            type: task
            verify:
              - name: check-output
                run: "echo hello"
                expect:
                  contains: "hello"
            ---
            Run and verify
        """)
        work_item = parser.parse(markdown)
        assert len(work_item.verify) == 1
        assert work_item.verify[0].name == "check-output"
        assert work_item.verify[0].expect.contains == "hello"

    def test_parse_task_with_budget(self) -> None:
        parser = MarkdownPlanParser()
        markdown = textwrap.dedent("""\
            ---
            id: task-003
            title: Budget task
            type: task
            budget:
              max_tokens: 50000
              max_cost_usd: 0.5
            ---
            Work within budget
        """)
        work_item = parser.parse(markdown)
        assert work_item.budget.max_tokens == 50000
        assert work_item.budget.max_cost_usd == 0.5

    def test_parse_task_with_gates(self) -> None:
        parser = MarkdownPlanParser()
        # Note: YAML `on:` is a boolean (true) unless quoted
        markdown = textwrap.dedent("""\
            ---
            id: task-004
            title: Gated task
            type: task
            gates:
              - name: no-secrets
                "on": every_agent_response
                type: string_match
                check: secret
            ---
            Must pass gate
        """)
        work_item = parser.parse(markdown)
        assert len(work_item.gates) == 1
        assert work_item.gates[0].name == "no-secrets"


# --- Verification Runner Integration ---


class TestVerificationRunnerIntegration:
    """Test real subprocess execution + verification checks."""

    @pytest.mark.asyncio
    async def test_simple_command_passes(self) -> None:
        """Run a real shell command and verify output."""
        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(sandbox_manager=sandbox)
        checks = [
            VerificationCheck(
                name="echo-check",
                run="echo 'integration test'",
                expect=Expectation(contains="integration test"),
            ),
        ]
        report = await runner.run_checks(checks)
        assert report.all_passed is True
        assert len(report.results) == 1
        assert report.results[0].passed is True

    @pytest.mark.asyncio
    async def test_failing_command_reports_failure(self) -> None:
        """A command that doesn't match expectation should fail."""
        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(sandbox_manager=sandbox)
        checks = [
            VerificationCheck(
                name="wrong-output",
                run="echo 'actual output'",
                expect=Expectation(contains="expected but missing"),
            ),
        ]
        report = await runner.run_checks(checks)
        assert report.all_passed is False
        assert report.failed[0].name == "wrong-output"

    @pytest.mark.asyncio
    async def test_exit_code_check(self) -> None:
        """Verify exit code checking works with real processes."""
        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(sandbox_manager=sandbox)
        checks = [
            VerificationCheck(
                name="success-exit",
                run="true",
                expect=Expectation(exit_code=0),
            ),
            VerificationCheck(
                name="failure-exit",
                run="false",
                expect=Expectation(exit_code=1),
            ),
        ]
        report = await runner.run_checks(checks)
        assert report.all_passed is True

    @pytest.mark.asyncio
    async def test_python_execution(self) -> None:
        """Run actual Python code in subprocess and verify."""
        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(sandbox_manager=sandbox)
        checks = [
            VerificationCheck(
                name="python-calc",
                run='python3 -c "print(2 + 2)"',
                expect=Expectation(equals="4"),
            ),
        ]
        report = await runner.run_checks(checks)
        assert report.all_passed is True

    @pytest.mark.asyncio
    async def test_multiple_checks_mixed_results(self) -> None:
        """Multiple checks where some pass and some fail."""
        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(sandbox_manager=sandbox)
        checks = [
            VerificationCheck(
                name="pass-1",
                run="echo ok",
                expect=Expectation(contains="ok"),
            ),
            VerificationCheck(
                name="fail-1",
                run="echo wrong",
                expect=Expectation(contains="right"),
            ),
            VerificationCheck(
                name="pass-2",
                run='python3 -c "print(42)"',
                expect=Expectation(equals="42"),
            ),
        ]
        report = await runner.run_checks(checks)
        assert report.all_passed is False
        assert len(report.results) == 3
        assert len(report.failed) == 1
        assert report.failed[0].name == "fail-1"

    @pytest.mark.asyncio
    async def test_file_exists_check(self, tmp_path: Path) -> None:
        """Verify file_exists expectation with real filesystem."""
        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(
            sandbox_manager=sandbox,
            project_dirs=[str(tmp_path)],
        )
        target = tmp_path / "output.txt"
        target.write_text("result")

        checks = [
            VerificationCheck(
                name="file-check",
                run="true",
                expect=Expectation(file_exists=str(target)),
            ),
        ]
        report = await runner.run_checks(checks)
        assert report.all_passed is True


# --- Output Gate Runner Integration ---


class TestOutputGateRunnerIntegration:
    """Test output gate evaluation with unified SilasGateRunner (PR #70)."""

    def test_string_match_gate(self) -> None:
        """String match gate should evaluate against response text."""
        runner = SilasGateRunner()
        gates = [
            Gate(
                name="no-secrets",
                on=GateTrigger.every_agent_response,
                type=GateType.string_match,
                check="password",
            ),
        ]
        _response, results = runner.evaluate_output(
            response_text="Your password is hunter2",
            response_taint=TaintLevel.owner,
            sender_id="user-1",
            gates=gates,
        )
        assert len(results) == 1
        assert results[0].gate_name == "no-secrets"

    def test_empty_gates_pass_through(self) -> None:
        """No gates configured = response passes unchanged."""
        runner = SilasGateRunner()
        safe_response, results = runner.evaluate_output(
            response_text="safe response",
            response_taint=TaintLevel.owner,
            sender_id="user-1",
            gates=[],
        )
        assert safe_response == "safe response"
        assert results == []

    def test_regex_gate(self) -> None:
        """Regex gate should match patterns in response."""
        runner = SilasGateRunner()
        gates = [
            Gate(
                name="no-api-keys",
                on=GateTrigger.every_agent_response,
                type=GateType.regex,
                check=r"sk-[a-zA-Z0-9]{20,}",
            ),
        ]
        _response, results = runner.evaluate_output(
            response_text="Here's your key: sk-abcdefghij1234567890",
            response_taint=TaintLevel.owner,
            sender_id="user-1",
            gates=gates,
        )
        assert len(results) == 1
        assert results[0].gate_name == "no-api-keys"


# --- Context + Verification Pipeline ---


class TestContextVerificationPipeline:
    """Test context management feeding into verification."""

    @pytest.mark.asyncio
    async def test_context_tracks_verification_results(self) -> None:
        """Context manager stores verification outcomes for future turns."""
        counter = HeuristicTokenCounter()
        budget = TokenBudget()
        ctx_mgr = LiveContextManager(token_budget=budget, token_counter=counter)

        scope = "test-scope"
        content = "Verification passed: echo-check (contains 'hello')"
        ctx_mgr.add(
            scope,
            ContextItem(
                ctx_id="verification:result:1",
                zone=ContextZone.workspace,
                content=content,
                token_count=counter.count(content),
                created_at=datetime.now(UTC),
                turn_number=1,
                source="verification_runner",
                taint="owner",
                kind="verification_result",
            ),
        )

        rendered = ctx_mgr.render(scope, turn_number=1)
        assert "Verification passed" in rendered


# --- Work Item Store Integration ---


class TestWorkItemStoreIntegration:
    """Test work item lifecycle through the store."""

    @pytest.mark.asyncio
    async def test_create_and_update_status(self) -> None:
        store = InMemoryWorkItemStore()
        wi = _make_work_item(title="Integration task")

        await store.save(wi)
        loaded = await store.get(wi.id)
        assert loaded is not None
        assert loaded.status == WorkItemStatus.pending

        updated = loaded.model_copy(update={"status": WorkItemStatus.running})
        await store.save(updated)
        reloaded = await store.get(wi.id)
        assert reloaded.status == WorkItemStatus.running

    @pytest.mark.asyncio
    async def test_full_lifecycle(self) -> None:
        """pending → running → done"""
        store = InMemoryWorkItemStore()
        wi = _make_work_item(title="Lifecycle task")

        await store.save(wi)
        assert (await store.get(wi.id)).status == WorkItemStatus.pending

        await store.save(wi.model_copy(update={"status": WorkItemStatus.running}))
        assert (await store.get(wi.id)).status == WorkItemStatus.running

        await store.save(wi.model_copy(update={"status": WorkItemStatus.done}))
        assert (await store.get(wi.id)).status == WorkItemStatus.done


# --- Full Pipeline: Parse → Verify ---


class TestFullPipeline:
    """End-to-end: parse a plan, extract verification checks, run them."""

    @pytest.mark.asyncio
    async def test_parse_then_verify(self) -> None:
        """Parse a work item from markdown, then run its verification checks."""
        parser = MarkdownPlanParser()
        markdown = textwrap.dedent("""\
            ---
            id: e2e-001
            title: Echo test
            type: task
            verify:
              - name: echo-works
                run: "echo 'e2e success'"
                expect:
                  contains: "e2e success"
            ---
            Run echo and verify output contains the expected string.
        """)
        work_item = parser.parse(markdown)

        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(sandbox_manager=sandbox)
        report = await runner.run_checks(work_item.verify)

        assert report.all_passed is True
        assert report.results[0].name == "echo-works"

    @pytest.mark.asyncio
    async def test_parse_verify_fail_then_report(self) -> None:
        """Parse a work item with a failing check, verify it reports correctly."""
        parser = MarkdownPlanParser()
        markdown = textwrap.dedent("""\
            ---
            id: e2e-002
            title: Failing check
            type: task
            verify:
              - name: should-fail
                run: "echo 'wrong output'"
                expect:
                  contains: "correct output"
            ---
            This check should fail.
        """)
        work_item = parser.parse(markdown)

        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(sandbox_manager=sandbox)
        report = await runner.run_checks(work_item.verify)

        assert report.all_passed is False
        assert report.failed[0].name == "should-fail"
        assert "correct output" in report.failed[0].reason

    @pytest.mark.asyncio
    async def test_parse_verify_with_python(self) -> None:
        """Full pipeline with Python execution."""
        parser = MarkdownPlanParser()
        markdown = textwrap.dedent("""\
            ---
            id: e2e-003
            title: Python math
            type: task
            verify:
              - name: math-check
                run: "python3 -c \\"print(6 * 7)\\""
                expect:
                  equals: "42"
              - name: not-empty
                run: "python3 -c \\"print('hello')\\""
                expect:
                  not_empty: true
            ---
            Run Python calculations and verify results.
        """)
        work_item = parser.parse(markdown)

        sandbox = SubprocessSandboxManager()
        runner = SilasVerificationRunner(sandbox_manager=sandbox)
        report = await runner.run_checks(work_item.verify)

        assert report.all_passed is True
        assert len(report.results) == 2


# --- Audit Trail Integration ---


class TestAuditIntegration:
    """Test that audit logging captures the right events through pipeline."""

    @pytest.mark.asyncio
    async def test_audit_captures_events(self) -> None:
        audit = InMemoryAuditLog()
        await audit.log("plan_parsed", work_item_id="e2e-001")
        await audit.log("verification_started", checks=2)
        await audit.log("verification_complete", all_passed=True)

        assert len(audit.events) == 3
        assert audit.events[0]["event"] == "plan_parsed"
        assert audit.events[2]["data"]["all_passed"] is True
