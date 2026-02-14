# Queue & Work Orchestration Spec

> Subsystem reference for `silas/queue/` and `silas/work/`.
> Covers architecture, message contracts, consumer lifecycle, durability, error recovery, and security.

---

## 1. Architecture Overview

The queue subsystem replaces direct agent invocations with a durable, message-driven bus. Three agent consumers (proxy, planner, executor) communicate exclusively through typed `QueueMessage` envelopes persisted in SQLite.

### Component Map

```
┌─────────┐    dispatch_turn()    ┌─────────────┐
│  Stream  │─────────────────────▶│ QueueBridge  │
└─────────┘    collect_response() └──────┬───────┘
                                         │
                              ┌──────────▼──────────┐
                              │    QueueRouter       │
                              │  (ROUTE_TABLE lookup)│
                              └──────────┬───────────┘
                                         │ enqueue()
                              ┌──────────▼──────────┐
                              │  DurableQueueStore   │
                              │  (SQLite + leases)   │
                              └──────────┬───────────┘
                                         │ lease()
                    ┌────────────────────┼────────────────────┐
                    │                    │                     │
             ┌──────▼──────┐   ┌────────▼────────┐  ┌────────▼────────┐
             │ProxyConsumer │   │PlannerConsumer   │  │ExecutorConsumer  │
             │ proxy_queue  │   │ planner_queue    │  │ executor_queue   │
             └──────────────┘   └─────────────────┘  └──────────────────┘
```

**Orchestrator** (`QueueOrchestrator`) runs all consumers as concurrent asyncio tasks with exponential backoff (0.1 s base, 5 s cap). **Factory** (`create_queue_system`) wires store → router → consumers → orchestrator → bridge in one call.

### Data Flow — User Turn (Queue Path)

1. `Stream` calls `QueueBridge.dispatch_turn(text, trace_id, metadata)`.
2. Bridge builds a `user_message` `QueueMessage` and routes it to `proxy_queue`.
3. `ProxyConsumer` leases the message, runs the proxy agent.
   - If route = `"planner"` → produces `plan_request` → routed to `planner_queue`.
   - If route = `"direct"` → produces `agent_response` → stays in `proxy_queue`.
4. `PlannerConsumer` leases `plan_request`, runs planner agent.
   - May dispatch `research_request` messages to `executor_queue` (via `ResearchStateMachine`).
   - Produces `plan_result` → routed to `proxy_queue`.
5. `ProxyConsumer` receives `plan_result`, parses plan, requests approval via channel.
   - On approval → produces `execution_request` → routed to `executor_queue`.
6. `ExecutorConsumer` leases `execution_request`, runs executor (with self-healing cascade).
   - Produces `execution_status` → routed to `proxy_queue`.
7. `QueueBridge.collect_response(trace_id)` polls `proxy_queue` for `agent_response` matching `trace_id`.

### Autonomous Goals (Scheduler Path)

`QueueBridge.dispatch_goal(goal_id, description, trace_id)` enqueues a `plan_request` with `sender="runtime"` and `autonomous=True` directly to `planner_queue`, bypassing proxy.

---

## 2. Queue Message Types & Lifecycle

### 2.1 Envelope: `QueueMessage`

Every message shares a canonical Pydantic envelope:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | `str` (UUID4) | Unique, auto-generated idempotency key |
| `queue_name` | `str` | Set by router before enqueue |
| `message_kind` | `MessageKind` | Discriminator for routing and payload parsing |
| `sender` | `Sender` | `"user" \| "proxy" \| "planner" \| "executor" \| "runtime"` |
| `trace_id` | `str` (UUID4) | Propagated unchanged across all hops for distributed tracing |
| `payload` | `dict[str, object]` | Extensible body; typed access via `typed_payload()` |
| `created_at` | `datetime` | UTC timestamp, ISO 8601 in SQLite |
| `lease_id` / `lease_expires_at` | `str? / datetime?` | Set by store during lease operations |
| `attempt_count` | `int` | Incremented on nack |
| `scope_id` | `str?` | Executor scope (worktree isolation) |
| `taint` | `TaintLevel?` | Security taint from inbound source |
| `task_id` / `parent_task_id` | `str?` | Cross-message linking for plan→execute→status chain |
| `work_item_id` | `str?` | Reference to work item being executed |
| `approval_token` | `str?` | Consumed by approval engine at execution entry |
| `tool_allowlist` | `list[str]` | Per-hop tool exposure contract |
| `urgency` | `Urgency` | `"background" \| "informational" \| "needs_attention"` |

### 2.2 Message Kinds

| Kind | Route → Queue | Sender → Receiver | Typed Payload |
|------|--------------|-------------------|---------------|
| `user_message` | `proxy_queue` | user → proxy | `UserMessagePayload` |
| `plan_request` | `planner_queue` | proxy/runtime → planner | `PlanRequestPayload` |
| `plan_result` | `proxy_queue` | planner → proxy | *(raw dict)* |
| `execution_request` | `executor_queue` | proxy → executor | `ExecutionRequestPayload` |
| `execution_status` | `proxy_queue` | executor → proxy | `StatusPayload` |
| `research_request` | `executor_queue` | planner → executor | *(raw dict)* |
| `research_result` | `planner_queue` | executor → planner | *(raw dict)* |
| `planner_guidance` | `runtime_queue` | planner → runtime | *(raw dict)* |
| `replan_request` | `planner_queue` | runtime → planner | *(raw dict)* |
| `approval_request` | `proxy_queue` | runtime → proxy | *(raw dict)* |
| `approval_result` | `runtime_queue` | proxy → runtime | *(raw dict)* |
| `agent_response` | `proxy_queue` | proxy → bridge | `AgentResponsePayload` |
| `system_event` | `proxy_queue` | runtime → proxy | *(raw dict)* |

### 2.3 Message Lifecycle

```
enqueue → [available] → lease → [leased] → process
                                    │
                          ┌─────────┼─────────┐
                          ▼         ▼         ▼
                        ack       nack    dead_letter
                      (delete)  (release   (move to
                                +incr      dead_letters
                                attempt)    table)
```

- **Lease duration**: 60 s default. Extended via heartbeat every 20 s during processing.
- **Max attempts**: 5 (configurable per consumer). Exceeded → dead-letter.
- **Idempotency**: `processed_messages` table tracks `(consumer, message_id)` pairs. Consumers check `has_processed()` before executing side effects.

---

## 3. Consumer Responsibilities

All consumers extend `BaseConsumer`, which provides:
- `poll_once()` — lease one message, dispatch to `_process()`, ack/nack.
- Heartbeat task — background coroutine extending lease every 20 s.
- Idempotency check via `has_processed()` / `mark_processed()`.
- Dead-letter on `attempt_count >= max_attempts`.
- Tool allowlist enforcement via `FilteredToolset` wrapping.

### 3.1 ProxyConsumer (`proxy_queue`)

**Handles**: `user_message`, `plan_result`, `execution_status`, `agent_response`, `approval_request`, `system_event`.

| Input Kind | Behavior |
|------------|----------|
| `user_message` | Runs proxy agent. If output route = `"planner"` → emits `plan_request`. If `"direct"` → emits `agent_response` (with memory ops). |
| `plan_result` | Parses plan markdown via `MarkdownPlanParser`, requests approval via channel, emits `execution_request` on approval. |
| `execution_status` | Routes to UI surfaces via `route_to_surface()`. Terminal — no outbound message. |
| `agent_response` | Terminal. Consumed by `QueueBridge.collect_response()`. |
| Other | Runs proxy agent for informational display. |

### 3.2 PlannerConsumer (`planner_queue`)

**Handles**: `plan_request`, `research_result`, `replan_request`.

| Input Kind | Behavior |
|------------|----------|
| `plan_request` | Resets `ResearchStateMachine`, runs planner. If planner requests research → dispatches `research_request` messages, returns `None` (deferred). Otherwise emits `plan_result`. |
| `research_result` | Feeds into `ResearchStateMachine`. When all results collected (or timed out) → re-runs planner with research context → emits `plan_result`. |
| `replan_request` | Runs planner with failure history context, explicitly requesting alternative strategy. Emits `plan_result` with `is_replan=True`. |

#### Research State Machine (§4.8)

Manages multi-step research during planning with strict controls:

- **States**: `planning` → `awaiting_research` → `ready_to_finalize` (or `expired`).
- **Caps**: max 3 in-flight, max 5 rounds total, 120 s per-request timeout.
- **Dedup**: SHA-256 hash of `(query, return_format, max_tokens)`.
- **Partial finalization**: if some requests timeout, planner proceeds with available results.

### 3.3 ExecutorConsumer (`executor_queue`)

**Handles**: `execution_request`, `research_request`.

| Input Kind | Behavior |
|------------|----------|
| `execution_request` | If `WorkItemExecutor` is wired and payload contains serialized `WorkItem` → delegates to `LiveWorkItemExecutor`. Otherwise runs executor agent directly with self-healing cascade. Emits `execution_status`. |
| `research_request` | Runs executor in read-only mode (`RESEARCH MODE` prefix) with `RESEARCH_TOOL_ALLOWLIST`. Emits `research_result`. |

#### Self-Healing Cascade (Principle #8)

When execution fails (`on_stuck = "consult_planner"`):

1. **Consult planner** — `ConsultPlannerManager` enqueues guidance request to `planner_queue`, polls `runtime_queue` for response (90 s timeout).
2. **Guided retry** — re-run executor with planner guidance appended to prompt.
3. **Replan** — if guided retry fails, `ReplanManager` enqueues `replan_request` (max depth = 2).
4. **Escalate** — if replan depth exceeded, report `execution_status` with `escalated=True`.

---

## 4. Queue Bridge Interface

`QueueBridge` is the integration seam between Stream (procedural) and queue (message-driven) execution.

### `dispatch_turn(user_message, trace_id, metadata, *, scope_id, taint, tool_allowlist)`

Builds a `user_message` QueueMessage and routes to `proxy_queue`. Stream calls this instead of `proxy.run()`.

### `dispatch_goal(goal_id, goal_description, trace_id)`

Enqueues `plan_request` with `autonomous=True` directly to `planner_queue`. Used by the scheduler for standing-approved goals.

### `collect_response(trace_id, timeout_s=30.0) → QueueMessage | None`

Polls `proxy_queue` using `lease_filtered(trace_id, "agent_response")` at 100 ms intervals. Returns the matching `agent_response` or `None` on timeout. Uses filtered lease to avoid O(n) scanning and message reordering.

---

## 5. Durable Store

### 5.1 SQLite Schema

**`queue_messages`** — active messages (deleted on ack):

```sql
id TEXT PRIMARY KEY,
queue_name TEXT NOT NULL,
message_kind TEXT NOT NULL,
sender TEXT NOT NULL,
trace_id TEXT NOT NULL,
payload TEXT NOT NULL DEFAULT '{}',    -- JSON
created_at TEXT NOT NULL,               -- ISO 8601
lease_id TEXT,
lease_expires_at TEXT,
attempt_count INTEGER NOT NULL DEFAULT 0,
max_attempts INTEGER NOT NULL DEFAULT 5,
scope_id TEXT, taint TEXT, task_id TEXT, parent_task_id TEXT,
work_item_id TEXT, approval_token TEXT,
tool_allowlist TEXT NOT NULL DEFAULT '[]',  -- JSON array
urgency TEXT NOT NULL DEFAULT 'informational'
```

**Indexes**: `(queue_name, lease_id, lease_expires_at, created_at)` for lease queries; `(scope_id)` for scope filtering.

**`dead_letters`** — preserved indefinitely for debugging. Same schema + `dead_letter_reason`, `dead_lettered_at`.

**`processed_messages`** — idempotency tracking: `PRIMARY KEY (consumer, message_id)`.

### 5.2 Lease/Heartbeat Model

- **Lease**: atomic `UPDATE ... RETURNING` with subquery selecting oldest unleased (or expired-lease) message. Lease ID = UUID4, default duration 60 s.
- **Heartbeat**: extends `lease_expires_at` by 60 s. Consumers send every 20 s during processing.
- **Nack**: clears lease, increments `attempt_count`. Message returns to queue.
- **Crash recovery**: `requeue_expired()` on startup clears all expired leases.

### 5.3 Filtered Lease

`lease_filtered(queue_name, filter_trace_id, filter_message_kind)` — atomic lease restricted to messages matching specific trace and kind. Used by `collect_response()` to avoid scanning unrelated messages.

---

## 6. Error Handling & Retry Strategy

### 6.1 Queue-Level Retries

| Mechanism | Trigger | Behavior |
|-----------|---------|----------|
| **Nack + re-lease** | Consumer `_process()` raises exception | `attempt_count` incremented, message released for retry |
| **Dead-letter** | `attempt_count >= max_attempts` (default 5) | Moved to `dead_letters` table, logged as warning |
| **Lease expiry** | Consumer crashes mid-processing | Lease expires → message available for re-lease |

### 6.2 Work-Level Retries (LiveWorkItemExecutor)

| Phase | Budget Guard | Behavior |
|-------|-------------|----------|
| **Execution attempts** | `budget.max_attempts` | Retry loop with error context appended to next attempt |
| **Consult planner** | `budget.max_planner_calls` | Suspend, ask planner for guidance, guided retry |
| **Replan** | `MAX_REPLAN_DEPTH = 2` | New plan from planner (up to 2 replans = 3 total strategies) |
| **Escalate** | All above exhausted | Report to user with `escalated=True` |

### 6.3 WorkItemRunner Retry Policy

Configured via `work_item.on_failure`:

| Policy | Behavior |
|--------|----------|
| `"retry"` | Retry up to `max_attempts` with exponential backoff (1 s base, 30 s cap) |
| `"report"` | No retry, report failure immediately |
| `"escalate"` | One retry, then trigger escalation callback |
| `"pause"` | No retry, mark as `stuck` for human intervention |

### 6.4 Typed Errors

`ErrorPayload` carries structured error info: `error_code` (one of `tool_failure`, `budget_exceeded`, `gate_blocked`, `approval_denied`, `verification_failed`, `timeout`), `retryable` flag, `origin_agent`, and optional `detail`.

---

## 7. Security Model

### 7.1 Tool Allowlists

Every `QueueMessage` carries a `tool_allowlist: list[str]` field. When non-empty, `BaseConsumer._run_agent_with_allowlist()` wraps the agent's toolset in a `FilteredToolset` that only exposes listed tools. Allowlists are propagated per-hop:

- `metadata.planner_tool_allowlist` → `PlannerConsumer`
- `metadata.executor_tool_allowlist` → `ExecutorConsumer`
- `metadata.proxy_tool_allowlist` → back to `ProxyConsumer`

Research mode uses a hardcoded `RESEARCH_TOOL_ALLOWLIST` (`web_search`, `read_file`, `memory_search`).

### 7.2 Gate Enforcement

`LiveWorkItemExecutor` checks gates at two points:

1. **`on_tool_call`** — before each execution attempt. If gate blocks → `WorkItemStatus.blocked`.
2. **`after_step`** — after each failed attempt. If gate blocks → `WorkItemStatus.blocked`.

Gates are defined per work item (`work_item.gates: list[Gate]`). Gate results: `continue`, `require_approval`, or block.

### 7.3 Approval Verification

Before any execution, `LiveWorkItemExecutor` calls `ApprovalVerifier.check(token, work_item)`. Missing or invalid token → `blocked` status. Approval is also checked for plan results before dispatching `execution_request` (via channel approval flow).

### 7.4 Taint Propagation

`QueueMessage.taint: TaintLevel` propagates from the inbound message source through all downstream messages. Stored in SQLite for audit. Used by consumers to adjust trust level of agent operations.

---

## 8. Work Execution Subsystem

### 8.1 LiveWorkItemExecutor

Full work-item execution pipeline:

1. **Resolve dependencies** — topological sort of `depends_on` + `tasks` references.
2. **Build waves** — group independent items for parallel dispatch.
3. **Per-item execution**: approval check → gate check → execute (skill/shell/python) → verification → gate after-step.
4. **Budget tracking**: `BudgetUsed` aggregates tokens, attempts, wall time, planner calls, executor runs.

Executor types: `skill` (via `SkillExecutor`), `shell` (via `ShellExecutor` + sandbox), `python` (via `PythonExecutor` + sandbox).

### 8.2 LiveExecutorPool (§7.1)

Concurrency-capped async pool for parallel work-item dispatch:

- **Per-scope semaphore**: default 8 concurrent per scope.
- **Global semaphore**: default 16 concurrent total.
- **Conflict detection** (§7.2): items with overlapping `input_artifacts_from` paths are serialized.
- **Priority ordering**: `approved_execution` (0) > `research` (1) > `status` (2).

### 8.3 BatchExecutor

Executes approved batch action proposals against work item stores with optional gate checks. Filters items by `BatchActionDecision` verdict (`approve` / `decline` / `edit_selection`).

---

## 9. Integration with Stream

The queue path and direct path coexist:

| Aspect | Direct Path | Queue Path |
|--------|-------------|------------|
| Entry point | `proxy.run()` / `executor.execute()` | `QueueBridge.dispatch_turn()` |
| Durability | In-memory only | SQLite-persisted, crash-recoverable |
| Agent communication | Direct function calls | Typed messages via queue |
| Error recovery | Caller handles | Automatic cascade (retry → consult → replan → escalate) |
| Concurrency | Sequential | Parallel via `LiveExecutorPool` + wave scheduling |

Stream decides which path based on configuration (`queue_execution` flag). The bridge encapsulates all queue interaction, keeping Stream's ~800-line orchestration untouched.

### Status Routing to UI

`route_to_surface(status)` maps execution statuses to UI surfaces:

| Status | Surfaces |
|--------|----------|
| `running` | `activity` only |
| `done`, `failed`, `stuck`, `blocked`, `verification_failed` | `stream` + `activity` |

---

## 10. Queue Names Reference

| Queue | Consumers | Message Kinds Received |
|-------|-----------|----------------------|
| `proxy_queue` | `ProxyConsumer`, `QueueBridge.collect_response` | `user_message`, `plan_result`, `execution_status`, `approval_request`, `agent_response`, `system_event` |
| `planner_queue` | `PlannerConsumer` | `plan_request`, `research_result`, `replan_request` |
| `executor_queue` | `ExecutorConsumer` | `execution_request`, `research_request` |
| `runtime_queue` | `ConsultPlannerManager` (polling) | `planner_guidance`, `approval_result` |
