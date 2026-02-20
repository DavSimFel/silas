"""Tests for dynamic skill tool taint classification."""

from __future__ import annotations

from silas.models.messages import TaintLevel
from silas.models.skills import SkillDefinition
from silas.gates.taint import TaintTracker
from silas.skills.registry import SkillRegistry


class TestSkillDefinitionTaintLevel:
    """SkillDefinition accepts an optional taint_level field."""

    def test_default_none(self) -> None:
        skill = SkillDefinition(name="foo", description="d", version="1.0")
        assert skill.taint_level is None

    def test_valid_values(self) -> None:
        for level in ("owner", "auth", "external"):
            skill = SkillDefinition(name="foo", description="d", version="1.0", taint_level=level)
            assert skill.taint_level == level

    def test_invalid_value(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="taint_level"):
            SkillDefinition(name="foo", description="d", version="1.0", taint_level="bogus")


class TestDynamicTaintRegistration:
    """TaintTracker.add_tool_taint allows runtime registration."""

    def setup_method(self) -> None:
        # Clear dynamic registry between tests
        TaintTracker._dynamic_tool_taints.clear()

    def test_external_skill_classified_correctly(self) -> None:
        tracker = TaintTracker()
        tracker.reset()
        TaintTracker.add_tool_taint("my_api_skill", TaintLevel.external)
        result = tracker.on_tool_output("my_api_skill")
        assert result == TaintLevel.external

    def test_no_declaration_defaults_to_owner(self) -> None:
        tracker = TaintTracker()
        tracker.reset()
        result = tracker.on_tool_output("unknown_skill")
        assert result == TaintLevel.owner

    def test_dynamic_overrides_hardcoded(self) -> None:
        """A dynamic registration for a hardcoded-external tool can override it."""
        tracker = TaintTracker()
        tracker.reset()
        # web_search is hardcoded as external; override to auth
        TaintTracker.add_tool_taint("web_search", TaintLevel.auth)
        result = tracker.on_tool_output("web_search")
        assert result == TaintLevel.auth

    def test_multiple_skills_different_taints(self) -> None:
        tracker = TaintTracker()
        tracker.reset()
        TaintTracker.add_tool_taint("skill_a", TaintLevel.external)
        TaintTracker.add_tool_taint("skill_b", TaintLevel.auth)
        TaintTracker.add_tool_taint("skill_c", TaintLevel.owner)

        # Test each in isolation (reset between)
        assert tracker.on_tool_output("skill_a") == TaintLevel.external

        tracker.reset()
        assert tracker.on_tool_output("skill_b") == TaintLevel.auth

        tracker.reset()
        assert tracker.on_tool_output("skill_c") == TaintLevel.owner


class TestRegistryWiring:
    """SkillRegistry.register wires taint into TaintTracker."""

    def setup_method(self) -> None:
        TaintTracker._dynamic_tool_taints.clear()

    def test_register_skill_with_taint(self) -> None:
        registry = SkillRegistry()
        skill = SkillDefinition(
            name="weather_api",
            description="Fetches weather",
            version="1.0",
            taint_level="external",
        )
        registry.register(skill)

        tracker = TaintTracker()
        tracker.reset()
        assert tracker.on_tool_output("weather_api") == TaintLevel.external

    def test_register_skill_without_taint(self) -> None:
        registry = SkillRegistry()
        skill = SkillDefinition(
            name="internal_tool",
            description="Does stuff",
            version="1.0",
        )
        registry.register(skill)

        tracker = TaintTracker()
        tracker.reset()
        assert tracker.on_tool_output("internal_tool") == TaintLevel.owner

    def test_register_auth_skill(self) -> None:
        registry = SkillRegistry()
        skill = SkillDefinition(
            name="crm_reader",
            description="Reads CRM",
            version="1.0",
            taint_level="auth",
        )
        registry.register(skill)

        tracker = TaintTracker()
        tracker.reset()
        assert tracker.on_tool_output("crm_reader") == TaintLevel.auth
