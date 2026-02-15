from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, select_autoescape

from silas.config import PromptConfig, PromptsConfig

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"<!--\s*(?:prompt[-_ ]?)?version\s*:\s*([^>]+?)\s*-->")

_DEFAULT_FALLBACKS: dict[str, str] = {
    "proxy": (
        "You are the Silas Proxy agent.\n\n"
        "Return a valid RouteDecision object for every request.\n\n"
        'Routing criteria:\n- route="direct": simple questions, greetings, factual lookups, '
        'and single-step tasks.\n- route="planner": multi-step tasks, tasks requiring '
        "tools/skills, or tasks with dependencies.\n\nOutput contract:\n"
        "- direct route: set response.message with the user-facing answer.\n- planner "
        "route: set response to null; planner will produce plan actions.\n- always set "
        "reason, interaction_register, interaction_mode, and context_profile.\n\n"
        "Context profile guidance:\n- conversation: general dialogue and simple Q&A\n"
        "- coding: code/debug/implementation tasks\n- research: investigation and "
        "source-heavy lookups\n- support: troubleshooting and helpdesk-style requests\n"
        "- planning: explicit multi-step orchestration requests\n\n"
        "When tools are available, use them to gather information BEFORE making your\n"
        "routing decision. For example, search memory for relevant context or look up\n"
        "facts via web search.\n"
    ),
    "planner": (
        "You are the Silas Planner agent.\n\n"
        "Return a valid AgentResponse with a plan_action that includes markdown plan text.\n\n"
        "Requirements:\n- Always produce parseable markdown with YAML front matter.\n"
        "- YAML must include: id, type, title.\n- Keep the body actionable and concise.\n"
        "- Set needs_approval=true for executable plans.\n- Use "
        "interaction_mode=act_and_report unless the task is clearly high-risk.\n"
    ),
    "executor": (
        "You are the Silas Executor agent.\n\n"
        "Return valid `ExecutorAgentOutput` JSON.\n\nRules:\n- Summarize the action taken.\n"
        "- Emit tool calls in execution order.\n- Keep artifact_refs aligned to produced "
        "artifacts.\n- Return concise, actionable next_steps.\n"
    ),
}


class PromptManager:
    """Resolve, render, and cache system prompts for agents.

    Why this manager exists: prompt behavior must be externally configurable
    and auditable while preserving deterministic fallback behavior.
    """

    def __init__(
        self,
        prompts_config: PromptsConfig | None = None,
        *,
        prompt_dir: Path | None = None,
        fallback_prompts: Mapping[str, str] | None = None,
        base_context: Mapping[str, Any] | None = None,
    ) -> None:
        self._prompts_config = prompts_config or PromptsConfig()
        self._prompt_dir = prompt_dir or (
            Path(__file__).resolve().parent.parent / "agents" / "prompts"
        )
        self._fallback_prompts = dict(fallback_prompts or _DEFAULT_FALLBACKS)
        self._base_context = dict(base_context or {})
        self._jinja = Environment(
            undefined=StrictUndefined,
            autoescape=select_autoescape(
                enabled_extensions=("html", "xml"),
                disabled_extensions=("md",),
                default_for_string=False,
                default=False,
            ),
        )
        self._render_cache: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
        self._source_cache: dict[str, _PromptSource] = {}

    def get_prompt(self, agent_name: str, **context: object) -> str:
        """Return a rendered prompt for `agent_name`.

        The source selection order is:
        1) prompt config (path/template),
        2) `silas/agents/prompts/<agent>_system.md`,
        3) built-in hardcoded fallback.
        """

        normalized_name = agent_name.strip().lower()
        if not normalized_name:
            raise ValueError("agent_name must be non-empty")

        source = self._resolve_source(normalized_name)
        render_context = self._build_context(normalized_name, context)
        cache_key = (normalized_name, _freeze_context(render_context))
        if cache_key in self._render_cache:
            return self._render_cache[cache_key]

        template = self._jinja.from_string(source.template)
        rendered = template.render(render_context).strip()
        self._render_cache[cache_key] = rendered
        return rendered

    def _resolve_source(self, agent_name: str) -> _PromptSource:
        cached = self._source_cache.get(agent_name)
        if cached is not None:
            return cached

        configured = self._resolve_config_source(agent_name)
        if configured is not None:
            self._log_active_prompt(agent_name, configured)
            self._source_cache[agent_name] = configured
            return configured

        file_source = self._resolve_file_source(agent_name)
        if file_source is not None:
            self._log_active_prompt(agent_name, file_source)
            self._source_cache[agent_name] = file_source
            return file_source

        fallback_text = self._fallback_prompts.get(agent_name)
        if fallback_text is None:
            raise KeyError(f"no fallback prompt configured for agent '{agent_name}'")
        fallback_source = _PromptSource(
            template=fallback_text,
            version="builtin",
            source=f"builtin:{agent_name}",
        )
        self._log_active_prompt(agent_name, fallback_source)
        self._source_cache[agent_name] = fallback_source
        return fallback_source

    def _resolve_config_source(self, agent_name: str) -> _PromptSource | None:
        config = getattr(self._prompts_config, agent_name, None)
        if not isinstance(config, PromptConfig):
            return None

        if config.path is not None:
            path = Path(config.path)
            if path.exists():
                template = path.read_text(encoding="utf-8").strip()
                if template:
                    return _PromptSource(
                        template=template,
                        version=config.version or _extract_version(template),
                        source=str(path),
                    )
                logger.warning("Configured prompt path is empty: %s", path)
            else:
                logger.warning("Configured prompt path not found: %s", path)

        if config.template is not None and config.template.strip():
            return _PromptSource(
                template=config.template.strip(),
                version=config.version or _extract_version(config.template),
                source=f"config:prompts.{agent_name}.template",
            )
        return None

    def _resolve_file_source(self, agent_name: str) -> _PromptSource | None:
        file_path = self._prompt_dir / f"{agent_name}_system.md"
        if not file_path.exists():
            return None
        template = file_path.read_text(encoding="utf-8").strip()
        if not template:
            return None
        return _PromptSource(
            template=template,
            version=_extract_version(template),
            source=str(file_path),
        )

    def _build_context(
        self,
        agent_name: str,
        runtime_context: Mapping[str, object],
    ) -> dict[str, object]:
        merged: dict[str, object] = {
            "agent_name": agent_name,
            **self._base_context,
            **self._prompts_config.variables,
        }

        agent_cfg = getattr(self._prompts_config, agent_name, None)
        if isinstance(agent_cfg, PromptConfig):
            merged.update(agent_cfg.variables)

        merged.update(runtime_context)
        return merged

    def _log_active_prompt(self, agent_name: str, source: _PromptSource) -> None:
        logger.info(
            "PromptManager active prompt agent=%s version=%s source=%s",
            agent_name,
            source.version or "unknown",
            source.source,
        )


def _extract_version(template: str) -> str | None:
    lines = template.splitlines()
    for line in lines[:5]:
        match = _VERSION_RE.match(line.strip())
        if match:
            version = match.group(1).strip()
            return version or None
    return None


def _freeze_context(context: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, _freeze_value(value)) for key, value in context.items()))


def _freeze_value(value: object) -> str:
    if isinstance(value, Mapping):
        frozen = tuple(sorted((str(key), _freeze_value(item)) for key, item in value.items()))
        return repr(frozen)
    if isinstance(value, list):
        return repr(tuple(_freeze_value(item) for item in value))
    if isinstance(value, tuple):
        return repr(tuple(_freeze_value(item) for item in value))
    return repr(value)


class _PromptSource:
    def __init__(self, *, template: str, version: str | None, source: str) -> None:
        self.template = template
        self.version = version
        self.source = source
