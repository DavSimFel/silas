from __future__ import annotations

from silas.core.token_counter import HeuristicTokenCounter
from silas.gates.output import OutputGateRunner
from silas.gates.runner import SilasGateRunner
from silas.models.gates import Gate, GateTrigger
from silas.models.messages import TaintLevel


def _output_gate(name: str, check: str, config: dict[str, object] | None = None) -> Gate:
    return Gate(
        name=name,
        on=GateTrigger.every_agent_response,
        check=check,
        config=config or {},
    )


# ── Tests using the unified SilasGateRunner ────────────────────────


def test_taint_ceiling_gate_blocks_high_taint_responses() -> None:
    runner = SilasGateRunner()
    gates = [_output_gate("taint_guard", "taint_ceiling", {"threshold": "auth"})]
    rewritten, results = runner.evaluate_output(
        response_text="sensitive response",
        response_taint=TaintLevel.external,
        sender_id="customer-1",
        gates=gates,
    )

    assert rewritten == "sensitive response"
    assert len(results) == 1
    assert results[0].action == "block"
    assert "exceeds threshold" in results[0].reason


def test_length_limit_gate_truncates_long_responses() -> None:
    runner = SilasGateRunner()
    gates = [_output_gate("length_guard", "length_limit", {"max_tokens": 8, "mode": "truncate"})]
    response_text = "A" * 200

    rewritten, results = runner.evaluate_output(
        response_text=response_text,
        response_taint=TaintLevel.owner,
        sender_id="owner",
        gates=gates,
    )

    assert len(results) == 1
    assert results[0].action == "continue"
    assert "truncated" in results[0].flags
    assert rewritten != response_text
    assert results[0].modified_context == {"response": rewritten}
    assert HeuristicTokenCounter().count(rewritten) <= 8


def test_pii_marker_gate_flags_email_and_phone() -> None:
    runner = SilasGateRunner()
    gates = [_output_gate("pii_guard", "pii_marker")]
    response_text = "Contact me at silas@example.com or +1 (212) 555-0100."

    rewritten, results = runner.evaluate_output(
        response_text=response_text,
        response_taint=TaintLevel.owner,
        sender_id="owner",
        gates=gates,
    )

    assert rewritten == response_text
    assert len(results) == 1
    assert results[0].action == "continue"
    assert "warn" in results[0].flags
    assert "pii_detected" in results[0].flags
    assert "pii_email" in results[0].flags
    assert "pii_phone" in results[0].flags


def test_gate_pipeline_passes_clean_response_unchanged() -> None:
    runner = SilasGateRunner()
    gates = [
        _output_gate("taint_guard", "taint_ceiling", {"threshold": "external"}),
        _output_gate("length_guard", "length_limit", {"max_tokens": 200, "mode": "truncate"}),
        _output_gate("pii_guard", "pii_marker"),
    ]
    response_text = "Everything is ready. The build passed and tests are green."

    rewritten, results = runner.evaluate_output(
        response_text=response_text,
        response_taint=TaintLevel.owner,
        sender_id="owner",
        gates=gates,
    )

    assert rewritten == response_text
    assert len(results) == 3
    assert all(result.action == "continue" for result in results)
    assert all("warn" not in result.flags for result in results)


def test_set_output_gates_used_when_no_gates_passed() -> None:
    """evaluate_output uses stored gates when caller omits the gates arg."""
    runner = SilasGateRunner()
    runner.set_output_gates([_output_gate("pii_guard", "pii_marker")])

    _, results = runner.evaluate_output(
        response_text="hello@example.com",
        response_taint=TaintLevel.owner,
        sender_id="owner",
    )
    assert len(results) == 1
    assert "pii_email" in results[0].flags


# ── Legacy OutputGateRunner still works (backward compat) ──────────


def test_legacy_output_gate_runner_still_works() -> None:
    runner = OutputGateRunner(
        [_output_gate("taint_guard", "taint_ceiling", {"threshold": "auth"})]
    )
    _rewritten, results = runner.evaluate(
        response_text="sensitive response",
        response_taint=TaintLevel.external,
        sender_id="customer-1",
    )
    assert results[0].action == "block"
