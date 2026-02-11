from __future__ import annotations

import re

from silas.core.token_counter import HeuristicTokenCounter
from silas.models.gates import Gate, GateLane, GateResult, GateTrigger
from silas.models.messages import TaintLevel

_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(
    r"\b(?:\+?\d{1,2}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
_TAINT_ORDER = {
    TaintLevel.owner: 0,
    TaintLevel.auth: 1,
    TaintLevel.external: 2,
}


class OutputGateRunner:
    """Deterministic output-gate evaluator for the Stream response path."""

    def __init__(
        self,
        gates: list[Gate],
        token_counter: HeuristicTokenCounter | None = None,
    ) -> None:
        self._gates = list(gates)
        self._token_counter = token_counter or HeuristicTokenCounter()

    def evaluate(
        self,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> tuple[str, list[GateResult]]:
        working_response = response_text
        results: list[GateResult] = []

        for gate in self._gates:
            result = self._evaluate_gate(
                gate=gate,
                response_text=working_response,
                response_taint=response_taint,
                sender_id=sender_id,
            )
            results.append(result)

            updated_response = self._extract_response_mutation(result)
            if updated_response is not None:
                working_response = updated_response

        return working_response, results

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
            return GateResult(
                gate_name=gate.name,
                lane=GateLane.policy,
                action="block",
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

        return self._continue_result(
            gate_name=gate.name,
            reason=f"PII markers detected: {', '.join(kinds)}",
            flags=flags,
            value=", ".join(kinds),
        )

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
