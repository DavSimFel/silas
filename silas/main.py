"""Silas CLI entry point and dependency wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from silas.agents.proxy import build_proxy_agent
from silas.agents.scorer import build_scorer_agent
from silas.approval import LiveApprovalManager
from silas.audit.sqlite_audit import SQLiteAuditLog
from silas.channels.web import WebChannel
from silas.config import SilasSettings, load_config
from silas.core.context_manager import LiveContextManager
from silas.core.stream import Stream
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.turn_context import TurnContext
from silas.gates import OutputGateRunner
from silas.memory.sqlite_store import SQLiteMemoryStore
from silas.persistence.chronicle_store import SQLiteChronicleStore
from silas.persistence.migrations import run_migrations
from silas.persistence.nonce_store import SQLiteNonceStore
from silas.persistence.work_item_store import SQLiteWorkItemStore
from silas.proactivity import SimpleAutonomyCalibrator, SimpleSuggestionEngine
from silas.skills.executor import SkillExecutor, register_builtin_skills
from silas.skills.registry import SkillRegistry


def _db_path(settings: SilasSettings) -> str:
    data_dir = settings.data_dir
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "silas.db")


def build_stream(settings: SilasSettings) -> tuple[Stream, WebChannel]:
    web_cfg = settings.channels.web
    db = _db_path(settings)

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
    scorer = build_scorer_agent(model=settings.models.scorer)

    memory_store = SQLiteMemoryStore(db)
    chronicle_store = SQLiteChronicleStore(db)
    work_item_store = SQLiteWorkItemStore(db)  # noqa: F841 — wired in Phase 3
    audit = SQLiteAuditLog(db)
    nonce_store = SQLiteNonceStore(db)  # noqa: F841 — wired in Phase 3
    token_counter = HeuristicTokenCounter()
    context_manager = LiveContextManager(
        token_budget=settings.context.as_token_budget(),
        token_counter=token_counter,
        scorer_agent=scorer,
    )
    skill_registry = SkillRegistry()
    register_builtin_skills(skill_registry)
    skill_executor = SkillExecutor(skill_registry=skill_registry, memory_store=memory_store)
    approval_manager = LiveApprovalManager()
    suggestion_engine = SimpleSuggestionEngine()
    autonomy_calibrator = SimpleAutonomyCalibrator()
    output_gate_runner = (
        OutputGateRunner(settings.output_gates, token_counter=token_counter)
        if settings.output_gates
        else None
    )

    turn_context = TurnContext(
        scope_id=settings.owner_id,
        context_manager=context_manager,
        live_context_manager=context_manager,
        memory_store=memory_store,
        chronicle_store=chronicle_store,
        proxy=proxy,
        skill_registry=skill_registry,
        skill_executor=skill_executor,
        approval_manager=approval_manager,
        suggestion_engine=suggestion_engine,
        autonomy_calibrator=autonomy_calibrator,
        audit=audit,
        config=settings,
    )

    stream = Stream(
        channel=channel,
        turn_context=turn_context,
        context_manager=context_manager,
        owner_id=settings.owner_id,
        default_context_profile=settings.context.default_profile,
        output_gate_runner=output_gate_runner,
        suggestion_engine=suggestion_engine,
        autonomy_calibrator=autonomy_calibrator,
    )
    return stream, channel


@click.group()
def cli() -> None:
    """Silas runtime CLI."""


@cli.command("init")
@click.option("--config", "config_path", default="config/silas.yaml", show_default=True)
def init_command(config_path: str) -> None:
    """Initialize Silas data directory and run migrations."""
    settings = load_config(config_path)
    db = _db_path(settings)

    # Run migrations synchronously
    asyncio.run(run_migrations(db))
    click.echo(f"Database initialized: {db}")


async def _start_runtime(settings: SilasSettings) -> None:
    # Run migrations before starting
    db = _db_path(settings)
    await run_migrations(db)

    stream, web_channel = build_stream(settings)
    await asyncio.gather(web_channel.serve(), stream.start())


@cli.command("start")
@click.option("--config", "config_path", default="config/silas.yaml", show_default=True)
def start_command(config_path: str) -> None:
    """Start the Silas runtime."""
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
