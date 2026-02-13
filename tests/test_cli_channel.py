"""Tests for the CLI channel adapter."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from silas.channels.cli import _CONNECTION_ID, CLIChannel, CLIChannelConfig


@pytest.fixture
def channel() -> CLIChannel:
    return CLIChannel(CLIChannelConfig(color=False))


class TestConfig:
    def test_defaults(self) -> None:
        cfg = CLIChannelConfig()
        assert cfg.prompt == "silas> "
        assert cfg.color is True
        assert cfg.history_file is None

    def test_color_disabled_when_not_tty(self) -> None:
        """Color auto-detection should disable when stdout is not a TTY."""
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            ch = CLIChannel(CLIChannelConfig(color=True))
            assert ch._color is False

    def test_color_enabled_when_tty(self) -> None:
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            ch = CLIChannel(CLIChannelConfig(color=True))
            assert ch._color is True


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_prints_to_stdout(self, channel: CLIChannel) -> None:
        buf = StringIO()
        with patch("builtins.print", side_effect=lambda *a, **kw: buf.write(str(a[0]) + "\n")):
            await channel.send(_CONNECTION_ID, "hello world")
        assert "hello world" in buf.getvalue()

    @pytest.mark.asyncio
    async def test_send_with_color(self) -> None:
        """When color is on, output should contain ANSI escape codes."""
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            ch = CLIChannel(CLIChannelConfig(color=True))

        buf = StringIO()
        with patch("builtins.print", side_effect=lambda *a, **kw: buf.write(str(a[0]) + "\n")):
            await ch.send(_CONNECTION_ID, "colored")
        assert "\033[" in buf.getvalue()


class TestInputLoop:
    @pytest.mark.asyncio
    async def test_input_creates_authenticated_message(self, channel: CLIChannel) -> None:
        """Local CLI input should produce an authenticated ChannelMessage."""
        inputs = iter(["hello silas"])
        with patch.object(channel, "_read_line", side_effect=lambda: next(inputs, None)):
            channel._running = True
            # Run the loop briefly — it will read one line then get None and exit
            await channel._input_loop()

        msg, session = channel._incoming.get_nowait()
        assert msg.text == "hello silas"
        assert msg.is_authenticated is True
        assert msg.channel == "cli"
        assert msg.sender_id == _CONNECTION_ID
        assert session == "owner"

    @pytest.mark.asyncio
    async def test_quit_triggers_stop(self, channel: CLIChannel) -> None:
        """The /quit command should set _running to False."""
        inputs = iter(["/quit"])
        channel._running = True

        # Patch stop to just flip the flag (avoid task cancellation in test)
        async def fake_stop() -> None:
            channel._running = False

        with (
            patch.object(channel, "_read_line", side_effect=lambda: next(inputs, None)),
            patch.object(channel, "stop", side_effect=fake_stop),
        ):
            await channel._input_loop()

        assert channel._running is False

    @pytest.mark.asyncio
    async def test_help_does_not_enqueue(self, channel: CLIChannel) -> None:
        """/help is handled internally and should not produce a ChannelMessage."""
        inputs = iter(["/help"])
        channel._running = True
        with (
            patch.object(channel, "_read_line", side_effect=lambda: next(inputs, None)),
            patch("builtins.print"),
        ):
            await channel._input_loop()

        assert channel._incoming.empty()

    @pytest.mark.asyncio
    async def test_empty_lines_skipped(self, channel: CLIChannel) -> None:
        inputs = iter(["", "   "])
        channel._running = True
        with patch.object(channel, "_read_line", side_effect=lambda: next(inputs, None)):
            await channel._input_loop()

        assert channel._incoming.empty()


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        channel = CLIChannel(CLIChannelConfig(color=False))
        with (
            patch("builtins.print"),
            patch.object(channel, "_input_loop", return_value=None),
        ):
            await channel.start()
            assert channel._running is True
            await channel.stop()
            assert channel._running is False
