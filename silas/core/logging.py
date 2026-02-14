"""Structured logging setup with correlation context propagation.

This module centralizes runtime logging configuration so all components
emit consistent records with turn/scope/work-item correlation fields.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Correlation identifiers for grouping related log records.

    A single runtime turn may involve multiple components (proxy, planner,
    execution). Keeping these IDs in one context object ensures every record
    can be tied back to the same orchestrated flow.
    """

    turn_id: str | None = None
    scope_id: str | None = None
    work_item_id: str | None = None


_EMPTY_CONTEXT = CorrelationContext()
_CORRELATION_CONTEXT: contextvars.ContextVar[CorrelationContext | None] = contextvars.ContextVar(
    "silas_correlation_context",
    default=None,
)


def get_correlation_context() -> CorrelationContext:
    """Return current correlation IDs for the active execution context.

    Reads from ``contextvars`` so async call chains share per-turn metadata
    without requiring correlation IDs in every function signature.
    """

    context = _CORRELATION_CONTEXT.get()
    if context is None:
        return _EMPTY_CONTEXT
    return context


def _current_otel_trace_id() -> str:
    """Extract the current OTel trace ID as a hex string, or empty."""
    try:
        from opentelemetry import trace as _trace_api

        span = _trace_api.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001
        pass
    return ""


class CorrelationFilter(logging.Filter):
    """Inject correlation fields into every ``LogRecord`` before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        context: CorrelationContext = get_correlation_context()
        record.turn_id = context.turn_id
        record.scope_id = context.scope_id
        record.work_item_id = context.work_item_id
        record.otel_trace_id = _current_otel_trace_id()
        return True


class _JsonFormatter(logging.Formatter):
    """Render logs as compact JSON for machine-readable ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object | None] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "turn_id": getattr(record, "turn_id", None),
            "scope_id": getattr(record, "scope_id", None),
            "work_item_id": getattr(record, "work_item_id", None),
            "trace_id": getattr(record, "otel_trace_id", None),
        }
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def setup_logging(level: int | str = logging.INFO, json_output: bool = False) -> None:
    """Configure root logging once with correlation-aware handlers.

    Centralizing setup avoids inconsistent formatter/filter combinations across
    modules and guarantees correlation fields are present for every log record.
    """

    root_logger: logging.Logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.filters.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    if json_output:
        formatter: logging.Formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s "
            "turn_id=%(turn_id)s scope_id=%(scope_id)s work_item_id=%(work_item_id)s "
            "trace_id=%(otel_trace_id)s "
            "%(message)s",
        )
    handler.setFormatter(formatter)

    correlation_filter = CorrelationFilter()
    handler.addFilter(correlation_filter)
    root_logger.addFilter(correlation_filter)
    root_logger.addHandler(handler)


@contextmanager
def correlation_scope(
    *,
    turn_id: str | None = None,
    scope_id: str | None = None,
    work_item_id: str | None = None,
) -> Iterator[None]:
    """Temporarily apply correlation IDs to the current async execution context.

    Nested scopes inherit outer values unless explicitly overridden, which
    keeps logs linked at turn-level while allowing more specific work-item IDs.
    """

    current: CorrelationContext = get_correlation_context()
    updated = CorrelationContext(
        turn_id=current.turn_id if turn_id is None else turn_id,
        scope_id=current.scope_id if scope_id is None else scope_id,
        work_item_id=current.work_item_id if work_item_id is None else work_item_id,
    )
    token: contextvars.Token[CorrelationContext | None] = _CORRELATION_CONTEXT.set(updated)
    try:
        yield
    finally:
        _CORRELATION_CONTEXT.reset(token)


__all__ = [
    "CorrelationContext",
    "CorrelationFilter",
    "correlation_scope",
    "get_correlation_context",
    "setup_logging",
]
