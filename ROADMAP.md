# Silas Roadmap

Last updated: 2026-02-12

## Vision

Silas is a fully autonomous AI runtime — capable of indefinite autonomous operation, restricted ONLY by the cryptographic approval system. Standing approvals enable long-running work without human presence. The self-healing cascade (retry → consult-planner → re-plan → escalate) exhausts all automated recovery before ever involving the user.

---

## Architecture

Three independent pydantic-ai agent loops communicating via typed durable queues:

- **Proxy** (fast model) — routes, searches memory, talks to user
- **Planner** (deep model) — researches via executor micro-tasks, writes plans
- **Executor** (capable model) — executes with full tool loop in sandboxed environment

All agents use `pydantic-ai-backend` (ConsoleToolset + DockerSandbox) as the base tooling layer. Our runtime wraps with gates, approval tokens, and verification.

Full spec: `specs/agent-loop-architecture.md` (998 lines, v3.2)

---

## Current State (2026-02-12)

- **63 commits ahead of main**, 37 PRs merged (#27-#62)
- **~690 tests**, 0 lint errors
- Core runtime exists: agents (one-shot), gates, approval engine, execution pipeline, memory, context, sandbox, channels, onboarding, frontend
- **Agent loop spec complete** — reviewed 4 rounds with Codex, all issues closed
- Agents currently run as one-shot structured output — no tool loops, no queues

---

## Build Phases

### Phase 0: Queue Infrastructure ← NEXT
**Goal:** Durable message bus connecting all agents  
**Estimate:** 2 PRs, ~600 LOC

- [ ] `DurableQueueStore` (SQLite-backed, lease/ack/nack/dead-letter)
- [ ] `QueueMessage`, `StatusPayload`, `ErrorPayload`, `QueuePayload` types
- [ ] `RuntimeAuditEvent`, `QueueTelemetryEvent` schemas
- [ ] Idempotency contract (`has_processed` / `mark_processed`)
- [ ] Lease heartbeat contract
- [ ] Queue routing table (proxy_queue, planner_queue, executor_queue, runtime_queue)
- [ ] SQLite migration for queue tables
- [ ] Tests: lifecycle, crash recovery, heartbeat, idempotency

### Phase 1: Proxy Tool Loop
**Goal:** Proxy can search memory + web before routing  
**Estimate:** 1 PR, ~300 LOC

- [ ] Install `pydantic-ai-backend[console]`
- [ ] Wire `LocalBackend(READONLY)` + `ConsoleToolset` on ProxyAgent
- [ ] Add custom tools: `memory_search`, `web_search`, `tell_user`, `context_inspect`
- [ ] Change §5.1 step 7 to `agent.run()` with toolset
- [ ] Feature flag: `config.agent_loops.proxy_tools`
- [ ] Tests: tool loop produces RouteDecision, memory search works mid-loop

### Phase 2: Planner Research Flow
**Goal:** Planner delegates fact-finding to executor micro-tasks  
**Estimate:** 2 PRs, ~500 LOC

- [ ] Wire `LocalBackend(READONLY)` on PlannerAgent
- [ ] Add custom tools: `request_research`, `validate_plan`, `memory_search`
- [ ] Implement planner research state machine (§4.8): planning → awaiting_research → ready_to_finalize → expired
- [ ] Research request/result queue flow (non-blocking)
- [ ] Replan handling (§4.6.1): `replan_request` → revised plan
- [ ] Feature flag: `config.agent_loops.planner_research`
- [ ] Tests: research delegation, state machine transitions, timeout/cancel, replan

### Phase 3: Executor Tool Loop
**Goal:** Executor uses tools iteratively within runtime-controlled attempts  
**Estimate:** 2 PRs, ~600 LOC

- [ ] Install `pydantic-ai-backend[docker]`
- [ ] Wire `DockerSandbox` + `ConsoleToolset` for execution mode
- [ ] Wire `LocalBackend(READONLY)` for research mode with `RESEARCH_TOOL_ALLOWLIST` clamping
- [ ] Full wrapper chain: `SkillToolset → PreparedToolset → FilteredToolset → ApprovalRequiredToolset`
- [ ] Change §5.2.1 step 2c to `run_structured_agent(executor_agent, ...)`
- [ ] Consult-planner suspend/resume contract (§5.2.3)
- [ ] Budget accounting: executor → work-item budget, planner consult → plan budget
- [ ] Feature flag: `config.agent_loops.executor_tools`
- [ ] Tests: tool loop, wrapper chain enforcement, research mode allowlist, consult timeout

### Phase 4: Queue-Based Execution
**Goal:** Execution moves to background via queues; status events flow to proxy  
**Estimate:** 2-3 PRs, ~500 LOC

- [ ] Move execution dispatch from synchronous to queue-based
- [ ] Status event routing (§6.3): `route_to_surface()` with dual-emit
- [ ] Executor pool with concurrency caps (per-scope + global)
- [ ] Self-healing cascade: retry → consult → re-plan → escalate (Principle #8)
- [ ] Git-worktree workspace isolation for parallel executors (§7.4)
- [ ] Feature flag: `config.agent_loops.queue_execution`
- [ ] Tests: full flow, parallel execution, status routing, replan cascade

### Phase 5: Integration + Migration
**Goal:** Remove procedural fallback, all agents on tool loops  
**Estimate:** 1-2 PRs, ~300 LOC

- [ ] Parity test suite (queue-based matches procedural behavior)
- [ ] Remove procedural fallback paths
- [ ] Frontend adaptation for queue status events + Activity surface
- [ ] Standing approvals wiring for long-term autonomy
- [ ] Load testing

### Phase 6: Hardening (lower priority)
**Goal:** Production-ready  

- [ ] Proactivity (cron, heartbeats, background goals)
- [ ] Operations hardening (error taxonomy, graceful shutdown, rate limiting)
- [ ] Secure input flow completion
- [ ] Connection lifecycle completion
- [ ] Telegram/CLI channels
- [ ] GuardrailsAI gate provider
- [ ] Memory portability
- [ ] Benchmarking / Pydantic Evals

---

## Timeline Estimate

| Phase | Estimate | Cumulative |
|-------|----------|------------|
| 0: Queue Infrastructure | 1-2 days | 1-2 days |
| 1: Proxy Tool Loop | 0.5-1 day | 2-3 days |
| 2: Planner Research | 1-2 days | 3-5 days |
| 3: Executor Tool Loop | 1-2 days | 4-7 days |
| 4: Queue Execution | 1-2 days | 5-9 days |
| 5: Integration | 1-2 days | 6-11 days |
| 6: Hardening | ongoing | — |

**Target: Autonomous runtime (Phases 0-5) in ~2 weeks.**

---

## Key Dependencies

- `pydantic-ai` — agent framework (already in use)
- `pydantic-ai-backend` — file ops, sandbox, permissions (NEW)
- SQLite — queue store, existing stores
- Docker — executor sandbox (Phase 3+)

## Key Specs

- `specs/agent-loop-architecture.md` — multi-agent queue architecture (998 lines, v3.2)
- `specs.md` — core runtime spec (main behavioral contract)
- `specs/security-model.md` — security invariants
- `specs/protocols.md` — protocol interfaces
- `specs/models.md` — data models
