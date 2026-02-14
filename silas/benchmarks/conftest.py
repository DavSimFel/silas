"""Pytest plugin for benchmark marker support.

Why a conftest here instead of a pytest plugin: keeps the benchmark infra
self-contained within the silas package. Tests use ``@pytest.mark.benchmark``
and run with ``pytest -m benchmark``.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "benchmark: mark test as a benchmark (deselect with '-m \"not benchmark\"')"
    )
