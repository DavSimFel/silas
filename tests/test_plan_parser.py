"""Tests for MarkdownPlanParser (Phase 1c)."""

from __future__ import annotations

import pytest
from silas.models.work import WorkItemType

MINIMAL_PLAN = """---
id: task-001
type: task
title: Fix the auth bug
---

# Context
Auth module is broken.

# What to do
Fix it.
"""

FULL_PLAN = """---
id: task-002
type: task
title: Build REST API
interaction_mode: act_and_report
skills: [coding]
budget:
  max_tokens: 100000
  max_cost_usd: 1.0
  max_wall_time_seconds: 900
  max_attempts: 3
verify:
  - name: tests_pass
    run: "pytest tests/"
    expect:
      exit_code: 0
  - name: server_starts
    run: "curl http://localhost:8000/health"
    expect:
      contains: "ok"
on_stuck: consult_planner
on_failure: report
---

# Context
We need a REST API for the todos app.

# What to do
1. Create FastAPI app
2. Add CRUD endpoints
3. Write tests

# Constraints
- No external databases
- Use in-memory storage
"""

GOAL_PLAN = """---
id: goal-001
type: goal
title: Health monitor
schedule: "*/30 * * * *"
verify:
  - name: disk_space
    run: "df -h / | awk 'NR==2{print $5}' | tr -d '%'"
    expect:
      output_lt: 90.0
on_failure: spawn_task
failure_context: "Disk usage exceeded threshold. $failed_checks"
---

Monitor disk space every 30 minutes.
"""

NO_FRONTMATTER = """
Just a plain markdown document with no YAML front matter.
"""

MISSING_REQUIRED = """---
type: task
title: No ID
---
Body here.
"""


class TestMarkdownPlanParser:
    @pytest.fixture
    def parser(self):
        from silas.core.plan_parser import MarkdownPlanParser
        return MarkdownPlanParser()

    def test_minimal_plan(self, parser) -> None:
        wi = parser.parse(MINIMAL_PLAN)
        assert wi.id == "task-001"
        assert wi.type == WorkItemType.task
        assert wi.title == "Fix the auth bug"
        assert "Auth module" in wi.body
        assert "Fix it" in wi.body

    def test_full_plan_budget(self, parser) -> None:
        wi = parser.parse(FULL_PLAN)
        assert wi.id == "task-002"
        assert wi.budget.max_tokens == 100000
        assert wi.budget.max_cost_usd == 1.0
        assert wi.budget.max_attempts == 3

    def test_full_plan_verification(self, parser) -> None:
        wi = parser.parse(FULL_PLAN)
        assert len(wi.verify) == 2
        assert wi.verify[0].name == "tests_pass"
        assert wi.verify[0].expect.exit_code == 0
        assert wi.verify[1].expect.contains == "ok"

    def test_full_plan_skills(self, parser) -> None:
        wi = parser.parse(FULL_PLAN)
        assert wi.skills == ["coding"]

    def test_full_plan_interaction_mode(self, parser) -> None:
        from silas.models.agents import InteractionMode
        wi = parser.parse(FULL_PLAN)
        assert wi.interaction_mode == InteractionMode.act_and_report

    def test_goal_plan(self, parser) -> None:
        wi = parser.parse(GOAL_PLAN)
        assert wi.type == WorkItemType.goal
        assert wi.schedule == "*/30 * * * *"
        assert wi.on_failure == "spawn_task"
        assert "$failed_checks" in (wi.failure_context or "")

    def test_goal_verification(self, parser) -> None:
        wi = parser.parse(GOAL_PLAN)
        assert len(wi.verify) == 1
        assert wi.verify[0].expect.output_lt == 90.0

    def test_no_frontmatter_raises(self, parser) -> None:
        with pytest.raises((ValueError, KeyError)):
            parser.parse(NO_FRONTMATTER)

    def test_missing_required_id_raises(self, parser) -> None:
        with pytest.raises((ValueError, KeyError)):
            parser.parse(MISSING_REQUIRED)

    def test_body_excludes_frontmatter(self, parser) -> None:
        wi = parser.parse(MINIMAL_PLAN)
        assert "---" not in wi.body.strip().split("\n")[0]  # body shouldn't start with ---
        assert "id:" not in wi.body
        assert "type:" not in wi.body

    def test_on_stuck_default(self, parser) -> None:
        wi = parser.parse(MINIMAL_PLAN)
        assert wi.on_stuck == "consult_planner"
