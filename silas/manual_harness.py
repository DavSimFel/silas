"""Interactive manual acceptance harness for Silas runtime workflows."""

from __future__ import annotations

import logging
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import click
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ManualHarnessProfile = Literal["core", "full"]
ManualHarnessStatus = Literal["pass", "fail", "skip"]
ManualScenarioTier = Literal["core", "extended"]


class ManualScenario(BaseModel):
    """One manually executed acceptance scenario mapped to spec requirements."""

    scenario_id: str
    tier: ManualScenarioTier
    title: str
    objective: str
    spec_refs: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected: list[str] = Field(default_factory=list)


class ManualScenarioResult(BaseModel):
    """Recorded outcome for one manual scenario execution."""

    scenario_id: str
    status: ManualHarnessStatus
    notes: str = ""
    evidence: list[str] = Field(default_factory=list)
    completed_at: datetime


class ManualHarnessRun(BaseModel):
    """Structured output of one manual harness run."""

    run_id: str
    profile: ManualHarnessProfile
    base_url: str
    git_commit: str | None
    started_at: datetime
    completed_at: datetime
    total: int
    passed: int
    failed: int
    skipped: int
    results: list[ManualScenarioResult]


@dataclass(frozen=True)
class ManualHarnessArtifacts:
    """Written output paths for one manual harness run."""

    json_report: Path
    markdown_report: Path
    run: ManualHarnessRun


PromptFunc = Callable[[int, int, ManualScenario], ManualScenarioResult]


def available_manual_scenarios() -> list[ManualScenario]:
    """Return the canonical manual-acceptance scenario set for Silas."""

    return [
        ManualScenario(
            scenario_id="core-01-bootstrap-health",
            tier="core",
            title="Bootstrap and web health",
            objective="Validate first-start path, runtime boot, and health endpoint availability.",
            spec_refs=["§12", "§5.1 start()", "§8.1"],
            preconditions=[
                "Fresh or known-good config at config/silas.yaml",
                "Test machine has uv, browser, and required env vars",
            ],
            steps=[
                "Run `uv run silas init --config config/silas.yaml` and complete prompts.",
                "Start runtime: `SILAS_SIGNING_PASSPHRASE=<pass> uv run silas start --config config/silas.yaml`.",
                "Open `http://127.0.0.1:8420/` and call `GET /health`.",
            ],
            expected=[
                "Onboarding/init succeeds without stack traces.",
                "Web UI loads and health returns status=ok with a connection count field.",
            ],
        ),
        ManualScenario(
            scenario_id="core-02-websocket-auth",
            tier="core",
            title="WebSocket auth enforcement",
            objective="Verify remote bind requires auth and unauthorized websocket clients are rejected.",
            spec_refs=["§8.1", "§11 startup validation"],
            preconditions=[
                "Web channel host set to 0.0.0.0 in a test-only config copy",
                "auth_token configured",
            ],
            steps=[
                "Start Silas with remote bind test config.",
                "Connect websocket without token and observe close code/reason.",
                "Connect websocket with valid token and send a message event.",
            ],
            expected=[
                "Unauthorized websocket is rejected.",
                "Authorized websocket stays connected and can exchange messages.",
            ],
        ),
        ManualScenario(
            scenario_id="core-03-direct-turn-roundtrip",
            tier="core",
            title="Direct turn and chronicle update",
            objective="Confirm baseline user-message to response flow and persisted conversation trail.",
            spec_refs=["§5.1 steps 2-3, 11, 13"],
            preconditions=["Runtime running with web UI connected"],
            steps=[
                "Send a simple message that should route direct (e.g., greeting/status).",
                "Confirm response appears in Stream.",
                "Inspect persisted chronicle rows in SQLite for both user and agent entries.",
            ],
            expected=[
                "User sees response quickly without planner/approval flow.",
                "Both user and agent messages are persisted with timestamps and scope linkage.",
            ],
        ),
        ManualScenario(
            scenario_id="core-04-plan-approval-execution",
            tier="core",
            title="Planner proposal, approval, and execution",
            objective="Verify plan approval gate before execution and post-approval task dispatch.",
            spec_refs=["§5.1.2", "§5.2.1", "INV-01"],
            preconditions=["A request likely to route to planner (multi-step coding task)"],
            steps=[
                "Ask for a non-trivial action requiring a plan.",
                "Review approval card contents and decline once.",
                "Re-run request, approve, and observe execution progress events.",
            ],
            expected=[
                "Decline prevents execution.",
                "Approval produces execution and status updates until completion or fail.",
            ],
        ),
        ManualScenario(
            scenario_id="core-05-verification-truth",
            tier="core",
            title="External verification determines completion",
            objective="Ensure failed verification does not report success from agent self-claims.",
            spec_refs=["§5.3", "INV-03"],
            preconditions=["Plan with explicit verification checks where one check can be forced to fail"],
            steps=[
                "Execute a task whose tool step appears successful but verification check is intentionally failing.",
                "Observe final status and reported verification details.",
            ],
            expected=[
                "Final result is failed/stuck/blocked, not done.",
                "Verification report identifies failed check deterministically.",
            ],
        ),
        ManualScenario(
            scenario_id="core-06-policy-gate-block",
            tier="core",
            title="Policy-lane gate enforcement",
            objective="Validate policy gates can block and quality gates remain advisory.",
            spec_refs=["§5.1 steps 1 & 8", "§5.4", "§5.5"],
            preconditions=["Known gate rules configured in config and/or active work item"],
            steps=[
                "Send an input that should trip a blocking policy gate.",
                "Send an input that should raise only a quality-lane concern.",
                "Review audit entries for both events.",
            ],
            expected=[
                "Policy gate blocks or escalates deterministically.",
                "Quality signal is logged without blocking response flow.",
            ],
        ),
        ManualScenario(
            scenario_id="core-07-secret-isolation",
            tier="core",
            title="Secret isolation via /secrets endpoint",
            objective="Confirm credentials never enter websocket/chat context and are stored only by ref_id.",
            spec_refs=["§0.5 secret isolation", "§5.10.1", "§8.1 /secrets"],
            preconditions=["Connection or secure-input flow available"],
            steps=[
                "Trigger secure-input request and submit a test secret through `POST /secrets/{ref_id}`.",
                "Inspect websocket traffic and audit records for the same interaction.",
                "Inspect chat/chronicle/memory artifacts for leaked secret content.",
            ],
            expected=[
                "Websocket carries only metadata and success signal, never secret value.",
                "Audit contains ref_id marker only.",
                "No raw secret string appears in context, logs, or memory rows.",
            ],
        ),
        ManualScenario(
            scenario_id="core-08-approval-replay-protection",
            tier="core",
            title="Approval replay protection",
            objective="Verify nonce and binding checks prevent token replay for unauthorized repeats.",
            spec_refs=["§5.11", "INV-02"],
            preconditions=["Completed approval token available in test environment"],
            steps=[
                "Execute an approved action once with valid token.",
                "Attempt to replay same authorization context/token for a second run.",
                "Attempt with modified payload bound to original token hash.",
            ],
            expected=[
                "Replay attempt is denied.",
                "Payload mismatch against plan hash is denied.",
            ],
        ),
        ManualScenario(
            scenario_id="core-09-context-budget-eviction",
            tier="core",
            title="Context budget and eviction behavior",
            objective="Exercise two-tier budget enforcement and memory-before-discard persistence.",
            spec_refs=["§5.1 step 5", "§5.7"],
            preconditions=["Long enough session to exceed context profile budget"],
            steps=[
                "Create sustained conversation/tool activity to exceed budget.",
                "Observe masking/trivial-drop/subscription deactivation behavior.",
                "Verify evicted content is still recoverable through memory retrieval.",
            ],
            expected=[
                "Budget enforcement triggers without crashing turn flow.",
                "Previously evicted information can be recalled via memory query.",
            ],
        ),
        ManualScenario(
            scenario_id="core-10-scope-isolation",
            tier="core",
            title="Multi-scope isolation",
            objective="Validate per-connection scope boundaries for context, memory injections, and access state.",
            spec_refs=["§5.1 isolation model", "§4.3", "§15 security model"],
            preconditions=["Two concurrent client sessions available"],
            steps=[
                "Open two separate websocket/browser sessions.",
                "Trigger distinct context and tool usage in each session.",
                "Check that each session sees only its own chronicle/memory/workspace state.",
            ],
            expected=[
                "No cross-session leakage of messages, memories, or access transitions.",
                "Per-session decisions do not mutate the other scope state.",
            ],
        ),
        ManualScenario(
            scenario_id="core-11-goal-standing-approval",
            tier="core",
            title="Goal cycle and standing approval path",
            objective="Verify spawned fix-task authorization rules in recurring goal execution.",
            spec_refs=["§5.2.3", "§4.4", "INV-01"],
            preconditions=["Scheduled goal configured with spawn_task on failure"],
            steps=[
                "Force a goal verification failure to trigger spawned fix task.",
                "Validate behavior with valid standing token and with invalid/missing standing token.",
                "Confirm fallback interactive approval path works when standing verify fails.",
            ],
            expected=[
                "Valid standing token allows spawned task execution within bound scope.",
                "Invalid or missing standing token blocks or requires fresh approval.",
            ],
        ),
        ManualScenario(
            scenario_id="core-12-audit-chain-integrity",
            tier="core",
            title="Audit chain integrity check",
            objective="Ensure audit log chain verification reports valid state under normal flow.",
            spec_refs=["§4.16", "§15"],
            preconditions=["Runtime has processed several turns and actions"],
            steps=[
                "Invoke audit verification routine/checkpoint path from admin or diagnostic flow.",
                "Inspect returned chain-verification result and entry count.",
            ],
            expected=[
                "Audit chain verifies successfully.",
                "Checkpoint path can verify incrementally without full-history scan errors.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-01-connections-escalation",
            tier="extended",
            title="Connection setup and permission escalation",
            objective="Exercise setup conversation, then permission escalation with decision outcomes.",
            spec_refs=["§5.10.1", "§5.10.2", "§3.12"],
            preconditions=["At least one connection skill installed in test environment"],
            steps=[
                "Run setup flow for device_code/browser_redirect/secure_input strategy.",
                "Trigger action needing higher permission than currently granted.",
                "Test approve, just-this-once, and deny escalation paths.",
            ],
            expected=[
                "Setup cards progress and complete with structured results.",
                "Escalation outcomes match decision semantics and audit records.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-02-review-card-stack",
            tier="extended",
            title="Review queue card behavior",
            objective="Validate reviewed batch and decision-card queue semantics.",
            spec_refs=["§0.5.1", "§0.5.3", "§5.1.5"],
            preconditions=["Goal/task that can emit batch and decision cards"],
            steps=[
                "Generate multiple pending review items.",
                "Confirm one active card focus with up-next stack.",
                "Execute approve, decline, and edit-selection outcomes.",
            ],
            expected=[
                "Only one active card is actionable at a time.",
                "Batch edit-selection requires subset handling and re-approval where required.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-03-proactivity-autonomy-loop",
            tier="extended",
            title="Suggestion and autonomy calibration loop",
            objective="Check proactive suggestion cadence and threshold-proposal review flow.",
            spec_refs=["§5.1.6", "§5.9"],
            preconditions=["Heartbeat scheduler enabled and enough interaction history for metrics"],
            steps=[
                "Allow suggestion heartbeat to run and review generated cards.",
                "Drive correction outcomes to hit autonomy proposal thresholds.",
                "Approve or decline threshold proposal and observe resulting behavior.",
            ],
            expected=[
                "Suggestions honor cooldown/dedupe behavior.",
                "Autonomy changes only occur after explicit approved proposal.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-04-restart-rehydration",
            tier="extended",
            title="Crash/restart rehydration path",
            objective="Validate state continuity after restart for active scopes and work items.",
            spec_refs=["§5.1.3", "§17.2"],
            preconditions=["Runtime with active scope context and at least one in-progress item"],
            steps=[
                "Capture current state snapshot (turn history, running work item IDs).",
                "Stop runtime and restart.",
                "Verify context/work item/persona state is rehydrated as expected.",
            ],
            expected=[
                "Recent chronicle and relevant context return after restart.",
                "In-progress work resumes or is marked with deterministic recovery state.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-05-skill-install-import",
            tier="extended",
            title="Skill install and external adaptation",
            objective="Validate deterministic skill validation/reporting and install approval boundaries.",
            spec_refs=["§10.4", "§10.4.1", "INV-06"],
            preconditions=["Test skill source available (local path or GitHub)"],
            steps=[
                "Run skill validation/install for a native-format skill.",
                "Run external import/adaptation flow and review transformation report.",
                "Attempt install without approval token where policy requires one.",
            ],
            expected=[
                "Validation report is deterministic and explicit about issues/transforms.",
                "Activation does not occur without required approval path.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-06-personality-hooks",
            tier="extended",
            title="Personality injection and event decay hooks",
            objective="Exercise style directive injection and post-turn mood update/decay pipeline.",
            spec_refs=["§5.1.4", "§5.14", "§5.15"],
            preconditions=["Personality enabled in config"],
            steps=[
                "Run turns in different contexts (code review, casual, status).",
                "Trigger events (success, failure, feedback) and inspect persona state deltas.",
                "Wait/advance time and verify decay trends toward neutral.",
            ],
            expected=[
                "Directive style changes by context without affecting security/approval semantics.",
                "Event and decay updates persist to persona state/event storage.",
            ],
        ),
    ]


def run_manual_harness(
    profile: ManualHarnessProfile,
    base_url: str,
    output_dir: Path,
    prompt_func: PromptFunc | None = None,
) -> ManualHarnessArtifacts:
    """Run manual scenarios interactively and persist JSON/Markdown reports."""

    started_at: datetime = _utc_now()
    scenarios: list[ManualScenario] = _select_scenarios(profile)
    if not scenarios:
        msg: str = f"No manual scenarios are defined for profile={profile}"
        raise ValueError(msg)

    runner: PromptFunc = prompt_func or _interactive_prompt_for_scenario
    results: list[ManualScenarioResult] = []
    total: int = len(scenarios)

    click.echo("")
    click.echo("Silas Manual Harness")
    click.echo(f"Profile: {profile}")
    click.echo(f"Base URL: {base_url}")
    click.echo(f"Scenarios: {total}")

    for index, scenario in enumerate(scenarios, start=1):
        result: ManualScenarioResult = runner(index, total, scenario)
        results.append(result)

    completed_at: datetime = _utc_now()
    passed, failed, skipped = _count_statuses(results)

    run: ManualHarnessRun = ManualHarnessRun(
        run_id=f"manual-harness-{started_at.strftime('%Y%m%dT%H%M%SZ')}",
        profile=profile,
        base_url=base_url,
        git_commit=_safe_git_commit(),
        started_at=started_at,
        completed_at=completed_at,
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        results=results,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp: str = started_at.strftime("%Y%m%dT%H%M%SZ")
    json_report: Path = output_dir / f"manual-harness-{profile}-{stamp}.json"
    markdown_report: Path = output_dir / f"manual-harness-{profile}-{stamp}.md"

    json_report.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    markdown_report.write_text(
        _render_markdown_report(run=run, scenarios=scenarios),
        encoding="utf-8",
    )

    click.echo("")
    click.echo("Manual harness complete.")
    click.echo(f"Passed: {run.passed}  Failed: {run.failed}  Skipped: {run.skipped}")
    click.echo(f"JSON report: {json_report}")
    click.echo(f"Markdown report: {markdown_report}")

    return ManualHarnessArtifacts(
        json_report=json_report,
        markdown_report=markdown_report,
        run=run,
    )


def _interactive_prompt_for_scenario(
    index: int,
    total: int,
    scenario: ManualScenario,
) -> ManualScenarioResult:
    click.echo("")
    click.echo(f"[{index}/{total}] {scenario.scenario_id} — {scenario.title}")
    click.echo(f"Objective: {scenario.objective}")
    _echo_list("Spec refs", scenario.spec_refs)
    _echo_list("Preconditions", scenario.preconditions)
    _echo_list("Steps", scenario.steps)
    _echo_list("Expected", scenario.expected)

    status_choice: str = click.prompt(
        "Result",
        type=click.Choice(["pass", "fail", "skip"], case_sensitive=False),
        default="pass",
    )
    status: ManualHarnessStatus = cast(ManualHarnessStatus, status_choice.lower())
    notes: str = click.prompt("Notes (optional)", default="", show_default=False)
    evidence_raw: str = click.prompt(
        "Evidence paths/URLs (comma separated, optional)",
        default="",
        show_default=False,
    )

    return ManualScenarioResult(
        scenario_id=scenario.scenario_id,
        status=status,
        notes=notes.strip(),
        evidence=_split_evidence(evidence_raw),
        completed_at=_utc_now(),
    )


def _render_markdown_report(run: ManualHarnessRun, scenarios: list[ManualScenario]) -> str:
    scenario_map: dict[str, ManualScenario] = {scenario.scenario_id: scenario for scenario in scenarios}

    lines: list[str] = [
        f"# Silas Manual Harness Report ({run.profile})",
        "",
        f"- Run ID: `{run.run_id}`",
        f"- Started (UTC): `{run.started_at.isoformat()}`",
        f"- Completed (UTC): `{run.completed_at.isoformat()}`",
        f"- Base URL: `{run.base_url}`",
        f"- Git commit: `{run.git_commit or 'unknown'}`",
        f"- Summary: `{run.passed} passed / {run.failed} failed / {run.skipped} skipped`",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Status | Notes | Evidence |",
        "|---|---|---|---|",
    ]

    for result in run.results:
        scenario: ManualScenario = scenario_map[result.scenario_id]
        notes: str = _md_cell(result.notes)
        evidence_text: str = _md_cell(", ".join(result.evidence))
        lines.append(
            f"| `{scenario.scenario_id}` {scenario.title} | **{result.status.upper()}** | {notes} | {evidence_text} |"
        )

    lines.extend(["", "## Scenario Details", ""])
    for result in run.results:
        scenario = scenario_map[result.scenario_id]
        lines.append(f"### {scenario.scenario_id} — {scenario.title}")
        lines.append(f"- Status: `{result.status}`")
        lines.append(f"- Completed (UTC): `{result.completed_at.isoformat()}`")
        lines.append(f"- Objective: {scenario.objective}")
        lines.append(f"- Spec refs: {', '.join(scenario.spec_refs) if scenario.spec_refs else 'none'}")
        lines.append(f"- Notes: {result.notes or '(none)'}")
        lines.append(f"- Evidence: {', '.join(result.evidence) if result.evidence else '(none)'}")
        lines.append("")

    lines.append("")
    return "\n".join(lines)


def _select_scenarios(profile: ManualHarnessProfile) -> list[ManualScenario]:
    tiers: set[ManualScenarioTier] = {"core"} if profile == "core" else {"core", "extended"}
    all_scenarios: list[ManualScenario] = available_manual_scenarios()
    return [scenario for scenario in all_scenarios if scenario.tier in tiers]


def _count_statuses(results: list[ManualScenarioResult]) -> tuple[int, int, int]:
    counts: Counter[str] = Counter(result.status for result in results)
    passed: int = counts.get("pass", 0)
    failed: int = counts.get("fail", 0)
    skipped: int = counts.get("skip", 0)
    return passed, failed, skipped


def _echo_list(title: str, values: list[str]) -> None:
    if not values:
        return
    click.echo(f"{title}:")
    for value in values:
        click.echo(f"  - {value}")


def _split_evidence(raw: str) -> list[str]:
    items: list[str] = [part.strip() for part in raw.split(",")]
    return [item for item in items if item]


def _md_cell(value: str) -> str:
    if not value:
        return "-"
    return value.replace("|", "\\|")


def _safe_git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        logger.debug("Unable to read git commit for manual harness report", exc_info=True)
        return None

    if result.returncode != 0:
        return None
    commit: str = result.stdout.strip()
    return commit or None


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ManualHarnessArtifacts",
    "ManualHarnessRun",
    "ManualScenario",
    "ManualScenarioResult",
    "available_manual_scenarios",
    "run_manual_harness",
]
