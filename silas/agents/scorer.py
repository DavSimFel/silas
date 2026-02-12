"""Scorer Agent — structured context eviction scorer."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pydantic_ai import Agent

from silas.agents.structured import run_structured_agent
from silas.models.scorer import ScorerOutput

logger = logging.getLogger(__name__)

DEFAULT_SCORER_SYSTEM_PROMPT = """You are the Silas context relevance scorer.

Return a valid ScorerOutput for every request.

Group related context block IDs together in keep_groups or evict_groups.
Prefer coherent group eviction over orphaning dependent blocks.
"""

_SCORER_TIMEOUT_SECONDS = 2.0
_SCORER_BREAKER_FAILURE_LIMIT = 3
_SCORER_BREAKER_COOLDOWN = timedelta(minutes=5)


@dataclass(slots=True)
class ScorerRunResult:
    output: ScorerOutput


class ContextScorer:
    def __init__(
        self,
        model: str,
        timeout_seconds: float = _SCORER_TIMEOUT_SECONDS,
        breaker_failure_limit: int = _SCORER_BREAKER_FAILURE_LIMIT,
        breaker_cooldown: timedelta = _SCORER_BREAKER_COOLDOWN,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.breaker_failure_limit = breaker_failure_limit
        self.breaker_cooldown = breaker_cooldown
        self.system_prompt = DEFAULT_SCORER_SYSTEM_PROMPT

        self._consecutive_failures = 0
        self._breaker_open_until: datetime | None = None

        try:
            self.agent: Agent[None, ScorerOutput] | None = Agent(
                model=model,
                output_type=ScorerOutput,
                system_prompt=self.system_prompt,
            )
        except Exception:
            logger.warning("Failed to initialize scorer agent; deterministic fallback mode")
            self.agent = None

    async def run(self, prompt: str) -> ScorerRunResult:
        if self._breaker_is_open():
            return self._deterministic_fallback()

        if self.agent is None:
            return self._deterministic_fallback()

        try:
            result = await asyncio.wait_for(
                run_structured_agent(
                    agent=self.agent,
                    prompt=prompt,
                    call_name="scorer",
                ),
                timeout=self.timeout_seconds,
            )
        except Exception:
            self._record_failure()
            logger.warning("Scorer call failed; deterministic fallback output")
            return self._deterministic_fallback()

        if not isinstance(result, ScorerOutput):
            self._record_failure()
            logger.warning("Scorer returned invalid output type; deterministic fallback output")
            return self._deterministic_fallback()

        self._record_success()
        return ScorerRunResult(output=result)

    def _deterministic_fallback(self) -> ScorerRunResult:
        return ScorerRunResult(output=ScorerOutput())

    def _breaker_is_open(self) -> bool:
        if self._breaker_open_until is None:
            return False

        now = datetime.now(timezone.utc)
        if now >= self._breaker_open_until:
            self._record_success()
            return False
        return True

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._breaker_open_until = None

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures < self.breaker_failure_limit:
            return
        self._breaker_open_until = datetime.now(timezone.utc) + self.breaker_cooldown


async def score_context_blocks(agent: ContextScorer, prompt: str) -> ScorerOutput:
    result = await agent.run(prompt)
    return result.output


def build_scorer_agent(model: str) -> ContextScorer:
    return ContextScorer(model=model)


__all__ = [
    "ContextScorer",
    "ScorerRunResult",
    "build_scorer_agent",
    "score_context_blocks",
]
