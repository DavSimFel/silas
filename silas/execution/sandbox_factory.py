"""Factory for selecting the sandbox backend based on config.

Keeps the choice centralised so callers don't need to import both backends
or duplicate the selection logic.
"""

from __future__ import annotations

import logging
from typing import Union

from silas.execution.docker_sandbox import DockerSandboxManager, is_docker_available
from silas.execution.sandbox import SubprocessSandboxManager

logger = logging.getLogger(__name__)

SandboxBackend = Union[SubprocessSandboxManager, DockerSandboxManager]


def create_sandbox_manager(
    backend: str = "subprocess",
    *,
    docker_image: str = "python:3.12-slim",
) -> SandboxBackend:
    """Instantiate the right sandbox manager for *backend*.

    Falls back to subprocess if Docker is requested but unavailable,
    so the system degrades gracefully in environments without Docker.
    """
    if backend == "docker":
        if is_docker_available():
            logger.info("Using Docker sandbox backend")
            return DockerSandboxManager(base_image=docker_image)
        logger.warning(
            "Docker sandbox requested but Docker is unavailable — "
            "falling back to subprocess backend"
        )

    return SubprocessSandboxManager()


__all__ = ["SandboxBackend", "create_sandbox_manager"]
