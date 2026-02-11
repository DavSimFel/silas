from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import click

from silas.agents.proxy import build_proxy_agent
from silas.channels.web import WebChannel
from silas.config import SilasSettings, load_config
from silas.core.stream import Stream
from silas.core.turn_context import TurnContext


@dataclass(slots=True)
class InMemoryAuditLog:
    events: list[dict[str, object]] = field(default_factory=list)

    async def log(self, event: str, **data: object) -> str:
        event_id = uuid.uuid4().hex
        self.events.append({"id": event_id, "event": event, "data": data})
        return event_id

    async def verify_chain(self) -> tuple[bool, int]:
        return True, len(self.events)

    async def write_checkpoint(self) -> str:
        return uuid.uuid4().hex

    async def verify_from_checkpoint(self, checkpoint_id: str | None = None) -> tuple[bool, int]:
        del checkpoint_id
        return True, len(self.events)


def build_stream(settings: SilasSettings) -> tuple[Stream, WebChannel]:
    web_cfg = settings.channels.web
    channel = WebChannel(
        host=web_cfg.host,
        port=web_cfg.port,
        web_dir=Path("web"),
        scope_id=settings.owner_id,
    )

    proxy = build_proxy_agent(
        model=settings.models.proxy,
        default_context_profile=settings.context.default_profile,
    )

    turn_context = TurnContext(
        scope_id=settings.owner_id,
        proxy=proxy,
        audit=InMemoryAuditLog(),
        config=settings,
    )

    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        owner_id=settings.owner_id,
        default_context_profile=settings.context.default_profile,
    )
    return stream, channel


@click.group()
def cli() -> None:
    """Silas runtime CLI."""


@cli.command("init")
@click.option("--config", "config_path", default="config/silas.yaml", show_default=True)
def init_command(config_path: str) -> None:
    settings = load_config(config_path)
    data_dir = settings.data_dir
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "silas.db"
    db_path.touch(exist_ok=True)

    click.echo(f"Initialized data dir: {data_dir}")
    click.echo(f"DB placeholder: {db_path}")


async def _start_runtime(settings: SilasSettings) -> None:
    stream, web_channel = build_stream(settings)
    await asyncio.gather(web_channel.serve(), stream.start())


@cli.command("start")
@click.option("--config", "config_path", default="config/silas.yaml", show_default=True)
def start_command(config_path: str) -> None:
    settings = load_config(config_path)
    if not settings.channels.web.enabled:
        raise click.ClickException("Phase 1a requires channels.web.enabled=true")

    try:
        asyncio.run(_start_runtime(settings))
    except KeyboardInterrupt:
        click.echo("Shutting down.")


__all__ = ["cli", "build_stream"]


if __name__ == "__main__":
    cli()
