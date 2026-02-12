# Implementation Status

Last updated: 2026-02-12

---

## Vision

Silas is a fully autonomous AI runtime — three pydantic-ai agent loops (proxy/planner/executor) communicating via typed durable queues. Capable of indefinite autonomous operation, restricted ONLY by the cryptographic approval system. Self-healing cascade: retry → consult-planner → re-plan → escalate.

**Key specs:**
- `specs/agent-loop-architecture.md` — multi-agent queue architecture (998 lines, v3.2, reviewed 4 rounds)
- `specs.md` — core runtime behavioral contract
- `specs/security-model.md` — security invariants (INV-01..05)
- `specs/protocols.md` — protocol interfaces
- `specs/models.md` — data models

---

## Current State

- **65 commits ahead of main**, 37 PRs merged (#27-#62)
- **~690 tests**, 0 lint errors (ruff strict, C901 max=12)
- Core runtime exists: agents (one-shot), gates, approval engine, execution pipeline, memory, context, sandbox, channels, onboarding, frontend
- Agents run as one-shot structured output — no tool loops, no queues yet

---

## ✅ What Exists (Component + Tested)

### Agents & Execution
- `ProxyAgent`, `PlannerAgent`, `ExecutorAgent` — one-shot `run_structured_agent`
- `LiveWorkItemExecutor` — retry loop, verification, budget, attempt tracking, INV-01/INV-03 enforced
- `SQLiteWorkItemStore`, `SQLiteChronicleStore`, `SQLiteAuditLog`
- `ExecutionEnvelope`, `SandboxConfig`, executor type registry (shell/python/skill)
- `MarkdownPlanParser`, plan action execution

### Security & Approval
- `SilasApprovalVerifier` (Ed25519), `SQLiteNonceStore`
- `LiveApprovalManager` — token issue/verify lifecycle
- `SilasGateRunner` + providers (`PredicateChecker`, `ScriptChecker`, `LLMChecker`)
- `SilasAccessController` — gate-driven access state
- Secret isolation (Tier 1 + Tier 2), `POST /secrets/{ref_id}`
- Two-tier key storage (Ed25519 signing keys)

### Memory & Context
- `SQLiteMemoryStore`, `SilasMemoryRetriever`, `SilasMemoryConsolidator`
- `LiveContextManager` — context budget enforcement, eviction
- `SilasPersonalityEngine`, `SQLitePersonaStore`

### Infrastructure
- `WebChannel` (WebSocket + REST), onboarding flow
- `SilasScheduler` (APScheduler)
- `SilasSkillLoader`, `LiveSkillResolver`
- `SimpleSuggestionEngine`, `SimpleAutonomyCalibrator`
- Frontend (Phase A+B+C)

### Models (Pydantic, all constrained)
- `AgentResponse`, `RouteDecision`, `Expectation`, `ContextProfile`
- `WorkItem`, `WorkItemResult`, `WorkItemStatus`, `BudgetUsed`
- `ExecutionResult`, `ExecutorToolCall`, `VerificationReport`

---

## 🏗️ Agent Loop Refactor — Work Items

The core gap: agents need tool loops and queue-based communication. ~1,650 LOC delta.

### WI-1: Durable Queue Store + Message Types
**Status:** Not started  
**Estimate:** ~400 LOC  
**Scope:**
- `silas/queue/store.py`: `DurableQueueStore` — SQLite-backed, `enqueue()`, `lease()`, `ack()`, `nack()`, `dead_letter()`, `heartbeat()`
- `silas/queue/types.py`: `QueueMessage`, `StatusPayload`, `ErrorPayload`, `ErrorCode` enum, `QueuePayload` union type, `message_kind` literals
- `silas/queue/router.py`: `QueueRouter` — routes messages to correct queue by kind (proxy_queue, planner_queue, executor_queue, runtime_queue)
- Idempotency contract: `has_processed(consumer, msg_id)` / `mark_processed()`
- Lease heartbeat: consumers with long runs must heartbeat at `lease_duration_s / 3`
- SQLite migration for queue + idempotency tables
- `silas/queue/telemetry.py`: `QueueTelemetryEvent`, `RuntimeAuditEvent` schemas
- Tests: lifecycle (enqueue→lease→ack), crash recovery (lease expiry→re-lease), heartbeat, idempotency, dead-letter, routing table

**Spec refs:** §2.1-2.5, §6.1-6.2

---

### WI-2: Wire pydantic-ai Tool Loops on All Agents
**Status:** Not started  
**Estimate:** ~500 LOC  
**Scope:**
- Add `pydantic-ai-backend[console]` dependency
- **ProxyAgent:** Register tools via `create_console_toolset(include_execute=False)` with `READONLY_RULESET` + custom `memory_search`, `web_search`, `tell_user` tools. Change `agent.run()` from one-shot structured output to tool-loop `agent.run()` that produces `RouteDecision` after optional tool use.
- **PlannerAgent:** Register `create_console_toolset(include_execute=False)` with `READONLY_RULESET` + custom `request_research`, `validate_plan`, `memory_search` tools. Implement research state machine (§4.8): `planning → awaiting_research → ready_to_finalize → expired` with in-flight cap=3, timeout=120s, dedupe.
- **ExecutorAgent:** Register `create_console_toolset()` with `DEFAULT_RULESET` for execution mode, `READONLY_RULESET` for research mode. Wire full wrapper chain: `ConsoleToolset → SkillToolset → PreparedToolset → FilteredToolset → ApprovalRequiredToolset`. Research mode uses `RESEARCH_TOOL_ALLOWLIST` clamping (hard-disabled mutation tools).
- Add `pydantic-ai-backend[docker]` dependency, wire `DockerSandbox` as executor sandbox backend (feature-flagged, subprocess fallback)
- Feature flags: `config.agent_loops.proxy_tools`, `config.agent_loops.planner_research`, `config.agent_loops.executor_tools`
- Tests: proxy tool loop produces RouteDecision, planner research delegation + state machine transitions, executor tool loop with wrapper chain enforcement, research mode allowlist blocks writes

**Spec refs:** §3, §4.1-4.8, §5.1-5.2, §11.1-11.3

---

### WI-3: Queue-Based Agent Communication + Execution
**Status:** Not started  
**Estimate:** ~450 LOC  
**Scope:**
- Replace procedural calls in `Stream._process_turn` with queue dispatch: proxy enqueues to planner_queue/executor_queue, receives results via proxy_queue
- Status event routing (§6.3): `route_to_surface()` with dual-emit (STREAM + ACTIVITY) for failure statuses
- Consult-planner suspend/resume: executor persists `awaiting_planner_guidance`, enqueues to planner_queue, waits on runtime_queue with 90s timeout. Budget split: executor tokens → work-item budget, consult tokens → plan budget.
- Replan cascade (Principle #8): after all attempts + consult exhausted → `replan_request` to planner_queue. Planner §4.6.1 produces revised plan (alternative strategy, not retry). `max_replan_depth=2`, then escalate to user.
- `trace_id` propagation across all hops
- Executor pool with concurrency caps (per-scope + global)
- Feature flag: `config.agent_loops.queue_execution`
- Tests: full flow (user msg → proxy → planner → executor → status → proxy → user), consult timeout, replan cascade, parallel execution, status routing, trace propagation

**Spec refs:** §5.2.3, §4.6.1, §6.3, §7.3-7.4

---

### WI-4: Integration + Migration
**Status:** Not started  
**Estimate:** ~300 LOC  
**Scope:**
- Parity test suite: queue-based behavior matches procedural for all existing test scenarios
- Remove procedural fallback paths (behind feature flag first, then delete)
- Frontend adaptation: queue status events → Activity surface, execution progress cards
- Standing approvals wiring for long-term autonomous goals (§5.2.3 spawn policy)
- Git-worktree workspace isolation for parallel executors (§7.4): snapshot baseline_commit, ephemeral worktree per task, three-way merge on success, per-scope merge lock
- Update STATUS.md, close remaining spec gaps
- Load testing with concurrent work items

**Spec refs:** §7.4, §5.2.3, §8 (migration), §9 (testing)

---

## ⚠️ Remaining Runtime Spec Gaps (Post-Refactor)

These are lower priority — addressed after the agent loop refactor lands.

| Priority | Gap | Spec Reference |
|----------|-----|----------------|
| Medium | Message trust/signing flow (Ed25519 inbound) | §5.1 step 2 |
| Medium | Stream startup sequence completion | §5.1 steps 2-7 |
| Medium | Rehydration completeness | §5.1.3 |
| Medium | Secure-input endpoint contract | §5.10.1 |
| Medium | ConnectionManager lifecycle | §5.10.1-§5.10.2 |
| Medium | Per-connection isolation model | §5.1 |
| Medium | Sandbox network/resource enforcement | §9.1 |
| Medium | Output gate escalation model | §5.1 step 8 |
| Medium | Proactivity/autonomy loops (heartbeat-driven) | §5.1.6 |
| Medium | Web search executor (provider-backed) | §9.2 |
| Medium | Memory portability | §4.2.3 |
| Low | GuardrailsAI gate provider | — |
| Low | Telegram/CLI channels | — |
| Low | Benchmarking / Pydantic Evals | §19-20 |
| Low | Operations hardening (error taxonomy, shutdown, rate limits) | §17 |

---

## ✅ Recently Closed Gaps

| Date | Gap | Fix |
|------|-----|-----|
| 2026-02-12 | Agent loop architecture spec | v3.2 complete, 4 review rounds, all issues closed |
| 2026-02-12 | INV-01 enforced at execution entry | `LiveWorkItemExecutor` requires approval_token |
| 2026-02-12 | INV-03 enforced for completion truth | External verification for `work_item.verify` |
| 2026-02-12 | Standing-approval spawn verification | `SilasGoalManager` verifies token before clearing needs_approval |
| 2026-02-12 | Planner route handoff | Stream calls `turn_context.planner` on route="planner" |
| 2026-02-12 | Turn pipeline step-0/step-1 gates | Two-lane input gate evaluation before routing |
| 2026-02-12 | Step-5 budget enforcement | Context budget enforced + eviction persisted as memory |

---

## Timeline

| Work Item | Estimate | Cumulative |
|-----------|----------|------------|
| WI-1: Queue Store + Types | 1-2 days | 1-2 days |
| WI-2: Tool Loops on All Agents | 1-2 days | 2-4 days |
| WI-3: Queue Communication + Execution | 1-2 days | 3-6 days |
| WI-4: Integration + Migration | 1 day | 4-7 days |

**Target: Autonomous runtime in ~1 week.**

---

## Build History

| PR | Description |
|----|-------------|
| #27-#36 | Core components, tests, lint, complexity |
| #37-#44 | Code quality, security, integration tests |
| #45-#54 | Protocols, logging, onboarding, secrets, approval, compliance |
| #55-#62 | RichCardChannel, memory, preferences, review models |

---

## Key Dependencies

- `pydantic-ai` — agent framework (existing)
- `pydantic-ai-backend` — file ops, sandbox, permissions (NEW — WI-2)
- SQLite — all stores including new queue store
- Docker — executor sandbox (WI-2, feature-flagged)
