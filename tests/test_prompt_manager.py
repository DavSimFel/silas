from __future__ import annotations

from pathlib import Path

from silas.config import PromptConfig, PromptsConfig
from silas.core.prompt_manager import PromptManager


def test_loads_prompt_from_file_when_present(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "proxy_system.md").write_text(
        "<!-- prompt-version: v-file -->\nProxy file", "utf-8"
    )

    manager = PromptManager(prompt_dir=prompt_dir)

    prompt = manager.get_prompt("proxy")

    assert "Proxy file" in prompt


def test_config_path_override_wins_over_default_prompt_file(tmp_path: Path) -> None:
    override_path = tmp_path / "override_proxy.md"
    override_path.write_text("<!-- prompt-version: v-config -->\nConfig proxy", "utf-8")
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "proxy_system.md").write_text("Proxy from file", "utf-8")

    manager = PromptManager(
        prompts_config=PromptsConfig(proxy=PromptConfig(path=override_path)),
        prompt_dir=prompt_dir,
    )

    prompt = manager.get_prompt("proxy")

    assert "Config proxy" in prompt
    assert "Proxy from file" not in prompt


def test_renders_template_with_context_and_custom_variables() -> None:
    manager = PromptManager(
        prompts_config=PromptsConfig(
            variables={"response_style": "precise"},
            planner=PromptConfig(
                template=(
                    "Planner {{ agent_name }} style={{ response_style }} "
                    "traits={{ personality_traits.warmth }}"
                )
            ),
        ),
        base_context={"agent_name": "Silas"},
    )

    prompt = manager.get_prompt("planner", personality_traits={"warmth": "high"})

    assert prompt == "Planner Silas style=precise traits=high"


def test_uses_hardcoded_fallback_when_config_and_file_missing(tmp_path: Path) -> None:
    manager = PromptManager(prompt_dir=tmp_path / "missing-prompts")

    prompt = manager.get_prompt("executor")

    assert "You are the Silas Executor agent." in prompt


def test_caches_rendered_prompt_for_identical_context(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "proxy_system.md").write_text("Value {{ token }}", "utf-8")
    manager = PromptManager(prompt_dir=prompt_dir)
    calls = {"count": 0}
    original_from_string = manager._jinja.from_string

    def counting_from_string(template: str) -> object:
        calls["count"] += 1
        return original_from_string(template)

    manager._jinja.from_string = counting_from_string  # type: ignore[assignment]

    first = manager.get_prompt("proxy", token="abc")
    second = manager.get_prompt("proxy", token="abc")

    assert first == "Value abc"
    assert second == "Value abc"
    assert calls["count"] == 1
