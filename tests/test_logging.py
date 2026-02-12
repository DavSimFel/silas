from __future__ import annotations

import logging

from silas.core.logging import (
    CorrelationFilter,
    correlation_scope,
    get_correlation_context,
)


def test_correlation_filter_injects_fields() -> None:
    correlation_filter = CorrelationFilter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello",
        args=(),
        exc_info=None,
    )

    with correlation_scope(turn_id="turn-1", scope_id="scope-1", work_item_id="work-1"):
        assert correlation_filter.filter(record) is True

    assert record.turn_id == "turn-1"
    assert record.scope_id == "scope-1"
    assert record.work_item_id == "work-1"


def test_correlation_scope_sets_and_clears_context() -> None:
    baseline = get_correlation_context()

    with correlation_scope(turn_id="turn-2", scope_id="scope-2", work_item_id="work-2"):
        current = get_correlation_context()
        assert current.turn_id == "turn-2"
        assert current.scope_id == "scope-2"
        assert current.work_item_id == "work-2"

    assert get_correlation_context() == baseline


def test_correlation_scope_nested_inherits_and_restores() -> None:
    baseline = get_correlation_context()

    with correlation_scope(turn_id="turn-outer", scope_id="scope-outer"):
        outer = get_correlation_context()
        assert outer.turn_id == "turn-outer"
        assert outer.scope_id == "scope-outer"
        assert outer.work_item_id is None

        with correlation_scope(scope_id="scope-inner", work_item_id="work-inner"):
            inner = get_correlation_context()
            assert inner.turn_id == "turn-outer"
            assert inner.scope_id == "scope-inner"
            assert inner.work_item_id == "work-inner"

        restored = get_correlation_context()
        assert restored == outer

    assert get_correlation_context() == baseline
