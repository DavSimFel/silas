from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from silas.models.agents import RouteDecision
from silas.models.context import ContextProfile, TokenBudget
from silas.models.gates import Gate


class ModelsConfig(BaseModel):
    proxy: str = "openrouter:anthropic/claude-haiku-4-5"
    planner: str = "openrouter:anthropic/claude-sonnet-4-5"
    executor: str = "openrouter:anthropic/claude-haiku-4-5"
    scorer: str = "openrouter:anthropic/claude-haiku-4-5"
    api_key: str | None = None
    """Direct API key (legacy/testing). Prefer api_key_ref for production."""
    api_key_ref: str | None = None
    """Opaque ref_id pointing to the API key in SecretStore (§0.5).
    Resolved at startup via SecretStore.get(). Takes precedence over api_key."""

    def resolve_api_key(self, data_dir: Path | None = None) -> str | None:
        """Resolve the effective API key: ref_id → SecretStore, else direct."""
        if self.api_key_ref and data_dir:
            from silas.secrets import SecretStore

            store = SecretStore(data_dir)
            secret = store.get(self.api_key_ref)
            if secret:
                return secret

        return self.api_key

    def inject_api_key_env(self, data_dir: Path | None = None) -> None:
        """Push api_key into the process environment for PydanticAI to pick up.

        Resolves from SecretStore if api_key_ref is set, else falls back to
        the direct api_key field. Only sets the env var if the corresponding
        env var isn't already set (explicit env vars take precedence).
        """
        effective_key = self.resolve_api_key(data_dir)
        if not effective_key:
            return

        # Detect provider from any model string
        providers = {
            m.split(":")[0]
            for m in [self.proxy, self.planner, self.executor, self.scorer]
            if ":" in m
        }
        env_map = {
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google-gla": "GOOGLE_API_KEY",
            "groq": "GROQ_API_KEY",
        }
        for provider in providers:
            env_var = env_map.get(provider)
            if env_var and not os.environ.get(env_var):
                os.environ[env_var] = effective_key


class WebChannelConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8420
    auth_token: str | None = None

    @model_validator(mode="after")
    def _validate_remote_requires_auth(self) -> WebChannelConfig:
        if self.host == "0.0.0.0" and self.auth_token is None:  # noqa: S104
            raise ValueError("channels.web.auth_token is required when host is 0.0.0.0")
        return self


class ChannelsConfig(BaseModel):
    web: WebChannelConfig = Field(default_factory=WebChannelConfig)


class ContextConfig(BaseModel):
    total_tokens: int = 180_000
    system_max: int = 8_000
    skill_metadata_budget_pct: float = 0.02
    eviction_threshold_pct: float = 0.80
    scorer_threshold_pct: float = 0.90
    max_subscription_tokens: int = 2_000
    subscription_ttl_turns: int = 10
    observation_mask_after_turns: int = 5
    use_scorer: bool = True
    default_profile: str = "conversation"
    profiles: dict[str, ContextProfile] = Field(default_factory=dict)

    @field_validator("profiles", mode="before")
    @classmethod
    def _inject_profile_names(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        prepared: dict[str, object] = {}
        for name, profile in value.items():
            if isinstance(profile, dict):
                prepared[name] = {"name": name, **profile}
            else:
                prepared[name] = profile
        return prepared

    def as_token_budget(self) -> TokenBudget:
        return TokenBudget(
            total=self.total_tokens,
            system_max=self.system_max,
            skill_metadata_budget_pct=self.skill_metadata_budget_pct,
            eviction_threshold_pct=self.eviction_threshold_pct,
            scorer_threshold_pct=self.scorer_threshold_pct,
            max_subscription_tokens=self.max_subscription_tokens,
            subscription_ttl_turns=self.subscription_ttl_turns,
            observation_mask_after_turns=self.observation_mask_after_turns,
            default_profile=self.default_profile,
            profiles=self.profiles,
        )


class SkillsConfig(BaseModel):
    shipped_dir: Path = Path("./silas/skills/shipped")
    custom_dir: Path = Path("./silas/skills/custom")


class ExecutionConfig(BaseModel):
    """Controls which execution path Stream uses for turn processing.

    Why a separate config: the queue path is now the default, but we need
    a kill-switch for environments where queue infra can't be initialized
    (minimal tests, single-process deployments).
    """

    use_queue_path: bool = True


class TelemetryConfig(BaseModel):
    """OpenTelemetry tracing configuration."""

    enabled: bool = True
    endpoint: str = "localhost:4317"
    env: str = "dev"


class ObservabilityConfig(BaseModel):
    """Observability wiring — Loki log shipping + Prometheus metrics."""

    loki_url: str | None = None
    metrics_enabled: bool = True
    env: str = "dev"


class StreamConfig(BaseModel):
    streaming_enabled: bool = True
    chunk_size: int = Field(default=50, ge=1)
    max_memory_ops_per_turn: int = Field(default=10, ge=1)


class SilasSettings(BaseSettings):
    owner_id: str = "owner"
    agent_name: str = "Silas"
    owner_name: str = ""
    registration_open: bool = True
    data_dir: Path = Path("./data")
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    output_gates: list[Gate] = Field(default_factory=list)
    queue_bridge_timeout_s: float = 120.0
    # Backward-compatible alias; prefer queue_bridge_timeout_s.
    queue_timeout_s: float | None = None

    model_config = SettingsConfigDict(
        env_prefix="SILAS_",
        env_nested_delimiter="__",
        extra="ignore",
    )


# Backward-compatible alias used by older call-sites/docs.
SilasConfig = SilasSettings


def _coerce_env_value(value: str) -> object:
    parsed = yaml.safe_load(value)
    return value if parsed is None else parsed


def _set_nested(mapping: dict[str, object], path: list[str], value: object) -> None:
    current = mapping
    for key in path[:-1]:
        existing = current.get(key)
        if not isinstance(existing, dict):
            existing = {}
            current[key] = existing
        current = existing
    current[path[-1]] = value


def _apply_env_overrides(data: dict[str, object]) -> dict[str, object]:
    merged = dict(data)
    prefix = "SILAS_"
    for key, raw_value in os.environ.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("__")
        _set_nested(merged, path, _coerce_env_value(raw_value))
    return merged


def load_config(path: str | Path = "config/silas.yaml") -> SilasSettings:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("config file must contain a top-level mapping")

    raw = loaded.get("silas", loaded)
    if not isinstance(raw, dict):
        raise ValueError("silas config section must be a mapping")

    merged = _apply_env_overrides(raw)
    settings = SilasSettings.model_validate(merged)
    settings.models.inject_api_key_env(data_dir=settings.data_dir)
    RouteDecision.configure_profiles(set(settings.context.profiles.keys()))
    return settings


__all__ = [
    "ChannelsConfig",
    "ContextConfig",
    "ExecutionConfig",
    "ModelsConfig",
    "SilasConfig",
    "SilasSettings",
    "StreamConfig",
    "WebChannelConfig",
    "load_config",
]
