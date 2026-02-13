"""Benchmark runner — collects and executes registered benchmarks.

Why a custom runner instead of pytest-benchmark: we need async support,
structured JSON output for CI, and tight integration with Silas's own
metrics (memory store sizes, queue depths, etc.).
"""

from __future__ import annotations

import asyncio
import json
import resource
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Global registry — populated by @benchmark decorator at import time.
_REGISTRY: dict[str, BenchmarkEntry] = {}


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Single benchmark measurement."""

    operation: str
    latency_ms: float
    throughput_ops_sec: float
    memory_mb: float
    iterations: int
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "latency_ms": round(self.latency_ms, 3),
            "throughput_ops_sec": round(self.throughput_ops_sec, 2),
            "memory_mb": round(self.memory_mb, 3),
            "iterations": self.iterations,
            "tags": self.tags,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkEntry:
    """Registered benchmark function with metadata."""

    name: str
    func: Callable[..., Any]
    tags: list[str]
    iterations: int
    is_async: bool


def benchmark(
    name: str | None = None,
    *,
    tags: list[str] | None = None,
    iterations: int = 100,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a function as a benchmark.

    Why a decorator instead of autodiscovery: explicit registration avoids
    accidentally benchmarking helper functions and lets us attach metadata
    (tags, iteration count) at definition time.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        entry_name = name or func.__qualname__
        is_async = asyncio.iscoroutinefunction(func)
        entry = BenchmarkEntry(
            name=entry_name,
            func=func,
            tags=tags or [],
            iterations=iterations,
            is_async=is_async,
        )
        _REGISTRY[entry_name] = entry
        # Preserve the original function for direct use in tests.
        func._benchmark_entry = entry  # type: ignore[attr-defined]
        return func

    return decorator


def get_registry() -> dict[str, BenchmarkEntry]:
    """Return the global benchmark registry (read-only view)."""
    return dict(_REGISTRY)


def _get_memory_mb() -> float:
    """Current process RSS in megabytes via getrusage.

    Why getrusage over psutil: zero extra dependencies. On Linux ru_maxrss
    is in KB; on macOS it's in bytes — we normalise to MB.
    """
    import platform

    usage = resource.getrusage(resource.RUSAGE_SELF)
    if platform.system() == "Darwin":
        return usage.ru_maxrss / (1024 * 1024)
    # Linux: ru_maxrss is in KB
    return usage.ru_maxrss / 1024


class BenchmarkRunner:
    """Discovers and runs registered benchmarks, collecting structured results."""

    def __init__(self, tag_filter: list[str] | None = None) -> None:
        self._tag_filter = tag_filter
        self._results: list[BenchmarkResult] = []

    @property
    def results(self) -> list[BenchmarkResult]:
        return list(self._results)

    def _should_run(self, entry: BenchmarkEntry) -> bool:
        """Filter benchmarks by tag if a filter is set."""
        if not self._tag_filter:
            return True
        return any(t in entry.tags for t in self._tag_filter)

    async def run_all(self) -> list[BenchmarkResult]:
        """Execute all matching registered benchmarks and return results."""
        self._results.clear()
        for entry in _REGISTRY.values():
            if not self._should_run(entry):
                continue
            result = await self._run_one(entry)
            self._results.append(result)
        return self.results

    async def _run_one(self, entry: BenchmarkEntry) -> BenchmarkResult:
        """Run a single benchmark entry and measure it."""
        mem_before = _get_memory_mb()

        start = time.perf_counter()
        for _ in range(entry.iterations):
            if entry.is_async:
                await entry.func()
            else:
                entry.func()
        elapsed = time.perf_counter() - start

        mem_after = _get_memory_mb()
        elapsed_ms = elapsed * 1000
        avg_latency_ms = elapsed_ms / entry.iterations
        throughput = entry.iterations / elapsed if elapsed > 0 else float("inf")

        return BenchmarkResult(
            operation=entry.name,
            latency_ms=avg_latency_ms,
            throughput_ops_sec=throughput,
            memory_mb=max(0.0, mem_after - mem_before),
            iterations=entry.iterations,
            tags=list(entry.tags),
        )

    def to_json(self) -> str:
        """Serialize collected results to JSON string."""
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "benchmarks": [r.to_dict() for r in self._results],
            },
            indent=2,
        )

    def write_report(self, directory: str | Path = "reports") -> Path:
        """Write results to a dated JSON file in the given directory."""
        dir_path = Path(directory)
        dir_path.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        path = dir_path / f"benchmarks-{date_str}.json"
        path.write_text(self.to_json())
        return path
