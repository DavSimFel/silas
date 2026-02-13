from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from silas.core.token_counter import HeuristicTokenCounter
from silas.models.gates import Gate, GateLane, GateResult, GateTrigger
from silas.models.messages import TaintLevel

_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(
    r"\b(?:\+?\d{1,2}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_TAINT_ORDER = {
    TaintLevel.owner: 0,
    TaintLevel.auth: 1,
    TaintLevel.external: 2,
}
_DEFAULT_BLOCK_MESSAGE = "I cannot share that"


@dataclass(frozen=True)
class _Escalation:
    action: str
    message: str | None = None


class OutputGateRunner:
    """Deterministic output-gate evaluator for the Stream response path.

    .. deprecated::
        Use ``SilasGateRunner.evaluate_output`` instead. This class exists
        only for backward compatibility and will be removed in a future release.
    """

    def __init__(
        self,
        gates: list[Gate],
        token_counter: HeuristicTokenCounter | None = None,
        escalation_map: Mapping[str, object] | None = None,
    ) -> None:
        self._gates = list(gates)
        self._token_counter = token_counter or HeuristicTokenCounter()
        self._escalation_map = dict(escalation_map or {})

    def evaluate(
        self,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> tuple[str, list[GateResult]]:
        working_response = response_text
        policy_results: list[GateResult] = []
        quality_results: list[GateResult] = []

        for gate in self._gates:
            if gate.lane != GateLane.policy:
                continue
            result = self._evaluate_policy_gate(
                gate=gate,
                response_text=working_response,
                response_taint=response_taint,
                sender_id=sender_id,
            )
            policy_results.append(result)
            if result.action != "continue":
                continue
            updated_response = self._extract_response_mutation(result)
            if updated_response is not None:
                working_response = updated_response

        for gate in self._gates:
            if gate.lane != GateLane.quality:
                continue
            quality_results.append(
                self._evaluate_quality_gate(
                    gate=gate,
                    response_text=working_response,
                    response_taint=response_taint,
                    sender_id=sender_id,
                )
            )

        return working_response, [*policy_results, *quality_results]

    def _evaluate_policy_gate(
        self,
        gate: Gate,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> GateResult:
        result = self._evaluate_gate(
            gate=gate,
            response_text=response_text,
            response_taint=response_taint,
            sender_id=sender_id,
        )
        if result.action != "block":
            return result
        return self._apply_escalation(
            gate=gate,
            result=result,
            response_text=response_text,
        )

    def _evaluate_quality_gate(
        self,
        gate: Gate,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> GateResult:
        raw = self._evaluate_gate(
            gate=gate,
            response_text=response_text,
            response_taint=response_taint,
            sender_id=sender_id,
        )
        flags = list(raw.flags)
        reason = raw.reason

        if raw.action != "continue":
            flags.append("quality_lane_violation")
            reason = f"{reason} (quality action overridden to continue)"
        if isinstance(raw.modified_context, dict):
            flags.append("quality_mutation_ignored")

        return GateResult(
            gate_name=raw.gate_name,
            lane=GateLane.quality,
            action="continue",
            reason=reason,
            value=raw.value,
            score=raw.score,
            flags=sorted(set(flags)),
            modified_context=None,
        )

    def _evaluate_gate(
        self,
        gate: Gate,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> GateResult:
        if gate.on != GateTrigger.every_agent_response:
            return self._continue_result(
                gate_name=gate.name,
                reason=f"skipped trigger={gate.on.value}",
            )

        check_name = self._check_name(gate)
        if check_name == "taint_ceiling":
            return self._taint_ceiling(gate, response_taint)
        if check_name == "length_limit":
            return self._length_limit(gate, response_text)
        if check_name == "pii_marker":
            return self._pii_marker(gate, response_text)

        return self._continue_result(
            gate_name=gate.name,
            reason=f"unknown output gate check: {check_name}",
            flags=["warn", "unknown_output_gate"],
            value=sender_id,
        )

    def _taint_ceiling(self, gate: Gate, response_taint: TaintLevel) -> GateResult:
        raw_threshold = (
            gate.config.get("threshold")
            or gate.config.get("max_taint")
            or gate.config.get("taint_ceiling")
            or TaintLevel.external.value
        )
        threshold = self._to_taint(raw_threshold)
        if threshold is None:
            return self._continue_result(
                gate_name=gate.name,
                reason=f"invalid taint threshold: {raw_threshold!r}",
                flags=["warn", "invalid_gate_config"],
            )

        taint_rank = _TAINT_ORDER[response_taint]
        threshold_rank = _TAINT_ORDER[threshold]
        if taint_rank > threshold_rank:
            return self._block_result(
                gate_name=gate.name,
                reason=(
                    f"response taint {response_taint.value} exceeds threshold {threshold.value}"
                ),
                value=response_taint.value,
            )

        return self._continue_result(
            gate_name=gate.name,
            reason=f"response taint {response_taint.value} within threshold {threshold.value}",
            value=response_taint.value,
        )

    def _length_limit(self, gate: Gate, response_text: str) -> GateResult:
        raw_limit = gate.config.get("max_tokens")
        if not isinstance(raw_limit, int) or raw_limit <= 0:
            return self._continue_result(
                gate_name=gate.name,
                reason=f"invalid max_tokens: {raw_limit!r}",
                flags=["warn", "invalid_gate_config"],
            )

        token_count = self._token_counter.count(response_text)
        if token_count <= raw_limit:
            return self._continue_result(
                gate_name=gate.name,
                reason=f"response length {token_count} <= limit {raw_limit}",
                value=float(token_count),
            )

        mode = str(gate.config.get("mode", "truncate")).strip().lower()
        if mode == "warn":
            return self._continue_result(
                gate_name=gate.name,
                reason=f"response length {token_count} exceeds limit {raw_limit}",
                flags=["warn", "length_exceeded"],
                value=float(token_count),
            )
        if mode == "block":
            return self._block_result(
                gate_name=gate.name,
                reason=f"response length {token_count} exceeds limit {raw_limit}",
                value=float(token_count),
            )

        truncated = self._truncate_to_token_limit(response_text, raw_limit)
        return self._continue_result(
            gate_name=gate.name,
            reason=f"response length {token_count} truncated to <= {raw_limit} tokens",
            flags=["warn", "length_exceeded", "truncated"],
            value=float(token_count),
            modified_context={"response": truncated},
        )

    def _pii_marker(self, gate: Gate, response_text: str) -> GateResult:
        has_email = bool(_EMAIL_PATTERN.search(response_text))
        has_phone = bool(_PHONE_PATTERN.search(response_text))

        if not has_email and not has_phone:
            return self._continue_result(
                gate_name=gate.name,
                reason="no PII marker detected",
            )

        flags = ["warn", "pii_detected"]
        kinds: list[str] = []
        if has_email:
            flags.append("pii_email")
            kinds.append("email")
        if has_phone:
            flags.append("pii_phone")
            kinds.append("phone")

        if self._has_explicit_escalation(gate):
            return self._block_result(
                gate_name=gate.name,
                reason=f"PII markers detected: {', '.join(kinds)}",
                flags=flags,
                value=", ".join(kinds),
            )

        return self._continue_result(
            gate_name=gate.name,
            reason=f"PII markers detected: {', '.join(kinds)}",
            flags=flags,
            value=", ".join(kinds),
        )

    def _apply_escalation(
        self,
        gate: Gate,
        result: GateResult,
        response_text: str,
    ) -> GateResult:
        escalation = self._resolve_escalation(gate)
        escalation_flag = f"escalation:{escalation.action}"
        flags = sorted({*result.flags, escalation_flag})

        if escalation.action == "redact":
            redacted = self._redact_sensitive_content(response_text)
            return GateResult(
                gate_name=result.gate_name,
                lane=GateLane.policy,
                action="continue",
                reason=f"{result.reason} (redacted)",
                value=result.value,
                score=result.score,
                flags=flags,
                modified_context={"response": redacted},
            )

        if escalation.action == "require_approval":
            fallback_message = escalation.message or _DEFAULT_BLOCK_MESSAGE
            return GateResult(
                gate_name=result.gate_name,
                lane=GateLane.policy,
                action="require_approval",
                reason=f"{result.reason} (approval required)",
                value=result.value,
                score=result.score,
                flags=flags,
                modified_context={"response": fallback_message},
            )

        if escalation.action == "log_and_pass":
            passthrough_flags = sorted({*flags, "warn", "logged_violation"})
            return GateResult(
                gate_name=result.gate_name,
                lane=GateLane.policy,
                action="continue",
                reason=f"{result.reason} (logged and passed)",
                value=result.value,
                score=result.score,
                flags=passthrough_flags,
            )

        block_message = escalation.message or _DEFAULT_BLOCK_MESSAGE
        return GateResult(
            gate_name=result.gate_name,
            lane=GateLane.policy,
            action="block",
            reason=f"{result.reason} (blocked)",
            value=result.value,
            score=result.score,
            flags=flags,
            modified_context={"response": block_message},
        )

    def _resolve_escalation(self, gate: Gate) -> _Escalation:
        configured = self._escalation_map.get(gate.name)
        if configured is None:
            configured = gate.config.get("escalation")
        if configured is None:
            configured = gate.config.get("on_block")
        if configured is None and gate.on_block != "report":
            configured = gate.on_block

        parsed = self._parse_escalation(configured)
        if parsed is not None:
            return parsed
        return _Escalation(action="block_with_message", message=_DEFAULT_BLOCK_MESSAGE)

    def _parse_escalation(self, raw: object) -> _Escalation | None:
        if isinstance(raw, str):
            return self._parse_escalation_string(raw)
        if isinstance(raw, Mapping):
            action = self._normalize_escalation_action(raw.get("action") or raw.get("type"))
            if action is None:
                return None
            message = self._message_from_raw(raw.get("message") or raw.get("msg") or raw.get("text"))
            return _Escalation(action=action, message=message)
        return None

    def _parse_escalation_string(self, raw: str) -> _Escalation | None:
        stripped = raw.strip()
        if not stripped:
            return None

        if stripped.startswith("block_with_message(") and stripped.endswith(")"):
            inner = stripped.removeprefix("block_with_message(").removesuffix(")")
            return _Escalation(
                action="block_with_message",
                message=self._message_from_raw(inner),
            )
        if stripped.startswith("block_with_message:"):
            _, _, tail = stripped.partition(":")
            return _Escalation(
                action="block_with_message",
                message=self._message_from_raw(tail),
            )

        action = self._normalize_escalation_action(stripped)
        if action is None:
            return None
        return _Escalation(action=action)

    def _normalize_escalation_action(self, raw: object) -> str | None:
        if not isinstance(raw, str):
            return None
        normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "block": "block_with_message",
            "respond": "block_with_message",
            "report": "block_with_message",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"block_with_message", "redact", "require_approval", "log_and_pass"}:
            return None
        return normalized

    def _message_from_raw(self, raw: object) -> str | None:
        if not isinstance(raw, str):
            return None
        message = raw.strip().strip('"').strip("'").strip()
        if not message:
            return None
        return message

    def _has_explicit_escalation(self, gate: Gate) -> bool:
        if gate.name in self._escalation_map:
            return True
        if "escalation" in gate.config:
            return True
        if "on_block" in gate.config:
            return True
        return gate.on_block != "report"

    def _redact_sensitive_content(self, text: str) -> str:
        redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
        redacted = _PHONE_PATTERN.sub("[REDACTED_PHONE]", redacted)
        return _API_KEY_PATTERN.sub("[REDACTED_KEY]", redacted)

    def _truncate_to_token_limit(self, text: str, max_tokens: int) -> str:
        if self._token_counter.count(text) <= max_tokens:
            return text

        max_chars = max(int(max_tokens * 3.5), 1)
        truncated = text[:max_chars]
        while truncated and self._token_counter.count(truncated) > max_tokens:
            truncated = truncated[:-1]
        return truncated

    def _extract_response_mutation(self, result: GateResult) -> str | None:
        if not isinstance(result.modified_context, dict):
            return None
        candidate = result.modified_context.get("response")
        if isinstance(candidate, str):
            return candidate
        return None

    def _continue_result(
        self,
        gate_name: str,
        reason: str,
        *,
        flags: list[str] | None = None,
        value: str | float | None = None,
        modified_context: dict[str, object] | None = None,
    ) -> GateResult:
        return GateResult(
            gate_name=gate_name,
            lane=GateLane.policy,
            action="continue",
            reason=reason,
            value=value,
            flags=flags or [],
            modified_context=modified_context,
        )

    def _block_result(
        self,
        gate_name: str,
        reason: str,
        *,
        flags: list[str] | None = None,
        value: str | float | None = None,
    ) -> GateResult:
        return GateResult(
            gate_name=gate_name,
            lane=GateLane.policy,
            action="block",
            reason=reason,
            value=value,
            flags=flags or [],
        )

    def _check_name(self, gate: Gate) -> str:
        source = gate.check or gate.name
        return source.strip().lower().replace("-", "_").replace(" ", "_")

    def _to_taint(self, raw: object) -> TaintLevel | None:
        if isinstance(raw, TaintLevel):
            return raw
        if isinstance(raw, str):
            try:
                return TaintLevel(raw.strip().lower())
            except ValueError:
                return None
        return None


__all__ = ["OutputGateRunner"]
