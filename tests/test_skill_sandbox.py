"""Tests for skill sandboxing via signed permission manifests (#282)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from silas.core.manifest_signer import ManifestSigner
from silas.core.skill_sandbox import SandboxedRunner, SandboxViolationError, SkillSandbox
from silas.models.skill_manifest import (
    ManifestPermissions,
    ManifestSignature,
    SkillManifest,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_manifest(
    perms: ManifestPermissions | None = None,
    signed: bool = True,
) -> SkillManifest:
    sig = (
        ManifestSignature(
            signature="AAAA",
            signer="test-owner",
            signed_at=datetime.now(UTC),
        )
        if signed
        else None
    )
    return SkillManifest(
        name="test-skill",
        version="0.1.0",
        permissions=perms or ManifestPermissions(),
        signature=sig,
    )


def _ok_signer() -> ManifestSigner:
    km = MagicMock()
    signer = ManifestSigner(km)
    signer.verify_manifest = MagicMock(return_value=(True, "Valid"))  # type: ignore[method-assign]
    return signer


def _bad_signer() -> ManifestSigner:
    km = MagicMock()
    signer = ManifestSigner(km)
    signer.verify_manifest = MagicMock(return_value=(False, "Invalid signature"))  # type: ignore[method-assign]
    return signer


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


class TestManifestValidation:
    def test_valid_manifest_passes(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        sandbox.validate(_make_manifest())  # should not raise

    def test_invalid_signature_raises(self) -> None:
        sandbox = SkillSandbox(_bad_signer())
        with pytest.raises(SandboxViolationError, match="Manifest verification failed"):
            sandbox.validate(_make_manifest())


# ---------------------------------------------------------------------------
# Import blocking
# ---------------------------------------------------------------------------


class TestImportBlocking:
    def test_network_import_blocked_without_permission(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        guarded = sandbox.restricted_import(ManifestPermissions())
        with pytest.raises(SandboxViolationError, match="no network permission"):
            guarded("socket")

    def test_network_import_allowed_with_permission(self) -> None:
        perms = ManifestPermissions(network_hosts=["example.com"])
        sandbox = SkillSandbox(_ok_signer())
        guarded = sandbox.restricted_import(perms)
        mod = guarded("socket")
        assert mod is not None

    def test_subprocess_blocked_without_permission(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        guarded = sandbox.restricted_import(ManifestPermissions())
        with pytest.raises(SandboxViolationError, match="no shell permission"):
            guarded("subprocess")

    def test_subprocess_allowed_with_permission(self) -> None:
        perms = ManifestPermissions(shell_commands=["ls"])
        sandbox = SkillSandbox(_ok_signer())
        guarded = sandbox.restricted_import(perms)
        mod = guarded("subprocess")
        assert mod is not None

    def test_os_system_blocked_without_shell(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        guarded = sandbox.restricted_import(ManifestPermissions())
        os_mod = guarded("os")
        with pytest.raises(SandboxViolationError, match="blocked"):
            os_mod.system("echo hi")

    def test_safe_import_allowed(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        guarded = sandbox.restricted_import(ManifestPermissions())
        mod = guarded("json")
        assert mod is not None


# ---------------------------------------------------------------------------
# Sandboxed runner
# ---------------------------------------------------------------------------


class TestSandboxedRunner:
    def test_run_calls_handler(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        runner = SandboxedRunner(sandbox)
        manifest = _make_manifest()

        result = runner.run(lambda ctx: ctx.get("env"), manifest)
        assert result == {}

    def test_run_rejects_bad_manifest(self) -> None:
        sandbox = SkillSandbox(_bad_signer())
        runner = SandboxedRunner(sandbox)
        manifest = _make_manifest()

        with pytest.raises(SandboxViolationError):
            runner.run(lambda ctx: None, manifest)

    def test_env_filtering(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        runner = SandboxedRunner(sandbox)
        perms = ManifestPermissions(env_vars=["HOME"])
        manifest = _make_manifest(perms=perms)

        result = runner.run(lambda ctx: ctx["env"], manifest)
        # HOME should be present (it's always set), others filtered out
        assert "PATH" not in result


# ---------------------------------------------------------------------------
# Restricted globals
# ---------------------------------------------------------------------------


class TestRestrictedGlobals:
    def test_no_exec_in_builtins(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        g = sandbox.create_globals(_make_manifest())
        assert "exec" not in g["__builtins__"]
        assert "eval" not in g["__builtins__"]

    def test_open_blocked_without_file_perms(self) -> None:
        sandbox = SkillSandbox(_ok_signer())
        g = sandbox.create_globals(_make_manifest())
        assert "open" not in g["__builtins__"]

    def test_open_guarded_with_file_perms(self) -> None:
        perms = ManifestPermissions(file_paths=["/tmp"])
        sandbox = SkillSandbox(_ok_signer())
        g = sandbox.create_globals(_make_manifest(perms=perms))
        assert "open" in g["__builtins__"]
        # Should allow /tmp paths
        f = g["__builtins__"]["open"]("/tmp/test-sandbox-282", "w")
        f.close()
        import os

        os.unlink("/tmp/test-sandbox-282")

    def test_guarded_open_blocks_disallowed_path(self) -> None:
        perms = ManifestPermissions(file_paths=["/tmp/safe"])
        sandbox = SkillSandbox(_ok_signer())
        g = sandbox.create_globals(_make_manifest(perms=perms))
        with pytest.raises(SandboxViolationError, match="not in allowed paths"):
            g["__builtins__"]["open"]("/etc/passwd")


# ---------------------------------------------------------------------------
# skills.py manifest_path field
# ---------------------------------------------------------------------------


class TestSkillDefinitionManifest:
    def test_manifest_path_default_none(self) -> None:
        from silas.models.skills import SkillDefinition

        sd = SkillDefinition(name="x", description="x", version="1.0")
        assert sd.manifest_path is None

    def test_manifest_path_set(self) -> None:
        from silas.models.skills import SkillDefinition

        sd = SkillDefinition(
            name="x", description="x", version="1.0", manifest_path="/skills/x/manifest.yaml"
        )
        assert sd.manifest_path == "/skills/x/manifest.yaml"
