"""Parse Markdown files with YAML frontmatter into Topic models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import yaml

from silas.topics.model import Topic


class TopicParseError(Exception):
    """Raised when topic markdown cannot be parsed."""


def parse_topic(markdown: str) -> Topic:
    """Parse a markdown string with YAML frontmatter into a Topic.

    The markdown must start with ``---`` followed by YAML frontmatter
    and a closing ``---``. Everything after is the body.
    """
    stripped = markdown.strip()
    if not stripped.startswith("---"):
        raise TopicParseError("Missing frontmatter delimiter")

    # Split on the second ---
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        raise TopicParseError("Incomplete frontmatter: missing closing delimiter")

    frontmatter_raw = parts[1].strip()
    body = parts[2].strip()

    try:
        frontmatter: dict = yaml.safe_load(frontmatter_raw) or {}  # type: ignore[assignment]
    except yaml.YAMLError as exc:
        raise TopicParseError(f"Invalid YAML in frontmatter: {exc}") from exc

    if not isinstance(frontmatter, dict):
        raise TopicParseError("Frontmatter must be a YAML mapping")

    now = datetime.now(tz=UTC)

    # Build the base dict of known fields; extras are ignored silently.
    topic_data: dict = {
        "id": frontmatter.get("id", str(uuid4())),
        "name": frontmatter.get("name", "Untitled"),
        "scope": frontmatter.get("scope", "session"),
        "agent": frontmatter.get("agent", "proxy"),
        "status": frontmatter.get("status", "active"),
        "triggers": frontmatter.get("triggers", []),
        "soft_triggers": frontmatter.get("soft_triggers", []),
        "approvals": frontmatter.get("approvals", []),
        "body": body,
        "created_at": frontmatter.get("created_at", now),
        "updated_at": frontmatter.get("updated_at", now),
    }

    # Optional goal-behaviour fields — only inject if present in frontmatter.
    for field in (
        "subscriptions",
        "schedule",
        "standing_approvals",
        "reporting",
        "work_template",
        "urgency",
    ):
        if field in frontmatter:
            topic_data[field] = frontmatter[field]

    return Topic(**topic_data)


def topic_to_markdown(topic: Topic) -> str:
    """Serialize a Topic back to markdown with YAML frontmatter."""
    data = topic.model_dump(mode="json")
    body = data.pop("body")
    frontmatter = yaml.dump(data, default_flow_style=False, sort_keys=False)
    return f"---\n{frontmatter}---\n\n{body}\n"
