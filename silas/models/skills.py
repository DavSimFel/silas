from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SkillMetadata(BaseModel):
    name: str
    description: str
    activation: str | None = None
    ui: dict[str, object] = Field(default_factory=dict)
    composes_with: list[str] = Field(default_factory=list)
    script_args: dict[str, dict[str, object]] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    requires_approval: bool = False
    tool_name: str | None = None
    tool_description: str | None = None
    tool_schema: dict[str, object] = Field(default_factory=dict)

    @property
    def exposed_tool_name(self) -> str:
        return self.tool_name or self.name

    @property
    def exposed_tool_description(self) -> str:
        return self.tool_description or self.description


class SkillRef(BaseModel):
    name: str


class SkillDefinition(BaseModel):
    name: str
    description: str
    version: str
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    requires_approval: bool = False
    verified_hash: str | None = None
    max_retries: int = 0
    timeout_seconds: int = 30
    taint_level: str | None = None
    manifest_path: str | None = None

    @field_validator("taint_level")
    @classmethod
    def _validate_taint_level(cls, value: str | None) -> str | None:
        if value is not None and value not in ("owner", "auth", "external"):
            raise ValueError("taint_level must be 'owner', 'auth', or 'external'")
        return value

    @field_validator("max_retries")
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_retries must be >= 0")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout_seconds(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return value


class SkillResult(BaseModel):
    skill_name: str
    success: bool
    output: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0
    retries_used: int = 0

    @field_validator("duration_ms", "retries_used")
    @classmethod
    def _validate_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("duration_ms and retries_used must be >= 0")
        return value


__all__ = ["SkillDefinition", "SkillMetadata", "SkillRef", "SkillResult"]
