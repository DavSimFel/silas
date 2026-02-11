from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SkillDefinition(BaseModel):
    name: str
    description: str
    version: str
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    requires_approval: bool = False
    max_retries: int = 0
    timeout_seconds: int = 30

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


__all__ = ["SkillDefinition", "SkillResult"]
