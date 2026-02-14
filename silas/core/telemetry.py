"""OpenTelemetry tracing setup for the Silas runtime.

Provides optional tracing that exports to Tempo via OTLP gRPC.
When no endpoint is configured, all tracing is a no-op.
"""

from __future__ import annotations

import logging
from importlib.metadata import version as pkg_version

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NoOpTracerProvider

logger = logging.getLogger(__name__)

_tracer_provider: TracerProvider | NoOpTracerProvider | None = None


def init_tracing(
    *,
    service_name: str = "silas",
    env: str = "dev",
    endpoint: str | None = "localhost:4317",
) -> TracerProvider | NoOpTracerProvider:
    """Initialize OpenTelemetry tracing with OTLP gRPC exporter.

    If endpoint is None or empty, returns a no-op provider so callers
    don't need conditional logic.
    """
    global _tracer_provider  # noqa: PLW0603

    if not endpoint:
        provider = NoOpTracerProvider()
        trace.set_tracer_provider(provider)
        _tracer_provider = provider
        logger.info("Tracing disabled (no endpoint configured)")
        return provider

    try:
        silas_version = pkg_version("silas")
    except Exception:
        silas_version = "0.0.0"

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": env,
            "service.version": silas_version,
        }
    )

    provider = TracerProvider(resource=resource)

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("Tracing enabled → %s (env=%s)", endpoint, env)
    except Exception:
        logger.warning("Failed to initialize OTLP exporter", exc_info=True)

    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    return provider


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer from the configured provider."""
    return trace.get_tracer(name)


def shutdown_tracing() -> None:
    """Flush and shut down the tracer provider."""
    if isinstance(_tracer_provider, TracerProvider):
        _tracer_provider.shutdown()


__all__ = ["get_tracer", "init_tracing", "shutdown_tracing"]
