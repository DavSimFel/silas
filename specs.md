# Silas — Specification v4.0

**The AI That Does Things. Securely.**

This spec is split into two parts:
- **Part 1: The System** — What Silas is. Built, working, shippable. Each section is verifiable.
- **Part 2: Ideas & Future Direction** — Planned, not yet shipped. Explicitly aspirational.

---

# Part 1: The System

Everything in Part 1 describes the current v0.1.0 contract. Someone should be able to read any section and check "does this actually work?"

---

## 0. Security Invariants

These are non-negotiable. Every section in Part 1 preserves them.

| ID | Invariant | Enforcement Point |
|---|---|---|
| `INV-01` | Executable actions require cryptographically verified approval tokens (Ed25519). | Approval engine + execution entry gate (§5.2.1, step 0) |
| `INV-02` | Approval tokens are content-bound and replay-protected (plan-hash binding + nonce). | Approval verifier (§5.11) |
| `INV-03` | Completion truth is external deterministic verification, not agent self-report. | Verification runner (§5.3) |
| `INV-04` | Policy gates run deterministically and can block execution; quality gates are advisory only. | Gate runner (§5.4, §5.5) |
| `INV-05` | Execution isolation and taint propagation remain outside agent control. | Sandbox + taint tracker (§9, §5.12) |
| `INV-06` | Skill activation requires deterministic validation, approval, and hash-bound versioning. | Skill install/import flow (§10.4) |

---

## 1. System Overview

Silas is a single-user AI agent with persistent memory, tool execution, and safety gates. It runs locally, connects to LLM providers via API, and communicates through a WebSocket-based web UI.

### 1.1 Core Loop

```
User Message → Channel → Stream → Context Assembly → Agent → Tool Execution → Gates → Response → Channel → User
```

Every interaction follows this loop. There are no other paths.

### 1.2 Deployment

- **Runtime:** Python 3.13+, FastAPI, single process
- **Storage:** SQLite (memory, audit, work items)
- **LLM:** OpenRouter (or direct provider APIs) — configurable per agent role
- **Interface:** WebSocket (`localhost:8420` by default)
- **Config:** YAML file (`config/silas.yaml`)

### 1.3 Single User

v0.1.0 supports exactly one user (the owner). The `owner_id` in config identifies them.

✅ Verify: `silas start` boots and accepts a WebSocket connection on `localhost:8420`.

---

## 2. Channels

A channel is the transport layer between the user and the Stream.

### 2.1 Web Channel (v0.1.0 scope)

- FastAPI application serving a WebSocket endpoint
- Static PWA frontend (HTML/CSS/JS, no framework)
- Sends and receives JSON messages over WebSocket
- Supports streaming responses (token-by-token via WebSocket frames)

### 2.2 Channel Message

Every inbound message becomes a `ChannelMessage`:

| Field | Type | Description |
|-------|------|-------------|
| `channel` | str | Channel identifier (`"web"`) |
| `sender_id` | str | User identifier |
| `text` | str | Message content |
| `timestamp` | datetime | UTC timestamp |
| `attachments` | list[str] | File references (v0.1.0: empty) |
| `reply_to` | str \| None | Message being replied to |
| `is_authenticated` | bool | Whether channel verified sender identity |

### 2.3 What Channels Do NOT Do

- No authorization (that's Gates)
- No context assembly (that's the Context Manager)
- No routing decisions (that's the Proxy Agent)

✅ Verify: Send a JSON message via WebSocket, receive a streamed response.

---

## 3. The Stream

The Stream is the persistent orchestration session. It processes turns sequentially per connection.

### 3.1 Turn Processing

A turn is one user message → one agent response cycle. Within a turn:

1. **Input gates** run on the raw message (can block, transform, or pass)
2. **Inbound message is signed** with taint level and turn number
3. **Message is added to chronicle** (conversation history)
4. **Memories are auto-retrieved** (semantic search triggered by message content)
5. **Context budget is enforced** (evict lowest-priority items if over budget)
6. **Agent toolsets are prepared** (tools available for this turn)
7. **Proxy agent runs** → produces a `RouteDecision`
8. Based on route:
   - `direct` → response is returned immediately
   - `planner` → Planner produces a plan, plan is executed
9. **Output gates** run on the response (can block or modify)
10. **Response is sent** to the channel

### 3.2 Session Continuity (Rehydration)

The Stream persists across restarts:
- Chronicle items are stored in SQLite
- On startup, the Stream loads recent chronicle to restore conversation context
- Turn number continues from where it left off

Rehydration sequence:
1. Load system zone (constitution, tool descriptions, config) as pinned `ContextItem`s
2. Load recent chronicle entries (configurable `max_chronicle_entries`)
3. Search memory for user profile and inject as pinned memory
4. Restore active context subscriptions from work item store
5. Add system message: `"[SYSTEM] Session rehydrated after restart."`
6. Load in-progress work items and resume them

### 3.3 Connection Isolation

Each WebSocket connection gets its own `TurnProcessor` with isolated turn context, chronicle, and memory scope. Turns within a connection are serialized via `asyncio.Lock`.

✅ Verify: Stop and restart Silas. Previous conversation context is restored; turn counter continues.

---

## 4. Agents

Three agent roles, each a PydanticAI agent with a specific model tier.

### 4.1 Proxy Agent

**Purpose:** Route every message and handle simple interactions.

- **Model:** `models.proxy` (fast/cheap)
- **Output:** `RouteDecision` — `route` (direct/planner), `reason`, `response`, `context_profile`
- **Tools:** Memory search, context inspection, web search (read-only)

Handles ~80% of messages directly (greetings, simple questions, factual lookups). Only complex tasks get routed to Planner.

### 4.2 Planner Agent

**Purpose:** Decompose complex requests into executable plans.

- **Model:** `models.planner` (deep reasoning)
- **Output:** Markdown plan with YAML frontmatter (id, type, title, skills, budget, verify, on_stuck)
- **Tools:** Memory search, context inspection, web search

Plans are parsed by `MarkdownPlanParser` into `WorkItem` objects. Plan format:

```markdown
---
id: task-{uuid}
type: task
title: {descriptive title}
skills: [coding]
budget: { max_tokens: 200000, max_cost_usd: 2.00 }
verify:
  - name: tests_pass
    run: "pytest tests/ -x"
    expect: { exit_code: 0 }
on_stuck: consult_planner
---

# Context
{Background}

# What to do
{Prose instructions for executor}
```

### 4.3 Executor Agent

**Purpose:** Execute individual plan steps.

- **Model:** `models.executor` (cost-optimized)
- **Output:** `ExecutorAgentOutput` (action summary, artifact refs, next-step suggestions)
- **Tools:** `shell_exec`, `python_exec`, `web_search`, skill scripts
- **Stateless:** Sees only the current step's instructions and scoped tools

### 4.4 Model Configuration

```yaml
models:
  proxy: openrouter:anthropic/claude-haiku-4-5
  planner: openrouter:anthropic/claude-sonnet-4-5
  executor: openrouter:anthropic/claude-haiku-4-5
  scorer: openrouter:anthropic/claude-haiku-4-5
```

### 4.5 Structured Output Reliability

All agent calls use `run_structured_agent`: one retry on schema validation failure with error appended, then deterministic fallback per agent type. No infinite retry loops.

✅ Verify: Send "refactor function X" → Proxy routes to Planner → Planner outputs a valid plan → Executor runs it.

---

## 5. Context Management

The context manager controls what the agent sees in each turn.

### 5.1 Context Zones

| Zone | Purpose | Content | Eviction |
|------|---------|---------|----------|
| **system** | Fixed instructions | System prompt, tool descriptions | Never (pinned) |
| **chronicle** | Conversation history | Recent messages | Oldest first |
| **memory** | Retrieved knowledge | Relevant memories | Lowest relevance first |
| **workspace** | Active resources | Plans, subscriptions | Completed first |

### 5.2 Context Profiles

Profiles allocate budget percentages across zones. The Proxy selects the profile per turn:

| Profile | Chronicle | Memory | Workspace | Use Case |
|---------|-----------|--------|-----------|----------|
| conversation | 45% | 20% | 15% | Chat, Q&A |
| coding | 20% | 20% | 40% | Development |
| research | 20% | 40% | 20% | Deep research |
| support | 40% | 25% | 15% | Troubleshooting |
| planning | 15% | 25% | 35% | Multi-step |

Remaining budget (~20%) is unallocated headroom for the heuristic token counter.

### 5.3 Two-Tier Eviction

When context exceeds budget:

**Tier 1 — Heuristic (no model call):**
1. Observation masking: old tool results → short placeholders
2. Drop trivial messages (< 20 tokens, "ok"/"thanks" patterns)
3. Deactivate stale subscriptions (no reference in `subscription_ttl_turns`)
4. Zone-specific eviction (oldest chronicle, lowest relevance memory, completed workspace)

**Tier 2 — Scorer model (only if Tier 1 insufficient):**
- Lightweight LLM scores context blocks by group relevance
- Outputs `ScorerOutput` with `keep_groups`/`evict_groups`
- Circuit breaker: open after 3 failures, 5-min cooldown → fall back to aggressive heuristic
- Evicted items are persisted to memory before removal (nothing permanently lost)

### 5.4 Context Subscriptions

Subscriptions watch external resources and inject updates into workspace:

| Type | What It Watches | Example |
|------|-----------------|---------|
| `file` | File on disk | `config.yaml` |
| `file_lines` | Specific line range | Source code function |
| `url` | HTTP endpoint | API status page |

TTL in turns, auto-removed when expired. Total subscription tokens capped.

### 5.5 Token Counter

Heuristic: `int(len(text) / 3.5)` (characters ÷ 3.5). Error absorbed by 20% headroom. No external dependency.

✅ Verify: In a long conversation, context stays within budget; old messages are masked/evicted without crash.

---

## 6. Memory

Persistent storage for facts, experiences, and knowledge.

### 6.1 SQLite Store

SQLite-backed with FTS5 for keyword search, optional sqlite-vec for semantic search.

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique identifier |
| `content` | str | The memory text |
| `category` | str | Classification (fact, experience, preference) |
| `importance` | float | 0.0–1.0 |
| `taint` | TaintLevel | Trust level of source |
| `session_id` | str \| None | Scoped to session or global |
| `created_at` | datetime | When stored |

FTS5 tokenizer: `porter unicode61 tokenchars "_-"`. WAL mode, `PRAGMA synchronous=NORMAL`, `busy_timeout=5000`.

### 6.2 Memory Operations

- **Store:** Agent stores via `memory_store` tool
- **Search:** Automatic semantic search on each user message; agent can also search explicitly via `memory_search`
- **Auto-ingest:** Raw user messages stored as low-importance memories
- **Taint:** Memories inherit taint from source. External-tainted content cannot become verified without explicit confirmation.

### 6.3 Memory in Context

Retrieved memories are injected into the `memory` zone, ranked by relevance, within the zone's budget.

### 6.4 Embedder (Optional)

Wraps `fastembed` (ONNX, `all-MiniLM-L6-v2`, 384 dims) for local embeddings when semantic search is enabled.

### 6.5 Migrations

Sequential, zero-padded (`001_*.sql`, `002_*.sql`). Idempotent (`IF NOT EXISTS`). Run on `silas start` before Stream startup. Checksum mismatch → startup fails.

✅ Verify: Store a memory via `memory_store`, then retrieve it later via `memory_search` with a related query.

---

## 7. Tools & Skills

### 7.1 Built-in Tools

| Tool | Agent | Description |
|------|-------|-------------|
| `memory_store` | Proxy, Executor | Store a memory |
| `memory_search` | Proxy, Executor | Search memories |
| `web_search` | Proxy, Executor | Search the web (loaded only when search credentials configured) |
| `web_fetch` | Proxy, Executor | Fetch a URL |
| `shell_exec` | Executor | Run a shell command (sandboxed) |
| `file_read` | Proxy, Executor | Read a file |
| `file_write` | Executor | Write a file |
| `tell_user` | Executor | Send a message mid-execution |
| `context_subscribe` | Proxy | Subscribe to a file/URL for context |
| `context_unsubscribe` | Proxy | Remove a subscription |

### 7.2 Skills (Agent Skills Standard)

Skills are packaged tool bundles following the [Agent Skills standard](https://agentskills.io/):

```
coding/
├── SKILL.md              # YAML frontmatter + markdown instructions
├── scripts/              # Python scripts via SandboxManager
│   ├── run_tests.py
│   └── apply_patch.py
└── references/           # Additional docs
```

**SKILL.md frontmatter** — required: `name`, `description`. Optional: `license`, `requirements`, `activation` (auto/manual/background), `script_args`, `ui`, `metadata`.

Skills are loaded from configured `skills_dir` (default `./silas/skills`). Default shipped: `coding`, `skill-maker`.

### 7.3 Skill Scoping

Skills belong to work items, not globally injected:
- **Proxy** sees metadata only (`name` + `description`, ~50-100 tokens/skill)
- **Planner** sees full SKILL.md body
- **Executor** sees full instructions + validated script paths
- **Verification** sees nothing (external, skill-independent)

Metadata capped at `skill_metadata_budget_pct` (default 2%) of context.

### 7.4 Skill Installation

1. Validation: frontmatter, description quality, script syntax (AST parse), forbidden patterns
2. Approval token with scope `skill_install`
3. Sandbox dry-run
4. Metadata indexed for discovery

### 7.5 Toolset Wrapper Chain

Tools are composed as: `SkillToolset → PreparedToolset → FilteredToolset → ApprovalRequiredToolset`.

✅ Verify: Executor runs `shell_exec` to execute a test suite during plan execution.

---

## 8. Gates

Gates are deterministic checks on every turn. They enforce safety, access, and content policies. **Gates are NOT LLM-based** (except optional LLM quality gate).

### 8.1 Gate Types

| Type | When | What |
|------|------|------|
| **Input** | Before agent processing | Block, transform, or pass messages |
| **Output** | After agent response | Block or modify responses |
| **Execution** | Before tool execution | Approve or deny tool calls |
| **Access** | On resource access | Check permissions |

### 8.2 Two-Lane Evaluation

- **Policy lane** (blocking): deterministic providers. Returns `pass`, `block`, or `require_approval`. On block → escalation action.
- **Quality lane** (non-blocking): advisory scores + flags. Cannot block. LLM gate results logged only.

### 8.3 Gate Providers

| Provider | Type | Description |
|----------|------|-------------|
| **GuardrailsAI** | Policy | Wraps `guardrails-ai` validators (toxicity, PII, jailbreak) |
| **Predicate** | Policy | Numeric range, string match, regex, file validation checks |
| **Script** | Policy | Custom shell scripts (context values passed as env vars, never interpolated) |
| **LLM** | Quality | Subjective checks via quality-tier model. Can be promoted to policy via `promote_to_policy: true` |

### 8.4 Mutation Allowlist

Gate runner enforces `ALLOWED_MUTATIONS = {"response", "message", "tool_args"}`. Keys outside this set are stripped and logged to audit.

### 8.5 Gate Block Handling

On block: lookup `on_block` in escalation dictionary (goal-level → built-in defaults). Escalation actions: `respond`, `report`, `escalate_human`, `transfer_to_queue`, `suppress_and_rephrase`, `retry`, `spawn_task`.

✅ Verify: Configure an input gate that blocks messages containing "DROP TABLE" → message is blocked with reason shown.

---

## 9. Approval & Security

### 9.1 Approval Flow

When an action requires approval:
1. Action queued with an `ApprovalToken`
2. User notified via channel
3. User approves or denies
4. Approved → execute; denied → logged, skipped

### 9.2 Standing Approvals

For recurring actions:
- Scoped by action type and parameters
- Time-limited (TTL), max executions
- Revocable
- Used by goal-spawned fix tasks (§5.2.3-style execution)

### 9.3 Ed25519 Signing

- Approval tokens signed by the harness (never the agent)
- Audit log entries signed for tamper evidence
- Private key stored in OS keyring, **never** in LLM context, logs, config, or env
- Keypair generated via `cryptography` library

### 9.4 Approval Engine

**`issue_token`:** SHA-256 hash of work item → generate nonce → sign with Ed25519 → return signed token.

**`verify`** (consuming): Check signature, plan hash binding, expiry, nonce replay protection. Increments `executions_used`.

**`check`** (non-consuming): Validates token is still valid without consuming a nonce. Used at execution entry.

### 9.5 Nonce Store

Replay protection: `is_used(namespace, nonce)` and `record(namespace, nonce)`. TTL-based pruning.

✅ Verify: Propose a plan → approve → token is minted and verified → execution proceeds. Replay the same token → rejected.

---

## 10. Taint Tracking

Every context item carries a `TaintLevel`:

| Level | Meaning | Source |
|-------|---------|--------|
| `owner` | Trusted | Direct messages with valid Ed25519 signature |
| `auth` | Authenticated external | Verified channel identity |
| `external` | Untrusted | Web search, unverified input |

Propagation rules:
- Tool reading external data → response inherits `external` taint
- `web_search` outputs → always `external`
- Memory items inherit source taint
- External-tainted data cannot be stored as verified/constitutional
- Tracked per-turn via `TaintTracker` using contextvars

✅ Verify: A web search result carries `external` taint; a response derived from it is also tagged `external`.

---

## 11. Audit Log

Every significant action logged to SQLite with Ed25519 signatures.

| Field | Type | Description |
|-------|------|-------------|
| `event` | str | Event type (`turn_processed`, `tool_executed`, `gate_blocked`, etc.) |
| `turn_number` | int | Which turn |
| `timestamp` | datetime | When |
| `data` | dict | Event-specific payload |
| `signature` | bytes | Ed25519 signature |

**What gets audited:** Turn start/end, gate evaluations, tool executions, approval requests/responses, memory operations, plan creation/execution, errors.

✅ Verify: After processing turns, query the audit log → entries exist with valid signatures.

---

## 12. Work Item Execution

### 12.1 Task Execution

0. **Approval gate (mandatory):** Validate `approval_token` before any execution. No token → blocked.
1. Budget tracking from work item budget.
2. Retry loop: run executor → collect tool ledger → mid-execution gates → external verification → retry on failure → consult planner if stuck → report if budget exhausted.
3. Verification operates on sandbox artifacts, not in-memory objects. Agent has zero influence.

### 12.2 Project Execution

Topologically sort child tasks by dependencies → execute each → project-level verification.

### 12.3 Goal Cycle Execution

Recurring: run verification checks → if failing and `on_failure == "spawn_task"` → create fix task with standing approval → execute.

### 12.4 Verification Runner

Runs checks OUTSIDE the agent's sandbox:
- Dedicated sandbox instance (no shared state with execution)
- Minimal environment (`PATH` + `HOME` only, no secrets)
- Network disabled by default
- Predicates: `exit_code`, `equals`, `contains`, `regex`, `output_lt`/`output_gt`, `file_exists`, `not_empty`

✅ Verify: Execute a plan with `verify: [{run: "pytest", expect: {exit_code: 0}}]` → verification runs independently.

---

## 13. Execution Layer

### 13.1 Sandbox Backends

All execution through `SandboxManager` protocol. Commands as argument lists (not shell strings).

**Subprocess backend (default):** `asyncio.create_subprocess_exec`, dedicated working directory, minimal env, timeout enforcement, network deny.

**Docker backend (optional):** Same `SandboxManager` interface, containerized isolation. Enabled via `sandbox.backend: "docker"`.

### 13.2 Executor Registry

Three core executors:
- `shell_exec` — runs shell commands via sandbox
- `python_exec` — runs Python scripts via sandbox
- `web_search` — provider-backed web retrieval (loaded only when credentials configured)

Skill scripts execute via `python_exec` with paths resolved from work item's skills list.

✅ Verify: `shell_exec("echo hello")` returns `"hello"` via subprocess sandbox.

---

## 14. Queue Path (Agent Loop v3)

Three agent loops communicate via typed, durable SQLite queues with lease semantics.

### 14.1 Queue Infrastructure

`QueueMessage` with `message_id` (idempotency key), `trace_id`, `message_kind`, `scope_id`, `taint`. Durable store: `enqueue`, `lease`, `heartbeat`, `ack`, `nack`, `dead_letter`. Crash recovery: re-queue leased messages on startup.

### 14.2 Routing

| Source | Destination | Kind |
|--------|------------|------|
| User → proxy_queue | `user_message` |
| Proxy → planner_queue | `plan_request` |
| Planner → proxy_queue | `plan_result` |
| Planner → executor_queue | `research_request` |
| Executor → planner_queue | `research_result` |
| Runtime → planner_queue | `consult_planner` / `replan_request` |

### 14.3 Research Mode

Executor in read-only mode for planner fact-finding. Tools clamped to `RESEARCH_TOOL_ALLOWLIST` at runtime. No approval token required (preserves INV-01 — no execution without approval).

### 14.4 Consult-Planner Contract

On stuck: runtime suspends → enqueues `consult_planner` → waits for `planner_guidance` (90s timeout) → resumes. If all paths exhausted → automatic re-plan → if that fails → escalate to user.

✅ Verify: Proxy enqueues a plan_request → Planner dequeues, processes, returns plan_result → Proxy dequeues result.

---

## 15. Web Channel Implementation

### 15.1 Server

FastAPI serving HTTP + WebSocket. Security defaults:
- Binds `127.0.0.1` (loopback only)
- Remote access requires `--host 0.0.0.0` AND `auth_token` configured
- WebSocket auth: `Sec-WebSocket-Protocol` bearer token or first-message auth (5s timeout)

### 15.2 Message Protocol (JSON over WebSocket)

**Client → Server:** `auth`, `message`, `approval_response`, `gate_response`, `checkpoint`.

**Server → Client:** `message` (with streaming), `approval_request`, `gate_approval`, `checkpoint`.

### 15.3 PWA Frontend

Static files: `web/index.html`, `web/style.css`, `web/app.js`, `web/manifest.json`, `web/sw.js`. Mobile-first single-column stream. Installable. Card-first interactions for approvals.

### 15.4 Health Endpoint

`GET /health` → `{"status": "ok", "connections": int}`.

✅ Verify: Open `localhost:8420` in a browser → PWA loads → send a message → receive a streamed response.

---

## 16. Observability

### 16.1 Prometheus Metrics

Exported on the web channel's HTTP server:

| Metric | Type |
|--------|------|
| `silas_turns_total` | Counter |
| `silas_turn_duration_seconds` | Histogram |
| `silas_gate_evaluations_total` | Counter (by type/result) |
| `silas_tool_executions_total` | Counter (by tool name) |
| `silas_context_tokens_used` | Gauge |
| `silas_memory_operations_total` | Counter |
| `silas_subscriptions_active` | Gauge |
| `silas_agent_calls_total` | Counter (by role) |

### 16.2 OpenTelemetry Tracing

Spans: `stream.process_turn`, `agent.{proxy,planner,executor}.run`, `gate.evaluate`, `memory.{search,store}`, `subscription.materialize`. Export to OTLP (default `localhost:4317`).

### 16.3 Structured Logging

JSON-structured with correlation IDs (`turn_id`, `scope_id`).

✅ Verify: After a turn, `/metrics` shows incremented counters; OTel traces exist for the turn.

---

## 17. Configuration

### 17.1 Config File

`config/silas.yaml`, loaded via Pydantic Settings with environment variable overrides.

```yaml
silas:
  owner_id: "owner"
  data_dir: "./data"

  models:
    proxy: "openrouter:anthropic/claude-haiku-4-5"
    planner: "openrouter:anthropic/claude-sonnet-4-5"
    executor: "openrouter:anthropic/claude-haiku-4-5"
    scorer: "openrouter:anthropic/claude-haiku-4-5"

  context:
    total_tokens: 180000
    system_max: 8000
    skill_metadata_budget_pct: 0.02
    eviction_threshold_pct: 0.80
    observation_mask_after_turns: 5
    default_profile: "conversation"
    profiles:
      conversation: { chronicle_pct: 0.45, memory_pct: 0.20, workspace_pct: 0.15 }
      coding:       { chronicle_pct: 0.20, memory_pct: 0.20, workspace_pct: 0.40 }
      research:     { chronicle_pct: 0.20, memory_pct: 0.40, workspace_pct: 0.20 }
      support:      { chronicle_pct: 0.40, memory_pct: 0.25, workspace_pct: 0.15 }

  rehydration:
    max_chronicle_entries: 50

  memory:
    embedding_model: "all-MiniLM-L6-v2"
    embedding_dim: 384

  channels:
    web:
      enabled: true
      host: "127.0.0.1"
      port: 8420
      auth_token: null

  sandbox:
    backend: "subprocess"
    verify_dir: "./data/verify"

  gates:
    system:
      - name: input_guard
        on: every_user_message
        provider: guardrails_ai
        check: jailbreak
        on_block: polite_redirect

  skills_dir: "./silas/skills"
```

### 17.2 Startup Validation (fail-fast)

- `host == "0.0.0.0"` requires non-null `auth_token`
- `verify_dir` and `customer_context_dir` must be different paths
- Context profiles must satisfy `0.0 <= pct <= 1.0`, combined ≤ 0.80
- Search provider set → API key required; otherwise `web_search` disabled

✅ Verify: Start with `host: "0.0.0.0"` and no `auth_token` → startup fails with validation error.

---

## 18. Entry Point

### `silas init`

1. Generate Ed25519 keypair, store private key in OS keyring
2. Create SQLite database, run migrations
3. Create verification sandbox directory
4. Resolve guardrails validators from configured gates

### `silas start`

1. Load config from `config/silas.yaml`
2. Wire all components via dependency injection (`build_stream`)
3. Start the Stream

### Dependency Wiring Order

Key manager → Audit → Memory store → Chronicle store → Work item store → Nonce store → Context manager → Approval engine → Agents → Verification runner → Gate runner → Plan parser → Skill loader → Access controller factory → Executor registry → Channels → Work executor → Turn processor factory → Stream.

Work executor MUST be created AFTER channels. Stream receives a turn-processor factory, not direct dependencies.

✅ Verify: `silas init` then `silas start` → Silas boots without errors, ready for messages.

---

## 19. Access Controller

Manages tool access based on gate state. Fully deterministic — LLM cannot influence access levels.

- Tracks current access level, verified gate names, customer context
- Owner connection always bypasses goal-scoped access levels (full owner access)
- Level transitions: gate passes → check if higher level requirements met → transition and log
- Expired levels drop back to `"public"`

✅ Verify: In single-user mode, owner always has full tool access regardless of gate state.

---

## 20. Dependencies

### Core

| Package | Purpose |
|---|---|
| `pydantic-ai-slim[openrouter,logfire]` | Agent framework |
| `pydantic` / `pydantic-settings` | Data models, config |
| `pyyaml` | YAML config |
| `click` | CLI |
| `httpx` | HTTP client for web_search |
| `cryptography` | Ed25519 signing |
| `fastapi` / `uvicorn[standard]` | Web server |
| `guardrails-ai` | Gate validators |

### Optional

| Package | Purpose | Install |
|---|---|---|
| `fastembed` | Local embeddings | `silas[vector]` |
| `sqlite-vec` | Vector similarity | `silas[vector]` |
| `docker` | Docker sandbox backend | `silas[docker]` |
| `logfire` | OTel dashboard | `silas[logfire]` |

✅ Verify: `pip install silas` installs core deps; `pip install silas[vector]` adds embedding support.

---

## 21. Companion Files

| File | Content | Status |
|------|---------|--------|
| `specs/reference/models.md` | Data models (§3.1–3.12) | Normative reference |
| `specs/reference/protocols.md` | Protocols (§4.1–4.24) | Normative reference |
| `specs/reference/examples.md` | Example plans | Informative reference |
| `specs/reference/security-model.md` | Security model matrix | Normative reference |
| `specs/adrs.md` | Architecture decision records | Informative rationale |

---

## 22. What v0.1.0 Does NOT Include

See Part 2 for full details on these planned features:

| Feature | Status |
|---------|--------|
| Telegram/Discord/Slack channels | Not wired |
| Goals and proactive behavior | Partially built, not stable |
| ContextRegistry (unified model) | Built, not integrated |
| Personality engine | Built, not essential |
| Card-based UX (Review/Activity) | Not built |
| Risk ladder interactions | Not built |
| Secret management (keyring) | Not built |
| Docker sandbox | Not integrated |
| Multi-modal input | Not built |

---

## 23. Verification Criteria

v0.1.0 is shippable when all pass:

### Functional
- [ ] WebSocket message → streamed response
- [ ] Proxy routes direct vs planner correctly
- [ ] Planner produces parseable plan
- [ ] Executor runs plan steps with results
- [ ] Memory store + search works
- [ ] Context budget enforced (no overflow crash)
- [ ] Input/output gates run every turn
- [ ] Streaming works (tokens appear incrementally)
- [ ] Session rehydration works

### Safety
- [ ] Taint propagation: external tool output → response carries `external` taint
- [ ] Gate blocking: configured gate blocks → user sees reason
- [ ] Approval flow: action requiring approval → prompt → approve/deny works
- [ ] Audit log: every turn produces signed entries
- [ ] Agent cannot access signing key

### Observability
- [ ] Prometheus metrics at `/metrics`
- [ ] OTel traces for turns, agent calls, gates
- [ ] Structured logs with turn_id correlation

### Stability
- [ ] All tests pass (`pytest`)
- [ ] No lint errors (`ruff check`)
- [ ] No crashes on 50 sequential mixed turns
- [ ] Clean shutdown (SIGTERM → graceful stop)
- [ ] Memory stable over 100 turns

---
---

# Part 2: Ideas & Future Direction

Everything below is planned but **not yet shipped**. These are design specs for future work. They retain full detail for when implementation begins.

---

## F1. Channel Expansion

### F1.1 Telegram Channel

Requires `pip install silas[telegram]`. Uses `python-telegram-bot`:
- Long-polling for message reception
- Inline keyboards for approval/gate responses
- Configured via `SILAS_TELEGRAM_TOKEN` and `SILAS_TELEGRAM_OWNER_ID`
- Implements `ChannelAdapterCore`; rich-card interactions use text fallback

### F1.2 Discord/Slack/WhatsApp Channels

Not yet designed. Planned as additional channel adapters following the same `ChannelAdapterCore` protocol.

### F1.3 CLI Channel (Dev/Debug)

Simple stdin/stdout for development. Approvals via text prompts. Exists in code, not production-intended.

---

## F2. UX Vision

### F2.1 Persistent UI Surfaces

Three persistent surfaces for decision-making:

| Surface | Purpose | Content |
|---|---|---|
| **Stream** | Conversation | Chat, progress updates, observations |
| **Review** | Decision queue | One card at a time, up-next stack |
| **Activity** | Audit narrative | Timeline of actions, changes, approvals |

Review enforces single-card focus to prevent decision overload.

### F2.2 Card Contract

All interactive cards follow standardized anatomy:

| Field | Required | Description |
|---|---|---|
| `intent` | yes | One-line description |
| `risk_level` | yes | `low`/`medium`/`high`/`irreversible` |
| `rationale` | yes | 1-2 sentences why |
| `consequence_label` | yes | Concrete outcome per CTA |

CTA ordering: recommended first → alternatives → destructive last. Max body height: 300px on 375px viewport.

### F2.3 Risk Ladder

| Risk | Interaction | Examples |
|------|------------|---------|
| `low` | Single tap | Acknowledge, archive non-critical |
| `medium` | Tap + confirm | Send draft, install known skill |
| `high` | Tap + slide confirm | Modify permissions, run migration |
| `irreversible` | Tap + slide + biometric | Drop data, revoke connection |

### F2.4 UX Quality Metrics

| Metric | Target |
|--------|--------|
| Decision time (median) | 2-5s low, 5-15s medium |
| Taps per batch | ≤3 |
| Decline rate | 5-15% |
| Undo rate | <10% |
| Correction rate | <10% mature |

### F2.5 Undo/Recover Pattern

Time-boxed reverse action log (5 min window). Single-tap undo on post-execution card. After window: new plan + approval.

### F2.6 Approval Fatigue Mitigation

Standing approvals, batch review with anomaly highlighting, approval cadence tracking, queue density cues. No hard throttling.

---

## F3. UX Principles (Hard Constraints for Future UX)

1. One-screen interactions
2. Tap-first interaction
3. No open-ended mandatory prompts — tappable options always available
4. Connection setup is conversational
5. Domain tagging over hard silos
6. Progressive disclosure
7. Invisible-by-default auditability
8. Decision chips over prose
9. Default-and-offer below confirmation threshold
10. No agent-management burden

**Secret isolation rule:** Credentials MUST NEVER enter the agent pipeline. Secrets never traverse WebSocket, never in audit/chronicle/memory/context. Agent references secrets by opaque `ref_id` only. Ingestion: user input → channel secure form → HTTPS `POST /secrets/{ref_id}` → OS keyring.

---

## F4. Context Evolution

### F4.1 ContextRegistry (Unified Model)

Built but not integrated. Replaces four-zone model with unified `ContextItem` model. Bridge code missing.

### F4.2 Topics as Activation Layer

Partially built. Topics serve as context containers and skill activation triggers. Depends on ContextRegistry integration.

### F4.3 Interaction Register + Mode

Every turn carries `interaction_register` (exploration/execution/review/status) and `interaction_mode` (default_and_offer/act_and_report/confirm_only_when_required). Built but not user-facing.

Deterministic resolution via `resolve_interaction_mode()` with precedence: risk_requires_confirmation > planner_override > work_item_mode > proxy_mode > initiative default.

---

## F5. Personality Engine

Built but not essential for v0.1.0. System prompt is sufficient.

### F5.1 Architecture

Two runtime hooks:
1. **Pre-agent:** Renders directives (~200-400 tokens) injected into system zone
2. **Post-turn:** Applies mood events and decay

Precedence: constitution > safety/approval > task constraints > personality.

### F5.2 Axis Profiles

Axes: warmth, assertiveness, verbosity, formality, humor, initiative, certainty. Each 0.0–1.0.

`effective = clamp(baseline + context_delta + mood_delta + user_override, 0.0, 1.0)`

### F5.3 Context Modifiers

| Context | Key Changes |
|---------|-------------|
| code_review | assertiveness +0.20, humor -0.20 |
| casual_chat | warmth +0.20, humor +0.30, formality -0.30 |
| crisis | verbosity -0.30, humor -0.50, initiative +0.30 |
| group_chat | assertiveness -0.10, initiative -0.20 |
| deep_research | verbosity +0.30, certainty -0.20 |

### F5.4 Mood Model

Dimensions: energy, patience, curiosity, frustration. Event-driven + time-decay toward neutral. Persisted in SQLite (`persona_state`, `persona_events` tables).

### F5.5 Presets

Named templates: `default`, `work`, `review`, `chill`, custom names. Map to axis+voice defaults.

### F5.6 Trust Boundary

Trusted sources (owner) may drift baseline. Untrusted sources: transient mood only, rate-limited.

### F5.7 Failure Behavior

Fail open to neutral style. Personality failure MUST NOT block turn processing.

---

## F6. Goals & Proactive Behavior

### F6.1 Goal Packs

Domain-goal execution with connections, skills, batch review, standing approvals.

### F6.2 Proactive Suggestions

Heartbeat-triggered `suggestion_engine.generate_idle()`. Deterministic heuristics first, optional proxy-tier model for wording. Deduped by `cooldown_key`.

### F6.3 Autonomy Calibration Loop

Tracks correction metrics per action family. Proposes widening/tightening via `AutonomyThresholdProposal` cards.

Anti-ratchet: min samples, hysteresis, hard caps, single-tap rollback.

### F6.4 Confidence Bands

| Band | Behavior | Surface |
|------|----------|---------|
| high | Batch proposal | Batch review card |
| medium | Draft/review path | Draft review card |
| low | Escalate to planner/user | Attention card |
| novel | Request teach/decision | Teaching card |

### F6.5 Reviewed Batch Execution

Retrieve candidates → classify + confidence → chunk into `BatchProposal` → batch review → execute approved → present decision cards for ambiguous items.

---

## F7. Connection Lifecycle

### F7.1 Connections Are Skills

Connections are skill directories with lifecycle scripts:

```
m365-outlook/
├── SKILL.md              # auth_strategy, initial_permissions, available_permissions
├── scripts/
│   ├── discover.py       # Detect provider, return auth strategy
│   ├── setup.py          # Interactive auth flow (NDJSON protocol)
│   ├── refresh_token.py  # Token refresh
│   ├── health_check.py   # Returns HealthCheckResult
│   ├── recover.py        # Recovery on failure
│   └── probe.py          # Test capability
```

### F7.2 Setup Conversation Protocol

Interactive multi-step flow. Scripts communicate via NDJSON (one JSON per line). Three auth strategies: device_code, browser_redirect, secure_input.

### F7.3 Incremental Permission Model

Start with minimum permissions → escalate via `PermissionEscalationCard` → approve/just-this-once/deny.

### F7.4 Proactive Token Refresh

Health checks return expiry info → auto-refresh before expiry → surface reconnection card for expiring refresh tokens.

### F7.5 Connection Failure Recovery

Structured `ConnectionFailure` → `ConnectionFailureCard` with recovery options per failure type (enterprise_policy_block, consent_denied, mfa_required, token_revoked, rate_limited, service_unavailable).

---

## F8. Security Hardening

### F8.1 Secret Management via OS Keyring

`POST /secrets/{ref_id}` endpoint → OS keyring. Agent only references by `ref_id`. Currently API keys via config/env only.

### F8.2 Inbound Message Signing

Ed25519 signed inbound messages for `owner` taint classification. Nonce + timestamp freshness window for replay protection.

### F8.3 Batch Security Binding

Standing approval tokens with `spawn_policy_hash` for goal-spawned tasks. Cryptographic binding of spawned task content to approved policy.

---

## F9. Memory Evolution

### F9.1 Multi-Graph Retrieval

Planned strategies beyond FTS5 + semantic:
- Temporal search (time-window queries)
- Entity graph traversal
- Causal chain following

Schema has indexes for `entity_refs` and `causal_refs` (JSON arrays) ready for incremental expansion.

### F9.2 Memory Consolidation

Background process (default 30 min):
1. Find frequently-accessed working memories
2. Merge duplicates
3. Promote to verified (with owner confirmation)
4. Prune stale memories
5. Re-embed updated content

### F9.3 Behavioral Preference Inference

Ingest behavior signals → convert to preference memories → promote via review cards → consumed by Planner/Proxy as defaults.

### F9.4 Memory Portability

Export/import as JSONL bundles with versioning. Merge/replace modes. Preserves taint and trust level.

---

## F10. Execution Evolution

### F10.1 Docker Sandbox Backend

Drop-in replacement via `sandbox.backend: "docker"`. Stronger filesystem/process/network isolation. `base_image` configurable.

### F10.2 Multi-Modal Input

Audio, image, PDF processing. Text-only in v0.1.0.

### F10.3 Parallel Execution

Executor pool (max 8 per-scope, 16 global), conflict detection, artifact merge via `input_artifacts_from`, git-worktree isolation.

### F10.4 Skill Creation by Silas

Detect capability gap → plan skill creation step → `skill-maker` builds SKILL.md + scripts → `skill_install` approval → sandbox test → activation.

### F10.5 External Skill Import

Import from OpenAI/Claude skill packs. Adaptation pipeline: fetch → parse → normalize frontmatter → adapt scripts → transformation report → standard install flow.

---

## F11. Governance & Operations

### F11.1 MVP Milestones

**MVP-1: Task Execution Loop** — Ask Silas to do X → see plan → approve → watch execution → see results.

**MVP-2: Goal Packs + Batch Review** — Full domain-goal execution with connections, skills, batch review, standing approvals, proactive suggestions.

### F11.2 Governance Model

User governs approvals and exceptions; Silas executes within approved scope.

---

## F12. Design Language & Benchmarking

### F12.1 Design Language ("Quiet")

Full design language spec in `specs/design-language.md`. Apple HIG-influenced but for an agent interface. Covers typography, color, spacing, animation, card anatomy.

### F12.2 Benchmarking Framework

Full benchmarking spec in `specs/benchmarking.md`. Eviction feedback loop, context quality metrics, agent eval suites. Not yet implemented.

---

## F13. Capability Gap Analysis (Original v4 Framing)

The original spec opened with a capability gap analysis comparing Silas to common failure modes. This table is preserved as design rationale:

| Capability | Common Failure | Silas Design |
|---|---|---|
| Multi-channel | All channels trusted equally | Ed25519 signed messages, channel trust tagging |
| Skills system | Supply-chain attacks | Agent Skills standard, sandboxed, cryptographic approval |
| Persistent memory | Malicious injections stored as "facts" | Trust-leveled memory, taint tracking |
| Shell/file execution | Root access by default | Ephemeral executors, pluggable sandbox, deterministic policy |
| Proactive (cron) | Full-permission cron | Goals with scheduled verification, standing approvals |
| Self-improving | Self-built skills bypass security | Planner plans, executor builds, approval + hash-binding |
| Context management | Auto-compaction (lossy) | Harness-controlled sliding window, two-tier eviction |
| Multi-model | Single model | Role-tier mapping (proxy/planner/executor) |
| Local-first | Shodan-indexed instances | Loopback-only, auth required for remote |
| Guardrails | None | Unified gate system, deterministic enforcement |
