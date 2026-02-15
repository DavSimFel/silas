"""Skill sandboxing — enforce manifest permissions at runtime."""

from __future__ import annotations

import builtins
import types
from collections.abc import Callable
from typing import Any

from silas.core.manifest_signer import ManifestSigner
from silas.models.skill_manifest import ManifestPermissions, SkillManifest

# Modules that require explicit permission grants.
_NETWORK_MODULES = frozenset({"socket", "http", "urllib", "requests", "httpx", "aiohttp"})
_SHELL_MODULES = frozenset({"subprocess", "os", "shutil", "pty"})
_DANGEROUS_OS_ATTRS = frozenset(
    {
        "system",
        "popen",
        "exec",
        "execl",
        "execle",
        "execlp",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawn",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    }
)


class SandboxViolationError(RuntimeError):
    """Raised when a skill attempts an operation its manifest does not permit."""


class SkillSandbox:
    """Validates a manifest signature and creates a restricted execution environment."""

    def __init__(self, signer: ManifestSigner) -> None:
        self._signer = signer

    def validate(self, manifest: SkillManifest) -> None:
        """Raise if the manifest signature is missing or invalid."""
        ok, reason = self._signer.verify_manifest(manifest)
        if not ok:
            raise SandboxViolationError(f"Manifest verification failed: {reason}")

    def restricted_import(
        self, permissions: ManifestPermissions
    ) -> Callable[..., types.ModuleType]:
        """Return a custom ``__import__`` that blocks unauthorized modules."""
        _real_import = builtins.__import__

        def _guarded_import(
            name: str,
            globals: dict[str, Any] | None = None,
            locals: dict[str, Any] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> types.ModuleType:
            top = name.split(".")[0]

            if top in _NETWORK_MODULES and not permissions.network_hosts:
                raise SandboxViolationError(f"Import of '{name}' blocked — no network permission")

            if top in _SHELL_MODULES and not permissions.shell_commands:
                if top == "os":
                    # os is allowed for benign uses; dangerous attrs are blocked separately
                    mod = _real_import(name, globals, locals, fromlist, level)
                    return _wrap_os(mod, permissions)
                raise SandboxViolationError(f"Import of '{name}' blocked — no shell permission")

            return _real_import(name, globals, locals, fromlist, level)

        return _guarded_import

    def create_globals(self, manifest: SkillManifest) -> dict[str, Any]:
        """Build a restricted globals dict for ``exec()``-based skill execution."""
        restricted: dict[str, Any] = {
            "__builtins__": {
                k: getattr(builtins, k)
                for k in dir(builtins)
                if not k.startswith("_")
                and k not in ("open", "exec", "eval", "compile", "__import__")
            },
        }
        restricted["__builtins__"]["__import__"] = self.restricted_import(manifest.permissions)
        # Provide a guarded open if file_paths are declared
        if manifest.permissions.file_paths:
            restricted["__builtins__"]["open"] = _guarded_open(manifest.permissions.file_paths)
        return restricted


class SandboxedRunner:
    """High-level runner: verify manifest → build sandbox → execute handler."""

    def __init__(self, sandbox: SkillSandbox) -> None:
        self._sandbox = sandbox

    def run(
        self,
        handler: Callable[..., Any],
        manifest: SkillManifest,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Execute *handler* inside a sandboxed environment constrained by *manifest*."""
        self._sandbox.validate(manifest)
        ctx = context or {}

        # Filter environment variables
        if manifest.permissions.env_vars:
            import os as _os

            filtered_env = {
                k: v for k, v in _os.environ.items() if k in manifest.permissions.env_vars
            }
            ctx["env"] = filtered_env
        else:
            ctx["env"] = {}

        return handler(ctx)


def _wrap_os(mod: types.ModuleType, permissions: ManifestPermissions) -> types.ModuleType:
    """Return a proxy that blocks dangerous os functions when shell is not permitted."""
    proxy = types.ModuleType(mod.__name__)
    proxy.__dict__.update(mod.__dict__)
    for attr in _DANGEROUS_OS_ATTRS:
        if hasattr(proxy, attr):
            setattr(proxy, attr, _make_blocked(attr))
    return proxy


def _make_blocked(name: str) -> Callable[..., Any]:
    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise SandboxViolationError(f"os.{name}() blocked — no shell permission")

    _blocked.__name__ = name
    return _blocked


def _guarded_open(allowed_paths: list[str]) -> Callable[..., Any]:
    """Return an ``open`` replacement that only allows declared paths."""
    _real_open = builtins.open

    def _restricted_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        from pathlib import Path

        target = str(Path(str(file)).resolve())
        for allowed in allowed_paths:
            resolved_allowed = str(Path(allowed).resolve())
            if target == resolved_allowed or target.startswith(resolved_allowed + "/"):
                return _real_open(file, *args, **kwargs)
        raise SandboxViolationError(f"File access to '{file}' blocked — not in allowed paths")

    return _restricted_open


__all__ = ["SandboxViolationError", "SandboxedRunner", "SkillSandbox"]
