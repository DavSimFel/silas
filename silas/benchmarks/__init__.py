"""Benchmarking framework for Silas runtime components.

Provides a decorator-based benchmark registration system and a runner that
collects latency, throughput, and memory metrics. Results are serializable
to JSON for CI reporting.
"""

from silas.benchmarks.runner import BenchmarkResult, BenchmarkRunner, benchmark

__all__ = ["BenchmarkResult", "BenchmarkRunner", "benchmark"]
