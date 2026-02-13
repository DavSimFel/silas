"""Portable bundle models for memory migration between Silas instances."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from silas.models.memory import MemoryItem
from silas.models.messages import utc_now

# Bump when MemoryItem schema changes — import rejects incompatible majors
SCHEMA_VERSION = "1.0"


class BundleMetadata(BaseModel):
    exported_at: datetime = Field(default_factory=utc_now)
    source_instance_id: str = ""
    schema_version: str = SCHEMA_VERSION
    item_count: int = 0


class MemoryBundle(BaseModel):
    metadata: BundleMetadata
    items: list[MemoryItem]


class ImportResult(BaseModel):
    imported_count: int = 0
    skipped_count: int = 0
    conflict_count: int = 0
    errors: list[str] = Field(default_factory=list)


__all__ = ["BundleMetadata", "ImportResult", "MemoryBundle", "SCHEMA_VERSION"]  # noqa: RUF022
