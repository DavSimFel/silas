"""Silas CLI entry point and dependency wiring."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
import httpx
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from silas.agents.executor_agent import build_executor_agent
from silas.agents.planner import build_planner_agent
from silas.agents.proxy import build_proxy_agent
from silas.approval import LiveApprovalManager, SilasApprovalVerifier
from silas.audit.sqlite_audit import SQLiteAuditLog
from silas.channels.web import WebChannel
from silas.config import SilasSettings, load_config
from silas.connections.lifecycle import (
    AuthStrategy,
    ConnectionConfig,
    HealthStatusLevel,
    LiveConnectionManager,
)
from silas.context.scorer import ContextScorer
from silas.core.context_manager import LiveContextManager
from silas.core.logging import setup_logging
from silas.core.plan_parser import MarkdownPlanParser
from silas.core.stream import Stream
from silas.core.token_counter import HeuristicTokenCounter
from silas.core.turn_context import TurnContext
from silas.core.verification_runner import SilasVerificationRunner
from silas.execution.sandbox import SubprocessSandboxManager
from silas.gates import SilasGateRunner
from silas.manual_harness import run_manual_harness
from silas.memory.sqlite_store import SQLiteMemoryStore
from silas.models.approval import ApprovalToken
from silas.models.connections import Connection, HealthCheckResult, SetupStep, SetupStepResponse
from silas.models.personality import AxisProfile, PersonaPreset, VoiceConfig
from silas.models.skills import SkillMetadata
from silas.models.work import WorkItem
from silas.persistence.chronicle_store import SQLiteChronicleStore
from silas.persistence.migrations import run_migrations
from silas.persistence.nonce_store import SQLiteNonceStore
from silas.persistence.persona_store import SQLitePersonaStore
from silas.persistence.work_item_store import SQLiteWorkItemStore
from silas.personality.engine import SilasPersonalityEngine
from silas.proactivity import SimpleAutonomyCalibrator, SimpleSuggestionEngine
from silas.queue.bridge import QueueBridge
from silas.queue.consult import ConsultPlannerManager
from silas.queue.factory import create_queue_system
from silas.queue.replan import ReplanManager
from silas.queue.router import QueueRouter
from silas.queue.store import DurableQueueStore
from silas.scheduler import SilasScheduler
from silas.skills.executor import SkillExecutor, register_builtin_skills
from silas.skills.loader import SilasSkillLoader
from silas.skills.registry import SkillRegistry
from silas.tools.resolver import LiveSkillResolver
from silas.work.executor import LiveWorkItemExecutor

logger = logging.getLogger(__name__)

_DEFAULT_OWNER_ID = "owner"
_DEFAULT_AGENT_NAME = "Silas"
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_DEFAULT_CONNECTION_HEALTH_INTERVAL_S = 60

@dataclass(slots=True)
class _ConnectionMetadata:
    skill_name: str
    provider: str
    permissions_granted: list[str]
    domain: str | None


class _LifecycleConnectionManagerAdapter:
    """Protocol-compatible adapter around LiveConnectionManager."""

    def __init__(self, manager: LiveConnectionManager) -> None:
        self._manager = manager
        self._metadata: dict[str, _ConnectionMetadata] = {}

    async def discover_connection(
        self,
        skill_name: str,
        identity_hint: dict[str, object],
    ) -> dict[str, object]:
        return {
            "skill_name": skill_name,
            "identity_hint": dict(identity_hint),
        }

    async def run_setup_flow(
        self,
        skill_name: str,
        identity_hint: dict[str, object],
        responses: list[SetupStepResponse] | None = None,
    ) -> list[SetupStep]:
        del skill_name, identity_hint, responses
        return []

    async def activate_connection(
        self,
        skill_name: str,
        provider: str,
        auth_payload: dict[str, object],
        approval: ApprovalToken | None = None,
    ) -> str:
        del approval
        config = ConnectionConfig(
            name=provider,
            auth_strategy=_resolve_auth_strategy(auth_payload.get("auth_strategy")),
            endpoint=_as_optional_str(auth_payload.get("endpoint")),
            health_check_interval_s=_as_positive_int(
                auth_payload.get("health_check_interval_s"),
                default=_DEFAULT_CONNECTION_HEALTH_INTERVAL_S,
            ),
            token=_as_optional_str(auth_payload.get("token")),
            refresh_token=_as_optional_str(auth_payload.get("refresh_token")),
            token_expires_at=_coerce_datetime(
                auth_payload.get("token_expires_at"),
            ),
            skill_id=skill_name,
        )
        handle = self._manager.register_connection(config)
        self._metadata[handle.id] = _ConnectionMetadata(
            skill_name=skill_name,
            provider=provider,
            permissions_granted=_as_str_list(auth_payload.get("permissions_granted")),
            domain=_as_optional_str(auth_payload.get("domain")),
        )
        return handle.id

    async def escalate_permission(
        self,
        connection_id: str,
        requested_permissions: list[str],
        reason: str,
        channel: object | None = None,
        recipient_id: str | None = None,
    ) -> bool:
        del reason, channel, recipient_id
        metadata = self._metadata.get(connection_id)
        if metadata is None:
            return False
        merged_permissions = list(
            dict.fromkeys([*metadata.permissions_granted, *requested_permissions]),
        )
        self._metadata[connection_id] = _ConnectionMetadata(
            skill_name=metadata.skill_name,
            provider=metadata.provider,
            permissions_granted=merged_permissions,
            domain=metadata.domain,
        )
        return True

    async def run_health_checks(self) -> list[HealthCheckResult]:
        results: list[HealthCheckResult] = []
        for handle in self._manager.list_connections():
            if not handle.active:
                continue
            health = await self._manager.health_check(handle.id)
            is_healthy = health.level != HealthStatusLevel.unhealthy
            warnings: list[str] = []
            error: str | None = None
            if health.level == HealthStatusLevel.degraded:
                warnings.append(health.message or "connection degraded")
            elif health.level == HealthStatusLevel.unhealthy:
                error = health.message or "connection unhealthy"
            results.append(
                HealthCheckResult(
                    healthy=is_healthy,
                    token_expires_at=handle.config.token_expires_at,
                    error=error,
                    warnings=warnings,
                ),
            )
        return results

    async def schedule_proactive_refresh(
        self,
        connection_id: str,
        health: HealthCheckResult | None = None,
    ) -> None:
        if health is None or health.token_expires_at is None:
            return
        if health.token_expires_at <= datetime.now(UTC):
            await self.refresh_token(connection_id)

    async def refresh_token(self, connection_id: str) -> bool:
        return await self._manager.refresh_credentials(connection_id)

    async def recover(self, connection_id: str) -> tuple[bool, str]:
        refreshed = await self._manager.refresh_credentials(connection_id)
        health = await self._manager.health_check(connection_id)
        if health.level == HealthStatusLevel.unhealthy:
            return False, health.message or "connection unhealthy"
        if refreshed:
            return True, "credentials refreshed"
        return True, health.message or "connection recovered"

    async def list_connections(self, domain: str | None = None) -> list[Connection]:
        connections: list[Connection] = []
        for handle in self._manager.list_connections():
            metadata = self._metadata.get(handle.id) or _ConnectionMetadata(
                skill_name=handle.config.skill_id or handle.config.name,
                provider=handle.config.name,
                permissions_granted=[],
                domain=None,
            )
            if domain and metadata.domain != domain:
                continue
            status = "inactive"
            if handle.active:
                status = "error" if handle.status == HealthStatusLevel.unhealthy else "active"

            updated_at = handle.last_health_check or handle.created_at
            connections.append(
                Connection(
                    connection_id=handle.id,
                    skill_name=metadata.skill_name,
                    provider=metadata.provider,
                    status=status,
                    permissions_granted=list(metadata.permissions_granted),
                    token_expires_at=handle.config.token_expires_at,
                    last_health_check=handle.last_health_check,
                    created_at=handle.created_at,
                    updated_at=updated_at,
                ),
            )

        connections.sort(key=lambda item: item.connection_id)
        return connections


class _CompositeSkillLoader:
    """Chain multiple skill loaders while preferring custom skills over shipped."""

    def __init__(self, *loaders: SilasSkillLoader) -> None:
        self._loaders = tuple(loaders)

    def scan(self) -> list[SkillMetadata]:
        merged: dict[str, SkillMetadata] = {}
        for loader in self._loaders:
            for metadata in loader.scan():
                if metadata.name in merged:
                    continue
                merged[metadata.name] = metadata
        return [item.model_copy(deep=True) for item in merged.values()]

    def load_metadata(self, skill_name: str) -> SkillMetadata:
        for loader in self._loaders:
            try:
                metadata = loader.load_metadata(skill_name)
                return metadata.model_copy(deep=True)
            except ValueError:
                continue
        raise ValueError(f"skill not found: {skill_name}")

    def load_full(self, skill_name: str) -> str:
        for loader in self._loaders:
            try:
                return loader.load_full(skill_name)
            except ValueError:
                continue
        raise ValueError(f"skill not found: {skill_name}")

    def resolve_script(self, skill_name: str, script_path: str) -> str:
        for loader in self._loaders:
            try:
                return loader.resolve_script(skill_name, script_path)
            except ValueError:
                continue
        raise ValueError(f"skill not found: {skill_name}")

    def validate(self, skill_name: str) -> dict[str, object]:
        for loader in self._loaders:
            try:
                return loader.validate(skill_name)
            except ValueError:
                continue
        return {"valid": False, "errors": [f"skill not found: {skill_name}"], "warnings": []}

    def import_external(self, source: str, format_hint: str | None = None) -> dict[str, object]:
        if not self._loaders:
            raise ValueError("no skill loaders configured")
        return self._loaders[0].import_external(source, format_hint=format_hint)


def _run_awaitable_blocking(awaitable: Awaitable[Any]) -> Any:
    """Run async initialization from sync wiring code.

    Supports both no-loop contexts (tests, CLI bootstrap) and already-running
    loops (`build_stream()` called inside async startup).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    sentinel = object()
    result: object = sentinel
    error: Exception | None = None

    def _runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(awaitable)
        except Exception as exc:
            error = exc

    worker = threading.Thread(
        target=_runner,
        name="silas-build-stream-async-init",
        daemon=False,
    )
    worker.start()
    worker.join()

    if error is not None:
        raise error
    if result is sentinel:
        raise RuntimeError("async initializer returned no result")
    return result


def _resolve_approval_signing_key(
    signing_key: Ed25519PrivateKey | bytes | None,
) -> Ed25519PrivateKey:
    if isinstance(signing_key, Ed25519PrivateKey):
        return signing_key
    if isinstance(signing_key, bytes):
        try:
            return Ed25519PrivateKey.from_private_bytes(signing_key)
        except (TypeError, ValueError):
            logger.warning("Invalid raw signing key bytes for approval verifier; generating ephemeral key")
    return Ed25519PrivateKey.generate()


def _default_personality_presets() -> dict[str, PersonaPreset]:
    default_axes = AxisProfile(
        warmth=0.5,
        assertiveness=0.5,
        verbosity=0.5,
        formality=0.5,
        humor=0.5,
        initiative=0.5,
        certainty=0.5,
    )
    default_voice = VoiceConfig(tone="neutral")
    return {
        "default": PersonaPreset(
            name="default",
            axes=default_axes,
            voice=default_voice,
        ),
    }


def _resolve_queue_bridge(
    *,
    enabled: bool,
    db_path: str,
    proxy_agent: object,
    planner_agent: object,
    executor_agent: object,
    channel: object | None,
    approval_recipient_id: str,
) -> QueueBridge | None:
    if not enabled:
        return None
    try:
        _, bridge = _run_awaitable_blocking(
            create_queue_system(
                db_path=db_path,
                proxy_agent=proxy_agent,
                planner_agent=planner_agent,
                executor_agent=executor_agent,
                channel=channel,
                approval_recipient_id=approval_recipient_id,
            ),
        )
        return bridge
    except (RuntimeError, OSError, ValueError):
        logger.warning(
            "Queue path initialization failed; continuing with procedural runtime path",
            exc_info=True,
        )
    return None


def _resolve_auth_strategy(raw: object) -> AuthStrategy:
    if isinstance(raw, AuthStrategy):
        return raw
    if isinstance(raw, str):
        try:
            return AuthStrategy(raw)
        except ValueError:
            return AuthStrategy.none
    return AuthStrategy.none


def _as_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _as_positive_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                normalized.append(cleaned)
    return normalized


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _db_path(settings: SilasSettings) -> str:
    data_dir = settings.data_dir
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / "silas.db")


def build_stream(
    settings: SilasSettings,
    signing_key: Ed25519PrivateKey | bytes | None = None,
) -> tuple[Stream, WebChannel]:
    web_cfg = settings.channels.web
    db = _db_path(settings)

    channel = WebChannel(
        host=web_cfg.host,
        port=web_cfg.port,
        web_dir=Path("web"),
        scope_id=settings.owner_id,
        auth_token=web_cfg.auth_token,
        data_dir=settings.data_dir,
    )

    proxy = build_proxy_agent(
        model=settings.models.proxy,
        default_context_profile=settings.context.default_profile,
    )
    planner = build_planner_agent(
        model=settings.models.planner,
        default_context_profile="planning",
    )
    queue_executor_agent = build_executor_agent(model=settings.models.executor)

    memory_store = SQLiteMemoryStore(db)
    chronicle_store = SQLiteChronicleStore(db)
    work_item_store = SQLiteWorkItemStore(db)
    skill_loader = _CompositeSkillLoader(
        SilasSkillLoader(settings.skills.custom_dir),
        SilasSkillLoader(settings.skills.shipped_dir),
    )

    def _lookup_work_item(work_item_id: str) -> WorkItem | None:
        return _run_awaitable_blocking(work_item_store.get(work_item_id))

    skill_resolver = LiveSkillResolver(
        skill_loader=skill_loader,
        work_item_lookup=_lookup_work_item,
    )

    persona_store = SQLitePersonaStore(db)
    audit = SQLiteAuditLog(db)
    nonce_store = SQLiteNonceStore(db)
    token_counter = HeuristicTokenCounter()
    context_scorer = ContextScorer()
    context_manager = LiveContextManager(
        token_budget=settings.context.as_token_budget(),
        token_counter=token_counter,
        use_scorer=settings.context.use_scorer,
        scorer=context_scorer,
    )
    skill_registry = SkillRegistry()
    register_builtin_skills(skill_registry)
    skill_executor = SkillExecutor(skill_registry=skill_registry, memory_store=memory_store)
    approval_manager = LiveApprovalManager()
    approval_verifier = SilasApprovalVerifier(
        signing_key=_resolve_approval_signing_key(signing_key),
        nonce_store=nonce_store,
    )
    suggestion_engine = SimpleSuggestionEngine()
    autonomy_calibrator = SimpleAutonomyCalibrator()
    personality_engine = SilasPersonalityEngine(
        store=persona_store,
        presets=_default_personality_presets(),
        context_registry={"default": {}},
    )
    verification_runner = SilasVerificationRunner(
        sandbox_manager=SubprocessSandboxManager(),
        verify_dir=settings.data_dir / "sandbox" / "verify",
        project_dirs=[Path.cwd()],
    )
    # Wire the self-healing cascade into the direct execution path.
    # When queue_bridge is enabled, the ExecutorConsumer has its own cascade;
    # this wiring covers the procedural (non-queue) path through LiveWorkItemExecutor.
    queue_store = DurableQueueStore(db)
    _run_awaitable_blocking(queue_store.initialize())
    queue_router = QueueRouter(queue_store)
    consult_manager = ConsultPlannerManager(queue_store, queue_router)
    replan_manager_inst = ReplanManager(queue_router)
    gate_runner = SilasGateRunner(token_counter=token_counter)

    work_executor = LiveWorkItemExecutor(
        skill_executor=skill_executor,
        work_item_store=work_item_store,
        approval_verifier=approval_verifier,
        verification_runner=verification_runner,
        gate_runner=gate_runner,
        audit=audit,
        consult_manager=consult_manager,
        replan_manager=replan_manager_inst,
    )
    scheduler = SilasScheduler()
    connection_manager = _LifecycleConnectionManagerAdapter(
        LiveConnectionManager(
            scheduler=scheduler,
            skill_executor=skill_executor,
        ),
    )
    plan_parser = MarkdownPlanParser()
    queue_bridge = _resolve_queue_bridge(
        enabled=settings.execution.use_queue_path,
        db_path=db,
        proxy_agent=proxy,
        planner_agent=planner,
        executor_agent=queue_executor_agent,
        channel=channel,
        approval_recipient_id=settings.owner_id,
    )
    # Why reuse gate_runner: unified two-lane model for both input and output gates.
    output_gate_runner: SilasGateRunner | None = None
    if settings.output_gates:
        output_gate_runner = gate_runner
        gate_runner.set_output_gates(settings.output_gates)

    turn_context = TurnContext(
        scope_id=settings.owner_id,
        context_manager=context_manager,
        live_context_manager=context_manager,
        memory_store=memory_store,
        chronicle_store=chronicle_store,
        proxy=proxy,
        planner=planner,
        work_executor=work_executor,
        gate_runner=gate_runner,
        personality_engine=personality_engine,
        skill_loader=skill_loader,
        skill_resolver=skill_resolver,
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
        scheduler=scheduler,
        plan_parser=plan_parser,
        work_item_store=work_item_store,
        connection_manager=connection_manager,
        queue_bridge=queue_bridge,
        owner_id=settings.owner_id,
        default_context_profile=settings.context.default_profile,
        output_gate_runner=output_gate_runner,
        suggestion_engine=suggestion_engine,
        autonomy_calibrator=autonomy_calibrator,
        _signing_key=signing_key,
        _nonce_store=nonce_store,
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


_API_KEY_REF_ID = "openrouter-api-key"


def _write_onboarding_config(
    config_path: Path,
    root_mapping: dict[str, Any],
    silas_mapping: dict[str, Any],
    agent_name: str,
    owner_name: str,
    api_key: str,
) -> None:
    from silas.secrets import SecretStore

    silas_mapping["agent_name"] = agent_name
    silas_mapping["owner_name"] = owner_name
    # First successful onboarding closes registration until manually reopened.
    silas_mapping["registration_open"] = False

    # Store API key in SecretStore (§0.5 — never in config files)
    data_dir = Path(silas_mapping.get("data_dir", "./data"))
    secret_store = SecretStore(data_dir)
    secret_store.set(_API_KEY_REF_ID, api_key)

    # Config stores only the opaque ref_id
    models_mapping = silas_mapping.get("models")
    if not isinstance(models_mapping, dict):
        models_mapping = {}
        silas_mapping["models"] = models_mapping
    models_mapping["api_key_ref"] = _API_KEY_REF_ID

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


def _init_signing_key(data_dir: Path) -> None:
    """Generate an Ed25519 signing keypair in the Tier 2 store during onboarding."""
    from silas.secrets import SigningKeyStore

    passphrase = click.prompt(
        "Choose a signing passphrase (protects your approval keys)",
        type=str,
        hide_input=True,
        confirmation_prompt=True,
    )
    store = SigningKeyStore(data_dir, passphrase)
    if store.has_keypair():
        click.echo("Signing keypair already exists — skipping generation.")
        return
    pub_hex = store.generate_keypair()
    click.echo(f"Ed25519 signing key generated. Public key: {pub_hex[:16]}...")


@cli.command("init")
@click.option("--config", "config_path", default="config/silas.yaml", show_default=True)
def init_command(config_path: str) -> None:
    """Run first-time onboarding then initialize Silas data directory."""
    config_file = Path(config_path)
    _run_onboarding(config_file)
    settings = load_config(config_path)
    db = _db_path(settings)

    # Generate Tier 2 signing keypair
    _init_signing_key(settings.data_dir)

    # Run migrations synchronously
    asyncio.run(run_migrations(db))
    click.echo(f"{settings.agent_name} is ready. Run `silas start` to begin.")


def _resolve_signing_passphrase() -> str:
    """Get signing passphrase from env or interactive prompt."""
    env_passphrase = os.environ.get("SILAS_SIGNING_PASSPHRASE")
    if env_passphrase:
        return env_passphrase
    return click.prompt("Signing passphrase", type=str, hide_input=True)


async def _start_runtime(settings: SilasSettings, passphrase: str) -> None:
    # Run migrations before starting
    db = _db_path(settings)
    await run_migrations(db)

    from silas.secrets import load_stream_signing_key

    signing_key = load_stream_signing_key(settings.data_dir, passphrase)
    logger.info("Tier 2 signing key loaded for stream inbound verification")

    stream, web_channel = build_stream(settings, signing_key=signing_key)
    await asyncio.gather(web_channel.serve(), stream.start())


@cli.command("start")
@click.option("--config", "config_path", default="config/silas.yaml", show_default=True)
def start_command(config_path: str) -> None:
    """Start the Silas runtime."""
    settings = load_config(config_path)
    if not settings.channels.web.enabled:
        raise click.ClickException("Phase 1a requires channels.web.enabled=true")

    passphrase = _resolve_signing_passphrase()

    try:
        asyncio.run(_start_runtime(settings, passphrase))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    except KeyboardInterrupt:
        click.echo("Shutting down.")


@cli.command("manual-harness")
@click.option(
    "--profile",
    type=click.Choice(["core", "full"], case_sensitive=False),
    default="core",
    show_default=True,
)
@click.option(
    "--base-url",
    default="http://127.0.0.1:8420",
    show_default=True,
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=Path("reports/manual-harness"),
    show_default=True,
)
def manual_harness_command(profile: str, base_url: str, output_dir: Path) -> None:
    """Run the interactive manual acceptance harness and save reports."""
    normalized_profile = profile.lower()
    run_manual_harness(
        profile=normalized_profile,
        base_url=base_url,
        output_dir=output_dir,
    )


__all__ = ["build_stream", "cli"]


if __name__ == "__main__":
    cli()
