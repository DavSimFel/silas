from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError
from silas.gates.llm import LLMChecker
from silas.gates.script import ScriptChecker
from silas.models.gates import Gate, GateProvider, GateTrigger


class _ValidationSchema(BaseModel):
    value: int


def _validation_error() -> ValidationError:
    try:
        _ValidationSchema.model_validate({"value": "x"})
    except ValidationError as err:
        return err
    raise AssertionError("validation should fail")


class _FakeStructuredAgent:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> object:
        self.prompts.append(prompt)
        if not self._responses:
            raise RuntimeError("no more fake responses")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _llm_gate(*, promote_to_policy: bool = False, extract: str | None = None) -> Gate:
    return Gate(
        name="llm_quality",
        on=GateTrigger.every_user_message,
        provider=GateProvider.llm,
        check="quality",
        config={},
        promote_to_policy=promote_to_policy,
        extract=extract,
    )


def _script_gate(
    command: str | None,
    *,
    timeout_seconds: float | None = None,
    extract: str | None = None,
    cwd: str | None = None,
) -> Gate:
    config: dict[str, object] = {}
    if timeout_seconds is not None:
        config["timeout_seconds"] = timeout_seconds
    if cwd is not None:
        config["cwd"] = cwd
    return Gate(
        name="script_guard",
        on=GateTrigger.every_user_message,
        provider=GateProvider.script,
        check=command,
        config=config,
        extract=extract,
    )


def _write_script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return str(path)


@pytest.mark.asyncio
async def test_llm_checker_quality_success_returns_advisory_result() -> None:
    agent = _FakeStructuredAgent(
        [{"score": 0.82, "flags": ["off_topic"], "reason": "partially relevant"}]
    )
    checker = LLMChecker(agent)

    result = await checker.check(_llm_gate(), {"message": "hello"})

    assert result.action == "continue"
    assert result.score == 0.82
    assert result.flags == ["off_topic"]
    assert result.reason == "partially relevant"


@pytest.mark.asyncio
async def test_llm_checker_promoted_policy_uses_llm_action() -> None:
    agent = _FakeStructuredAgent(
        [{"action": "require_approval", "reason": "needs review", "score": 0.49}]
    )
    checker = LLMChecker(agent)

    result = await checker.check(_llm_gate(promote_to_policy=True), {"response": "answer"})

    assert result.action == "require_approval"
    assert result.score == 0.49
    assert result.reason == "needs review"


@pytest.mark.asyncio
async def test_llm_checker_quality_parse_error_fails_open() -> None:
    agent = _FakeStructuredAgent([{"score": 1.2, "flags": [], "reason": "invalid score"}])
    checker = LLMChecker(agent)

    result = await checker.check(_llm_gate(), {"message": "hello"})

    assert result.action == "continue"
    assert result.score is None
    assert "llm_error" in result.flags
    assert "failed" in result.reason


@pytest.mark.asyncio
async def test_llm_checker_promoted_parse_error_fails_closed() -> None:
    agent = _FakeStructuredAgent([{"score": 0.8, "reason": "missing action"}])
    checker = LLMChecker(agent)

    result = await checker.check(_llm_gate(promote_to_policy=True), {"message": "hello"})

    assert result.action == "block"
    assert result.score is None
    assert "llm_error" in result.flags
    assert "failed" in result.reason


@pytest.mark.asyncio
async def test_llm_checker_handles_structured_fallback_in_quality_lane() -> None:
    agent = _FakeStructuredAgent([_validation_error(), _validation_error()])
    checker = LLMChecker(agent)

    result = await checker.check(_llm_gate(), {"message": "hello"})

    assert result.action == "continue"
    assert "llm_error" in result.flags
    assert len(agent.prompts) == 2
    assert "[SCHEMA VALIDATION ERROR]" in agent.prompts[1]


@pytest.mark.asyncio
async def test_llm_checker_circuit_breaker_opens_and_short_circuits_quality() -> None:
    agent = _FakeStructuredAgent(
        [
            RuntimeError("first failure"),
            RuntimeError("second failure"),
            {"score": 0.9, "flags": [], "reason": "ok"},
        ]
    )
    checker = LLMChecker(agent, failure_threshold=2, cooldown_seconds=60)
    gate = _llm_gate()

    first = await checker.check(gate, {"message": "a"})
    second = await checker.check(gate, {"message": "b"})
    third = await checker.check(gate, {"message": "c"})

    assert "llm_error" in first.flags
    assert "llm_error" in second.flags
    assert third.action == "continue"
    assert "circuit_open" in third.flags
    assert len(agent.prompts) == 2


@pytest.mark.asyncio
async def test_llm_checker_circuit_breaker_blocks_promoted_policy() -> None:
    agent = _FakeStructuredAgent([RuntimeError("failure"), {"action": "continue", "reason": "ok"}])
    checker = LLMChecker(agent, failure_threshold=1, cooldown_seconds=60)
    gate = _llm_gate(promote_to_policy=True)

    first = await checker.check(gate, {"message": "a"})
    second = await checker.check(gate, {"message": "b"})

    assert first.action == "block"
    assert "llm_error" in first.flags
    assert second.action == "block"
    assert "circuit_open" in second.flags
    assert len(agent.prompts) == 1


@pytest.mark.asyncio
async def test_llm_checker_circuit_breaker_recovers_after_cooldown() -> None:
    agent = _FakeStructuredAgent(
        [RuntimeError("failure"), {"score": 0.77, "flags": [], "reason": "ok"}]
    )
    checker = LLMChecker(agent, failure_threshold=1, cooldown_seconds=0.02)
    gate = _llm_gate()

    first = await checker.check(gate, {"message": "a"})
    immediate = await checker.check(gate, {"message": "b"})
    await asyncio.sleep(0.03)
    recovered = await checker.check(gate, {"message": "c"})

    assert "llm_error" in first.flags
    assert "circuit_open" in immediate.flags
    assert recovered.action == "continue"
    assert recovered.score == 0.77
    assert len(agent.prompts) == 2


@pytest.mark.asyncio
async def test_script_checker_exit_zero_passes(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path=tmp_path,
        name="pass.sh",
        body='content="$(cat)"; echo "ok:${content}"; exit 0',
    )
    checker = ScriptChecker()

    result = await checker.check(_script_gate(script), {"message": "hello"})

    assert result.action == "continue"
    assert "ok:hello" in result.reason


@pytest.mark.asyncio
async def test_script_checker_exit_one_blocks(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path=tmp_path,
        name="block.sh",
        body='cat >/dev/null; echo "blocked" >&2; exit 1',
    )
    checker = ScriptChecker()

    result = await checker.check(_script_gate(script), {"message": "hello"})

    assert result.action == "block"
    assert "script_block" in result.flags
    assert "blocked" in result.reason


@pytest.mark.asyncio
async def test_script_checker_exit_two_warns_without_blocking(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path=tmp_path,
        name="warn.sh",
        body='cat >/dev/null; echo "warned"; exit 2',
    )
    checker = ScriptChecker()

    result = await checker.check(_script_gate(script), {"message": "hello"})

    assert result.action == "continue"
    assert "warn" in result.flags
    assert "script_warn" in result.flags
    assert "warned" in result.reason


@pytest.mark.asyncio
async def test_script_checker_timeout_enforced(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path=tmp_path,
        name="slow.sh",
        body='cat >/dev/null; sleep 1; echo "late"; exit 0',
    )
    checker = ScriptChecker(default_timeout_seconds=0.05)

    result = await checker.check(_script_gate(script), {"message": "hello"})

    assert result.action == "block"
    assert "script_timeout" in result.flags
    assert "timed out" in result.reason


@pytest.mark.asyncio
async def test_script_checker_missing_command_blocks() -> None:
    checker = ScriptChecker()

    result = await checker.check(_script_gate(None), {"message": "hello"})

    assert result.action == "block"
    assert "script_error" in result.flags
    assert "required" in result.reason


@pytest.mark.asyncio
async def test_script_checker_extract_uses_selected_context_key(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path=tmp_path,
        name="extract.sh",
        body='content="$(cat)"; echo "seen:${content}"; exit 0',
    )
    checker = ScriptChecker()
    gate = _script_gate(script, extract="payload")

    result = await checker.check(gate, {"message": "wrong", "payload": "right"})

    assert result.action == "continue"
    assert "seen:right" in result.reason


@pytest.mark.asyncio
async def test_script_checker_unexpected_exit_code_blocks_as_error(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path=tmp_path,
        name="bad-exit.sh",
        body='cat >/dev/null; echo "boom"; exit 7',
    )
    checker = ScriptChecker()

    result = await checker.check(_script_gate(script), {"message": "hello"})

    assert result.action == "block"
    assert "script_error" in result.flags
    assert "boom" in result.reason
