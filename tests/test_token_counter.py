from __future__ import annotations

from silas.core.token_counter import HeuristicTokenCounter


def test_token_counter_uses_ceiling_ratio() -> None:
    counter = HeuristicTokenCounter()
    assert counter.count("") == 0
    assert counter.count("abcd") == 2
    assert counter.count("a" * 7) == 2
