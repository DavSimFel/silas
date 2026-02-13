# Agent Loop Architecture v3 — Spec Addendum

**Status:** Draft v3 — incorporates all v2 follow-up fixes (H1, M1-M8)
**Extends:** §5.1 (turn pipeline), §5.2 (execution), §7 (agent specs)
**Preserves fully:** §5.2.1 (task execution/retry/verification), §5.2.2 (project execution), §5.2.3 (goal execution), §3.6 (approval tokens), §7.2 (plan markdown format), §9 (sandbox), §0.5.1 (UI surfaces), all INV-01 through INV-05

---

## 0. Design Principles

1. **Plans are documents.** The planner writes markdown briefings (§7.2). It does not dispatch tasks or manage execution.
2. **The runtime owns execution lifecycle.** §5.2.1 (retry, budget, verification) stays runtime-controlled. The agent chooses tactical tool calls inside each attempt; the runtime controls attempt lifecycle.
3. **Agents communicate via typed queues.** Each agent has an inbound queue. Messages are durable and typed.
4. **Security invariants are runtime-enforced, never model-discretionary.** Gates, approval tokens, verification, taint — all deterministic.
5. **Executor is stateless per-run.** Receives an ExecutionEnvelope, uses tools, returns results. No persistent history.
6. **UI surface routing is deterministic.** Runtime routes events by type/risk policy. Agents propose intent; runtime decides surface.
7. **Migration is incremental.** Current procedural pipeline remains; queue bus runs alongside, taking over scope by scope.
8. **Full autonomy within approval boundaries.** The system is designed for indefinite autonomous operation. The approval system (§3.6) is the sole restriction boundary. Within approved scope, the runtime acts without human intervention — no artificial "check with human" defaults, no timeouts that require human presence. Standing approvals (§5.2.3) enable long-running autonomous operation. The self-healing cascade is: retry → consult-planner → re-plan → escalate. Each level must be exhausted before moving to the next. User escalation happens ONLY when: (a) approval is required by policy, (b) a gate blocks with `require_approval`, or (c) all automated recovery paths are exhausted.

---

## 1. Architecture Overview

Three agent loops + runtime bus. Each agent is a pydantic-ai `Agent` with registered tools. The Stream manages queues, lifecycle, gates, approval, and UI routing.

```
                         User
                          │
                    ┌─────▼──────┐
                    │   STREAM   │
                    │  (Runtime  │
                    │   Bus)     │
                    │            │
                    │ Owns:      │
                    │ • Queues   │
                    │ • Gates    │  Stream ──► Review
                    │ • Approval │  Stream ──► Activity  
                    │ • Context  │  
                    │ • §5.2.1   │
                    └──┬───┬───┬─┘
                       │   │   │
          ┌────────────┘   │   └────────────┐
          ▼                ▼                ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │  PROXY   │    │ PLANNER  │    │ EXECUTOR │
    │  Agent   │    │  Agent   │    │  Agent   │
    │  Loop    │    │  Loop    │    │  (per    │
    │          │    │          │    │  attempt)│
    │ model:   │    │ model:   │    │          │
    │ fast     │    │ deep     │    │ model:   │
    │          │    │          │    │ capable  │
    │ HAS      │    │ HAS      │    │          │
    │ history  │    │ history  │    │ STATELESS│
    │ (per     │    │ (per     │    │ per-run  │
    │  scope)  │    │  scope)  │    │          │
    └──────────┘    └──────────┘    └──────────┘
```

---

## 2. Queue Infrastructure

### 2.1 QueueMessage Contract

```python
ErrorCode = Literal[
    "tool_failure",
    "budget_exceeded",
    "gate_blocked",
    "approval_denied",
    "verification_failed",
    "timeout",
]

ExecutionStatus = Literal[
    "running",
    "done",
    "failed",
    "stuck",
    "blocked",
    "verification_failed",
]

@dataclass
class StatusPayload:
    status: ExecutionStatus
    detail: str | None = None
    attempt_number: int | None = None
    budget_remaining_tokens: int | None = None
    budget_remaining_usd: float | None = None

@dataclass
class ErrorPayload:
    error_code: ErrorCode
    retryable: bool
    origin_agent: Literal["proxy", "planner", "executor", "runtime"]
    attempt_number: int
    detail: str

QueuePayload = StatusPayload | ErrorPayload | dict[str, object]

@dataclass
class QueueMessage:
    message_id: str                          # Unique ID (idempotency key)
    trace_id: str                            # Cross-hop trace correlation ID
    content: str                             # Instruction/result text
    sender: Literal["user", "proxy", "planner", "executor", "runtime"]
    message_kind: Literal[
        "user_message",        # User input → proxy
        "plan_request",        # Proxy → planner
        "plan_result",         # Planner → proxy (contains plan_markdown)
        "research_request",    # Planner → executor (read-only micro-task)
        "research_result",     # Executor → planner (formatted result)
        "execution_status",    # Runtime → proxy (running/done/failed/stuck/blocked/verification_failed)
        "consult_planner",     # Runtime → planner (executor stuck, §5.2.1 on_stuck)
        "planner_guidance",    # Planner → runtime (revised briefing for retry)
        "replan_request",      # Runtime → planner (automatic re-plan after full exhaustion)
        "system_event",        # Runtime → any (lifecycle events)
    ]
    scope_id: str                            # Connection/tenant isolation
    taint: TaintLevel = TaintLevel.owner     # Propagated from source
    task_id: str | None = None               # Links related messages
    parent_task_id: str | None = None        # For research sub-tasks
    work_item: WorkItem | None = None        # For execution dispatch
    plan_markdown: str | None = None         # Planner output (§7.2 format)
    approval_token: ApprovalToken | None = None
    artifacts: dict[str, object] | None = None
    constraints: ResearchConstraints | None = None  # For research micro-tasks
    payload: QueuePayload | None = None      # Typed payload by message_kind
    error_code: ErrorCode | None = None      # Required for error-bearing messages
    retryable: bool | None = None            # Required for error-bearing messages
    origin_agent: Literal["proxy", "planner", "executor", "runtime"] | None = None
    attempt_number: int | None = None
    urgency: Literal["background", "informational", "needs_attention"] = "informational"
    created_at: datetime = field(default_factory=utc_now)

@dataclass
class ResearchConstraints:
    """Planner tells executor exactly what format to return."""
    return_format: str          # "3 bullet points, max 100 words"
    max_tokens: int = 500       # Budget for response
    tools_allowed: list[str] = field(default_factory=lambda: ["web_search", "read_file", "memory_search"])
    # Runtime MUST clamp tools_allowed to RESEARCH_TOOL_ALLOWLIST.
```

Normative payload contract:

- `message_kind=execution_status` MUST carry `payload=StatusPayload`.
- Non-status error events (for example `system_event` failures) MUST carry `payload=ErrorPayload`.
- Any error-bearing message MUST set normalized headers: `error_code`, `retryable`, `origin_agent`, `attempt_number`.
- If `message_kind=execution_status` and `status in {"failed", "stuck", "blocked", "verification_failed"}`, `error_code` MUST be set.
- `trace_id` MUST be copied unchanged across all derived messages (`plan_request -> research_request -> research_result -> plan_result -> execution_status`) for full-hop tracing.

### 2.2 Durable Queue Store

Queues are backed by SQLite for crash recovery:

```python
class DurableQueueStore:
    """Persistent queue with lease semantics for crash recovery."""
    
    async def enqueue(self, queue_name: str, message: QueueMessage) -> None
    async def lease(self, queue_name: str, lease_duration_s: float = 300) -> QueueMessage | None
        """Atomically lease next message. Returns None if empty."""
    async def heartbeat(self, message_id: str, extend_by_s: float = 120) -> None
        """Extend lease for long-running consumers (executor/planner)."""
    async def ack(self, message_id: str) -> None
        """Mark message as processed. Removes from queue."""
    async def nack(self, message_id: str) -> None
        """Return message to queue (failed processing). Increments retry count."""
    async def dead_letter(self, message_id: str, reason: str) -> None
        """Move to dead letter queue after max retries."""
    async def has_processed(self, consumer_name: str, message_id: str) -> bool
        """Dedup check before executing side effects."""
    async def mark_processed(self, consumer_name: str, message_id: str) -> None
        """Write processed marker after successful side effects."""
    
    # States: queued → leased → acked | nacked → (re-queued | dead_letter)
    # On startup: re-queue any messages in 'leased' state (crash recovery)
```

### 2.2.1 Idempotency + Replay Contract (normative)

1. `message_id` is the idempotency key for queue delivery.
2. Every consumer MUST call `has_processed(consumer_name, message_id)` before side effects (tool execution, status emission, approval calls, verifier runs).
3. If already processed: consumer MUST `ack` and return without re-running side effects.
4. After successful side effects, consumer MUST call `mark_processed(...)` before `ack`.
5. Tool calls are NOT assumed idempotent. On lease expiry/re-delivery, runtime MUST start a fresh attempt from the canonical work item state and MUST NOT replay partial in-flight tool calls from a previous crashed attempt.

### 2.2.2 Lease Heartbeat Contract (normative)

- Consumers with runs longer than `lease_duration_s / 3` MUST send periodic `heartbeat(...)`.
- Missing heartbeat until lease expiry is treated as consumer crash; message returns to `queued` and may be re-leased.
- `lease_duration_ms` and heartbeat extension counts MUST be recorded in telemetry for each lease.

### 2.3 Queue Routing Rules

| Source | Destination | message_kind | Trigger |
|--------|------------|-------------|---------|
| User | proxy_queue | `user_message` | WebSocket message arrives |
| Proxy | planner_queue | `plan_request` | Proxy routes to planner |
| Planner | proxy_queue | `plan_result` | Planner finished plan |
| Planner | executor_queue | `research_request` | Planner needs research |
| Executor | planner_queue | `research_result` | Research micro-task done |
| Runtime | proxy_queue | `execution_status` | Work item status change |
| Runtime | planner_queue | `consult_planner` | Executor stuck (on_stuck) |
| Planner | runtime_queue | `planner_guidance` | Revised briefing for stuck executor |
| Runtime | planner_queue | `replan_request` | Auto re-plan after full execution exhaustion (Principle #8) |

`runtime_queue` is a first-class durable queue. Runtime-directed messages MUST use the same lease/ack/nack/replay semantics as every other queue.

### 2.4 Telemetry Event Schema (normative)

```python
@dataclass
class QueueTelemetryEvent:
    event_name: Literal[
        "queue_depth_sample",
        "queue_wait_ms",
        "lease_duration_ms",
        "lease_heartbeat",
        "lease_expired",
    ]
    trace_id: str
    queue_name: str
    message_id: str | None = None
    value: float | int = 0
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
```

Required emission points:

- `queue_depth_sample`: sampled per queue on polling tick.
- `queue_wait_ms`: emitted on dequeue (`lease_time - created_at`).
- `lease_duration_ms`: emitted on `ack` using final lease span.
- `lease_heartbeat`: emitted on every heartbeat extension.
- `lease_expired`: emitted when a leased message is re-queued after timeout.

### 2.5 Audit Event Schema (normative)

```python
@dataclass
class RuntimeAuditEvent:
    event_type: Literal[
        "enqueue",
        "dequeue",
        "approval",
        "verify",
        "check",
        "gate_block",
    ]
    trace_id: str
    message_id: str | None
    scope_id: str
    actor: Literal[
        "user",
        "proxy",
        "planner",
        "executor",
        "runtime",
        "approval_engine",
        "gate_runner",
        "verification_runner",
    ]
    decision: str | None = None
    reason: str | None = None
    details: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
```

Audit requirements:

- `enqueue` / `dequeue`: every queue handoff.
- `approval`: every user approval decision (approve/reject/timeout).
- `verify`: every consuming token verification.
- `check`: every non-consuming token check at execution entry.
- `gate_block`: every gate block or approval denial on a tool call.

---

## 3. Proxy Agent Loop

### 3.1 Specification

- **Model:** `models.proxy` (fast/cheap)
- **Output type:** `RouteDecision` (preserved from §7.1)
- **History:** Per-scope, managed via `ContextManager.render()` (not raw pydantic-ai message_history)
- **Structured output:** Via `run_structured_agent` with retry/fallback (§5.1.0 preserved)

### 3.2 What Changes

The proxy gets a **tool loop** via pydantic-ai. Before producing its `RouteDecision`, it can call tools to gather information:

```python
proxy_agent = Agent(
    model=config.models.proxy,
    output_type=RouteDecision,
    system_prompt=proxy_system_prompt,
    toolsets=[proxy_toolset],
)
```

### 3.3 Proxy Tools

```python
# Information retrieval (read-only, no side effects)
memory_search(query: str, limit: int = 5) -> str
    """Search memory store. Returns formatted results."""

context_inspect(zone: str | None = None) -> str
    """Inspect current context state."""

web_search(query: str, max_results: int = 3) -> str
    """Search the web for factual information.
    Only available when search provider is configured."""

# User communication
tell_user(message: str) -> str
    """Send an interim message to the user in the Stream surface.
    Use for: status updates, acknowledgments before long operations.
    Runtime sends via WebSocket immediately."""
```

**No delegation tools on proxy.** Routing to planner happens via the `RouteDecision` output (route="planner"), same as §7.1. The runtime reads the route and enqueues to planner_queue. This preserves the existing contract.

### 3.4 Turn Pipeline Integration

The existing turn pipeline (§5.1 steps 0-16) stays. The change is in step 7:

**Before (current):** Proxy calls `agent.run(prompt)` → one LLM call → `RouteDecision`
**After:** Proxy calls `agent.run(prompt, toolsets=[proxy_toolset])` → tool loop (memory search, web search, etc.) → `RouteDecision`

The proxy can now look things up before deciding to route. Everything around it (gates, chronicle, memory, approval flow, output gates) stays exactly the same.

### 3.5 Status Event Handling

When the proxy queue receives `execution_status` messages from the runtime, a new agent.run() is triggered. The proxy decides how to present this to the user:

The proxy produces a `RouteDecision` with route="direct" and a response message. The runtime then applies the standard pipeline (output gates, chronicle, etc.).

**Surface routing is runtime-deterministic:**

| Event type | Surface | Rule |
|-----------|---------|------|
| `plan_result` (needs approval) | **Review** | Always → approval card |
| `execution_status` (done) | **Stream** | Always inform user |
| `execution_status` (running) | **Activity** | Background unless proxy response says otherwise |
| `execution_status` (`failed`/`stuck`/`blocked`/`verification_failed`) | **Stream** + **Activity** | Needs attention |
| Gate block | **Review** | Always → gate approval card |
| Suggestion | **Review** | Always → suggestion card |

Proxy proposes the response text; runtime routes to surface by event type.

---

## 4. Planner Agent Loop

### 4.1 Specification

- **Model:** `models.planner` (deep reasoning)
- **Output type:** `AgentResponse` with `plan_action.plan_markdown` (preserved from §7.2)
- **History:** Per-scope, per-plan. Reset between unrelated plans.
- **Structured output:** Via `run_structured_agent` with retry/fallback (§5.1.0 preserved)

### 4.2 What Changes

The planner gets a **tool loop** for research before writing the plan. It delegates fact-finding to executor micro-tasks instead of doing research directly.

```python
planner_agent = Agent(
    model=config.models.planner,
    output_type=AgentResponse,
    system_prompt=planner_system_prompt,
    toolsets=[planner_toolset],
)
```

### 4.3 Planner Tools

```python
# Research delegation (micro-tasks to executor, read-only)
request_research(task: str, return_format: str, max_tokens: int = 500) -> str
    """Delegate a research micro-task to an executor.
    
    The executor runs in research mode (read-only tools: web_search, 
    read_file, memory_search). It formats the result per return_format
    and truncates to max_tokens. Returns the formatted result.
    
    This is NON-BLOCKING. The tool enqueues the request and the result
    arrives as a new planner queue message (message_kind=research_result).
    The current agent.run() finishes. The planner continues planning 
    in the next run when the result arrives.
    """

# Memory (read-only, for checking existing plans/context)
memory_search(query: str, limit: int = 3) -> str
    """Search memory for relevant prior plans, decisions, context."""

# Self-validation
validate_plan(plan_markdown: str) -> str  
    """Parse plan through plan_parser.parse() and return validation result.
    Catches YAML errors, missing fields, invalid budget, etc."""
```

**No execution tools. No shell. No python. No web_search directly.** The planner's context is expensive. Raw search results waste it. Research goes through executor micro-tasks that return exactly what the planner asked for.

### 4.4 Research Flow (Non-Blocking)

```
Planner agent.run() starts
  → LLM: "I need to know the current stack"
  → calls request_research("What's the staging stack?", "tech list, 5 lines", 200)
  → tool enqueues research_request to executor_queue
  → tool returns: "Research dispatched (task_id=abc). Result will arrive as next message."
  → LLM: produces partial AgentResponse or a "waiting for research" status
  → agent.run() finishes

Executor picks up research_request
  → runs in RESEARCH MODE (read-only tools only)
  → web_search() → formats result per constraints
  → enqueues research_result to planner_queue

Planner agent.run() starts again (with history)
  → sees research result in message
  → continues planning with the data
  → when ready, produces AgentResponse with plan_markdown
```

### 4.5 Plan Output

The planner's final output is an `AgentResponse` with `plan_action.plan_markdown` in the §7.2 format. This goes to the proxy queue as `plan_result`. The proxy presents it. The runtime handles approval. Nothing changes in the plan contract.

### 4.6 Consultation Flow (on_stuck)

When an executor is stuck (§5.2.1 step 2e, `on_stuck: consult_planner`), the runtime enqueues a `consult_planner` message to the planner queue with:
- The original work item
- Failure details from the executor
- Attempt count and budget remaining

The planner produces `planner_guidance` — a revised briefing or strategy. The runtime injects this into the next executor attempt as additional instructions (appended to the work item body under a "Planner guidance" heading, per §5.2.1 step 2b).
`planner_guidance` is delivered through `runtime_queue` (durable), not an in-memory direct call.

### 4.6.1 Re-Plan Handling (message_kind=replan_request)

When planner receives `replan_request` from runtime (execution fully exhausted per Principle #8):

1. Planner receives the original goal, all prior failure context (attempt details, consult results, verification failures).
2. Planner MUST produce a **revised plan** with a different approach — not a retry of the same strategy. The system prompt includes: "Previous approach failed after N attempts with consult. Propose an alternative strategy."
3. Output is a standard `AgentResponse` with `plan_action.plan_markdown` (§7.2 format), enqueued as `plan_result` to proxy_queue.
4. The revised plan goes through the normal approval flow. If approved, execution begins fresh with the new plan.
5. If planner cannot produce a viable alternative (e.g., fundamental impossibility), it emits a failure `plan_result` with `error_code="verification_failed"` explaining why, which the proxy escalates to the user.
6. Maximum re-plan depth: `max_replan_depth=2` (configurable). After exhaustion, escalate to user unconditionally.

### 4.7 Planner Skills

The planner can load skills via its system prompt (§7.2: "full SKILL.md instructions for the active work item's skills"). Key skills for orchestration:

**orchestration-patterns** — Loaded when planner handles complex multi-step work:
```markdown
---
name: orchestration-patterns
description: Patterns for structuring complex multi-step plans
---

# Plan Patterns

## Sequential Pipeline
When: Each step needs the previous step's output.
Structure: Single work item with ordered prose steps.
Verification: Check final output only.

## Parallel Fan-Out
When: N independent sub-tasks, then aggregate.
Structure: Project-type work item (§5.2.2) with N child tasks, no dependencies.
Runtime handles parallel dispatch automatically.
Verification: Per-task checks + aggregate check.

## Diamond Dependency  
When: A → (B, C parallel) → D needs both.
Structure: Project with depends_on edges: D depends_on [B, C].
Runtime topologically sorts and parallelizes B/C.

## Iterative Refinement
When: Result needs multiple passes (draft → review → revise).
Structure: Sequential tasks where each follow_up_of previous.
Use continuation_of for artifact inheritance.

## Budget Allocation
- Research: <20% of total budget
- Execution: 60-70%
- Verification + retry: remaining 10-20%
- If 5+ research queries and no plan yet: decide with what you have
```

**research-methodology** — How to formulate micro-task requests:
```markdown
---
name: research-methodology
description: Efficient research delegation via executor micro-tasks
---

# Research Patterns

## Progressive Refinement
1. Broad: request_research("Main approaches to {X}?", "numbered list, 1 sentence each, max 5", 200)
2. Drill: request_research("Detail approach #3: pros/cons/requirements", "structured, 3 bullets each", 300)
3. Validate: request_research("Is {requirement} available in {stack}?", "yes/no + 1 line", 50)

## Anti-Patterns
❌ "Tell me everything about X" → context explosion
❌ Multiple broad queries → budget waste
✅ Specific questions with tight return_format
✅ max_tokens matched to actual need
```

### 4.8 Planner Research State Machine (normative)

Planner research flow is deterministic and queue-driven:

```
States:
- planning
- awaiting_research
- ready_to_finalize
- expired

Transitions:
planning --request_research--> awaiting_research
awaiting_research --research_result(all required)--> ready_to_finalize
awaiting_research --research_timeout--> planning
planning --planner_finalize--> ready_to_finalize
planning|awaiting_research --plan_timeout|max_research_rounds--> expired
ready_to_finalize --emit_plan_result--> planning (new plan context)
expired --emit_best_effort_or_failure--> planning (new plan context)
```

Normative controls:

- In-flight cap: at most `planner_research_max_in_flight=3` per `task_id`.
- Timeout: each research request has `research_timeout_s=120` default; timeout emits `ErrorPayload(error_code="timeout", retryable=True, origin_agent="executor", ...)`.
- Round cap: at most `planner_research_max_rounds=5` requests per plan before forced finalize/expire decision.
- Dedupe key: `hash(task + return_format + max_tokens)`; identical in-flight requests MUST NOT be enqueued twice.
- Replay handling: duplicate `research_result` messages with same `message_id` MUST be acked and ignored.
- Cancel semantics: when plan reaches `ready_to_finalize` or `expired`, planner marks still-pending research requests canceled; late results are ignored (audit-only).
- Completion criteria: planner MUST finalize when either:
  - all required research keys are present; or
  - timeout/round caps reached and enough context exists for a best-effort plan; otherwise emit explicit failure with `error_code="timeout"` or `error_code="verification_failed"` as applicable.

---

## 5. Executor Agent Loop

### 5.1 Specification

- **Model:** `models.executor` (cost-optimized, tool-capable)
- **Output type:** `ExecutorAgentOutput` (preserved from §7.3)
- **History:** NONE. Stateless per-run. Receives ExecutionEnvelope only.
- **Structured output:** Via `run_structured_agent` with retry/fallback (§5.1.0 preserved)

### 5.2 Two Execution Modes

The executor operates in two strictly separated modes:

#### 5.2.1 Research Mode (read-only, no approval required)

Triggered by `message_kind=research_request` from planner.

```python
RESEARCH_TOOL_ALLOWLIST = frozenset({"web_search", "read_file", "memory_search"})

def build_research_toolset(
    access_controller: AccessController,
    requested_tools: list[str],
) -> Toolset:
    clamped_names = [name for name in requested_tools if name in RESEARCH_TOOL_ALLOWLIST]
    runtime_research_tools = {
        "web_search": web_search,
        "read_file": read_file,
        "memory_search": memory_search,
    }
    # Runtime-hard disable of mutation tools (not prompt-enforced).
    disabled = {"shell_exec", "python_exec", "write_file", "skill_exec"}
    assert disabled.isdisjoint(runtime_research_tools.keys())

    selected = [runtime_research_tools[name] for name in clamped_names]
    return build_toolset_pipeline(
        SkillToolset(core_tools=selected),
        PreparedToolset(mode="research"),
        FilteredToolset(access_controller=access_controller),
        ApprovalRequiredToolset(approval_engine=approval_engine),
    )
```

- No WorkItem required
- No approval token required
- Tools requested by model constraints are runtime-clamped to `RESEARCH_TOOL_ALLOWLIST`
- No side effects possible (mutation tools are not present in runtime code)
- Tool-call gates (`on_tool_call`) apply to research tools exactly as in execution mode
- Result formatted per `constraints.return_format`, truncated to `constraints.max_tokens`
- Result enqueued to planner_queue as `research_result`

This preserves INV-01: no execution without approval. Research is explicitly read-only.

#### 5.2.2 Execution Mode (full tools, approval required)

Triggered by runtime dispatching an approved WorkItem (§5.2.1 lifecycle).

```python
execution_toolset = build_toolset_pipeline(
    # §5.1 step 6: canonical wrapper chain
    SkillToolset(core_tools=[shell_exec, python_exec, web_search] + skill_tools),
    PreparedToolset(work_item=work_item),
    FilteredToolset(access_controller=access_controller),
    ApprovalRequiredToolset(approval_engine=approval_engine),
)
```

- WorkItem required with valid approval_token
- §5.2.1 execution lifecycle is RUNTIME-OWNED (not agent-driven):

```
Runtime owns this loop:
  0. approval_engine.check(token, work_item)     # INV-01
  0.5. Follow-up artifact hydration
  1. Budget tracker initialization
  
  FOR each attempt (runtime-controlled):
    2a. Set status=running, persist
    2b. Build agent instructions from work_item.body + retry context
    
    2c. run_structured_agent(
            executor_agent,
            instructions,
            call_name="executor_attempt",
        )
        │
        │  THIS IS THE INNER TOOL LOOP (pydantic-ai native)
        │  Agent reads briefing → calls shell_exec → sees output
        │  → calls python_exec → sees result → iterates
        │  → produces ExecutorAgentOutput when done
        │  
        │  During this loop, runtime enforces:
        │  • Tool-call gates (§5.2.1 c2) via GatedTool wrappers
        │  • Argument validation against skill schemas
        │  • ApprovalRequiredToolset pauses for approval-required calls
        │  • UsageLimits (pydantic-ai) for token/request caps
        │  • Budget tracking per tool call
        │
    2c1. Collect tool execution ledger (actual results)
    2c3. Collect artifacts from actual results
    2d. Mid-execution gates (after_step)
    
    2e. VERIFICATION (runtime-enforced, NOT a tool)
        verification_runner.run_checks(work_item.verify)
        │  Runs in SEPARATE sandbox instance
        │  Agent has ZERO influence
        │  If pass → done
        │  If fail → check on_stuck → consult_planner or retry
    
    2f. If no verify checks → single success = done
  
  3. Budget exhausted / max attempts → stuck
```

**Key distinction:** The agent's tool loop (step 2c) runs INSIDE a runtime-controlled attempt. The runtime decides when to retry, when to consult planner, when to give up. The agent decides what tools to call within an attempt.

### 5.2.3 Consult-Planner Suspend/Resume Contract (normative)

When `on_stuck == "consult_planner"` and an attempt fails verification:

1. Runtime suspends the current attempt loop and persists state `awaiting_planner_guidance`.
2. Runtime enqueues `consult_planner` to `planner_queue` (durable, leased) with the same `trace_id`, failure context, and `attempt_number`.
3. Runtime waits for `planner_guidance` on `runtime_queue` with `consult_timeout_s` (default 90s).
4. On guidance arrival, runtime resumes at the next attempt (`attempt_number + 1`) and injects guidance into instructions under a deterministic "Planner guidance" section.
5. On timeout, runtime records `ErrorPayload(error_code="timeout", retryable=True, origin_agent="planner", ...)` and continues retry policy (or marks `stuck` if out of attempts).
6. If all attempts + consult exhausted → runtime triggers **automatic re-plan**: enqueues `replan_request` to planner_queue with the original goal, failure context, and all prior attempt/consult details. Planner produces a revised plan with alternative approach. This is the "re-plan" rung in the self-healing cascade (Principle #8).
7. If re-plan also fails (planner returns failure or re-planned execution also exhausts) → THEN escalate to user via Review surface.

Budget accounting:

- Tokens/cost from executor attempts and verifier runs charge to work-item budget.
- Tokens/cost from `consult_planner` and planner guidance generation charge to plan budget (the planner's own budget allocation, separate from the work-item execution budget per §7.2 budget allocation guidelines).
- Planner consult spending MUST NOT decrement the work-item execution budget.
- This is consistent with §5.2.1 "charges against the budget" — each agent charges against its own budget scope (executor → work-item budget, planner → plan budget).

### 5.3 Executor Tools (Execution Mode)

```python
# Sandboxed execution (§9.1)
shell_exec(command: list[str], env: dict | None = None) -> str
    """Execute shell command via sandbox backend.
    Commands as arg lists, not shell strings.
    Network/resource limits from SandboxConfig."""

python_exec(script: str) -> str
    """Execute Python script via sandbox backend."""

skill_exec(skill_name: str, script_name: str, args: dict) -> str
    """Execute a skill script. Path resolved from work_item.skills."""

# Research (available in both modes)
web_search(query: str, max_results: int = 5) -> str
    """Provider-backed web search."""

read_file(path: str) -> str
    """Read file from workspace/sandbox."""

write_file(path: str, content: str) -> str
    """Write file to workspace/sandbox. NOT available in research mode."""
```

**Verification is NOT a tool.** The runtime runs it after each attempt. The agent cannot skip, delay, or influence verification.

### 5.4 Executor Isolation

- No persistent message_history across work items
- Fresh sandbox instance per work item (isolated workspace)
- For parallel execution (§5.2.2): each child task gets its own executor instance with own sandbox
- Artifact namespaces: `{plan_id}/{task_id}/{attempt}/`
- No access to other executors' sandboxes, memory, or context
- Taint propagated from work item source through tool results

---

## 6. Runtime Responsibilities (Stream as Bus)

### 6.1 Turn Pipeline (§5.1 steps preserved)

The existing 16-step turn pipeline stays. Specific changes:

| Step | Before | After |
|------|--------|-------|
| 7 | Proxy: single LLM call | Proxy: agent.run() with tool loop |
| 7 (planner) | Planner: single LLM call | Planner: agent.run() with research tools |
| 12 | Procedural plan dispatch | Enqueue to executor via queue |
| 12 (execution) | Synchronous in turn | Background via queue (already spec-required) |

All other steps (0-6, 8-11, 13-16) are UNCHANGED.

### 6.2 Execution Dispatch (step 12, refined)

After approval (§5.1.2), the runtime:
1. Attaches verified token to work item
2. Enqueues work item to executor_queue
3. Sends immediate acknowledgment to user ("Approved, executing now")
4. Returns from turn (user-facing turn does NOT stall)

The runtime then processes the executor queue:
- Runs §5.2.1 lifecycle (approval check → retry loop → verification)
- Within each attempt, calls `run_structured_agent(...)` with tool loop
- Consumes `planner_guidance` from `runtime_queue` before resuming suspended attempts
- Status events (`running`, `done`, `failed`, `stuck`, `blocked`, `verification_failed`) enqueued to proxy_queue
- Proxy handles user notification per §6.3

### 6.3 Status Event Routing (deterministic)

Runtime routes events to UI surfaces by type — NOT by agent discretion:

```python
def route_to_surface(event: QueueMessage) -> UISurface | tuple[UISurface, ...]:
    """Deterministic surface routing. Agents do not decide this."""
    match event.message_kind:
        case "plan_result":
            return UISurface.REVIEW  # Approval card
        case "execution_status":
            status = cast(StatusPayload, event.payload).status
            match status:
                case "done":
                    return UISurface.STREAM  # Tell user
                case "running":
                    return UISurface.ACTIVITY  # Background
                case "failed" | "stuck" | "blocked" | "verification_failed":
                    return (UISurface.STREAM, UISurface.ACTIVITY)  # Dual-emit: user notification + audit timeline
        case "consult_planner":
            return UISurface.ACTIVITY  # Internal, user sees timeline entry
        case _:
            return UISurface.ACTIVITY  # Default: audit timeline
```

### 6.4 Gate Enforcement Points (all preserved)

| Gate trigger | Where enforced | Owner |
|-------------|---------------|-------|
| `every_user_message` | §5.1 step 1 (input gates) | Runtime |
| `on_tool_call` | §5.2.1 step c2 (tool-call gates) | Runtime via GatedTool wrapper |
| `after_step` | §5.2.1 step d (mid-execution gates) | Runtime |
| `every_agent_response` | §5.1 step 8 (output gates) | Runtime |

Gates are runtime-enforced wrappers around tools. The agent never sees gate configuration or can bypass it.

### 6.5 Approval State Machine (explicit)

```
requested → decided → issued → verified(consuming) → attached → checked(non-consuming) → executed

Transitions:
1. Runtime receives plan from planner (or tool-call gate requires approval)
2. Runtime sends approval card to Review surface
3. User decides (approve/reject) → decided
4. Runtime calls approval_engine.issue_token() → issued  
5. Runtime calls approval_engine.verify(token, work_item) → consuming verification
6. Runtime attaches token to work_item → attached
7. At execution entry, approval_engine.check(token, work_item) → non-consuming check
8. Execution proceeds → executed

Failure transitions:
- decided(rejected) → logged to audit, user notified
- verify failed → logged to audit, user notified, no execution
- check failed at execution → blocked, logged to audit
```

---

## 7. Parallel Execution (§5.2.2 enhanced)

When the planner writes a project plan with multiple work items:

1. Runtime parses plan → WorkItem tree with dependencies
2. Runtime topologically sorts by `depends_on`
3. Independent items dispatched to executor pool in parallel
4. Each executor instance: own sandbox, own budget tracker, own artifact namespace
5. As items complete, dependents are unblocked and dispatched

### 7.1 Executor Pool

```python
class ExecutorPool:
    max_concurrent: int = 8          # Per-scope cap
    max_concurrent_global: int = 16  # Cross-scope cap
    
    async def dispatch(self, work_item: WorkItem, scope_id: str) -> None
        """Dispatch to pool. Respects concurrency caps.
        Priority: approved_execution > research > status."""
    
    async def cancel(self, task_id: str) -> None
        """Cancel a running executor. Sends cancellation signal."""
```

### 7.2 Conflict Detection

Before dispatching parallel items, runtime checks:
- File resource overlaps (two items writing same paths)
- Shared service dependencies (two items deploying to same target)

If conflicts detected: serialize conflicting items regardless of dependency graph.

### 7.3 Artifact Merge

When parallel items complete and a dependent item needs their artifacts:
- Artifacts namespaced: `{plan_id}/{task_id}/{attempt}/`
- Dependent item receives explicit `input_artifacts_from` list
- No implicit merge — planner must specify artifact flow in plan

### 7.4 Workspace Isolation Model (normative, parallel executors)

Parallel executors use a git-worktree isolation model:

1. On dispatch, runtime snapshots `baseline_commit = HEAD` of the canonical workspace.
2. Runtime creates an ephemeral worktree at `.runtime/worktrees/{scope_id}/{task_id}/{attempt}` from `baseline_commit`.
3. Executor sandbox mounts only that worktree path as writable; canonical workspace is read-only to executors.
4. On success, runtime computes the patch between worktree and `baseline_commit`, acquires a per-scope merge lock, and applies via three-way merge into canonical workspace.
5. If merge conflict occurs, runtime marks the work item `blocked` with `error_code="tool_failure"` and emits a `consult_planner` or human review path per policy.
6. Worktree is deleted after `ack` or dead-letter transition.

This is copy-on-write at git object level and prevents cross-executor write races.

---

## 8. Testing Strategy

### 8.1 Unit Tests

- Queue: enqueue/lease/ack/nack/dead_letter lifecycle, crash recovery (re-lease on startup)
- Queue: heartbeat extension + lease expiry behavior
- Queue: processed-set idempotency (`has_processed` / `mark_processed`)
- Approval state machine: all transitions, failure paths
- Research mode: verify runtime allowlist clamp and mutation tools hard-disabled
- Gate enforcement: all trigger points, policy/quality lane separation
- Executor statelessness: verify no history persists between runs

### 8.2 Integration Tests (multi-agent)

- Proxy → planner → proxy (plan_request → plan_result flow)
- Planner → executor → planner (research_request → research_result flow)
- Full approval path: plan → approval card → approve → execute → done
- on_stuck flow: executor fails → consult_planner → guidance → retry
- on_stuck flow: consult timeout path with retryable timeout error payload
- Parallel execution: 3 independent tasks, verify isolation

### 8.3 Fault Injection

- Crash executor mid-tool-call: verify queue re-lease and work item recovery
- Duplicate message delivery: verify idempotency key dedup and side-effect suppression
- Planner timeout on research: verify timeout + fallback behavior
- Approval timeout: verify card expiry and cleanup

### 8.4 Load Tests

- Concurrent work items per scope (up to max_concurrent)
- Queue depth under sustained load
- Token cost tracking accuracy across agents

---

## 9. Migration Plan (Incremental)

### Phase 0: Queue Infrastructure (no behavior change)
- Implement DurableQueueStore, QueueMessage, AgentQueue
- Add queue tables to SQLite schema
- All existing behavior unchanged

### Phase 1: Proxy Tool Loop
- Register read-only tools (memory_search, web_search, context_inspect) on ProxyAgent
- Change step 7 to use `agent.run(prompt, toolsets=[proxy_toolset])`
- All other pipeline steps unchanged
- Feature flag: `config.agent_loops.proxy_tools: true`

### Phase 2: Planner Research Flow
- Register research tools on PlannerAgent
- Implement research_request/research_result queue flow
- Planner can gather info before writing plan
- Feature flag: `config.agent_loops.planner_research: true`

### Phase 3: Executor Tool Loop
- Register execution tools on ExecutorAgent via toolset pipeline
- Change §5.2.1 step 2c to use `run_structured_agent(executor_agent, ...)`
- Runtime still owns attempt lifecycle, budget, verification
- Feature flag: `config.agent_loops.executor_tools: true`

### Phase 4: Background Execution via Queue
- Move execution dispatch from synchronous to queue-based
- Implement status event flow (executor → proxy)
- Implement executor pool with concurrency caps
- Feature flag: `config.agent_loops.queue_execution: true`

### Phase 5: Full Integration
- Remove procedural fallback paths
- All agents running tool loops
- Queue-based execution for all work items
- Parity test suite must pass before removing old code

---

## 10. What This Addendum Does NOT Change

- §5.1 steps 0-6, 8-11, 13-16 (turn pipeline around agents)
- §5.2.1 execution lifecycle (runtime-owned retry/budget/verification)
- §5.2.2 project execution (topological sort, dependency tracking)
- §5.2.3 goal execution (standing approvals, spawn policy)
- §3.6 approval token lifecycle (Ed25519, nonce, consuming/non-consuming)
- §7.2 plan markdown format (YAML frontmatter + prose briefing)
- §9 sandbox isolation (subprocess/docker backends, resource limits)
- §0.5.1-§0.5.4 UI surfaces and card contracts
- §5.3 verification runner (external, deterministic, agent has zero influence)
- All INV-01 through INV-05 security invariants
- Toolset wrapper chain (Skill → Prepared → Filtered → ApprovalRequired)

---

## 11. Tooling Layer: pydantic-ai-backend

All three agents use [`pydantic-ai-backend`](https://github.com/vstorm-co/pydantic-ai-backend) as the base tooling and sandbox layer. This provides:

- **ConsoleToolset** — `ls`, `read_file`, `write_file`, `edit_file`, `grep`, `glob`, `execute` as pydantic-ai native tools
- **DockerSandbox** — container-isolated code execution with session management
- **Permission System** — pattern-based access control with presets (READONLY, DEFAULT, STRICT, PERMISSIVE)
- **Multiple Backends** — `StateBackend` (in-memory, tests), `LocalBackend` (filesystem), `DockerSandbox` (container)

### 11.1 Agent-to-Backend Mapping

| Agent | Mode | Backend | Permission Preset | Additional Tools |
|-------|------|---------|-------------------|-----------------|
| Proxy | — | `LocalBackend(root_dir=workspace, enable_execute=False)` | `READONLY_RULESET` | `web_search`, `memory_search`, `tell_user` (custom) |
| Planner | — | `LocalBackend(root_dir=workspace, enable_execute=False)` | `READONLY_RULESET` | `request_research`, `validate_plan`, `memory_search` (custom) |
| Executor | research | `LocalBackend(root_dir=worktree, enable_execute=False)` | `READONLY_RULESET` | `web_search`, `memory_search` (custom) |
| Executor | execution | `DockerSandbox(runtime=per_skill)` | `DEFAULT_RULESET` | `skill_exec` (custom) |

### 11.2 Integration with Wrapper Chain

pydantic-ai-backend tools are the innermost layer. Our wrapper chain wraps around them:

```
pydantic-ai-backend ConsoleToolset (base tools)
    → SkillToolset (adds skill-specific tools)
    → PreparedToolset (binds work_item context / research mode)
    → FilteredToolset (access_controller, runtime-enforced)
    → ApprovalRequiredToolset (gate enforcement, approval pause)
```

pydantic-ai-backend's permission system provides the base safety net. Our `FilteredToolset` + `ApprovalRequiredToolset` add the spec-mandated security layer on top (gates, approval tokens, taint propagation).

### 11.3 Sandbox Lifecycle

- **Proxy/Planner:** Use `LocalBackend` pointed at the workspace. Read-only, no execute.
- **Executor (research):** `LocalBackend` pointed at the worktree (§7.4). Read-only, no execute.
- **Executor (execution):** `DockerSandbox` created per work item attempt. Worktree mounted as working directory. Container destroyed after attempt completes or on dead-letter.
- **Tests:** `StateBackend` for fast, isolated unit tests. No filesystem or Docker dependency.
