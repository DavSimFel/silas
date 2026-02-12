from __future__ import annotations

from silas.core.plan_executor import build_skill_work_item


def test_build_skill_work_item_metadata_approval_cannot_be_downgraded() -> None:
    action = {
        "title": "Store sensitive memory",
        "body": "Persist memory payload.",
        "needs_approval": False,
        "work_item": {"needs_approval": False},
    }

    work_item = build_skill_work_item(
        "memory_store",
        action,
        turn_number=12,
        requires_approval=True,
    )

    assert work_item.needs_approval is True


def test_build_skill_work_item_accepts_stricter_planner_request() -> None:
    action = {
        "title": "Run safe skill with extra caution",
        "body": "Explicitly request approval anyway.",
        "needs_approval": True,
    }

    work_item = build_skill_work_item(
        "web_search",
        action,
        turn_number=12,
        requires_approval=False,
    )

    assert work_item.needs_approval is True
