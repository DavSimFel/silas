from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from silas.agents.structured import run_structured_agent
from silas.models.gates import Gate, GateLane, GateResult


class _QualityAdvice(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)
    reason: str


class _PolicyDecision(BaseModel):
    action: Literal["continue", "block", "require_approval"]
    reason: str
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)


class _StructuredRunnable(Protocol):
    async def run(self, prompt: str) -> object: ...


class LLMChecker:
    """LLM-backed gate provider for advisory quality checks."""

    def __init__(
        self,
        quality_agent: _StructuredRunnable,
        *,
        timeout_seconds: float = 8.0,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be > 0")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")

        self._quality_agent = quality_agent
        self._timeout_seconds = timeout_seconds
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._now_fn = now_fn or (lambda: datetime.now(UTC))

        self._consecutive_failures = 0
        self._breaker_open_until: datetime | None = None

    async def check(self, gate: Gate, context: dict[str, object]) -> GateResult:
        now = self._now_fn()
        if self._is_circuit_open(now):
            return self._circuit_open_result(gate)

        prompt = self._build_prompt(gate, context)
        try:
            raw = await asyncio.wait_for(
                run_structured_agent(
                    agent=self._quality_agent,
                    prompt=prompt,
                    call_name="planner",
                ),
                timeout=self._timeout_seconds,
            )
            result = self._parse_response(gate, raw)
            self._clear_failures()
            return result
        except Exception as exc:
            self._record_failure(now)
            return self._failure_result(gate, str(exc))

    def _parse_response(self, gate: Gate, payload: object) -> GateResult:
        if gate.promote_to_policy:
            parsed = _PolicyDecision.model_validate(payload)
            return GateResult(
                gate_name=gate.name,
                lane=GateLane.policy,
                action=parsed.action,
                reason=parsed.reason,
                score=parsed.score,
                flags=parsed.flags,
            )

        parsed = _QualityAdvice.model_validate(payload)
        return GateResult(
            gate_name=gate.name,
            lane=GateLane.quality,
            action="continue",
            reason=parsed.reason,
            score=parsed.score,
            flags=parsed.flags,
        )

    def _build_prompt(self, gate: Gate, context: Mapping[str, object]) -> str:
        content = self._extract_content(gate, context)
        check = gate.check or gate.type.value
        config_payload = json.dumps(gate.config, sort_keys=True)
        mode = "policy" if gate.promote_to_policy else "quality"

        if gate.promote_to_policy:
            schema_hint = (
                'Return JSON with fields: "action" ("continue"|"block"|"require_approval"), '
                '"reason" (string), optional "score" (0..1), optional "flags" (list[str]).'
            )
        else:
            schema_hint = (
                'Return JSON with fields: "score" (0..1), "flags" (list[str]), "reason" (string).'
            )

        return (
            "You are evaluating content for a Silas runtime gate.\n"
            f"Gate name: {gate.name}\n"
            f"Gate mode: {mode}\n"
            f"Check: {check}\n"
            f"Config: {config_payload}\n\n"
            "Content:\n"
            f"{content}\n\n"
            "Respond with strict JSON only.\n"
            f"{schema_hint}"
        )

    def _extract_content(self, gate: Gate, context: Mapping[str, object]) -> str:
        if isinstance(gate.extract, str) and gate.extract in context:
            return self._as_text(context[gate.extract])
        for key in ("response", "message", "text", "value", "step_output"):
            if key in context:
                return self._as_text(context[key])
        return self._as_text(context)

    def _as_text(self, value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, sort_keys=True)
        if value is None:
            return ""
        return str(value)

    def _is_circuit_open(self, now: datetime) -> bool:
        if self._breaker_open_until is None:
            return False
        if now >= self._breaker_open_until:
            self._breaker_open_until = None
            self._consecutive_failures = 0
            return False
        return True

    def _record_failure(self, now: datetime) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._breaker_open_until = now + timedelta(seconds=self._cooldown_seconds)

    def _clear_failures(self) -> None:
        self._consecutive_failures = 0
        self._breaker_open_until = None

    def _failure_result(self, gate: Gate, detail: str) -> GateResult:
        if gate.promote_to_policy:
            return GateResult(
                gate_name=gate.name,
                lane=GateLane.policy,
                action="block",
                reason=f"llm gate failed: {detail}",
                flags=["llm_error"],
            )
        return GateResult(
            gate_name=gate.name,
            lane=GateLane.quality,
            action="continue",
            reason=f"llm gate failed: {detail}",
            score=None,
            flags=["llm_error"],
        )

    def _circuit_open_result(self, gate: Gate) -> GateResult:
        reason = "llm gate skipped: circuit breaker open"
        if gate.promote_to_policy:
            return GateResult(
                gate_name=gate.name,
                lane=GateLane.policy,
                action="block",
                reason=reason,
                flags=["llm_error", "circuit_open"],
            )
        return GateResult(
            gate_name=gate.name,
            lane=GateLane.quality,
            action="continue",
            reason=reason,
            score=None,
            flags=["llm_error", "circuit_open"],
        )


__all__ = ["LLMChecker"]
