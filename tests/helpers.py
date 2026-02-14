"""Shared test helpers."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Union


async def wait_until(
    predicate: Union[Callable[[], bool], Callable[[], asyncio.coroutine]],
    timeout: float = 3.0,
    interval: float = 0.05,
) -> None:
    """Poll a condition until it passes or timeout is reached.

    Supports both sync and async predicates.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await asyncio.sleep(interval)
    raise TimeoutError("Condition not met within timeout")
