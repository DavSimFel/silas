"""Tests for HeuristicTokenCounter.

Spec: count = ceil(len(text) / 3.5)
"""

from __future__ import annotations

from silas.core.token_counter import HeuristicTokenCounter


class TestHeuristicTokenCounter:
    def test_empty_string(self) -> None:
        assert HeuristicTokenCounter().count("") == 0

    def test_short_string(self) -> None:
        # 4 chars / 3.5 = 1.14 → ceil = 2
        assert HeuristicTokenCounter().count("abcd") == 2

    def test_exact_multiple(self) -> None:
        # 7 chars / 3.5 = 2.0 → ceil = 2
        assert HeuristicTokenCounter().count("a" * 7) == 2

    def test_one_char(self) -> None:
        # 1 / 3.5 = 0.286 → ceil = 1
        assert HeuristicTokenCounter().count("x") == 1

    def test_longer_text(self) -> None:
        # 100 chars / 3.5 = 28.57 → ceil = 29
        assert HeuristicTokenCounter().count("a" * 100) == 29

    def test_unicode_counts_characters(self) -> None:
        # 4 unicode chars / 3.5 = 1.14 → ceil = 2
        text = "über"
        assert HeuristicTokenCounter().count(text) == 2

    def test_whitespace_counts(self) -> None:
        # "a b" = 3 chars / 3.5 = 0.857 → ceil = 1
        assert HeuristicTokenCounter().count("a b") == 1

    def test_large_text(self) -> None:
        # 10000 / 3.5 = 2857.14 → ceil = 2858
        assert HeuristicTokenCounter().count("x" * 10000) == 2858
