from __future__ import annotations

from collections.abc import Mapping

import yaml

from silas.models.agents import InteractionMode
from silas.models.gates import AccessLevel, Gate
from silas.models.work import (
    Budget,
    EscalationAction,
    VerificationCheck,
    WorkItem,
    WorkItemType,
)
from silas.protocols.work import PlanParser

_FRONT_MATTER_DELIMITER = "---"
_REQUIRED_FIELDS = ("id", "type", "title")


class MarkdownPlanParser(PlanParser):
    def parse(self, markdown: str) -> WorkItem:
        front_matter_text, body = self._split_front_matter(markdown)
        try:
            front_matter = yaml.safe_load(front_matter_text) or {}
        except yaml.YAMLError as exc:
            raise ValueError("invalid YAML front matter") from exc
        if not isinstance(front_matter, Mapping):
            raise ValueError("YAML front matter must be a mapping")

        missing = [
            field
            for field in _REQUIRED_FIELDS
            if field not in front_matter
            or front_matter[field] is None
            or (isinstance(front_matter[field], str) and not front_matter[field].strip())
        ]
        if missing:
            raise ValueError(f"missing required front matter fields: {', '.join(missing)}")

        work_item_type = self._parse_work_item_type(front_matter["type"])
        payload: dict[str, object] = {
            "id": str(front_matter["id"]),
            "type": work_item_type,
            "title": str(front_matter["title"]),
            "body": body,
        }

        self._populate_optional_fields(payload, front_matter)
        return WorkItem.model_validate(payload)

    def _populate_optional_fields(
        self,
        payload: dict[str, object],
        front_matter: Mapping[object, object],
    ) -> None:
        # Pass-through scalar fields that need no transformation
        scalar_fields = (
            "parent", "spawned_by", "follow_up_of", "domain",
            "agent", "needs_approval", "schedule", "on_stuck",
            "on_failure", "failure_context",
        )
        for field in scalar_fields:
            value = front_matter.get(field)
            if value is not None:
                payload[field] = value

        interaction_mode = front_matter.get("interaction_mode")
        if interaction_mode is not None:
            payload["interaction_mode"] = InteractionMode(interaction_mode)

        # String list fields share the same parsing logic
        for list_field in ("skills", "input_artifacts_from", "tasks", "depends_on"):
            value = front_matter.get(list_field)
            if value is not None:
                payload[list_field] = self._parse_string_list(list_field, value)

        # Complex validated fields each have their own schema
        self._populate_validated_fields(payload, front_matter)

    def _populate_validated_fields(
        self,
        payload: dict[str, object],
        front_matter: Mapping[object, object],
    ) -> None:
        """Parse and validate structured fields (budget, verify, gates, etc.)."""
        budget = front_matter.get("budget")
        if budget is not None:
            if not isinstance(budget, Mapping):
                raise ValueError("'budget' must be a mapping")
            payload["budget"] = Budget.model_validate(budget)

        verify = front_matter.get("verify")
        if verify is not None:
            if not isinstance(verify, list):
                raise ValueError("'verify' must be a list")
            payload["verify"] = [VerificationCheck.model_validate(item) for item in verify]

        gates = front_matter.get("gates")
        if gates is not None:
            if not isinstance(gates, list):
                raise ValueError("'gates' must be a list")
            payload["gates"] = [Gate.model_validate(item) for item in gates]

        access_levels = front_matter.get("access_levels")
        if access_levels is not None:
            if not isinstance(access_levels, Mapping):
                raise ValueError("'access_levels' must be a mapping")
            payload["access_levels"] = {
                str(level_name): AccessLevel.model_validate(level_data)
                for level_name, level_data in access_levels.items()
            }

        escalation = front_matter.get("escalation")
        if escalation is not None:
            if not isinstance(escalation, Mapping):
                raise ValueError("'escalation' must be a mapping")
            payload["escalation"] = {
                str(name): EscalationAction.model_validate(action)
                for name, action in escalation.items()
            }

    def _split_front_matter(self, markdown: str) -> tuple[str, str]:
        lines = markdown.splitlines()
        if not lines or lines[0].strip() != _FRONT_MATTER_DELIMITER:
            raise ValueError("markdown must start with YAML front matter delimited by '---'")

        closing_index = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.strip() == _FRONT_MATTER_DELIMITER),
            None,
        )
        if closing_index is None:
            raise ValueError("markdown is missing a closing YAML front matter delimiter")

        front_matter = "\n".join(lines[1:closing_index])
        body = "\n".join(lines[closing_index + 1 :]).strip()
        return front_matter, body

    def _parse_work_item_type(self, raw_type: object) -> WorkItemType:
        if isinstance(raw_type, WorkItemType):
            return raw_type
        try:
            return WorkItemType(str(raw_type))
        except ValueError as exc:
            valid_types = ", ".join(item.value for item in WorkItemType)
            raise ValueError(f"invalid work item type '{raw_type}'. Expected one of: {valid_types}") from exc

    def _parse_string_list(self, field_name: str, value: object) -> list[str]:
        if not isinstance(value, list):
            raise ValueError(f"'{field_name}' must be a list of strings")
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"'{field_name}' must be a list of strings")
            items.append(item)
        return items


__all__ = ["MarkdownPlanParser"]
