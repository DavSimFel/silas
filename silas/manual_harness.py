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


def print_scenarios(profile: ManualHarnessProfile) -> None:
    """Print all scenarios for *profile* without interactive prompts (dry-run mode)."""
    scenarios = _select_scenarios(profile)
    click.echo(f"Silas Manual Harness — Dry Run (profile={profile})")
    click.echo(f"Total scenarios: {len(scenarios)}")
    for index, scenario in enumerate(scenarios, start=1):
        click.echo("")
        click.echo(f"[{index}/{len(scenarios)}] {scenario.scenario_id} — {scenario.title}")
        click.echo(f"  Tier: {scenario.tier}")
        click.echo(f"  Objective: {scenario.objective}")
        _echo_list("  Spec refs", scenario.spec_refs)
        _echo_list("  Preconditions", scenario.preconditions)
        _echo_list("  Steps", scenario.steps)
        _echo_list("  Expected", scenario.expected)
    click.echo("")


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
                "Test machine has uv, a browser, and required env vars (SILAS_SIGNING_PASSPHRASE)",
            ],
            steps=[
                "Run `uv run silas init --config config/silas.yaml` and complete interactive prompts.",
                "Start runtime: `SILAS_SIGNING_PASSPHRASE=<pass> uv run silas start --config config/silas.yaml`.",
                "Open http://127.0.0.1:8420/ in a browser to verify the web UI loads.",
                "Run `curl -s http://127.0.0.1:8420/health` and inspect the JSON response.",
            ],
            expected=[
                "Onboarding/init succeeds without stack traces.",
                "Web UI index.html loads (HTTP 200).",
                "GET /health returns JSON with status='ok', connections (int), and sessions (list).",
            ],
        ),
        ManualScenario(
            scenario_id="core-02-websocket-auth",
            tier="core",
            title="WebSocket auth enforcement",
            objective="Verify remote bind requires auth and unauthorized websocket clients are rejected.",
            spec_refs=["§8.1", "§11 startup validation"],
            preconditions=[
                "Web channel host set to 0.0.0.0 and auth_token set in config/silas.yaml",
            ],
            steps=[
                "Start Silas with the auth-enabled config.",
                "Connect without token: `websocat ws://127.0.0.1:8420/ws` — observe close code 4401.",
                "Connect with valid token: `websocat 'ws://127.0.0.1:8420/ws?token=<auth_token>'`.",
                'Send `{"type":"message","text":"hello"}` on the authenticated connection.',
            ],
            expected=[
                "Connection without token is closed with code 4401 reason 'unauthorized'.",
                "Authenticated connection stays open and receives a JSON response.",
            ],
        ),
        ManualScenario(
            scenario_id="core-03-direct-turn-roundtrip",
            tier="core",
            title="Direct turn and chronicle update",
            objective="Confirm baseline user-message to response flow and persisted conversation trail.",
            spec_refs=["§5.1 steps 2-3, 11, 13"],
            preconditions=["Runtime running and web UI connected via WebSocket at /ws"],
            steps=[
                "In the web UI, send a simple message (e.g. 'What can you do?').",
                "Observe that a response appears in the chat stream (stream_start → stream_chunk → stream_end or message).",
                "Open the SQLite database at data/chronicle.db and query: `SELECT * FROM chronicle ORDER BY rowid DESC LIMIT 4;`",
            ],
            expected=[
                "User sees a response without plan-approval flow.",
                "Chronicle table contains both user and agent message rows with timestamps and scope_id.",
            ],
        ),
        ManualScenario(
            scenario_id="core-04-plan-approval-execution",
            tier="core",
            title="Planner proposal, approval, and execution",
            objective="Verify plan approval gate before execution and post-approval task dispatch.",
            spec_refs=["§5.1.2", "§5.2.1", "INV-01"],
            preconditions=[
                "Runtime running with web UI connected",
                "A request likely to route to planner (e.g. multi-step coding task or file modification)",
            ],
            steps=[
                "Send a non-trivial request that triggers planning (e.g. 'Create a Python script that fetches weather data').",
                "When the approval_request card appears, decline it.",
                "Re-send the same request, and this time approve the plan.",
                "Observe execution progress messages (stream events or status updates) in the WebSocket stream.",
            ],
            expected=[
                "Declining prevents any execution from starting.",
                "Approving produces execution with status updates until completion or failure.",
            ],
        ),
        ManualScenario(
            scenario_id="core-05-verification-truth",
            tier="core",
            title="External verification determines completion",
            objective="Ensure failed verification does not report success from agent self-claims.",
            spec_refs=["§5.3", "INV-03"],
            preconditions=[
                "A plan with explicit verification checks (e.g. 'create file X' where file creation can be prevented)",
            ],
            steps=[
                "Approve and execute a task with a verification check that will fail (e.g. create a file in a read-only directory).",
                "Observe the final status reported in the WebSocket stream.",
                "Check the verification report in the work item's final state.",
            ],
            expected=[
                "Final result status is failed/stuck/blocked, not 'done'.",
                "Verification report identifies the specific failed check.",
            ],
        ),
        ManualScenario(
            scenario_id="core-06-policy-gate-block",
            tier="core",
            title="Policy-lane gate enforcement",
            objective="Validate policy gates can block and quality gates remain advisory.",
            spec_refs=["§5.1 steps 1 & 8", "§5.4", "§5.5"],
            preconditions=[
                "Gate rules configured in config/silas.yaml under silas.gates section",
            ],
            steps=[
                "Send an input that should trip a blocking policy gate (e.g. a request matching a configured deny pattern).",
                "Send an input that should raise only a quality-lane concern (advisory, non-blocking).",
                "Query the audit log (data/audit.db) for gate-related entries.",
            ],
            expected=[
                "Policy gate blocks the request or escalates with a gate_approval card.",
                "Quality signal is logged in audit without blocking the response flow.",
            ],
        ),
        ManualScenario(
            scenario_id="core-07-secret-isolation",
            tier="core",
            title="Secret isolation via /secrets endpoint",
            objective="Confirm credentials never enter websocket/chat context and are stored only by ref_id.",
            spec_refs=["§0.5 secret isolation", "§5.10.1", "§8.1 /secrets"],
            preconditions=["Runtime running with web UI connected"],
            steps=[
                "Trigger a secure-input request (e.g. connection setup requiring a credential).",
                'Alternatively, submit a test secret directly: `curl -X POST http://127.0.0.1:8420/secrets/test-key -H "Content-Type: application/json" -d \'{"value":"s3cret"}\'`',
                "Monitor WebSocket traffic (browser DevTools → Network → WS tab) for the secure_input card exchange.",
                "Inspect data/secrets/ directory for stored secret files.",
                "Search chronicle, logs, and memory stores for the literal secret value.",
            ],
            expected=[
                "POST /secrets/{ref_id} returns {ref_id: 'test-key', success: true}.",
                "WebSocket carries only metadata (ref_id, label) and success signal, never the secret value.",
                "No raw secret string appears in chronicle, context, logs, or memory rows.",
            ],
        ),
        ManualScenario(
            scenario_id="core-08-approval-replay-protection",
            tier="core",
            title="Approval replay protection",
            objective="Verify nonce and binding checks prevent token replay for unauthorized repeats.",
            spec_refs=["§5.11", "INV-02"],
            preconditions=[
                "Runtime running with a completed approval token from a previous plan execution",
            ],
            steps=[
                "Execute an approved action once with a valid approval token (approve a plan and let it run).",
                "Capture the approval token/nonce from the approval flow.",
                "Attempt to replay the same authorization context/token for a second execution.",
                "Attempt with a modified payload bound to the original token hash.",
            ],
            expected=[
                "Replay attempt is denied (token is consumed/invalidated after first use).",
                "Payload mismatch against plan hash is denied.",
            ],
        ),
        ManualScenario(
            scenario_id="core-09-context-budget-eviction",
            tier="extended",
            title="Context budget and eviction behavior",
            objective="Exercise two-tier budget enforcement and memory-before-discard persistence.",
            spec_refs=["§5.1 step 5", "§5.7"],
            preconditions=[
                "Runtime running with a low context budget configured (set silas.context.budget_tokens to a small value like 2000)",
            ],
            steps=[
                "Send many messages or trigger tool calls to exceed the context token budget.",
                "Observe context management behavior (masking, trivial-drop) in the WebSocket stream or logs.",
                "Ask about information from early in the conversation to verify memory retrieval works for evicted content.",
            ],
            expected=[
                "Budget enforcement triggers without crashing the turn flow.",
                "Previously evicted information can be recalled via memory query.",
            ],
        ),
        ManualScenario(
            scenario_id="core-10-scope-isolation",
            tier="core",
            title="Multi-scope isolation",
            objective="Validate per-connection scope boundaries for context, memory injections, and access state.",
            spec_refs=["§5.1 isolation model", "§4.3", "§15 security model"],
            preconditions=["Runtime running; two browser windows or two websocat sessions"],
            steps=[
                "Open session A: connect to `ws://127.0.0.1:8420/ws?session=session-a`.",
                "Open session B: connect to `ws://127.0.0.1:8420/ws?session=session-b`.",
                "In session A, send a message with unique content (e.g. 'Remember the code word ALPHA').",
                "In session B, ask 'What is the code word?' — it should not know about ALPHA.",
                "Verify GET /health shows both sessions listed.",
            ],
            expected=[
                "No cross-session leakage of messages or memories.",
                "Each session maintains independent context and conversation state.",
            ],
        ),
        ManualScenario(
            scenario_id="core-11-goal-standing-approval",
            tier="extended",
            title="Goal cycle and standing approval path",
            objective="Verify spawned fix-task authorization rules in recurring goal execution.",
            spec_refs=["§5.2.3", "§4.4", "INV-01"],
            preconditions=[
                "Scheduled goal configured in config with spawn_task on failure",
                "Scheduler (APScheduler) enabled and running",
            ],
            steps=[
                "Force a goal verification failure to trigger a spawned fix task.",
                "Validate behavior with a valid standing approval token.",
                "Remove or invalidate the standing token and verify the fallback interactive approval path.",
            ],
            expected=[
                "Valid standing token allows spawned task execution within bound scope.",
                "Invalid or missing standing token blocks execution or requires fresh interactive approval.",
            ],
        ),
        ManualScenario(
            scenario_id="core-12-audit-chain-integrity",
            tier="core",
            title="Audit chain integrity check",
            objective="Ensure audit log chain verification reports valid state under normal flow.",
            spec_refs=["§4.16", "§15"],
            preconditions=[
                "Runtime has processed several turns and actions (run core-03 and core-04 first)",
            ],
            steps=[
                "Open the audit database at data/audit.db.",
                "Query audit entries: `SELECT COUNT(*) FROM audit_log;` to confirm entries exist.",
                "Run the audit chain verification (if exposed via CLI or admin endpoint), or inspect hash chain continuity manually.",
            ],
            expected=[
                "Audit chain verifies successfully (each entry's prev_hash matches prior entry).",
                "No gaps or hash mismatches in the chain.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-01-connections-escalation",
            tier="extended",
            title="Connection setup and permission escalation",
            objective="Exercise setup conversation, then permission escalation with decision outcomes.",
            spec_refs=["§5.10.1", "§5.10.2", "§3.12"],
            preconditions=["At least one connection skill installed (e.g. github_skill)"],
            steps=[
                "Trigger a connection setup flow via the web UI (e.g. ask Silas to connect to GitHub).",
                "Complete the setup card steps (device_code/browser_redirect/secure_input).",
                "Trigger an action needing higher permission than currently granted.",
                "Test approve, just-this-once, and deny on the permission_escalation card.",
            ],
            expected=[
                "Setup cards progress and complete with structured results.",
                "Escalation outcomes match decision semantics and are recorded in audit.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-02-review-card-stack",
            tier="extended",
            title="Review queue card behavior",
            objective="Validate reviewed batch and decision-card queue semantics.",
            spec_refs=["§0.5.1", "§0.5.3", "§5.1.5"],
            preconditions=["A goal or task that emits batch and decision cards"],
            steps=[
                "Generate multiple pending review items (e.g. trigger a batch action).",
                "Observe that only one card is active/actionable at a time in the UI.",
                "Execute approve, decline, and edit-selection outcomes on successive cards.",
            ],
            expected=[
                "Only one active card is actionable at a time (FIFO queue).",
                "Batch edit-selection requires subset handling and re-approval where required.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-03-proactivity-autonomy-loop",
            tier="extended",
            title="Suggestion and autonomy calibration loop",
            objective="Check proactive suggestion cadence and threshold-proposal review flow.",
            spec_refs=["§5.1.6", "§5.9"],
            preconditions=[
                "Heartbeat scheduler enabled in config",
                "Enough interaction history for metrics (run several core scenarios first)",
            ],
            steps=[
                "Allow the suggestion heartbeat to run and review generated suggestion cards.",
                "Drive correction outcomes (decline/edit suggestions) to approach autonomy proposal thresholds.",
                "When an autonomy_threshold_review card appears, approve or decline it.",
            ],
            expected=[
                "Suggestions honor cooldown/dedupe behavior.",
                "Autonomy changes only occur after an explicitly approved proposal.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-04-restart-rehydration",
            tier="extended",
            title="Crash/restart rehydration path",
            objective="Validate state continuity after restart for active scopes and work items.",
            spec_refs=["§5.1.3", "§17.2"],
            preconditions=[
                "Runtime running with an active conversation and at least one in-progress work item",
            ],
            steps=[
                "Note current state: recent messages, any running work item IDs.",
                "Stop the runtime (Ctrl+C or `kill`).",
                "Restart: `SILAS_SIGNING_PASSPHRASE=<pass> uv run silas start --config config/silas.yaml`.",
                "Reconnect the web UI and verify context/work item state is rehydrated.",
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
            preconditions=["A test skill source available (local path or GitHub URL)"],
            steps=[
                "Run skill validation/install for a native-format skill via the CLI or web UI.",
                "Run external import/adaptation flow and review the transformation report.",
                "Attempt install without an approval token where policy requires one.",
            ],
            expected=[
                "Validation report is deterministic and explicit about issues/transforms.",
                "Activation does not occur without the required approval path.",
            ],
        ),
        ManualScenario(
            scenario_id="ext-06-personality-hooks",
            tier="extended",
            title="Personality injection and event decay hooks",
            objective="Exercise style directive injection and post-turn mood update/decay pipeline.",
            spec_refs=["§5.1.4", "§5.14", "§5.15"],
            preconditions=["Personality enabled in config (silas.personality.enabled: true)"],
            steps=[
                "Run turns in different contexts (e.g. ask for a code review, then casual chat, then a status report).",
                "Trigger mood-affecting events (tool success, tool failure, user feedback) and inspect persona state.",
                "Wait or advance time and verify decay trends toward neutral baseline.",
            ],
            expected=[
                "Directive style varies by context without affecting security/approval semantics.",
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
    scenario_map: dict[str, ManualScenario] = {
        scenario.scenario_id: scenario for scenario in scenarios
    }

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
        lines.append(
            f"- Spec refs: {', '.join(scenario.spec_refs) if scenario.spec_refs else 'none'}"
        )
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
    "print_scenarios",
    "run_manual_harness",
]
