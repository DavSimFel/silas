from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from silas.core.token_counter import HeuristicTokenCounter
from silas.gates.guardrails_provider import GuardrailsChecker
from silas.gates.predicates import PredicateChecker
from silas.gates.script import ScriptChecker
from silas.models.gates import (
    ALLOWED_MUTATIONS,
    Gate,
    GateLane,
    GateProvider,
    GateResult,
    GateTrigger,
)
from silas.models.messages import TaintLevel
from silas.protocols.gates import GateCheckProvider, GateRunner

if TYPE_CHECKING:
    from silas.models.work import WorkItem

# PII patterns live here so both input and output paths share one definition.
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(
    r"\b(?:\+?\d{1,2}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_TAINT_ORDER: dict[TaintLevel, int] = {
    TaintLevel.owner: 0,
    TaintLevel.auth: 1,
    TaintLevel.external: 2,
}
_DEFAULT_BLOCK_MESSAGE = "I cannot share that"


@dataclass(frozen=True)
class _Escalation:
    action: str
    message: str | None = None


class SilasGateRunner(GateRunner):
    """Two-lane gate runner for both input and output gate evaluation.

    Why one runner: the spec mandates a single policy+quality lane model.
    Output-specific checks (PII, taint ceiling, length) are built-in
    providers keyed off GateTrigger.every_agent_response so they follow
    the same escalation and mutation-sanitisation pipeline as input gates.
    """

    def __init__(
        self,
        providers: Mapping[GateProvider | str, GateCheckProvider] | None = None,
        predicate_checker: PredicateChecker | None = None,
        script_checker: ScriptChecker | None = None,
        llm_checker: GateCheckProvider | None = None,
        token_counter: HeuristicTokenCounter | None = None,
        escalation_map: Mapping[str, object] | None = None,
    ) -> None:
        self._providers: dict[str, GateCheckProvider] = {}
        self.quality_log: list[GateResult] = []
        self.rejected_mutations: list[tuple[str, str]] = []
        self._token_counter = token_counter or HeuristicTokenCounter()
        self._escalation_map: dict[str, object] = dict(escalation_map or {})

        self.register_provider(GateProvider.predicate, predicate_checker or PredicateChecker())
        self.register_provider(GateProvider.script, script_checker or ScriptChecker())
        if llm_checker is not None:
            self.register_provider(GateProvider.llm, llm_checker)
        # Guardrails AI — always registered; fails clearly at check time if lib missing
        self.register_provider(GateProvider.guardrails_ai, GuardrailsChecker())
        if providers:
            for provider_name, provider in providers.items():
                self.register_provider(provider_name, provider)

    def register_provider(self, provider_name: GateProvider | str, provider: GateCheckProvider) -> None:
        self._providers[self._provider_key(provider_name)] = provider

    def precompile_turn_gates(
        self,
        system_gates: Sequence[Gate] | None = None,
        work_item_gates: Sequence[Gate] | None = None,
        work_item: WorkItem | None = None,
    ) -> tuple[Gate, ...]:
        compiled: list[Gate] = [gate.model_copy(deep=True) for gate in (system_gates or [])]

        if work_item_gates is not None:
            compiled.extend(gate.model_copy(deep=True) for gate in work_item_gates)
        elif work_item is not None:
            compiled.extend(gate.model_copy(deep=True) for gate in work_item.gates)

        return tuple(compiled)

    def precompile_execution_gates(
        self,
        system_gates: Sequence[Gate] | None = None,
        work_item: WorkItem | None = None,
    ) -> tuple[Gate, ...]:
        return self.precompile_turn_gates(system_gates=system_gates, work_item=work_item)

    async def check_after_step(
        self,
        gates: Sequence[Gate],
        step_index: int,
        context: Mapping[str, object],
    ) -> tuple[list[GateResult], list[GateResult], dict[str, object]]:
        scoped_context = dict(context)
        scoped_context["step_index"] = step_index
        return await self.check_gates(
            gates=list(gates),
            trigger=GateTrigger.after_step,
            context=scoped_context,
        )

    async def check_gates(
        self,
        gates: list[Gate],
        trigger: GateTrigger,
        context: dict[str, object],
    ) -> tuple[list[GateResult], list[GateResult], dict[str, object]]:
        step_index = self._step_index_from_context(context) if trigger == GateTrigger.after_step else None
        matched = self._matching_gates(gates, trigger, step_index)
        working_context = dict(context)

        policy_results: list[GateResult] = []
        quality_results: list[GateResult] = []

        for gate in matched:
            if gate.lane != GateLane.policy:
                continue
            result, allowed_mutations = await self._evaluate_policy_gate(gate, working_context)
            policy_results.append(result)
            if allowed_mutations:
                self._merge_allowed_mutations(working_context, allowed_mutations)

        for gate in matched:
            if gate.lane != GateLane.quality:
                continue
            result = await self._evaluate_quality_gate(gate, working_context)
            quality_results.append(result)

        self.quality_log.extend(quality_results)
        return policy_results, quality_results, working_context

    async def check_gate(self, gate: Gate, context: dict[str, object]) -> GateResult:
        if gate.lane == GateLane.quality:
            return await self._evaluate_quality_gate(gate, context)
        result, _ = await self._evaluate_policy_gate(gate, context)
        return result

    async def _evaluate_policy_gate(
        self,
        gate: Gate,
        context: Mapping[str, object],
    ) -> tuple[GateResult, dict[str, object] | None]:
        raw = await self._run_provider(gate, context)
        normalized = self._normalize_policy_result(gate, raw)
        return self._sanitize_policy_mutation(gate, normalized)

    async def _evaluate_quality_gate(self, gate: Gate, context: Mapping[str, object]) -> GateResult:
        raw = await self._run_provider(gate, context)
        return self._normalize_quality_result(gate, raw)

    async def _run_provider(self, gate: Gate, context: Mapping[str, object]) -> GateResult:
        provider_name = self._provider_key(gate.provider)
        provider = self._providers.get(provider_name)
        if provider is None:
            return GateResult(
                gate_name=gate.name,
                lane=GateLane.policy,
                action="block",
                reason=f"No provider: {provider_name}",
            )

        try:
            result = await provider.check(gate, dict(context))
        except (ValueError, TypeError, RuntimeError, OSError, TimeoutError) as exc:
            return GateResult(
                gate_name=gate.name,
                lane=GateLane.policy,
                action="block",
                reason=f"provider '{provider_name}' failed: {exc}",
                flags=["provider_error"],
            )

        if not isinstance(result, GateResult):
            return GateResult(
                gate_name=gate.name,
                lane=GateLane.policy,
                action="block",
                reason=f"provider '{provider_name}' returned invalid result",
            )
        return result

    def _normalize_policy_result(self, gate: Gate, result: GateResult) -> GateResult:
        flags = list(result.flags)
        if result.lane != GateLane.policy:
            flags.append("lane_coerced_policy")
        return GateResult(
            gate_name=gate.name,
            lane=GateLane.policy,
            action=result.action,
            reason=result.reason,
            value=result.value,
            score=result.score,
            flags=flags,
            modified_context=result.modified_context,
        )

    def _normalize_quality_result(self, gate: Gate, result: GateResult) -> GateResult:
        flags = list(result.flags)
        reason = result.reason

        if result.action != "continue":
            flags.append("quality_lane_violation")
            reason = f"{reason} (quality action overridden to continue)"
        if isinstance(result.modified_context, dict):
            flags.append("quality_mutation_ignored")

        return GateResult(
            gate_name=gate.name,
            lane=GateLane.quality,
            action="continue",
            reason=reason,
            value=result.value,
            score=result.score,
            flags=sorted(set(flags)),
            modified_context=None,
        )

    def _sanitize_policy_mutation(
        self,
        gate: Gate,
        result: GateResult,
    ) -> tuple[GateResult, dict[str, object] | None]:
        mutation = result.modified_context
        if not isinstance(mutation, dict):
            return result, None

        allowed: dict[str, object] = {}
        flags = list(result.flags)
        for key, value in mutation.items():
            if key in ALLOWED_MUTATIONS:
                allowed[key] = value
                continue
            self.rejected_mutations.append((gate.name, key))
            flags.append(f"rejected_mutation:{key}")

        sanitized_result = GateResult(
            gate_name=result.gate_name,
            lane=result.lane,
            action=result.action,
            reason=result.reason,
            value=result.value,
            score=result.score,
            flags=flags,
            modified_context=allowed or None,
        )
        return sanitized_result, allowed or None

    def _merge_allowed_mutations(
        self,
        context: dict[str, object],
        mutation: Mapping[str, object],
    ) -> None:
        for key, value in mutation.items():
            if key == "tool_args" and isinstance(value, Mapping):
                existing = context.get("tool_args")
                merged = dict(existing) if isinstance(existing, Mapping) else {}
                merged.update(value)
                context["tool_args"] = merged
                continue
            context[key] = value

    def _matching_gates(
        self,
        gates: Sequence[Gate],
        trigger: GateTrigger,
        step_index: int | None,
    ) -> list[Gate]:
        matched: list[Gate] = []
        for gate in gates:
            if gate.on != trigger:
                continue
            if trigger == GateTrigger.after_step and step_index is not None and gate.after_step != step_index:
                continue
            matched.append(gate)
        return matched

    def _step_index_from_context(self, context: Mapping[str, object]) -> int | None:
        raw = context.get("step_index")
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
            return int(raw)
        return None

    def _provider_key(self, provider_name: GateProvider | str) -> str:
        if isinstance(provider_name, GateProvider):
            return provider_name.value
        return str(provider_name).strip().lower()

    # ── Output gate evaluation ─────────────────────────────────────────
    # Why built-in: output checks (PII, taint, length) are deterministic
    # and don't need pluggable providers — they operate on the response
    # text directly.  Keeping them here preserves the two-lane model.

    def set_output_gates(self, gates: Sequence[Gate]) -> None:
        """Configure which gates run during output evaluation."""
        self._output_gates = list(gates)

    def evaluate_output(
        self,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
        gates: Sequence[Gate] | None = None,
    ) -> tuple[str, list[GateResult]]:
        """Synchronous output gate pipeline mirroring the async input path.

        Returns the (possibly rewritten) response and all gate results.
        """
        working_response = response_text
        active_gates = list(gates if gates is not None else getattr(self, "_output_gates", []))
        policy_results: list[GateResult] = []
        quality_results: list[GateResult] = []

        for gate in active_gates:
            if gate.lane != GateLane.policy:
                continue
            result = self._evaluate_output_policy_gate(
                gate, working_response, response_taint, sender_id,
            )
            policy_results.append(result)
            if result.action == "continue":
                updated = self._extract_response_mutation(result)
                if updated is not None:
                    working_response = updated

        for gate in active_gates:
            if gate.lane != GateLane.quality:
                continue
            quality_results.append(
                self._evaluate_output_quality_gate(
                    gate, working_response, response_taint, sender_id,
                )
            )

        self.quality_log.extend(quality_results)
        return working_response, [*policy_results, *quality_results]

    def _evaluate_output_policy_gate(
        self,
        gate: Gate,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> GateResult:
        result = self._evaluate_output_gate_check(
            gate, response_text, response_taint, sender_id,
        )
        if result.action != "block":
            return result
        return self._apply_escalation(gate, result, response_text)

    def _evaluate_output_quality_gate(
        self,
        gate: Gate,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> GateResult:
        raw = self._evaluate_output_gate_check(
            gate, response_text, response_taint, sender_id,
        )
        return self._normalize_quality_result(gate, raw)

    def _evaluate_output_gate_check(
        self,
        gate: Gate,
        response_text: str,
        response_taint: TaintLevel,
        sender_id: str,
    ) -> GateResult:
        if gate.on != GateTrigger.every_agent_response:
            return self._output_continue(
                gate.name, f"skipped trigger={gate.on.value}",
            )

        check_name = self._output_check_name(gate)
        if check_name == "taint_ceiling":
            return self._taint_ceiling(gate, response_taint)
        if check_name == "length_limit":
            return self._length_limit(gate, response_text)
        if check_name == "pii_marker":
            return self._pii_marker(gate, response_text)

        return self._output_continue(
            gate.name,
            f"unknown output gate check: {check_name}",
            flags=["warn", "unknown_output_gate"],
            value=sender_id,
        )

    # ── Built-in output checks ─────────────────────────────────────────

    def _taint_ceiling(self, gate: Gate, response_taint: TaintLevel) -> GateResult:
        raw_threshold = (
            gate.config.get("threshold")
            or gate.config.get("max_taint")
            or gate.config.get("taint_ceiling")
            or TaintLevel.external.value
        )
        threshold = self._to_taint(raw_threshold)
        if threshold is None:
            return self._output_continue(
                gate.name,
                f"invalid taint threshold: {raw_threshold!r}",
                flags=["warn", "invalid_gate_config"],
            )

        taint_rank = _TAINT_ORDER[response_taint]
        threshold_rank = _TAINT_ORDER[threshold]
        if taint_rank > threshold_rank:
            return self._output_block(
                gate.name,
                f"response taint {response_taint.value} exceeds threshold {threshold.value}",
                value=response_taint.value,
            )
        return self._output_continue(
            gate.name,
            f"response taint {response_taint.value} within threshold {threshold.value}",
            value=response_taint.value,
        )

    def _length_limit(self, gate: Gate, response_text: str) -> GateResult:
        raw_limit = gate.config.get("max_tokens")
        if not isinstance(raw_limit, int) or raw_limit <= 0:
            return self._output_continue(
                gate.name,
                f"invalid max_tokens: {raw_limit!r}",
                flags=["warn", "invalid_gate_config"],
            )

        token_count = self._token_counter.count(response_text)
        if token_count <= raw_limit:
            return self._output_continue(
                gate.name,
                f"response length {token_count} <= limit {raw_limit}",
                value=float(token_count),
            )

        mode = str(gate.config.get("mode", "truncate")).strip().lower()
        if mode == "warn":
            return self._output_continue(
                gate.name,
                f"response length {token_count} exceeds limit {raw_limit}",
                flags=["warn", "length_exceeded"],
                value=float(token_count),
            )
        if mode == "block":
            return self._output_block(
                gate.name,
                f"response length {token_count} exceeds limit {raw_limit}",
                value=float(token_count),
            )

        truncated = self._truncate_to_token_limit(response_text, raw_limit)
        return self._output_continue(
            gate.name,
            f"response length {token_count} truncated to <= {raw_limit} tokens",
            flags=["warn", "length_exceeded", "truncated"],
            value=float(token_count),
            modified_context={"response": truncated},
        )

    def _pii_marker(self, gate: Gate, response_text: str) -> GateResult:
        has_email = bool(_EMAIL_PATTERN.search(response_text))
        has_phone = bool(_PHONE_PATTERN.search(response_text))

        if not has_email and not has_phone:
            return self._output_continue(gate.name, "no PII marker detected")

        flags = ["warn", "pii_detected"]
        kinds: list[str] = []
        if has_email:
            flags.append("pii_email")
            kinds.append("email")
        if has_phone:
            flags.append("pii_phone")
            kinds.append("phone")

        if self._has_explicit_escalation(gate):
            return self._output_block(
                gate.name,
                f"PII markers detected: {', '.join(kinds)}",
                flags=flags,
                value=", ".join(kinds),
            )
        return self._output_continue(
            gate.name,
            f"PII markers detected: {', '.join(kinds)}",
            flags=flags,
            value=", ".join(kinds),
        )

    # ── Escalation (shared by output policy gates) ─────────────────────

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
            return _Escalation(action="block_with_message", message=self._message_from_raw(inner))
        if stripped.startswith("block_with_message:"):
            _, _, tail = stripped.partition(":")
            return _Escalation(action="block_with_message", message=self._message_from_raw(tail))
        action = self._normalize_escalation_action(stripped)
        if action is None:
            return None
        return _Escalation(action=action)

    def _normalize_escalation_action(self, raw: object) -> str | None:
        if not isinstance(raw, str):
            return None
        normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {"block": "block_with_message", "respond": "block_with_message", "report": "block_with_message"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"block_with_message", "redact", "require_approval", "log_and_pass"}:
            return None
        return normalized

    def _message_from_raw(self, raw: object) -> str | None:
        if not isinstance(raw, str):
            return None
        message = raw.strip().strip('"').strip("'").strip()
        return message or None

    def _has_explicit_escalation(self, gate: Gate) -> bool:
        if gate.name in self._escalation_map:
            return True
        if "escalation" in gate.config or "on_block" in gate.config:
            return True
        return gate.on_block != "report"

    # ── Output-gate helpers ────────────────────────────────────────────

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
        return candidate if isinstance(candidate, str) else None

    def _output_check_name(self, gate: Gate) -> str:
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

    def _output_continue(
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

    def _output_block(
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


__all__ = ["SilasGateRunner"]
