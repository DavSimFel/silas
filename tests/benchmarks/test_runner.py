"""Tests for the benchmark runner infrastructure.

Verifies that the decorator registers benchmarks, the runner collects
results, and results serialize correctly to JSON.
"""

from __future__ import annotations

import json

import pytest
from silas.benchmarks.runner import (
    _REGISTRY,
    BenchmarkResult,
    BenchmarkRunner,
    benchmark,
    get_registry,
)

# --- Decorator registration tests ---


def test_benchmark_decorator_registers_sync() -> None:
    """@benchmark registers a sync function in the global registry."""
    initial_count = len(_REGISTRY)

    @benchmark(name="test.sync_op", tags=["test"], iterations=5)
    def my_sync_bench() -> None:
        total = sum(range(100))  # noqa: F841

    assert "test.sync_op" in _REGISTRY
    entry = _REGISTRY["test.sync_op"]
    assert entry.name == "test.sync_op"
    assert entry.is_async is False
    assert entry.iterations == 5
    assert "test" in entry.tags
    assert len(_REGISTRY) == initial_count + 1


def test_benchmark_decorator_registers_async() -> None:
    """@benchmark registers an async function and detects it as async."""

    @benchmark(name="test.async_op", tags=["test"], iterations=3)
    async def my_async_bench() -> None:
        pass

    entry = _REGISTRY["test.async_op"]
    assert entry.is_async is True
    assert entry.iterations == 3


def test_benchmark_decorator_default_name() -> None:
    """When no name is given, the function's qualname is used."""

    @benchmark(iterations=1)
    def auto_named() -> None:
        pass

    # qualname includes the enclosing function
    assert any("auto_named" in k for k in _REGISTRY)


def test_get_registry_returns_copy() -> None:
    """get_registry returns a copy so mutations don't affect the global."""
    reg = get_registry()
    reg["fake"] = None  # type: ignore[assignment]
    assert "fake" not in _REGISTRY


# --- BenchmarkResult tests ---


def test_result_to_dict() -> None:
    result = BenchmarkResult(
        operation="test_op",
        latency_ms=1.2345,
        throughput_ops_sec=812.5,
        memory_mb=0.0,
        iterations=100,
        tags=["unit"],
    )
    d = result.to_dict()
    assert d["operation"] == "test_op"
    assert d["latency_ms"] == 1.234  # rounded to 3 decimals (truncates)
    assert d["throughput_ops_sec"] == 812.5
    assert d["tags"] == ["unit"]


# --- BenchmarkRunner tests ---


@pytest.mark.benchmark
async def test_runner_collects_results() -> None:
    """Runner executes registered benchmarks and collects results."""

    # Register a known benchmark for this test
    @benchmark(name="test.runner_target", tags=["runner_test"], iterations=2)
    def _target() -> None:
        _ = list(range(1000))

    runner = BenchmarkRunner(tag_filter=["runner_test"])
    results = await runner.run_all()
    assert len(results) >= 1
    target_result = next(r for r in results if r.operation == "test.runner_target")
    assert target_result.iterations == 2
    assert target_result.latency_ms > 0
    assert target_result.throughput_ops_sec > 0


@pytest.mark.benchmark
async def test_runner_async_benchmark() -> None:
    """Runner correctly handles async benchmark functions."""

    @benchmark(name="test.async_target", tags=["async_test"], iterations=2)
    async def _async_target() -> None:
        import asyncio

        await asyncio.sleep(0.001)

    runner = BenchmarkRunner(tag_filter=["async_test"])
    results = await runner.run_all()
    assert any(r.operation == "test.async_target" for r in results)


@pytest.mark.benchmark
async def test_runner_to_json() -> None:
    """Runner serializes results to valid JSON with expected structure."""

    @benchmark(name="test.json_target", tags=["json_test"], iterations=1)
    def _json_target() -> None:
        pass

    runner = BenchmarkRunner(tag_filter=["json_test"])
    await runner.run_all()
    raw = runner.to_json()
    data = json.loads(raw)
    assert "timestamp" in data
    assert "benchmarks" in data
    assert isinstance(data["benchmarks"], list)
    assert len(data["benchmarks"]) >= 1


@pytest.mark.benchmark
async def test_runner_write_report(tmp_path: object) -> None:
    """Runner writes a dated JSON report file."""
    import tempfile

    @benchmark(name="test.report_target", tags=["report_test"], iterations=1)
    def _report_target() -> None:
        pass

    runner = BenchmarkRunner(tag_filter=["report_test"])
    await runner.run_all()
    with tempfile.TemporaryDirectory() as tmpdir:
        path = runner.write_report(tmpdir)
        assert path.exists()
        assert path.suffix == ".json"
        data = json.loads(path.read_text())
        assert "benchmarks" in data


@pytest.mark.benchmark
async def test_runner_tag_filter() -> None:
    """Runner only runs benchmarks matching the tag filter."""

    @benchmark(name="test.included", tags=["include_me"], iterations=1)
    def _included() -> None:
        pass

    @benchmark(name="test.excluded", tags=["other_tag"], iterations=1)
    def _excluded() -> None:
        pass

    runner = BenchmarkRunner(tag_filter=["include_me"])
    results = await runner.run_all()
    ops = [r.operation for r in results]
    assert "test.included" in ops
    assert "test.excluded" not in ops
