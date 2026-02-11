from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from silas.models.agents import RouteDecision
from silas.models.context import ContextProfile, TokenBudget


class ModelsConfig(BaseModel):
    proxy: str = "openrouter:anthropic/claude-haiku-4-5"
    planner: str = "openrouter:anthropic/claude-sonnet-4-5"
    executor: str = "openrouter:anthropic/claude-haiku-4-5"
    scorer: str = "openrouter:anthropic/claude-haiku-4-5"


class WebChannelConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8420
    auth_token: str | None = None


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


class SilasSettings(BaseSettings):
    owner_id: str = "owner"
    data_dir: Path = Path("./data")
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)

    model_config = SettingsConfigDict(
        env_prefix="SILAS_",
        env_nested_delimiter="__",
        extra="ignore",
    )


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
    RouteDecision.configure_profiles(set(settings.context.profiles.keys()))
    return settings


__all__ = [
    "SilasSettings",
    "ModelsConfig",
    "ChannelsConfig",
    "WebChannelConfig",
    "ContextConfig",
    "load_config",
]
