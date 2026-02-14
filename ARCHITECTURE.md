# How Silas Works

A concise overview. For full contracts, see `specs/`.

---

## The Big Picture

Silas is three AI agents connected by a message bus, wrapped in a security layer.

```
User
  ↓
┌─────────┐    ┌──────────┐    ┌──────────┐
│  Proxy  │───→│ Planner  │───→│ Executor │
│ (fast)  │←───│ (deep)   │←───│ (capable)│
└─────────┘    └──────────┘    └──────────┘
     ↑              ↑               ↑
     └──────── Message Bus ─────────┘
            (SQLite queues)
```

**Proxy** — The front door. Talks to the user, searches memory, decides where to route. Fast model, sub-second responses. Never writes files or executes code.

**Planner** — The strategist. Receives complex requests from proxy, researches (by delegating to executor), writes structured plans. Can request multiple research tasks in parallel.

**Executor** — The worker. Runs code, edits files, calls APIs. Operates in a sandbox. Has two modes:
- **Research mode:** Read-only. Used by planner for fact-finding.
- **Execution mode:** Full power. Writes, executes, builds. Requires an approval token.

---

## How a Message Flows

### Simple question (proxy handles it)
```
User: "What's the status of project X?"
  → proxy_queue (user_message)
  → Proxy searches memory, finds answer
  → proxy_queue (agent_response)
  → User sees response
```

### Complex task (all three agents)
```
User: "Refactor the auth module"
  → proxy_queue (user_message)
  → Proxy routes to planner
  → planner_queue (plan_request)
  → Planner researches (sends research_requests to executor)
  → executor_queue (research_request) → Executor reads code → planner_queue (research_result)
  → Planner writes plan, sends to proxy for approval
  → proxy_queue (plan_result)
  → User approves plan
  → executor_queue (execution_request)
  → Executor works: read → write → execute → test → iterate
  → proxy_queue (execution_status: done)
  → User sees result
```

### Autonomous goal (no user involved)
```
Scheduler fires standing-approved goal
  → planner_queue (plan_request)
  → Planner decomposes → Executor executes
  → proxy_queue (execution_status → Activity surface)
  → User sees result next time they check in
```

---

## The Security Layer

Nothing executes without authorization. Three mechanisms:

### 1. Approval Tokens (Ed25519 signed)
Every work item needs a cryptographic token before execution. Tokens are:
- **Consuming:** Used once (normal tasks)
- **Non-consuming:** Checked but not spent (standing approvals for goals)

### 2. Gates
Checks that run before and after every turn:
- **Input gates:** Block/flag dangerous requests before they reach agents
- **Output gates:** Block/flag dangerous responses before they reach users
- Providers: predicate rules, scripts, LLM-based evaluation

### 3. Access Controller
Tracks what the user/agent is allowed to do based on gate results. State evolves over the conversation.

### The Wrapper Chain
Every tool an agent can use goes through this pipeline:
```
Raw Tool (read_file, execute, etc.)
  → SkillToolset (adds skill-specific tools)
  → PreparedToolset (binds work item context)
  → FilteredToolset (blocks disallowed tools — e.g., research mode blocks writes)
  → ApprovalRequiredToolset (pauses for approval if gate requires it)
```
This is enforced at the code level, not by prompts. An executor in research mode literally cannot call `write_file` — the tool doesn't exist in its toolset.

---

## Self-Healing (when things go wrong)

If executor fails, Silas doesn't just give up:

```
Attempt fails
  → Retry (up to N attempts)
  → Consult planner ("I'm stuck, here's what happened")
  → Planner sends guidance → Executor retries with new approach
  → Re-plan (planner creates entirely new plan)
  → Escalate to user (only after ALL automated recovery exhausted)
```

This is Design Principle #8: the system is built for full autonomy. User involvement is the last resort, not the default.

---

## Topics System

Topics are activated context containers — the AGENTS.md pattern applied to everything Silas works on. A Topic holds instructions, research, plans, approvals, and state in a single markdown file with YAML frontmatter.

```
topics/
  fix-ci-pipeline.md      ← project-scoped, executor-owned
  monitor-emails.md       ← infinite-scoped, proxy-owned
  refactor-auth.md        ← session-scoped, planner-owned
```

### Anatomy of a Topic
```yaml
---
id: fix-ci-pipeline
name: Fix CI Pipeline
scope: project          # session | project | infinite
agent: executor         # proxy | planner | executor
status: active          # active | paused | completed | archived
triggers:               # Hard triggers — event-driven activation
  - source: github
    event: check_run.completed
    filter:
      conclusion: failure
soft_triggers:           # Soft triggers — keyword/entity matching
  - keywords: [CI, pipeline, tests failing]
approvals:               # Tool-level approval requirements
  - tool: write_file
    constraints: { path_prefix: "silas/" }
---

## Instructions
(markdown body — procedural memory, plans, context)
```

### Activation Model
Topics are *activated*, not *executed*. They're memory areas that light up:
- **Hard triggers** — webhook events matched by source/event/filter (e.g., GitHub CI failure → activate fix-ci Topic)
- **Soft triggers** — keyword/entity matching against conversation context (associative memory)
- **Agent-specific activation**: Proxy activates by user/trigger/soft-match; Planner by trigger/self; Executor only by trigger

### Key Files
| Path | What |
|------|------|
| `silas/topics/model.py` | Topic, TriggerSpec, SoftTrigger, ApprovalSpec models |
| `silas/topics/parser.py` | Markdown ↔ Topic serialization |
| `silas/topics/registry.py` | Filesystem-backed CRUD + trigger queries |
| `silas/topics/matcher.py` | Hard trigger (exact match) + soft trigger (keyword scoring) |

### What's Next
- **Phase 2:** Wire hard triggers to webhook subscription system (event → find matching Topic → activate)
- **Phase 3:** Soft trigger matching in proxy conversation loop
- **Phase 4:** Plan ↔ WorkItem integration (checkbox steps → WorkItems)

---

## Data Storage

Everything is SQLite (single-process runtime, no external dependencies):

| Store | What |
|-------|------|
| Queue Store | Messages between agents (durable, lease-based) |
| Work Item Store | Tasks, status, attempts, results |
| Memory Store | Long-term agent memory |
| Chronicle Store | Conversation history |
| Nonce Store | Prevents approval token replay |
| Audit Log | Everything that happened (security trail) |

---

## The Queue Contract

Messages are typed (`QueueMessage`) with:
- `message_kind` — what it is (plan_request, execution_status, etc.)
- `sender` — who sent it (proxy, planner, executor, runtime)
- `trace_id` — follows the request across all hops
- `payload` — typed data (StatusPayload, ErrorPayload, etc.)

Delivery guarantees:
- **At-least-once:** If consumer crashes, lease expires, message re-delivered
- **Idempotency:** Consumers check `has_processed()` before side effects
- **Dead letter:** After max attempts, message goes to dead_letter table for debugging

---

## Config & Startup

- Config: `config/silas.yaml`
- Agents use pydantic-ai with registered tools
- Feature flags control which capabilities are active
- Owner registers via onboarding flow (CLI or web)
- Ed25519 key pair generated at first run

---

## Key Files

| Path | What |
|------|------|
| `silas/agents/proxy.py` | Proxy agent |
| `silas/agents/planner.py` | Planner agent |
| `silas/agents/executor_agent.py` | Executor agent |
| `silas/queue/store.py` | Durable queue (SQLite) |
| `silas/queue/types.py` | Message types |
| `silas/queue/router.py` | Message routing table |
| `silas/work/executor.py` | Work item execution lifecycle |
| `silas/approval/` | Approval engine |
| `silas/gates/` | Gate system |
| `silas/memory/` | Memory stores |
| `silas/topics/` | Topics system (model, parser, registry, matcher) |
| `silas/core/stream/` | Turn processing pipeline (split into modules) |
| `silas/core/turn_context.py` | Per-turn state |
| `specs/` | Full behavioral contracts |
