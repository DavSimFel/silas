"""Silas CLI entry point and dependency wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click
import httpx
import yaml

from silas.agents.proxy import build_proxy_agent
from silas.approval import LiveApprovalManager
from silas.audit.sqlite_audit import SQLiteAuditLog
from silas.channels.web import WebChannel
from silas.config import SilasSettings, load_config
from silas.core.context_manager import LiveContextManager
from silas.core.logging import setup_logging
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

_DEFAULT_OWNER_ID = "owner"
_DEFAULT_AGENT_NAME = "Silas"
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


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
        auth_token=web_cfg.auth_token,
    )

    proxy = build_proxy_agent(
        model=settings.models.proxy,
        default_context_profile=settings.context.default_profile,
    )

    memory_store = SQLiteMemoryStore(db)
    chronicle_store = SQLiteChronicleStore(db)
    work_item_store = SQLiteWorkItemStore(db)  # noqa: F841 — wired in Phase 3
    audit = SQLiteAuditLog(db)
    nonce_store = SQLiteNonceStore(db)  # noqa: F841 — wired in Phase 3
    token_counter = HeuristicTokenCounter()
    context_manager = LiveContextManager(
        token_budget=settings.context.as_token_budget(),
        token_counter=token_counter,
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
    setup_logging()


def _load_config_mapping(config_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("config file must contain a top-level mapping")

    silas_mapping = loaded.get("silas")
    if silas_mapping is None:
        return loaded, loaded
    if not isinstance(silas_mapping, dict):
        raise ValueError("silas config section must be a mapping")
    return loaded, silas_mapping


def _is_already_configured(silas_mapping: dict[str, Any]) -> bool:
    owner_id = silas_mapping.get("owner_id")
    return isinstance(owner_id, str) and owner_id != _DEFAULT_OWNER_ID


def _validate_openrouter_api_key(api_key: str) -> bool:
    if not api_key:
        return False

    try:
        response = httpx.get(
            _OPENROUTER_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _prompt_onboarding_values() -> tuple[str, str, str]:
    agent_name = click.prompt(
        "What should I call myself?",
        default=_DEFAULT_AGENT_NAME,
        show_default=True,
        type=str,
    ).strip()
    owner_name = click.prompt("What is your name?", type=str).strip()

    while True:
        api_key = click.prompt("OpenRouter API key", type=str, hide_input=True).strip()
        if _validate_openrouter_api_key(api_key):
            return agent_name or _DEFAULT_AGENT_NAME, owner_name, api_key
        click.echo("Invalid OpenRouter API key. Please try again.")


def _write_onboarding_config(
    config_path: Path,
    root_mapping: dict[str, Any],
    silas_mapping: dict[str, Any],
    agent_name: str,
    owner_name: str,
    api_key: str,
) -> None:
    silas_mapping["agent_name"] = agent_name
    silas_mapping["owner_name"] = owner_name

    models_mapping = silas_mapping.get("models")
    if not isinstance(models_mapping, dict):
        models_mapping = {}
        silas_mapping["models"] = models_mapping
    models_mapping["api_key"] = api_key

    config_path.write_text(yaml.safe_dump(root_mapping, sort_keys=False), encoding="utf-8")


def _run_onboarding(config_path: Path) -> None:
    root_mapping, silas_mapping = _load_config_mapping(config_path)
    if _is_already_configured(silas_mapping):
        click.echo("Already configured")
        return

    agent_name, owner_name, api_key = _prompt_onboarding_values()
    _write_onboarding_config(
        config_path=config_path,
        root_mapping=root_mapping,
        silas_mapping=silas_mapping,
        agent_name=agent_name,
        owner_name=owner_name,
        api_key=api_key,
    )


@cli.command("init")
@click.option("--config", "config_path", default="config/silas.yaml", show_default=True)
def init_command(config_path: str) -> None:
    """Run first-time onboarding then initialize Silas data directory."""
    config_file = Path(config_path)
    _run_onboarding(config_file)
    settings = load_config(config_path)
    db = _db_path(settings)

    # Run migrations synchronously
    asyncio.run(run_migrations(db))
    click.echo(f"{settings.agent_name} is ready. Run `silas start` to begin.")


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


__all__ = ["build_stream", "cli"]


if __name__ == "__main__":
    cli()
