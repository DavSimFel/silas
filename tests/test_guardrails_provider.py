"""Tests for the Guardrails AI gate provider.

All tests mock guardrails-ai — the actual library is NOT required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from silas.gates.guardrails_provider import GuardrailsChecker, _require_guardrails
from silas.models.gates import Gate, GateLane, GateProvider, GateTrigger


def _make_gate(validators: list[str], **kwargs: object) -> Gate:
    """Build a guardrails gate with given validators."""
    return Gate(
        name=kwargs.get("name", "test-guardrails"),
        on=GateTrigger.every_agent_response,
        provider=GateProvider.guardrails_ai,
        lane=GateLane.policy,
        config={"validators": validators},
    )


def _fake_validation(passed: bool, *, error: str = "", validated_output: str | None = None):
    """Create a mock validation result object."""
    v = MagicMock()
    v.validation_passed = passed
    v.error = error
    v.validated_output = validated_output
    return v


class TestGuardrailsChecker:
    """Core provider behavior — guardrails-ai is mocked via _run_single_validator."""

    @pytest.fixture(autouse=True)
    def _fake_available(self) -> None:
        """Pretend guardrails-ai is installed so _require_guardrails passes."""
        with patch("silas.gates.guardrails_provider._guardrails_available", True):
            yield

    @pytest.fixture
    def checker(self) -> GuardrailsChecker:
        return GuardrailsChecker()

    async def test_clean_content_passes(self, checker: GuardrailsChecker) -> None:
        gate = _make_gate(["toxic_language"])
        context: dict[str, object] = {"response": "Hello, how can I help?"}

        with patch.object(checker, "_run_single_validator") as mock_run:
            mock_run.return_value = {
                "failed": False,
                "reason": "",
                "modified_content": None,
                "flags": [],
            }
            result = await checker.check(gate, context)

        assert result.action == "continue"
        assert result.gate_name == "test-guardrails"

    async def test_toxic_content_blocked(self, checker: GuardrailsChecker) -> None:
        gate = _make_gate(["toxic_language"])
        context: dict[str, object] = {"response": "toxic garbage"}

        with patch.object(checker, "_run_single_validator") as mock_run:
            mock_run.return_value = {
                "failed": True,
                "reason": "Toxic language detected",
                "modified_content": None,
                "flags": [],
            }
            result = await checker.check(gate, context)

        assert result.action == "block"
        assert "toxic_language" in result.flags
        assert "Toxic language detected" in result.reason

    async def test_pii_detected_and_redacted(self, checker: GuardrailsChecker) -> None:
        """PII validator modifies content rather than blocking — redaction via context mutation."""
        gate = _make_gate(["pii"])
        original = "My SSN is 123-45-6789"
        redacted = "My SSN is [REDACTED]"
        context: dict[str, object] = {"response": original}

        with patch.object(checker, "_run_single_validator") as mock_run:
            mock_run.return_value = {
                "failed": False,
                "reason": "",
                "modified_content": redacted,
                "flags": [],
            }
            result = await checker.check(gate, context)

        assert result.action == "continue"
        assert result.modified_context == {"response": redacted}
        assert "pii_modified" in result.flags

    async def test_multiple_validators_run_in_sequence(self, checker: GuardrailsChecker) -> None:
        """All validators execute in order; first failure short-circuits."""
        gate = _make_gate(["toxic_language", "pii", "prompt_injection"])
        context: dict[str, object] = {"response": "some text"}

        call_order: list[str] = []

        def track_calls(hub_name: str, content: str, gate: Gate) -> dict:
            call_order.append(hub_name)
            # Second validator (pii) fails
            if hub_name == "guardrails/detect_pii":
                return {
                    "failed": True,
                    "reason": "PII found",
                    "modified_content": None,
                    "flags": [],
                }
            return {"failed": False, "reason": "", "modified_content": None, "flags": []}

        with patch.object(checker, "_run_single_validator", side_effect=track_calls):
            result = await checker.check(gate, context)

        # Should stop at pii, never reach prompt_injection
        assert call_order == ["guardrails/toxic_language", "guardrails/detect_pii"]
        assert result.action == "block"

    async def test_no_validators_configured(self, checker: GuardrailsChecker) -> None:
        gate = _make_gate([])
        context: dict[str, object] = {"response": "anything"}
        result = await checker.check(gate, context)

        assert result.action == "continue"
        assert "No validators" in result.reason

    async def test_extracts_content_from_message_key(self, checker: GuardrailsChecker) -> None:
        """Falls back to 'message' when 'response' isn't in context."""
        gate = _make_gate(["toxic_language"])
        context: dict[str, object] = {"message": "user input"}

        with patch.object(checker, "_run_single_validator") as mock_run:
            mock_run.return_value = {
                "failed": False,
                "reason": "",
                "modified_content": None,
                "flags": [],
            }
            result = await checker.check(gate, context)
            # Verify content was extracted from 'message'
            mock_run.assert_called_once()
            assert mock_run.call_args[0][1] == "user input"

        assert result.action == "continue"


class TestMissingDependency:
    """Behavior when guardrails-ai is not installed."""

    async def test_missing_guardrails_raises_clear_error(self) -> None:
        checker = GuardrailsChecker()
        gate = _make_gate(["toxic_language"])
        context: dict[str, object] = {"response": "test"}

        with (
            patch("silas.gates.guardrails_provider._guardrails_available", False),
            pytest.raises(RuntimeError, match="guardrails-ai is not installed"),
        ):
            await checker.check(gate, context)

    def test_require_guardrails_raises_when_unavailable(self) -> None:
        with (
            patch("silas.gates.guardrails_provider._guardrails_available", False),
            pytest.raises(RuntimeError, match="pip install"),
        ):
            _require_guardrails()

    def test_require_guardrails_ok_when_available(self) -> None:
        with patch("silas.gates.guardrails_provider._guardrails_available", True):
            _require_guardrails()  # Should not raise


class TestGateConfigParsing:
    """Validator list parsing edge cases."""

    @pytest.fixture(autouse=True)
    def _fake_available(self) -> None:
        with patch("silas.gates.guardrails_provider._guardrails_available", True):
            yield

    async def test_non_list_validators_ignored(self) -> None:
        """If config has wrong type for validators, treat as empty."""
        gate = Gate(
            name="bad-config",
            on=GateTrigger.every_agent_response,
            provider=GateProvider.guardrails_ai,
            lane=GateLane.policy,
            config={"validators": "not_a_list"},
        )
        checker = GuardrailsChecker()
        result = await checker.check(gate, {"response": "test"})
        assert result.action == "continue"
        assert "No validators" in result.reason

    async def test_custom_validator_name_passed_through(self) -> None:
        """Unknown names aren't aliased — passed as-is to guardrails."""
        checker = GuardrailsChecker()
        gate = _make_gate(["my_org/custom_check"])
        context: dict[str, object] = {"response": "test"}

        with patch.object(checker, "_run_single_validator") as mock_run:
            mock_run.return_value = {
                "failed": False,
                "reason": "",
                "modified_content": None,
                "flags": [],
            }
            await checker.check(gate, context)
            # Custom name should pass through without alias resolution
            mock_run.assert_called_once_with("my_org/custom_check", "test", gate)
