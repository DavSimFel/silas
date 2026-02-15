"""Tests for silas.topics — Phase 1: Model, Parser, Registry, Matcher, TopicManager."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from silas.core.topic_manager import TopicManager, TopicMatch
from silas.models.topics import TopicFrontmatter
from silas.topics.matcher import TriggerMatcher
from silas.topics.model import SoftTrigger, Topic, TriggerSpec
from silas.topics.parser import TopicParseError, parse_topic, topic_to_markdown
from silas.topics.registry import TopicRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_TOPIC_MD = """\
---
id: "abc-123"
name: "CI Failure Handler"
scope: project
agent: executor
status: active
triggers:
  - source: github
    event: check_suite.completed
    filter:
      repo: silas
      conclusion: failure
soft_triggers:
  - keywords: ["ci", "build", "failure"]
    entity: github
approvals:
  - tool: codex_exec
    constraints:
      max_runtime: 300
---

# CI Failure Handler

When CI fails, investigate and propose a fix.
"""

MINIMAL_TOPIC_MD = """\
---
name: "Quick Note"
scope: session
agent: proxy
---

Just a simple topic body.
"""


@pytest.fixture
def tmp_topics_dir(tmp_path: Path) -> Path:
    d = tmp_path / "topics"
    d.mkdir()
    return d


@pytest.fixture
def registry(tmp_topics_dir: Path) -> TopicRegistry:
    return TopicRegistry(tmp_topics_dir)


@pytest.fixture
def matcher() -> TriggerMatcher:
    return TriggerMatcher()


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_parse_valid_topic(self) -> None:
        topic = parse_topic(VALID_TOPIC_MD)
        assert topic.id == "abc-123"
        assert topic.name == "CI Failure Handler"
        assert topic.scope == "project"
        assert topic.agent == "executor"
        assert topic.status == "active"
        assert len(topic.triggers) == 1
        assert topic.triggers[0].source == "github"
        assert topic.triggers[0].filter["repo"] == "silas"
        assert len(topic.soft_triggers) == 1
        assert "ci" in topic.soft_triggers[0].keywords
        assert len(topic.approvals) == 1
        assert topic.approvals[0].tool == "codex_exec"
        assert "CI Failure Handler" in topic.body

    def test_parse_minimal_defaults(self) -> None:
        topic = parse_topic(MINIMAL_TOPIC_MD)
        assert topic.name == "Quick Note"
        assert topic.status == "active"  # default
        assert topic.triggers == []
        assert topic.soft_triggers == []
        assert topic.approvals == []
        # ID should be auto-generated UUID
        assert len(topic.id) > 0

    def test_parse_missing_frontmatter(self) -> None:
        with pytest.raises(TopicParseError, match="Missing frontmatter"):
            parse_topic("No frontmatter here")

    def test_parse_incomplete_frontmatter(self) -> None:
        with pytest.raises(TopicParseError, match="Incomplete frontmatter"):
            parse_topic("---\nname: test\nno closing delimiter")

    def test_parse_invalid_yaml(self) -> None:
        with pytest.raises(TopicParseError, match="Invalid YAML"):
            parse_topic("---\n: :\n  - ][bad\n---\nbody")

    def test_roundtrip(self) -> None:
        original = parse_topic(VALID_TOPIC_MD)
        md = topic_to_markdown(original)
        restored = parse_topic(md)
        assert original.id == restored.id
        assert original.name == restored.name
        assert original.scope == restored.scope
        assert original.triggers[0].source == restored.triggers[0].source


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestModel:
    def test_trigger_spec_defaults(self) -> None:
        t = TriggerSpec(source="manual")
        assert t.event is None
        assert t.filter == {}
        assert t.expr is None

    def test_topic_status_default(self) -> None:
        now = datetime.now(tz=UTC)
        topic = Topic(
            id="x",
            name="t",
            scope="session",
            agent="proxy",
            body="b",
            created_at=now,
            updated_at=now,
        )
        assert topic.status == "active"

    def test_invalid_scope_rejected(self) -> None:
        now = datetime.now(tz=UTC)
        with pytest.raises(Exception):  # noqa: B017
            Topic(
                id="x",
                name="t",
                scope="invalid",  # type: ignore[arg-type]
                agent="proxy",
                body="b",
                created_at=now,
                updated_at=now,
            )


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    async def test_create_and_get(self, registry: TopicRegistry) -> None:
        topic = await registry.create(VALID_TOPIC_MD)
        assert topic.id == "abc-123"
        loaded = await registry.get("abc-123")
        assert loaded is not None
        assert loaded.name == topic.name

    async def test_get_nonexistent(self, registry: TopicRegistry) -> None:
        assert await registry.get("nonexistent") is None

    async def test_list_all(self, registry: TopicRegistry) -> None:
        await registry.create(VALID_TOPIC_MD)
        await registry.create(MINIMAL_TOPIC_MD)
        topics = await registry.list()
        assert len(topics) == 2

    async def test_list_by_status(self, registry: TopicRegistry) -> None:
        paused_md = VALID_TOPIC_MD.replace("status: active", "status: paused").replace(
            'id: "abc-123"', 'id: "paused-1"'
        )
        await registry.create(VALID_TOPIC_MD)
        await registry.create(paused_md)
        active = await registry.list(status="active")
        assert len(active) == 1
        assert active[0].id == "abc-123"

    async def test_update(self, registry: TopicRegistry) -> None:
        await registry.create(VALID_TOPIC_MD)
        updated_md = VALID_TOPIC_MD.replace("status: active", "status: paused")
        topic = await registry.update("abc-123", updated_md)
        assert topic.status == "paused"

    async def test_update_nonexistent(self, registry: TopicRegistry) -> None:
        with pytest.raises(FileNotFoundError):
            await registry.update("nope", VALID_TOPIC_MD)

    async def test_delete(self, registry: TopicRegistry) -> None:
        await registry.create(VALID_TOPIC_MD)
        assert await registry.delete("abc-123") is True
        assert await registry.get("abc-123") is None

    async def test_delete_nonexistent(self, registry: TopicRegistry) -> None:
        assert await registry.delete("nope") is False

    async def test_find_by_trigger(self, registry: TopicRegistry) -> None:
        await registry.create(VALID_TOPIC_MD)
        matches = await registry.find_by_trigger(
            "github", "check_suite.completed", {"repo": "silas", "conclusion": "failure"}
        )
        assert len(matches) == 1
        assert matches[0].id == "abc-123"

    async def test_find_by_trigger_no_match(self, registry: TopicRegistry) -> None:
        await registry.create(VALID_TOPIC_MD)
        matches = await registry.find_by_trigger("gitlab", "push", {})
        assert len(matches) == 0

    async def test_find_by_keywords(self, registry: TopicRegistry) -> None:
        await registry.create(VALID_TOPIC_MD)
        matches = await registry.find_by_keywords("the ci build had a failure")
        assert len(matches) == 1
        assert matches[0].id == "abc-123"

    async def test_find_by_keywords_no_match(self, registry: TopicRegistry) -> None:
        await registry.create(VALID_TOPIC_MD)
        matches = await registry.find_by_keywords("deploy succeeded perfectly")
        assert len(matches) == 0

    async def test_lifecycle_active_to_paused_to_archived(self, registry: TopicRegistry) -> None:
        topic = await registry.create(VALID_TOPIC_MD)
        assert topic.status == "active"

        paused_md = VALID_TOPIC_MD.replace("status: active", "status: paused")
        topic = await registry.update("abc-123", paused_md)
        assert topic.status == "paused"

        archived_md = VALID_TOPIC_MD.replace("status: active", "status: archived")
        topic = await registry.update("abc-123", archived_md)
        assert topic.status == "archived"


# ---------------------------------------------------------------------------
# Matcher tests
# ---------------------------------------------------------------------------


class TestMatcher:
    def test_match_hard_exact(self, matcher: TriggerMatcher) -> None:
        triggers = [TriggerSpec(source="github", event="push")]
        assert matcher.match_hard({"source": "github", "event": "push"}, triggers) is True

    def test_match_hard_with_filter(self, matcher: TriggerMatcher) -> None:
        triggers = [TriggerSpec(source="github", event="push", filter={"branch": "main"})]
        assert (
            matcher.match_hard({"source": "github", "event": "push", "branch": "main"}, triggers)
            is True
        )
        assert (
            matcher.match_hard({"source": "github", "event": "push", "branch": "dev"}, triggers)
            is False
        )

    def test_match_hard_no_match(self, matcher: TriggerMatcher) -> None:
        triggers = [TriggerSpec(source="github", event="push")]
        assert matcher.match_hard({"source": "gitlab", "event": "push"}, triggers) is False

    def test_match_hard_empty_triggers(self, matcher: TriggerMatcher) -> None:
        assert matcher.match_hard({"source": "github"}, []) is False

    def test_match_soft_full_keywords(self, matcher: TriggerMatcher) -> None:
        st = [SoftTrigger(keywords=["ci", "failure"], entity="github")]
        score = matcher.match_soft("CI failure on github actions", st)
        assert score == 1.0

    def test_match_soft_partial_keywords(self, matcher: TriggerMatcher) -> None:
        st = [SoftTrigger(keywords=["ci", "failure", "deploy"])]
        score = matcher.match_soft("ci failure detected", st)
        assert 0.5 < score < 1.0  # 2/3 keywords

    def test_match_soft_no_match(self, matcher: TriggerMatcher) -> None:
        st = [SoftTrigger(keywords=["kubernetes", "pod"])]
        score = matcher.match_soft("the weather is nice", st)
        assert score == 0.0

    def test_match_soft_entity_only(self, matcher: TriggerMatcher) -> None:
        st = [SoftTrigger(entity="github")]
        score = matcher.match_soft("something about GitHub", st)
        assert score == 1.0

    def test_match_soft_empty(self, matcher: TriggerMatcher) -> None:
        assert matcher.match_soft("anything", []) == 0.0


# ---------------------------------------------------------------------------
# TopicFrontmatter tests
# ---------------------------------------------------------------------------


class TestTopicFrontmatter:
    def test_from_topic(self) -> None:
        topic = parse_topic(VALID_TOPIC_MD)
        fm = TopicFrontmatter.from_topic(topic)
        assert fm.id == topic.id
        assert fm.name == topic.name
        assert fm.scope == topic.scope
        assert not hasattr(fm, "body") or "body" not in fm.model_fields


# ---------------------------------------------------------------------------
# TopicManager tests
# ---------------------------------------------------------------------------


@pytest.fixture
def topic_files_dir(tmp_path: Path) -> Path:
    d = tmp_path / "topics"
    d.mkdir()
    (d / "ci.md").write_text(VALID_TOPIC_MD)
    (d / "note.md").write_text(MINIMAL_TOPIC_MD)
    return d


@pytest.fixture
def manager(topic_files_dir: Path) -> TopicManager:
    mgr = TopicManager(topic_files_dir)
    mgr.load()
    return mgr


class TestTopicManager:
    def test_load(self, manager: TopicManager) -> None:
        assert len(manager.topics) == 2

    def test_load_empty_dir(self, tmp_path: Path) -> None:
        mgr = TopicManager(tmp_path / "nonexistent")
        assert mgr.load() == 0

    def test_match_event(self, manager: TopicManager) -> None:
        matches = manager.match_event(
            {
                "source": "github",
                "event": "check_suite.completed",
                "repo": "silas",
                "conclusion": "failure",
            }
        )
        assert len(matches) == 1
        assert matches[0].topic.id == "abc-123"
        assert matches[0].match_type == "hard"

    def test_match_event_no_match(self, manager: TopicManager) -> None:
        matches = manager.match_event({"source": "gitlab", "event": "push"})
        assert len(matches) == 0

    def test_match_message(self, manager: TopicManager) -> None:
        matches = manager.match_message("the ci build had a failure")
        assert len(matches) >= 1
        assert matches[0].topic.id == "abc-123"
        assert matches[0].match_type == "soft"

    def test_match_message_no_match(self, manager: TopicManager) -> None:
        matches = manager.match_message("the weather is lovely today")
        assert len(matches) == 0

    def test_get_context(self, manager: TopicManager) -> None:
        topic = manager.topics[0]
        ctx = manager.get_context(topic)
        assert topic.name in ctx
        assert topic.body in ctx

    def test_get_context_for_matches(self, manager: TopicManager) -> None:
        matches = manager.match_message("ci failure")
        ctx = manager.get_context_for_matches(matches)
        assert "CI Failure Handler" in ctx

    def test_get_context_for_matches_empty(self, manager: TopicManager) -> None:
        assert manager.get_context_for_matches([]) == ""

    def test_get_context_for_matches_respects_max(self, manager: TopicManager) -> None:
        # Create fake matches
        topic = manager.topics[0]
        matches = [
            TopicMatch(topic=topic, score=1.0, match_type="soft"),
            TopicMatch(topic=topic, score=0.9, match_type="soft"),
            TopicMatch(topic=topic, score=0.8, match_type="soft"),
            TopicMatch(topic=topic, score=0.7, match_type="soft"),
        ]
        ctx = manager.get_context_for_matches(matches, max_topics=2)
        # Should only render 2 topics (even though same topic, the count matters)
        assert ctx.count("## Topic:") == 2
