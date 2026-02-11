from __future__ import annotations

from math import ceil


class HeuristicTokenCounter:
    def count(self, text: str) -> int:
        return ceil(len(text) / 3.5)


__all__ = ["HeuristicTokenCounter"]
