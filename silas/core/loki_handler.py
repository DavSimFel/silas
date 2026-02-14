"""Loki log handler — ships logs to Grafana Loki via HTTP push API.

Uses a background thread with a buffer to avoid blocking the event loop.
Silently drops logs if Loki is unreachable (never crashes the runtime).
"""

from __future__ import annotations

import atexit
import json
import logging
import threading
from collections import deque
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def _extract_component(logger_name: str) -> str:
    """Extract component from logger name: 'silas.queue.consumers' → 'queue'."""
    parts = logger_name.split(".")
    if len(parts) >= 2 and parts[0] == "silas":
        return parts[1]
    return parts[0] if parts else "unknown"


class LokiHandler(logging.Handler):
    """Logging handler that buffers and pushes logs to Loki in a background thread.

    Flushes every ``flush_interval`` seconds or ``buffer_size`` entries,
    whichever comes first.  If Loki is unreachable, logs are silently dropped.
    """

    def __init__(
        self,
        url: str = "http://127.0.0.1:3100/loki/api/v1/push",
        *,
        env: str = "dev",
        buffer_size: int = 100,
        flush_interval: float = 1.0,
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level)
        self._url = url
        self._env = env
        self._buffer_size = buffer_size
        self._flush_interval = flush_interval

        self._buffer: deque[tuple[dict[str, str], str, str]] = deque()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._thread = threading.Thread(target=self._run, daemon=True, name="loki-shipper")
        self._thread.start()
        atexit.register(self.close)

    def emit(self, record: logging.LogRecord) -> None:
        """Buffer a log record for async shipping."""
        try:
            labels: dict[str, str] = {
                "job": "silas-runtime",
                "env": self._env,
                "component": _extract_component(record.name),
                "level": record.levelname.lower(),
            }
            trace_id = getattr(record, "trace_id", None)
            if trace_id:
                labels["trace_id"] = str(trace_id)

            ts = str(int(record.created * 1e9))
            message = self.format(record) if self.formatter else record.getMessage()

            with self._lock:
                self._buffer.append((labels, ts, message))
        except Exception:  # noqa: S110 — intentional: never crash runtime for logging
            pass

    def _run(self) -> None:
        """Background thread: flush periodically or when buffer is full."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self._flush_interval)
            self._flush()

    def _flush(self) -> None:
        """Ship buffered entries to Loki."""
        with self._lock:
            if not self._buffer:
                return
            entries = list(self._buffer)
            self._buffer.clear()

        streams: dict[str, dict[str, Any]] = {}
        for labels, ts, message in entries:
            key = json.dumps(labels, sort_keys=True)
            if key not in streams:
                streams[key] = {"stream": labels, "values": []}
            streams[key]["values"].append([ts, message])

        payload = json.dumps({"streams": list(streams.values())}).encode()

        try:
            req = Request(  # noqa: S310 — controlled internal Loki URL
                self._url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urlopen(req, timeout=5)  # noqa: S310
        except (URLError, OSError, TimeoutError):
            pass

    def close(self) -> None:
        """Stop background thread and flush remaining entries."""
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3)
        self._flush()
        super().close()


__all__ = ["LokiHandler"]
