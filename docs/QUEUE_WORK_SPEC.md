# Queue / Work Orchestration Specification

> Silas subsystem reference — covers `silas/queue/*` and `silas/work/*`.

---

## 1. Architecture Overview

The queue/work subsystem replaces direct agent invocations with an asynchronous, message-driven bus. It sits between the **Stream** turn pipeline (user-facing) and the three core agents (proxy, planner, executor).

```
┌──────────┐         ┌─────────────┐
│  Stream  │────────▶│ QueueBridge │
└──────────┘         └──────┬──────┘
                            │ dispatch_turn / dispatch_goal
                            ▼
                     ┌─────────────┐
                     │ QueueRouter │  ← static ROUTE_TABLE
                     └──────┬──────┘
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                  ▼
   ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
   │ proxy_queue  │  │planner_queue │  │executor_queue│
   │  (Proxy      │  │  (Planner    │  │  (Executor   │
   │   Consumer)  │  │   Consumer)  │  │   Consumer)  │
   └─────────────┘  └──────────────┘  └──────────────┘
          │                 │                  │
          └─────────────────┼──────────────────┘
                            ▼
                   ┌─────────────────┐
                   │DurableQueueStore│  (SQLite, FIFO, lease-based)
                   └─────────────────┘
```

**Key components:**

| Component | Module | Role |
|-----------|--------|------|
| `DurableQueueStore` | `queue/store.py` | SQLite-backed persistence with lease semantics |
| `QueueRouter` | `queue/router.py` | Maps `message_kind` → destination queue |
| `QueueOrchestrator` | `queue/orchestrator.py` | Runs consumers as concurrent async tasks with backoff |
| `QueueBridge` | `queue/bridge.py` | Integration seam between Stream and the queue bus |
| `ProxyConsumer` | `queue/consumers.py` | Handles user messages, plan results, status events |
| `PlannerConsumer` | `queue/consumers.py` | Creates plans, manages research flow |
| `ExecutorConsumer` | `queue/consumers.py` | Executes tasks with self-healing cascade |
| `LiveWorkItemExecutor` | `work/executor.py` | Full work-item execution with gates, verification, budgets |
| `LiveExecutorPool` | `work/pool.py` | Concurrency-capped parallel dispatch with conflict detection |
| `WorkItemRunner` | `work/runner.py` | Retry loop with exponential backoff and escalation policies |
| `BatchExecutor` | `work/batch.py` | Gate-checked batch operations on work-item stores |

**Factory:** `queue/factory.py` → `create_queue_system()` wires all components in correct dependency order and returns `(QueueOrchestrator, QueueBridge)`.

---

## 2. Message Types

All messages use the `QueueMessage` envelope (`queue/types.py`), a Pydantic model with:

- `id` (UUID4, auto-generated)
- `queue_name` (set by router)
- `message_kind` (see table below)
- `sender` ∈ `{user, proxy, planner, executor, runtime}`
- `trace_id` (propagated unchanged across all hops)
- `payload` (dict, extensible; typed payloads available via `typed_payload()`)
- Lease fields: `lease_id`, `lease_expires_at`, `attempt_count`
- First-class fields (§2.1): `scope_id`, `taint`, `task_id`, `parent_task_id`, `work_item_id`, `approval_token`, `tool_allowlist`, `urgency`

### Message Kinds and Routing

| `message_kind` | Destination Queue | Typed Payload | Description |
|-----------------|-------------------|---------------|-------------|
| `user_message` | `proxy_queue` | `UserMessagePayload` | Inbound user turn |
| `agent_response` | `proxy_queue` | `AgentResponsePayload` | Proxy response for `collect_response` |
| `plan_request` | `planner_queue` | `PlanRequestPayload` | Request to create/revise a plan |
| `plan_result` | `proxy_queue` | — | Planner's completed plan (markdown) |
| `execution_request` | `executor_queue` | `ExecutionRequestPayload` | Task to execute |
| `execution_status` | `proxy_queue` | `StatusPayload` | Status update (done/failed/stuck/blocked) |
| `research_request` | `executor_queue` | — | Read-only research micro-task |
| `research_result` | `planner_queue` | — | Research findings returned to planner |
| `planner_guidance` | `runtime_queue` | — | Consult-planner response |
| `replan_request` | `planner_queue` | — | Request alternative strategy after failure |
| `approval_request` | `proxy_queue` | — | Plan approval request for user |
| `approval_result` | `runtime_queue` | — | Approval decision |
| `system_event` | `proxy_queue` | — | Informational runtime events |

### Error and Status Payloads

- **`ErrorPayload`**: `error_code` ∈ `{tool_failure, budget_exceeded, gate_blocked, approval_denied, verification_failed, timeout}`, plus `message`, `origin_agent`, `retryable`, optional `detail`.
- **`StatusPayload`**: `status` ∈ `{running, done, failed, stuck, blocked, verification_failed}`, `work_item_id`, `attempt`, optional `detail`.
- **`ResearchConstraints`**: `return_format`, `max_tokens`, `tools_allowed` (clamped to research allowlist).

---

## 3. Consumer Roles

### 3.1 ProxyConsumer (`proxy_queue`)

Entry point for user interaction. Handles:

| Incoming kind | Behavior |
|---------------|----------|
| `user_message` | Runs proxy agent → if `route=planner`, emits `plan_request`; otherwise emits `agent_response` |
| `plan_result` | Parses plan markdown → requests user approval → if approved, emits `execution_request` |
| `execution_status` | Routes to UI surfaces via `status_router` (stream, activity) |
| `agent_response` | Terminal — consumed by `QueueBridge.collect_response` |
| `approval_request`, `system_event` | Informational pass-through to proxy agent |

### 3.2 PlannerConsumer (`planner_queue`)

Creates and revises execution plans. Handles:

| Incoming kind | Behavior |
|---------------|----------|
| `plan_request` | Runs planner → if research needed, dispatches `research_request` messages; otherwise emits `plan_result` |
| `research_result` | Feeds result into `ResearchStateMachine`; when all results collected (or timed out), re-runs planner with research context → emits `plan_result` |
| `replan_request` | Runs planner with failure history → emits `plan_result` with `is_replan=True` |

### 3.3 ExecutorConsumer (`executor_queue`)

Executes tasks with the self-healing cascade. Handles:

| Incoming kind | Behavior |
|---------------|----------|
| `execution_request` | Runs executor → on failure, triggers consult→retry→replan→escalate cascade → emits `execution_status` |
| `research_request` | Runs executor in read-only research mode → emits `research_result` |

### Base Consumer Lifecycle (`BaseConsumer`)

All consumers share: `lease → idempotency check → dead-letter check → process → mark_processed → ack`. On failure: `nack` (returns message to queue). Lease heartbeats extend active leases every 20s during long processing.

---

## 4. Bridge Interface

`QueueBridge` (`queue/bridge.py`) is the integration seam for Stream:

| Method | Purpose |
|--------|---------|
| `dispatch_turn(user_message, trace_id, metadata, *, scope_id, taint, tool_allowlist)` | Enqueues `user_message` to `proxy_queue` |
| `dispatch_goal(goal_id, goal_description, trace_id)` | Enqueues autonomous `plan_request` directly to `planner_queue` (bypasses proxy) |
| `collect_response(trace_id, timeout_s=30.0)` | Polls `proxy_queue` for matching `agent_response` using `lease_filtered` |

**Design rationale:** Bridge avoids modifying Stream's ~800-line orchestration. Stream calls bridge methods instead of direct agent invocations, enabling incremental migration to queue-based dispatch.

**Polling:** `collect_response` polls at 100ms intervals using `lease_filtered` (filters by `trace_id` + `message_kind`), avoiding the O(n) lease-then-nack pattern.

---

## 5. Durable Store

`DurableQueueStore` (`queue/store.py`) — SQLite-backed via `aiosqlite`.

### Tables

| Table | Purpose |
|-------|---------|
| `queue_messages` | Active messages (FIFO per queue, indexed on `queue_name + lease state + created_at`) |
| `dead_letters` | Permanently failed messages (kept for debugging) |
| `processed_messages` | Idempotency ledger (`consumer × message_id`, unique constraint) |

### Operations

| Operation | Semantics |
|-----------|-----------|
| `enqueue(msg)` | INSERT with pre-set `queue_name` |
| `lease(queue_name, lease_duration_s=60)` | Atomic UPDATE+RETURNING on oldest available message (no lease or expired lease) |
| `lease_filtered(queue_name, trace_id, message_kind, ...)` | Filtered lease for specific response polling |
| `ack(message_id)` | DELETE from `queue_messages` |
| `nack(message_id)` | Clear lease, increment `attempt_count` |
| `dead_letter(message_id, reason)` | Move to `dead_letters`, delete from `queue_messages` |
| `heartbeat(message_id, extend_s=60)` | Extend `lease_expires_at` |
| `has_processed(consumer, message_id)` | Idempotency check |
| `mark_processed(consumer, message_id)` | Record processing (INSERT OR IGNORE) |
| `requeue_expired()` | Startup crash recovery — clear all expired leases |
| `pending_count(queue_name)` | Monitoring — count unleased messages |

### Schema Migration

`_migrate_add_columns()` uses `PRAGMA table_info` introspection to add new columns (`scope_id`, `taint`, `task_id`, `parent_task_id`, `work_item_id`, `approval_token`, `tool_allowlist`, `urgency`) without data loss.

---

## 6. Error Handling

### 6.1 Consumer-Level

- **Max attempts:** 5 (default). After exhaustion → `dead_letter` with reason `max_attempts_exceeded`.
- **Idempotency:** `has_processed` / `mark_processed` ensures exactly-once processing per consumer. Crash between `mark_processed` and `ack` is safe — re-leased message is detected as already processed.
- **Lease heartbeat:** Background task extends lease every 20s to prevent timeout during long agent runs.

### 6.2 Self-Healing Cascade (Principle #8)

When execution fails, the `ExecutorConsumer` escalates through:

```
execute ──fail──▶ consult_planner ──guidance──▶ guided_retry ──fail──▶ replan (depth ≤ 2) ──fail──▶ escalate_to_user
                       │                                                    │
                       └──timeout──▶ replan ─────────────────────────────────┘
```

1. **Consult planner** (`ConsultPlannerManager`): Sends `plan_request` with `consult=True` to planner, polls `runtime_queue` for `planner_guidance` response (90s timeout, 0.5s poll interval).
2. **Guided retry:** Re-runs executor with planner guidance appended to prompt.
3. **Replan** (`ReplanManager`): Sends `replan_request` with full failure history. Max depth = 2 (3 total attempts: original + 2 replans).
4. **Escalate:** Returns `execution_status` with `escalated=True` and status `failed`.

Budget attribution: executor tokens charge to work-item budget; consult/replan tokens charge to plan budget (routed through `planner_queue`).

### 6.3 Work-Item Level (`LiveWorkItemExecutor`)

- **Retry loop:** Up to `budget.max_attempts` with budget tracking (`BudgetUsed`).
- **Gate checks:** `on_tool_call` and `after_step` gates evaluated before/after each attempt.
- **Verification:** External `VerificationRunner` validates results post-execution.
- **Budget enforcement:** `used.exceeds(budget)` checked before each attempt.
- **Stuck recovery:** Mirrors the queue cascade — consult → guided retry → replan → escalate.

### 6.4 WorkItemRunner

- Exponential backoff: `base × 2^(attempt-1)`, capped at 30s.
- `on_failure` policies: `report` (no retry), `retry` (up to max), `escalate` (1 retry then escalate), `pause` (stop, mark stuck).

### 6.5 Status Routing

`status_router.py` maps execution statuses to UI surfaces:

| Status | Surfaces |
|--------|----------|
| `running` | `activity` only |
| `done`, `failed`, `stuck`, `blocked`, `verification_failed` | `stream` + `activity` |

---

## 7. Research Flow (§4.8)

`ResearchStateMachine` (`queue/research.py`) manages planner-initiated research:

### States

`planning` → `awaiting_research` → `ready_to_finalize` (or `expired`)

### Controls

| Control | Limit |
|---------|-------|
| Max in-flight | 3 concurrent requests |
| Max rounds | 5 total dispatches per planning session |
| Per-request timeout | 120s |
| Deduplication | SHA-256 hash of `query\|return_format\|max_tokens` |
| Message-ID dedup | Prevents replay of duplicate `research_result` messages |

### Flow

1. Planner runs → outputs research requests → `PlannerConsumer` dispatches `research_request` messages via SM.
2. `ExecutorConsumer` runs in read-only research mode → emits `research_result`.
3. `PlannerConsumer` feeds results into SM → when all collected (or timed out), re-runs planner with research context → emits final `plan_result`.
4. Partial results: if some requests time out, planner finalizes with available data (flagged `partial_research=True`).

---

## 8. Parallel Execution (§7)

`LiveExecutorPool` (`work/pool.py`) provides:

- **Per-scope semaphore:** Default 8 concurrent per scope.
- **Global semaphore:** Default 16 concurrent across all scopes.
- **Conflict detection (§7.2):** Items with overlapping `input_artifacts_from` paths are serialized; non-conflicting items dispatch in parallel.
- **Wave scheduling:** `LiveWorkItemExecutor._build_waves()` groups topologically-sorted work items into parallel waves based on dependency resolution.
- **Priority ordering:** `approved_execution` (0) > `research` (1) > `status` (2).
- **Cancellation:** `pool.cancel(task_id)` sends `CancelledError` to running task.

---

## 9. Security Model

### 9.1 Taint Propagation

`TaintLevel` (from `silas.models.messages`) is a first-class field on `QueueMessage`. Taint is set at message creation (from inbound source) and propagated through the bus. Consumers can inspect taint to enforce security policies.

### 9.2 Tool Allowlists

Per-hop tool exposure via `tool_allowlist` on `QueueMessage`:

- **Proxy → Planner:** `planner_tool_allowlist` from metadata.
- **Planner → Executor:** `executor_tool_allowlist` from metadata.
- **Research mode:** Clamped to `RESEARCH_TOOL_ALLOWLIST` (`web_search`, `read_file`, `memory_search`).
- Enforcement: `BaseConsumer._run_agent_with_allowlist()` wraps the agent's toolset in a `FilteredToolset`.

### 9.3 Approval Gates

- Plans require user approval before becoming `execution_request` messages (`ProxyConsumer._request_plan_approval`).
- Work items require valid `approval_token` checked by `ApprovalVerifier` before execution.
- Gate framework: `on_tool_call` and `after_step` triggers evaluated by configurable `GateRunner`.

### 9.4 Scope Isolation

`scope_id` on `QueueMessage` isolates worktrees/artifacts per connection. The executor pool tracks per-scope concurrency independently.

### 9.5 Autonomous Goal Constraints

Goals dispatched via `QueueBridge.dispatch_goal()` are marked `autonomous=True` in the payload. The planner/executor chain can apply stricter policies for autonomous execution (no user in the loop for approval beyond standing approval).

---

## 10. Orchestrator Lifecycle

`QueueOrchestrator` manages coordinated startup/shutdown:

- **`start()`**: Creates one `asyncio.Task` per consumer. Idempotent.
- **`stop()`**: Sets `running=False`, awaits all tasks. Exceptions logged, not propagated.
- **Poll loop:** Per-consumer exponential backoff (0.1s base, 2× multiplier, 5s cap). Resets to base on message found.

**Startup sequence** (via `create_queue_system`):
1. Initialize SQLite store (create tables, migrate schema).
2. `requeue_expired()` — recover messages from crashed consumers.
3. Wire router, consult manager, replan manager.
4. Create consumers (proxy, planner, executor).
5. Create orchestrator and bridge.
6. Caller invokes `orchestrator.start()`.
