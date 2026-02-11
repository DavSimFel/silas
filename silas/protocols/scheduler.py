from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskScheduler(Protocol):
    def add_cron_job(self, name: str, cron: str, callback: Callable[[], Awaitable[None]]) -> None: ...

    async def start(self) -> None: ...

    async def shutdown(self) -> None: ...


__all__ = ["TaskScheduler"]
