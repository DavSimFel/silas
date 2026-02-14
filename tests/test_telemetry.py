"""Tests for silas.core.telemetry — OpenTelemetry tracing setup."""

from __future__ import annotations

from unittest.mock import patch

from opentelemetry.trace import NoOpTracerProvider
from silas.core import telemetry
from silas.core.telemetry import get_tracer, init_tracing, shutdown_tracing


class TestInitTracing:
    def setup_method(self) -> None:
        # Reset module-level state between tests.
        telemetry._tracer_provider = None

    def test_no_endpoint_returns_noop(self) -> None:
        provider = init_tracing(endpoint=None)
        assert isinstance(provider, NoOpTracerProvider)

    def test_empty_endpoint_returns_noop(self) -> None:
        provider = init_tracing(endpoint="")
        assert isinstance(provider, NoOpTracerProvider)

    def test_with_endpoint_returns_tracer_provider(self) -> None:
        # Patch the OTLP exporter so we don't need a real gRPC endpoint.
        with patch(
            "silas.core.telemetry.BatchSpanProcessor",
        ):
            from opentelemetry.sdk.trace import TracerProvider

            provider = init_tracing(
                service_name="test-svc",
                env="test",
                endpoint="localhost:4317",
            )
            assert isinstance(provider, TracerProvider)

    def test_custom_service_name_in_resource(self) -> None:
        with patch("silas.core.telemetry.BatchSpanProcessor"):
            from opentelemetry.sdk.trace import TracerProvider

            provider = init_tracing(service_name="custom-svc", endpoint="localhost:4317")
            assert isinstance(provider, TracerProvider)
            attrs = dict(provider.resource.attributes)
            assert attrs["service.name"] == "custom-svc"

    def test_exporter_import_failure_still_returns_provider(self) -> None:
        """If the OTLP exporter can't be imported, tracing still initialises."""
        with patch(
            "silas.core.telemetry.BatchSpanProcessor",
            side_effect=ImportError("no grpc"),
        ):
            from opentelemetry.sdk.trace import TracerProvider

            provider = init_tracing(endpoint="localhost:4317")
            assert isinstance(provider, TracerProvider)


class TestGetTracer:
    def test_returns_tracer(self) -> None:
        tracer = get_tracer("test-module")
        assert tracer is not None


class TestShutdownTracing:
    def setup_method(self) -> None:
        telemetry._tracer_provider = None

    def test_shutdown_noop_provider(self) -> None:
        """Shutdown should not raise for NoOpTracerProvider."""
        init_tracing(endpoint=None)
        shutdown_tracing()  # should not raise

    def test_shutdown_real_provider(self) -> None:
        with patch("silas.core.telemetry.BatchSpanProcessor"):
            init_tracing(endpoint="localhost:4317")
            shutdown_tracing()  # should not raise

    def test_shutdown_without_init(self) -> None:
        """Shutdown before init should be a no-op."""
        shutdown_tracing()  # should not raise
