"""Research state machine for planner — spec §4.8.

Tracks the lifecycle of research requests during planning. The planner
can dispatch up to N concurrent research micro-tasks to the executor
before finalizing a plan. This module enforces caps, deduplication,
timeouts, and state transitions deterministically.

Why a separate module: the state machine is complex enough to warrant
isolation from consumer logic. PlannerConsumer delegates research
tracking here; this module knows nothing about queues or agents.
"""

from __future__ import annotations

import enum
import hashlib
import time
from dataclasses import dataclass, field


class ResearchState(enum.StrEnum):
    """States for the planner research lifecycle (§4.8)."""

    planning = "planning"
    awaiting_research = "awaiting_research"
    ready_to_finalize = "ready_to_finalize"
    expired = "expired"


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    """A single research micro-task dispatched to the executor."""

    request_id: str
    query: str
    return_format: str
    max_tokens: int
    dispatched_at: float  # monotonic time


def _dedupe_key(query: str, return_format: str, max_tokens: int) -> str:
    """Deterministic hash for deduplication per §4.8.

    Why hash instead of tuple: the key is stored in a set and used for
    fast lookup; hashing normalizes whitespace differences.
    """
    raw = f"{query}|{return_format}|{max_tokens}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class ResearchStateMachine:
    """Manages research request lifecycle during planning.

    Enforces §4.8 normative controls: in-flight cap, round cap,
    per-request timeout, deduplication, and cancel-on-finalize.
    """

    max_in_flight: int = 3
    max_rounds: int = 5
    timeout_s: float = 120.0

    state: ResearchState = field(default=ResearchState.planning)

    # Why separate dicts: in-flight needs timeout checking, completed
    # needs result storage, and dedupe spans both.
    _in_flight: dict[str, ResearchRequest] = field(default_factory=dict)
    _results: dict[str, str] = field(default_factory=dict)
    _dedupe_keys: dict[str, str] = field(default_factory=dict)  # dedupe_hash -> request_id
    _total_dispatched: int = field(default=0)
    _seen_message_ids: set[str] = field(default_factory=set)

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    @property
    def total_dispatched(self) -> int:
        return self._total_dispatched

    @property
    def results(self) -> dict[str, str]:
        """All completed research results keyed by request_id."""
        return dict(self._results)

    @property
    def pending_request_ids(self) -> list[str]:
        return list(self._in_flight.keys())

    def request_research(
        self,
        request_id: str,
        query: str,
        return_format: str,
        max_tokens: int = 500,
        *,
        now: float | None = None,
    ) -> bool:
        """Attempt to dispatch a research request. Returns False if rejected.

        Rejection reasons: in-flight cap, round cap, duplicate, or wrong state.
        """
        if self.state == ResearchState.expired:
            return False

        if self.state == ResearchState.ready_to_finalize:
            return False

        # Round cap: total dispatched across the entire planning session
        if self._total_dispatched >= self.max_rounds:
            return False

        if len(self._in_flight) >= self.max_in_flight:
            return False

        dk = _dedupe_key(query, return_format, max_tokens)
        if dk in self._dedupe_keys:
            return False

        ts = now if now is not None else time.monotonic()
        req = ResearchRequest(
            request_id=request_id,
            query=query,
            return_format=return_format,
            max_tokens=max_tokens,
            dispatched_at=ts,
        )
        self._in_flight[request_id] = req
        self._dedupe_keys[dk] = request_id
        self._total_dispatched += 1

        # Transition: first research request moves us to awaiting
        if self.state == ResearchState.planning:
            self.state = ResearchState.awaiting_research

        return True

    def receive_result(
        self,
        request_id: str,
        result: str,
        *,
        message_id: str | None = None,
    ) -> bool:
        """Record a research result. Returns False if ignored (replay/unknown).

        Why message_id dedup: §4.8 requires duplicate research_result
        messages with same message_id to be acked and ignored.
        """
        if message_id is not None:
            if message_id in self._seen_message_ids:
                return False
            self._seen_message_ids.add(message_id)

        if request_id not in self._in_flight:
            # Late result after cancel/expire — ignore per §4.8
            return False

        del self._in_flight[request_id]
        self._results[request_id] = result

        # All research complete → ready to finalize
        if not self._in_flight and self.state == ResearchState.awaiting_research:
            self.state = ResearchState.ready_to_finalize

        return True

    def check_timeouts(self, *, now: float | None = None) -> list[str]:
        """Expire timed-out research requests. Returns list of expired request_ids.

        Why return IDs: caller may want to log or send error payloads
        for each timed-out request individually.
        """
        ts = now if now is not None else time.monotonic()
        expired_ids: list[str] = []

        for rid, req in list(self._in_flight.items()):
            if ts - req.dispatched_at >= self.timeout_s:
                expired_ids.append(rid)
                del self._in_flight[rid]

        # If all in-flight expired and we were awaiting, transition
        if not self._in_flight and self.state == ResearchState.awaiting_research:
            if self._results:
                # Some results exist — can finalize with partial data
                self.state = ResearchState.ready_to_finalize
            else:
                self.state = ResearchState.expired

        return expired_ids

    def force_expire(self) -> None:
        """Force transition to expired, canceling all in-flight requests.

        Used when plan-level timeout or max_rounds reached.
        """
        self._in_flight.clear()
        self.state = ResearchState.expired

    @property
    def has_timed_out_requests(self) -> bool:
        """True if any requests were lost to timeout (partial data)."""
        return self._total_dispatched > len(self._results) + len(self._in_flight)

    def finalize(self) -> dict[str, str]:
        """Consume results and reset to planning for next cycle.

        Returns all collected results. Cancels any remaining in-flight
        requests per §4.8 cancel semantics.
        """
        had_incomplete = len(self._in_flight) > 0 or self.has_timed_out_requests
        self._in_flight.clear()
        results = dict(self._results)
        self._results.clear()
        self.state = ResearchState.planning
        # Stash flag for caller to check
        self._last_finalize_was_partial = had_incomplete
        return results

    @property
    def last_finalize_was_partial(self) -> bool:
        """Whether the last finalize() had missing research results."""
        return getattr(self, "_last_finalize_was_partial", False)

    def reset(self) -> None:
        """Full reset for a new planning task."""
        self._in_flight.clear()
        self._results.clear()
        self._dedupe_keys.clear()
        self._seen_message_ids.clear()
        self._total_dispatched = 0
        self.state = ResearchState.planning


__all__ = [
    "ResearchRequest",
    "ResearchState",
    "ResearchStateMachine",
]
