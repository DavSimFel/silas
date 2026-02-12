"""Tests for the interactive manual acceptance harness."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from silas.manual_harness import (
    ManualScenario,
    ManualScenarioResult,
    available_manual_scenarios,
    run_manual_harness,
)


def _deterministic_prompt(
    index: int,
    total: int,
    scenario: ManualScenario,
) -> ManualScenarioResult:
    del total
    status: str = "pass" if index % 2 == 0 else "skip"
    return ManualScenarioResult(
        scenario_id=scenario.scenario_id,
        status=status,
        notes=f"result-{index}",
        evidence=[f"evidence-{index}.txt"],
        completed_at=datetime.now(UTC),
    )


def test_available_manual_scenarios_have_unique_ids() -> None:
    scenarios = available_manual_scenarios()
    scenario_ids = [scenario.scenario_id for scenario in scenarios]

    assert scenarios
    assert len(scenario_ids) == len(set(scenario_ids))
    assert any(scenario.tier == "extended" for scenario in scenarios)


def test_run_manual_harness_writes_reports(tmp_path: Path) -> None:
    artifacts = run_manual_harness(
        profile="core",
        base_url="http://127.0.0.1:8420",
        output_dir=tmp_path,
        prompt_func=_deterministic_prompt,
    )

    assert artifacts.json_report.exists()
    assert artifacts.markdown_report.exists()

    report_data = json.loads(artifacts.json_report.read_text(encoding="utf-8"))
    assert report_data["profile"] == "core"
    assert report_data["total"] > 0
    assert report_data["failed"] == 0
    assert report_data["passed"] + report_data["skipped"] == report_data["total"]

    markdown = artifacts.markdown_report.read_text(encoding="utf-8")
    assert "# Silas Manual Harness Report (core)" in markdown
    assert "## Scenario Results" in markdown


def test_full_profile_has_more_scenarios_than_core(tmp_path: Path) -> None:
    core_artifacts = run_manual_harness(
        profile="core",
        base_url="http://127.0.0.1:8420",
        output_dir=tmp_path / "core",
        prompt_func=_deterministic_prompt,
    )
    full_artifacts = run_manual_harness(
        profile="full",
        base_url="http://127.0.0.1:8420",
        output_dir=tmp_path / "full",
        prompt_func=_deterministic_prompt,
    )

    assert full_artifacts.run.total > core_artifacts.run.total
