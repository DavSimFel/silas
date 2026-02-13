from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

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
from silas.protocols.gates import GateCheckProvider, GateRunner

if TYPE_CHECKING:
    from silas.models.work import WorkItem


class SilasGateRunner(GateRunner):
    """Two-lane gate runner for policy enforcement and quality observability."""

    def __init__(
        self,
        providers: Mapping[GateProvider | str, GateCheckProvider] | None = None,
        predicate_checker: PredicateChecker | None = None,
        script_checker: ScriptChecker | None = None,
        llm_checker: GateCheckProvider | None = None,
    ) -> None:
        self._providers: dict[str, GateCheckProvider] = {}
        self.quality_log: list[GateResult] = []
        self.rejected_mutations: list[tuple[str, str]] = []

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


__all__ = ["SilasGateRunner"]
