"""Tests for silas.core.loki_handler — Loki log shipping."""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

from silas.core.loki_handler import LokiHandler, _extract_component


class TestExtractComponent:
    def test_silas_module(self) -> None:
        assert _extract_component("silas.queue.consumers") == "queue"

    def test_silas_single_submodule(self) -> None:
        assert _extract_component("silas.core") == "core"

    def test_non_silas_logger(self) -> None:
        assert _extract_component("uvicorn.error") == "uvicorn"

    def test_simple_name(self) -> None:
        assert _extract_component("root") == "root"

    def test_empty_string(self) -> None:
        assert _extract_component("") == ""


class TestLokiHandlerEmit:
    def setup_method(self) -> None:
        # Patch urlopen globally so no real HTTP happens.
        self._urlopen_patcher = patch("silas.core.loki_handler.urlopen")
        self._mock_urlopen = self._urlopen_patcher.start()

    def teardown_method(self) -> None:
        self._urlopen_patcher.stop()

    def _make_handler(self, **kwargs: object) -> LokiHandler:
        handler = LokiHandler(
            url="http://fake:3100/loki/api/v1/push",
            flush_interval=999,  # don't auto-flush during tests
            **kwargs,  # type: ignore[arg-type]
        )
        return handler

    def test_emit_buffers_record(self) -> None:
        handler = self._make_handler()
        try:
            record = logging.LogRecord(
                name="silas.queue.router",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test message",
                args=None,
                exc_info=None,
            )
            handler.emit(record)

            assert len(handler._buffer) == 1
            labels, _ts, message = handler._buffer[0]
            assert labels["component"] == "queue"
            assert labels["level"] == "info"
            assert labels["job"] == "silas-runtime"
            assert message == "test message"
        finally:
            handler.close()

    def test_emit_includes_trace_id_when_present(self) -> None:
        handler = self._make_handler()
        try:
            record = logging.LogRecord(
                name="silas.core",
                level=logging.WARNING,
                pathname="",
                lineno=0,
                msg="traced",
                args=None,
                exc_info=None,
            )
            record.trace_id = "abc123"  # type: ignore[attr-defined]
            handler.emit(record)

            labels, _, _ = handler._buffer[0]
            assert labels["trace_id"] == "abc123"
        finally:
            handler.close()

    def test_flush_sends_payload(self) -> None:
        handler = self._make_handler()
        try:
            record = logging.LogRecord(
                name="silas.gates",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="boom",
                args=None,
                exc_info=None,
            )
            handler.emit(record)
            handler._flush()

            assert len(handler._buffer) == 0
            self._mock_urlopen.assert_called_once()
            req = self._mock_urlopen.call_args[0][0]
            payload = json.loads(req.data)
            assert "streams" in payload
            assert len(payload["streams"]) == 1
            assert payload["streams"][0]["values"][0][1] == "boom"
        finally:
            handler.close()

    def test_flush_empty_buffer_is_noop(self) -> None:
        handler = self._make_handler()
        try:
            handler._flush()
            self._mock_urlopen.assert_not_called()
        finally:
            handler.close()

    def test_flush_groups_by_labels(self) -> None:
        handler = self._make_handler()
        try:
            for name, level in [
                ("silas.core", logging.INFO),
                ("silas.queue", logging.INFO),
                ("silas.core", logging.INFO),
            ]:
                record = logging.LogRecord(
                    name=name,
                    level=level,
                    pathname="",
                    lineno=0,
                    msg=f"msg-{name}",
                    args=None,
                    exc_info=None,
                )
                handler.emit(record)

            handler._flush()
            req = self._mock_urlopen.call_args[0][0]
            payload = json.loads(req.data)
            # core x2 and queue x1 → 2 distinct streams
            assert len(payload["streams"]) == 2
        finally:
            handler.close()

    def test_close_stops_thread(self) -> None:
        handler = self._make_handler()
        handler.close()
        assert not handler._thread.is_alive()

    def test_network_error_silently_dropped(self) -> None:
        from urllib.error import URLError

        self._mock_urlopen.side_effect = URLError("connection refused")
        handler = self._make_handler()
        try:
            record = logging.LogRecord(
                name="silas.core",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test",
                args=None,
                exc_info=None,
            )
            handler.emit(record)
            handler._flush()  # should not raise
        finally:
            handler.close()

    def test_custom_env_label(self) -> None:
        handler = self._make_handler(env="production")
        try:
            record = logging.LogRecord(
                name="silas.core",
                level=logging.DEBUG,
                pathname="",
                lineno=0,
                msg="env-test",
                args=None,
                exc_info=None,
            )
            handler.emit(record)
            labels, _, _ = handler._buffer[0]
            assert labels["env"] == "production"
        finally:
            handler.close()
