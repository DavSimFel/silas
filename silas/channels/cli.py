"""CLI channel adapter for local development and testing.

Reads from stdin, writes to stdout. Single-user by design — the local
operator is always the owner, so every message is authenticated.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass

from silas.models.messages import ChannelMessage, utc_now

_CONNECTION_ID = "cli-local"


@dataclass
class CLIChannelConfig:
    prompt: str = "silas> "
    color: bool = True
    history_file: str | None = None


# ANSI codes — kept minimal to avoid a dependency on colorama/rich
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


class CLIChannel:
    """stdin/stdout channel implementing ChannelAdapterCore.

    Designed for single-user local sessions: the operator is always treated
    as the authenticated owner so every inbound message gets full trust.
    """

    channel_name = "cli"

    def __init__(self, config: CLIChannelConfig | None = None) -> None:
        self._config = config or CLIChannelConfig()
        self._incoming: asyncio.Queue[tuple[ChannelMessage, str]] = asyncio.Queue()
        self._running = False
        self._input_task: asyncio.Task[None] | None = None

        # Auto-detect color support — honor explicit config, but fall back
        # to TTY check so piped output stays clean.
        self._color = self._config.color and sys.stdout.isatty()

    # ── ChannelAdapterCore ───────────────────────────────────────────

    async def listen(self) -> AsyncIterator[tuple[ChannelMessage, str]]:
        while True:
            yield await self._incoming.get()

    async def send(
        self,
        recipient_id: str,
        text: str,
        reply_to: str | None = None,
    ) -> None:
        """Write agent response to stdout, with optional color."""
        if self._color:
            print(f"{_BOLD}{_CYAN}{text}{_RESET}", flush=True)
        else:
            print(text, flush=True)

    async def send_stream_start(self, connection_id: str) -> None:
        pass

    async def send_stream_chunk(self, connection_id: str, text: str) -> None:
        # Stream chunks print inline without newline for a typing effect
        print(text, end="", flush=True)

    async def send_stream_end(self, connection_id: str) -> None:
        # Terminate the streamed line
        print(flush=True)

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._print_welcome()
        self._input_task = asyncio.create_task(self._input_loop())

    async def stop(self) -> None:
        self._running = False
        if self._input_task is not None:
            self._input_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._input_task

    # ── Input handling ───────────────────────────────────────────────

    async def _input_loop(self) -> None:
        """Read lines from stdin in a thread so we don't block the event loop."""
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                line = await loop.run_in_executor(None, self._read_line)
            except EOFError:
                break

            if line is None:
                break

            text = line.strip()
            if not text:
                continue

            if self._handle_builtin(text):
                continue

            message = ChannelMessage(
                channel=self.channel_name,
                sender_id=_CONNECTION_ID,
                text=text,
                timestamp=utc_now(),
                # Local CLI user is always the authenticated owner
                is_authenticated=True,
            )
            await self._incoming.put((message, "owner"))

    def _read_line(self) -> str | None:
        """Blocking readline, run inside an executor thread."""
        try:
            return input(self._config.prompt)
        except EOFError:
            return None

    def _handle_builtin(self, text: str) -> bool:
        """Process built-in slash commands. Returns True if handled."""
        cmd = text.lower()
        if cmd == "/quit":
            asyncio.get_event_loop().create_task(self.stop())
            return True
        if cmd == "/help":
            self._print_help()
            return True
        return False

    # ── Output helpers ───────────────────────────────────────────────

    def _print_welcome(self) -> None:
        lines = [
            "Silas CLI — local development channel",
            "Type /help for commands, /quit to exit.",
        ]
        for line in lines:
            print(line, flush=True)

    def _print_help(self) -> None:
        lines = [
            "/help  — show this message",
            "/quit  — exit the CLI session",
        ]
        for line in lines:
            print(line, flush=True)


__all__ = ["CLIChannel", "CLIChannelConfig"]
