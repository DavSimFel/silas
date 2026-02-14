"""Guardrails AI gate provider — validates LLM output via guardrails-ai validators.

guardrails-ai is optional; importing this module always succeeds, but calling
check() without the library installed raises a clear error.
"""

from __future__ import annotations

from typing import Any

from silas.models.gates import Gate, GateLane, GateResult

# Lazy import — guardrails-ai may not be installed
_guardrails_available: bool
try:
    import guardrails as gd  # noqa: F401

    _guardrails_available = True
except ImportError:
    _guardrails_available = False


# Maps short config names to guardrails-ai validator hub identifiers.
# Keeps gate configs concise while staying explicit about what runs.
VALIDATOR_ALIASES: dict[str, str] = {
    "toxic_language": "guardrails/toxic_language",
    "pii": "guardrails/detect_pii",
    "prompt_injection": "guardrails/detect_prompt_injection",
}


def _require_guardrails() -> None:
    """Fail fast with actionable message when the optional dep is missing."""
    if not _guardrails_available:
        raise RuntimeError(
            "guardrails-ai is not installed. "
            "Install it with: pip install 'silas[guardrails]' or pip install guardrails-ai"
        )


def _resolve_validator_name(name: str) -> str:
    """Expand short aliases to full hub identifiers, pass through unknowns."""
    return VALIDATOR_ALIASES.get(name, name)


class GuardrailsChecker:
    """GateCheckProvider that runs guardrails-ai validators against context content."""

    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult:
        _require_guardrails()

        validator_names = self._parse_validators(gate)
        if not validator_names:
            return GateResult(
                gate_name=gate.name,
                lane=gate.lane or GateLane.policy,
                action="continue",
                reason="No validators configured",
            )

        content = self._extract_content(context)
        all_flags: list[str] = []
        modified_context: dict[str, object] | None = None

        for name in validator_names:
            hub_name = _resolve_validator_name(name)
            result = self._run_single_validator(hub_name, content, gate)

            if result["failed"]:
                return GateResult(
                    gate_name=gate.name,
                    lane=gate.lane or GateLane.policy,
                    action="block",
                    reason=f"Validator '{name}' failed: {result['reason']}",
                    flags=[name, "guardrails_blocked"],
                )

            if result.get("modified_content") and result["modified_content"] != content:
                # Validator transformed output (e.g. PII redaction) — propagate via context mutation
                content = result["modified_content"]
                modified_context = {"response": content}
                all_flags.append(f"{name}_modified")

            all_flags.extend(result.get("flags", []))

        return GateResult(
            gate_name=gate.name,
            lane=gate.lane or GateLane.policy,
            action="continue",
            reason="All validators passed",
            flags=all_flags,
            modified_context=modified_context,
        )

    def _parse_validators(self, gate: Gate) -> list[str]:
        """Extract validator list from gate config."""
        raw = gate.config.get("validators", [])
        if isinstance(raw, list):
            return [str(v) for v in raw]
        return []

    def _extract_content(self, context: dict[str, object]) -> str:
        """Pull the text to validate — response first, then message."""
        for key in ("response", "message", "content"):
            val = context.get(key)
            if isinstance(val, str) and val:
                return val
        return ""

    def _run_single_validator(self, hub_name: str, content: str, gate: Gate) -> dict[str, Any]:
        """Run one guardrails-ai validator. Isolated for easy mocking in tests."""
        import guardrails as gd

        guard = gd.Guard().use(hub_name)
        validation = guard.validate(content)

        failed = validation.validation_passed is False
        reason = ""
        if failed and hasattr(validation, "error") and validation.error:
            reason = str(validation.error)
        elif failed:
            reason = "Validation failed"

        return {
            "failed": failed,
            "reason": reason,
            "modified_content": getattr(validation, "validated_output", None),
            "flags": [],
        }


__all__ = ["GuardrailsChecker"]
