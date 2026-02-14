"""GateMixin — input and output gate evaluation."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from silas.models.approval import ApprovalScope, ApprovalVerdict
from silas.models.gates import Gate, GateResult, GateTrigger
from silas.models.messages import TaintLevel
from silas.models.work import WorkItem, WorkItemType

if TYPE_CHECKING:
    from silas.models.messages import ChannelMessage


class GateMixin:
    """Input and output gate compilation, evaluation, and approval."""

    def _precompile_active_gates(self) -> tuple[Gate, ...]:
        system_gates = self._load_system_gates()
        gate_runner = self._turn_context().gate_runner
        if gate_runner is None:
            return tuple(system_gates)

        precompile = getattr(gate_runner, "precompile_turn_gates", None)
        if not callable(precompile):
            return tuple(system_gates)

        compiled = precompile(system_gates=system_gates)
        return tuple(compiled)

    def _load_system_gates(self) -> list[Gate]:
        config = self._turn_context().config
        if config is None:
            return []

        # Current runtime config shape uses top-level output_gates; if
        # config.gates.system is introduced, it is merged below.
        raw_from_output = getattr(config, "output_gates", None)
        output_gates: list[Gate] = []
        if isinstance(raw_from_output, list):
            for gate in raw_from_output:
                if isinstance(gate, Gate):
                    output_gates.append(gate.model_copy(deep=True))

        raw_gates = getattr(config, "gates", None)
        if raw_gates is None:
            return output_gates

        raw_system = getattr(raw_gates, "system", None)
        if not isinstance(raw_system, list):
            return output_gates

        merged = list(output_gates)
        for gate in raw_system:
            if isinstance(gate, Gate):
                merged.append(gate.model_copy(deep=True))
        return merged

    async def _run_input_gates(
        self,
        *,
        active_gates: tuple[Gate, ...],
        message: ChannelMessage,
        connection_id: str,
        turn_number: int,
    ) -> tuple[str, str | None, list[GateResult]]:
        gate_runner = self._turn_context().gate_runner
        if gate_runner is None or not active_gates:
            await self._audit(
                "input_gates_evaluated",
                step=1,
                turn_number=turn_number,
                policy_results=[],
                quality_results=[],
                configured=bool(active_gates),
            )
            return message.text, None, []

        policy_results, quality_results, merged_context = await gate_runner.check_gates(
            gates=list(active_gates),
            trigger=GateTrigger.every_user_message,
            context={
                "message": message.text,
                "sender_id": message.sender_id,
            },
        )
        policy_payload = [result.model_dump(mode="json") for result in policy_results]
        quality_payload = [result.model_dump(mode="json") for result in quality_results]
        await self._audit(
            "input_gates_evaluated",
            step=1,
            turn_number=turn_number,
            policy_results=policy_payload,
            quality_results=quality_payload,
            configured=True,
        )
        if quality_payload:
            await self._audit(
                "quality_gate_input",
                turn_number=turn_number,
                results=quality_payload,
            )

        for result in policy_results:
            if result.action == "continue":
                continue

            if result.action == "require_approval":
                approved = await self._request_input_gate_approval(
                    result=result,
                    message=message,
                    connection_id=connection_id,
                    turn_number=turn_number,
                )
                if approved:
                    await self._audit(
                        "input_gate_approval_granted",
                        turn_number=turn_number,
                        gate_name=result.gate_name,
                    )
                    continue
                await self._audit(
                    "input_gate_approval_declined",
                    turn_number=turn_number,
                    gate_name=result.gate_name,
                    reason=result.reason,
                )
                blocked = self._input_gate_block_response(merged_context, result)
                return message.text, blocked, policy_results

            if result.action == "block":
                await self._audit(
                    "input_gate_blocked",
                    turn_number=turn_number,
                    gate_name=result.gate_name,
                    reason=result.reason,
                )
                blocked = self._input_gate_block_response(merged_context, result)
                return message.text, blocked, policy_results

        rewritten = merged_context.get("message")
        if isinstance(rewritten, str) and rewritten.strip():
            return rewritten, None, policy_results
        return message.text, None, policy_results

    def _input_gate_block_response(
        self,
        merged_context: dict[str, object],
        result: GateResult,
    ) -> str:
        response = merged_context.get("response")
        if isinstance(response, str) and response.strip():
            return response
        return f"Request blocked by gate '{result.gate_name}': {result.reason}"

    async def _request_input_gate_approval(
        self,
        *,
        result: GateResult,
        message: ChannelMessage,
        connection_id: str,
        turn_number: int,
    ) -> bool:
        approval_flow = self._approval_flow
        if approval_flow is None:
            return False

        work_item = WorkItem(
            id=f"input-gate:{turn_number}:{uuid.uuid4().hex}",
            type=WorkItemType.task,
            title=f"Input gate approval for {result.gate_name}",
            body=message.text,
        )
        decision, token = await approval_flow.request_skill_approval(
            work_item=work_item,
            scope=ApprovalScope.full_plan,
            skill_name=result.gate_name,
            connection_id=connection_id,
        )
        if decision is None or token is None:
            return False
        return decision.verdict == ApprovalVerdict.approved

    async def _evaluate_output_gates(
        self, response_text: str, response_taint: TaintLevel, sender_id: str, turn_number: int,
    ) -> tuple[str, list[str]]:
        blocked_gate_names: list[str] = []
        if self.output_gate_runner is None:
            await self._audit("output_gates_evaluated", turn_number=turn_number, results=[], configured=False)
            return response_text, blocked_gate_names

        response_text, gate_results = self.output_gate_runner.evaluate_output(
            response_text=response_text, response_taint=response_taint, sender_id=sender_id,
        )
        results_payload = [r.model_dump(mode="json") for r in gate_results]
        warnings = [r.model_dump(mode="json") for r in gate_results if "warn" in r.flags]
        blocked_gate_names = [r.gate_name for r in gate_results if r.action == "block"]

        await self._audit("output_gates_evaluated", turn_number=turn_number, results=results_payload, configured=True)
        if warnings:
            await self._audit("output_gate_warnings", turn_number=turn_number, warnings=warnings)
        if blocked_gate_names:
            response_text = "I cannot share that"
            await self._audit("output_gate_blocked", turn_number=turn_number, blocked_gates=blocked_gate_names)
        return response_text, blocked_gate_names
