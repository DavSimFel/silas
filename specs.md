# Silas — Implementation-Ready Specification v4.0

**The AI That Does Things. Securely.**
**Single-developer, implementation-ready spec — February 2026**

---

## 0. Capability Gap Analysis

| Capability | Common Failure Mode | Silas Design |
|---|---|---|
| Multi-channel (WhatsApp, Telegram, Discord, Slack, etc.) | All channels trusted equally, no identity verification | Ed25519 signed messages, channel-layer trust tagging |
| Skills system (SKILL.md files) | 26% of community skills contain vulnerabilities, supply-chain attacks | Agent Skills standard (agentskills.io): SKILL.md + Python scripts, sandboxed execution, cryptographic approval for install, skills scoped to work items |
| Persistent memory (MEMORY.md) | Memory stores malicious prompt injections as "facts" | Trust-leveled memory (working/verified/constitutional), taint tracking on external data |
| Shell/file/browser execution | Root access by default, LLM makes security decisions | Ephemeral executors behind a pluggable sandbox interface (subprocess default, Docker optional), deterministic policy enforcement, never LLM-gated |
| Proactive (cron, heartbeats, background tasks) | Cron jobs run with full permissions | Goals with scheduled verification, standing approvals with scope+budget limits |
| Self-improving (builds own skills) | Self-built skills bypass all security | Planner plans skill; ephemeral executor (via `skill-maker`) builds SKILL.md + scripts following guidelines; `skill_install` approval, sandbox dry-run, hash-bound versioning |
| Context management | Context overflow → auto-compaction (lossy) | Harness-controlled sliding window with metadata-tagged blocks, automated two-tier eviction (heuristic + scorer model), context subscriptions for live resources, agent limited to memory retrieval queries |
| Multi-model support | Single model per agent config | Role-tier model mapping (`proxy`, `planner`, `executor`) loaded from config |
| Local-first, runs on your machine | Shodan-indexed instances, localhost trust exploit, mDNS leaks | Web-first (FastAPI + WebSocket), loopback-only by default, auth required for remote |
| Guardrails / safety | None — LLM output goes directly to user | Unified gate system with guardrails-ai validators, deterministic enforcement on every turn |
| Chatbot deployment | Not designed for deployment | WorkItem-based goals with access levels, gates as guardrails, escalation pipelines |

**What Silas guarantees in this architecture:**

- Cryptographic approval system (Ed25519 signatures, unforgeable by LLM)
- Harness-controlled context with two-tier eviction, context subscriptions, and metadata-tagged blocks (not auto-compaction, not agent-controlled)
- Permanent Stream session with rehydration (consciousness continuity)
- Trust boundary between thinking (persistent brain) and doing (ephemeral hands)
- Multi-graph memory retrieval (semantic + temporal + entity + causal)
- Hash-chained audit log (tamper-evident)
- **Unified Work Items** — goals, projects, and tasks share one structure that scales from one-shot to indefinite
- **Plans as prose briefings** with machine-checkable verification and retry loops
- **Gates** — unified primitive for guardrails, mid-execution checks, approval flows, and access control
- **External verification** — the agent cannot self-report success; deterministic checks run outside the agent
- **Agent Skills standard** — skills (including connections) are SKILL.md + Python scripts, scoped to work items, self-buildable by Silas via the Planner + `skill-maker`

## 0.5 User Experience Principles (Hard Constraints)

These constraints apply to all UX, channel, and orchestration decisions in v4:

1. **One-screen interactions:** Every interaction must fit on one phone screen. If not, split into sequential cards.
2. **Tap-first interaction:** Tap is the default gesture. Higher-friction confirmation (slide confirm, biometric/local strong auth where available) is reserved for high-risk or irreversible actions (see risk ladder, §0.5.2).
3. **No open-ended mandatory prompts:** Silas does not require free-text to continue. Every question has tappable options; free-text is optional.
4. **Connection setup is conversational:** Silas builds and maintains connections through guided flows, not static settings pages.
5. **Domain tagging over hard silos:** Personal/business contexts are modeled with explicit domain tags and policy boundaries; cross-domain operations require explicit policy.
6. **Progressive disclosure:** New users see constrained controls first; advanced controls appear as usage maturity increases.
7. **Invisible-by-default auditability:** Audit trails are always captured and queryable in plain language (for example: "What did you do while I slept?").
8. **Decision chips over prose:** Decision points must render concrete options as chips/buttons, never paragraphs only.
9. **Default-and-offer below confirmation threshold:** When multiple valid approaches exist and risk does not require confirmation, Silas picks the best default action and offers alternatives after acting.
10. **No agent-management burden:** Users never manage internal modes or workflow settings in normal operation; Silas infers operating mode from context.

**Security preservation rule:** UX convenience levels are user-verification indicators, not authorization substitutes. In this spec, approval strength is `tap` for all scopes. Any executable action still requires cryptographically verifiable approval under Section 3.6.

**Secret isolation rule:** Credentials and secrets MUST NEVER enter the agent pipeline. Specifically:
- Secrets MUST NOT traverse the WebSocket or any channel message protocol
- Secrets MUST NOT appear in audit logs, chronicle, memory, or any `ContextItem`
- Secrets MUST NOT be stored in SQLite (the credential value itself lives only in the OS keyring)
- The agent MUST only reference secrets by opaque `ref_id`, never by value
- Secret ingestion uses a dedicated out-of-band path: user input → channel-level secure form → HTTPS `POST /secrets/{ref_id}` → OS keyring (see §5.10.1)
- Channels that cannot provide a secure input surface (e.g., Telegram, Discord) MUST redirect secret input to the web UI's secure form via a link
- OAuth/device-code flows are preferred precisely because they avoid manual secret entry entirely

### 0.5.1 Persistent UI Surfaces

The UI is a decision cockpit, not a chatbot. Three persistent surfaces, each serving a distinct cognitive mode:

| Surface | Purpose | Primary content |
|---|---|---|
| **Stream** | Conversation and status | Chat messages, progress updates, Silas observations |
| **Review** | Active decision queue | One actionable card at a time, "up next" stack below. All pending approvals, batch reviews, draft reviews, decisions |
| **Activity** | Audit narrative | Human-readable timeline of what Silas did, what changed, what was approved/declined. First-class "what changed" surface, not a raw log |

The Review surface enforces **single active card focus**: one actionable card is presented at a time with full context. Remaining decisions are stacked as a numbered "up next" list showing intent + risk level only. This prevents decision overload and ensures each approval gets full attention.

### 0.5.2 Risk Ladder

Every actionable card carries a risk level that determines the required interaction pattern:

| Risk level | Interaction | Examples |
|---|---|---|
| `low` | Single tap | Acknowledge status, apply safe formatting fix, archive non-critical item |
| `medium` | Tap + confirm chip | Send draft, batch approve default-size items, install known skill |
| `high` | Tap + slide confirm | Modify connection permissions, run high-impact migration, self-update |
| `irreversible` | Tap + slide + biometric/strong local auth (if available) | Drop data, revoke connection, hard-delete external records |

Risk level is determined by the approval scope and action type, not by the agent. The harness assigns risk level deterministically from the action's scope and reversibility.

### 0.5.3 Card Contract

All interactive cards follow a standardized anatomy. Users never relearn controls.

**Required card fields:**

| Field | Required | Description |
|---|---|---|
| `intent` | yes | One-line description of what this action does |
| `risk_level` | yes | `low`, `medium`, `high`, or `irreversible` — rendered as visual indicator |
| `rationale` | yes | 1-2 sentences explaining why Silas proposes this action |
| `consequence_label` | yes | Concrete outcome on each CTA (e.g., "Archive 10", "Approve + execute scripts", "Skip for now") |
| `affected_items` | if applicable | Count and summary of items affected |
| `details` | no | Expandable section with full payload, verification checks, affected item list |

**CTA ordering (enforced):**
1. Recommended action first (visually emphasized)
2. Alternative safe actions in the middle
3. Destructive or decline action last (visually de-emphasized)

**Max content height:** Card body (excluding expandable details) must fit in 300px on a 375px-wide viewport. If content exceeds this, it must be split into sequential cards or moved to expandable details.

**Details expansion rule (enforced):**
- `low` and `medium` risk cards: `details` collapsed by default
- `high` and `irreversible` cards: `details` expanded by default

**Connection setup card types:**

Connection setup uses specialized card variants that follow the standard card anatomy but add fields specific to auth flows:

| Card type | Additional fields | Channel behavior |
|---|---|---|
| `SecureInputCard` | `ref_id`, `label`, `input_hint`, `guidance` (instructions + help_url) | Web: renders password field, POSTs to `/secrets/{ref_id}` (bypasses WebSocket). CLI: `getpass.getpass()`. Messaging: redirects to web UI secure form link. |
| `DeviceCodeCard` | `verification_url`, `user_code`, `expires_in`, `poll_interval` | Shows code + tappable link + countdown timer. CTAs: `[I've done it]` `[Having trouble]` `[Cancel]` |
| `BrowserRedirectCard` | `auth_url`, `listening_on` | Shows "Opening your browser..." + progress. CTAs: `[Having trouble]` `[Cancel]` |
| `PermissionEscalationCard` | `connection_name`, `current_permissions`, `requested_permissions`, `reason` | Risk level: `medium` (read→write same resource) or `high` (new resource type). CTAs: `[Approve]` `[Just this once]` `[Deny]` |
| `ConnectionFailureCard` | `failure_type`, `service`, `message`, `recovery_options` | Renders each recovery option as a tappable chip. Always includes `[Skip]`. |

All connection cards inherit the standard card fields (`intent`, `risk_level`, `rationale`, `consequence_label`). `SecureInputCard` is special: it has no `risk_level` or approval semantics because it collects a credential, not a decision. Its rendering MUST follow the secret isolation rule (§0.5).

### 0.5.4 Approval Fatigue Mitigation

Repeated approvals cause habituation — users stop evaluating risk and tap reflexively. Countermeasures:

1. **Standing approvals** (§3.6): Recurring goals with stable scope earn standing approval tokens. Reduces approval volume for predictable work.
2. **Batch review with anomaly highlighting**: In batch review cards, items matching established patterns are pre-checked. Items that deviate from patterns are visually flagged and unchecked by default.
3. **Approval cadence tracking**: The harness tracks approval-to-decision time. If median decision time drops below 1 second for medium+ risk actions over a rolling window, surface a non-blocking visual attention cue.
4. **Queue density cue**: If more than 5 medium+ risk approvals are queued, show elevated visual warnings (badge + contrast + summary card) without adding waits or blocking user input.
5. **No hard throttling**: The system MUST NOT enforce mandatory cooldowns, delays, or forced pauses on user decisions.
6. **Autonomy threshold proposals**: When correction rate remains low over a minimum sample window, surface an explicit threshold-widening review card (Section 5.1.6) instead of silently changing behavior.

### 0.5.5 Undo/Recover Pattern

For reversible external actions (for example archive, mark read, label, move), Silas maintains a time-boxed reverse action log:

- Each batch execution records the reverse actions needed to undo it
- Undo window: 5 minutes from execution (configurable)
- Undo is a single-tap action on the post-execution confirmation card
- After the undo window closes, recovery requires a new plan + approval cycle
- Destructive actions (delete, unsubscribe) use the high-friction confirmation ladder (`tap` -> `slide` -> `biometric/local strong auth` when available). If provider supports delayed commit/recovery, expose an undo window; otherwise label as irreversible.

This reduces decision anxiety for routine actions: users know low-risk mistakes are recoverable.

### 0.5.6 UX Quality Metrics

Track these metrics to validate that the decision cockpit is working:

| Metric | Target | Signal |
|---|---|---|
| Decision time (median) | 2-5s for low-risk, 5-15s for medium | Below 1s = fatigue. Above 30s = confusion |
| Taps per completed batch | ≤3 for one default-size batch (`config.batch_review.default_size`) | More = UI friction |
| Decline rate | 5-15% | Below 2% = rubber-stamping. Above 30% = bad proposals |
| Undo rate | <10% of reversible actions | Higher = proposals need calibration |
| Correction rate | <10% for mature action families | Drives autonomy widen/tighten proposals |
| Approval fatigue triggers | <1 per week | Frequent = approval volume too high, expand standing approvals |
| Free-text usage rate | <20% of interactions | Higher is fine but indicates chips need improvement |

Metrics are collected in the audit log and queryable via the Activity surface. No external telemetry.

### 0.5.7 Interaction Register + Mode

Every turn carries two explicit control signals produced by the Proxy and consumed by the Stream, Personality Engine, Planner, and channel renderer:

| Signal | Values | Purpose |
|---|---|---|
| `interaction_register` | `exploration`, `execution`, `review`, `status` | What kind of interaction this turn represents |
| `interaction_mode` | `default_and_offer`, `act_and_report`, `confirm_only_when_required` | How assertively Silas should proceed this turn |

Deterministic behavior rule:
- If register is `exploration` and initiative is high, Silas proposes concrete options and recommends one.
- If register is `execution` and initiative is high, Silas executes within existing approval/policy boundaries and reports results.
- If risk/policy requires confirmation, `interaction_mode` is forced to `confirm_only_when_required` regardless of initiative.
- Users do not manually set these values; they are inferred each turn and audited.

## 0.6 Governance Model

Silas executes deterministically within explicit approval and policy boundaries.

**Governance model:** The user governs approvals and exceptions; Silas executes within approved scope.

## 0.7 v4 MVP Scope Lock: Two-Stage Delivery

This spec revision delivers two milestones:

**MVP-1: Task Execution Loop** (end of Phase 3)
- "Ask Silas to do X" → see plan in web UI → approve → watch execution → see verification results
- Proves: Stream, Proxy, Planner, approval engine, work executor, verification runner, web UI
- Example: "Fix the timezone bug in this repo" with pytest verification

**MVP-2: Goal Packs + Batch Review** (end of Phase 7)
- Full domain-goal execution with connections, skills, batch review, standing approvals, proactive suggestions, and autonomy-threshold proposals
- Proves: skill system, connection lifecycle, goal scheduling, confidence escalation, autonomy calibration loop

Both milestones preserve canonical security invariants `INV-01` through `INV-06` (Section 0.8.1). Additional roadmap items are tracked in the roadmap appendix (`specs/operations-roadmap.md#18-roadmap`).

## 0.8 Amendment Guardrail (Interoperability + Tooling)

The interoperability and skill-tooling additions in this revision are additive and MUST NOT weaken existing security or execution guarantees.

Non-regression requirement:
- Any change in this revision MUST preserve `INV-01` through `INV-06` (Section 0.8.1).

### 0.8.1 Canonical Security Invariants

| ID | Invariant | Primary enforcement point |
|---|---|---|
| `INV-01` | Executable actions require cryptographically verified approval tokens (Ed25519). | Approval engine + execution entry gate (`5.2.1`, step 0) |
| `INV-02` | Approval tokens are content-bound and replay-protected (plan-hash binding + nonce protection). | Approval verifier (`5.11`) |
| `INV-03` | Completion truth is external deterministic verification, not agent self-report. | Verification runner (`5.3`) |
| `INV-04` | Policy gates run deterministically and can block execution; quality gates are advisory only. | Gate runner (`5.4`, `5.5`) |
| `INV-05` | Execution isolation and taint propagation remain outside agent control. | Sandbox + taint tracker (`9`, `5.12`) |
| `INV-06` | Skill activation (native/imported) requires deterministic validation, approval, and hash-bound versioning. | Skill install/import flow (`10.4`, `10.4.1`) |

---


## 0.9 How To Read This Spec

`specs.md` is the normative implementation core.

Large reference material has been moved into companion files to keep this document executable and easier to navigate:

- `specs/project-structure.md` — Section 1 project tree and onboarding/frontend structure
- `specs/reference/models.md` — Section 3 data models (`3.1` to `3.12`)
- `specs/reference/protocols.md` — Section 4 protocols (`4.1` to `4.24`)
- `specs/reference/examples.md` — Section 13 example plans
- `specs/testing.md` — Section 14 testing strategy
- `specs/reference/security-model.md` — Section 15 security model matrix and prohibited-capability list
- `specs/adrs.md` — Section 16 architecture decision records
- `specs/operations-roadmap.md` — Sections 17-18 operations, reliability, roadmap
- Section 19 (Agent Loop Architecture v3) — integrated directly in this document

Section numbering is preserved in companion files so existing references remain semantically stable.

---

## 1. Project Structure

Moved to `specs/project-structure.md` (normative reference).

---
## 2. Dependencies

### Core

| Package | Purpose | Version Constraint |
|---|---|---|
| `pydantic-ai-slim[openrouter,logfire]` | Agent execution framework — LLM calls, structured output, tool dispatch, approval-paused tools, usage tracking, observability | `>=1.0,<2` |
| `pydantic` | Data models | `>=2.10,<3` |
| `pydantic-settings` | Configuration management | `>=2.7,<3` |
| `pyyaml` | YAML config file parsing | `>=6.0,<7` |
| `click` | CLI framework | `>=8.1,<9` |
| `httpx` | Outbound HTTP client for core `web_search` executor | `>=0.28,<1` |

**Personality layer note:** Personality shaping (axes, voice presets, mood drift, and directive rendering) is implemented with Pydantic models + SQLite persistence and introduces **no additional external dependencies** in v1.

### Cryptographic Approval

| Package | Purpose | Version Constraint |
|---|---|---|
| `cryptography` | Ed25519 signing/verification | `>=43.0` |
| `keyring` | OS credential store for private keys | `>=25.0,<26` |

### Memory

| Package | Purpose | Version Constraint |
|---|---|---|
| `sqlite3` (stdlib) + FTS5 | Day-one memory storage and keyword retrieval (no GPU needed) | Built into Python/SQLite |

### Web Search Tooling

`web_search` is a core harness tool. It is loaded only when search provider credentials are configured.

### Web (Primary Channel)

| Package | Purpose | Version Constraint |
|---|---|---|
| `fastapi` | HTTP + WebSocket server | `>=0.115,<1` |
| `uvicorn[standard]` | ASGI server | `>=0.34,<1` |

### Gates & Guardrails

| Package | Purpose | Version Constraint |
|---|---|---|
| `guardrails-ai` | Validator framework integration (config-driven checks) | `>=0.7,<1` |

### Sandboxing

| Package | Purpose | Version Constraint |
|---|---|---|
| `docker` | Optional Docker backend for the sandbox interface (subprocess backend is default) | `>=7.0,<8` |

### Scheduling

| Package | Purpose | Version Constraint |
|---|---|---|
| `apscheduler` | Cron-based goal verification scheduling | `>=3.11,<4` |

### 2.5 Connection Framework

Silas builds and maintains connections as managed resources. Connections are not static "settings"; they are lifecycle-managed work products.

**Connections are skills.** A connection is a skill (§10) whose `scripts/` handle authentication, health checks, and API interaction. The `ConnectionManager` is a thin lifecycle coordinator that invokes connection-skill scripts rather than implementing adapter logic directly. See §10.6 for the connection-as-skill structure.

**Important ID boundary:** The runtime already uses `connection_id` in channels as a session identifier (for access controller scope). Service connections use a separate identifier namespace and MUST NOT reuse channel session IDs.

**Connection model** (`silas/connections/registry.py`):

**ConnectionStatus** — Enum: `proposed`, `connecting`, `active`, `paused`, `failed`.

| Field | Type | Description |
|---|---|---|
| `connection_id` | `str` | Unique service-connection ID |
| `name` | `str` | Human-readable label (for example, `Outlook — user@example.com`) |
| `domain` | `str` | Domain tag (`personal`, `business:acme`, etc.) |
| `skill_name` | `str` | Name of the connection skill (flat namespace under `skills_dir`) |
| `auth_strategy` | `AuthStrategy` | Auth method used: `device_code`, `browser_redirect`, or `secure_input` (§3.12) |
| `permissions` | `list[ConnectionPermission]` | Granted Silas-level permission tiers |
| `permissions_granted` | `list[str]` | Actual service-level permissions/scopes (e.g., `["Mail.ReadBasic", "offline_access"]`) |
| `permissions_available` | `list[str]` | Service permissions available but not yet requested (from SKILL.md frontmatter) |
| `initial_permissions` | `list[str]` | Permissions granted at first setup (immutable, for audit trail) |
| `status` | `ConnectionStatus` | Current lifecycle state |
| `health_check` | `str` | Cron expression for connectivity checks (invokes the skill's `health_check.py` script) |
| `token_expires_at` | `datetime \| None` | When the current access token expires (updated by health checks) |
| `refresh_token_expires_at` | `datetime \| None` | When the refresh token expires (for sliding-window services like Microsoft Graph) |
| `last_health_check` | `datetime \| None` | Timestamp of last successful health check |
| `last_refresh` | `datetime \| None` | Timestamp of last token refresh |

**ConnectionPermission** tiers:

| Tier | Description | Required verification level |
|---|---|---|
| `observe` | Read/index/summarize | `tap` |
| `draft` | Prepare drafts for review | `tap` |
| `act` | Send/delete/modify/unsubscribe | `tap` |
| `manage` | Reconfigure services/rules/connections | `tap` |

**Authorization invariant:** Permission tier controls UX friction, not cryptographic requirements. Connection actions still require a valid signed approval token under Section 3.6.

**Connection lifecycle:** Building or repairing a connection is modeled as a WorkItem flow: plan -> approve -> execute (skill scripts) -> verify -> monitor. New connections are created by building a connection-type skill (§10.5).

### Optional

| Package | Purpose | Install Extra |
|---|---|---|
| `docker` (>=7,<8) | Docker sandbox backend (drop-in replacement for subprocess backend) | `pip install silas[docker]` |
| `fastembed` (>=0.7,<1) | Local embedding model (ONNX) for semantic retrieval mode | `pip install silas[vector]` |
| `sqlite-vec` (>=0.1.6,<1) | SQLite vector similarity index for semantic search mode | `pip install silas[vector]` |
| `python-telegram-bot` (>=21,<22) | Telegram channel | `pip install silas[telegram]` |
| `logfire` | OpenTelemetry observability dashboard | `pip install silas[logfire]` |

### Dev

| Package | Purpose |
|---|---|
| `pytest` (>=8) | Test runner |
| `pytest-asyncio` (>=0.24) | Async test support |
| `pydantic-evals` (>=0.2) | Dataset-driven evals |
| `ruff` (>=0.8) | Linter/formatter |

**Note on token counting:** PydanticAI tracks token *usage* from LLM API responses (input/output tokens consumed per call), but it does NOT provide a local token counting function for pre-measuring arbitrary text. Silas's context manager needs to pre-count tokens in context items before sending them to the LLM. For this, Silas uses a heuristic counter (`len(text) / 3.5`, characters ÷ 3.5) with bounded approximation error, absorbed by the required 20% context headroom.

**Provider note:** Default models run through OpenRouter. Set `OPENROUTER_API_KEY` in the environment for the default config.

Exact pre-counting roadmap options are tracked in the roadmap appendix (`specs/operations-roadmap.md#18-roadmap`).

### CLI Entry Point

The package exposes a single CLI command: `silas`, mapped to `silas.main:cli`.

---


## 3. Data Models

Moved to `specs/reference/models.md` (normative reference). Contains sections `3.1` through `3.12` with full field-level contracts.

---

## 4. Protocols

Moved to `specs/reference/protocols.md` (normative reference). Contains sections `4.1` through `4.24` with full method-level contracts.

---
## 5. Core Implementations

### 5.1 The Stream (Main Loop)

The Stream is Silas's permanent orchestration session. It receives messages, manages per-connection workers, dispatches approved work items, and runs gates on every turn. It never dies, never resets, and rehydrates on restart.

**Stream constructor dependencies** (all injected, orchestration-only):

`turn_processor_factory`, `connection_manager`, `access_controller_factory`, `channels` (list), `audit`, `scheduler`, `plan_parser`, `work_item_store`, `suggestion_engine`, `autonomy_calibrator`, `config`.

`TurnProcessor` owns the full per-turn pipeline through a single typed dependency container `TurnContext` (holding `context_manager`, `memory_store`, `chronicle_store`, `proxy`, `planner`, `work_executor`, `gate_runner`, `embedder`, `personality_engine`, `skill_loader`, `skill_resolver`, `suggestion_engine`, `autonomy_calibrator`, `config`). This removes constructor sprawl, reduces wiring mistakes, and keeps turn logic testable with one injectable context object.

**Access and context isolation model:** The Stream receives an `access_controller_factory` (a callable that creates `AccessController` instances) rather than a single instance. The Stream owns a `dict[str, AccessController]` mapping connection IDs to controllers and a matching `dict[str, TurnProcessor]` mapping connection IDs to scope-isolated processors. In single-user mode (owner-only), all messages use connection ID `"owner"`, so one controller/processor is reused. In chatbot deployment mode (active goal with `access_levels`), each unique connection ID gets its own controller and scope (`scope_id = connection_id`) so chronicle, memory injections, and workspace state cannot cross customer boundaries.

**Concurrency control (required):** Stream MUST serialize turns per connection with `asyncio.Lock` keyed by `connection_id`. Two messages from the same connection cannot execute concurrently. Different connections may run concurrently because their context scopes are isolated.

**Startup sequence (`start()`):**

1. Call `_rehydrate()` to restore state from previous run
2. Log `stream_started` to audit
3. Run connection health checks for active goals that declare connection dependencies
4. For unhealthy connections, attempt auto-recovery; if recovery fails, notify user
5. If `config.active_goal` is set, read the file content in Stream (`Path(...).read_text(encoding="utf-8")`), parse via `plan_parser.parse(markdown)`, and register the goal with the scheduler if it has a cron schedule
6. Register suggestion/autonomy heartbeat jobs (if enabled) with the scheduler
7. Start listening on all channels concurrently via `asyncio.gather`

**Turn processing (`_process_turn(message, channel, connection_id)`):**

The Stream maintains `dict[str, AccessController]`, `dict[str, TurnProcessor]`, and `dict[str, asyncio.Lock]` keyed by connection ID. When `_process_turn` is called, it acquires the connection lock, looks up/creates the scoped controller and scoped processor, derives `scope_id` (`"owner"` in single-user mode, otherwise the `connection_id`), and delegates execution to that processor. This guarantees in-order turns per connection and no shared mutable context across customers.

Each incoming message triggers this sequence (including preparatory steps 0 and 0.5). Steps execute in order. Steps 1 and 8 contain early-stop branches (gate blocks) that terminate the turn — all other steps are mandatory when reached.

0. **Precompile active gate set** — Build `active_gates = config.gates.system + current_work_item_or_goal_gates` once. This frozen list is reused for input gates (step 1), output gates (step 8), and any tool-call/after-step gates during execution. Building the set once prevents subtle bugs where the active work item changes mid-turn (e.g., a plan approved and started within the same turn).

0.5 **Review + proactive queue** — Before processing user input, poll:
   - pending batch reviews (`config.batch_review.default_size` item batches)
   - low-confidence escalations
   - draft reviews
   - connection warnings
   - idle suggestions from `suggestion_engine.generate_idle(scope_id, now)`
   - autonomy-threshold proposals from `autonomy_calibrator.evaluate(scope_id, now)`

   Queue surfaced items as batch/draft/decision/suggestion/autonomy-threshold cards. Policy-lane gates still run before any surfaced action is executed.

1. **Input gates** — Run `active_gates` with trigger `every_user_message` against incoming message text and sender ID, evaluated in two lanes:
   - **Policy lane** (blocking): For each policy-lane result:
     - If `block`: execute the escalation action (see Section 5.1.1), log to audit, and **stop processing this turn**.
     - If `require_approval`: send gate approval request to the user via the channel. If user declines, stop processing.
     - If `continue`: proceed. If the result includes `modified_context`, merge it (after allowlist filtering) and continue with the rewritten input context.
   - **Quality lane** (non-blocking): Quality-lane results are collected and logged to audit as `quality_gate_input`. Scores and flags are available for downstream observability but do not affect turn processing.

2. **Sign/taint** — Classify the message taint level. If the message has a valid Ed25519 signature AND the message nonce has not been consumed (checked via `nonce_store.is_used("msg", nonce)`) AND the timestamp is within a configurable freshness window (default 5 minutes), taint is `owner` and the nonce is recorded as consumed via `nonce_store.record("msg", nonce)`. If the signature is valid but the nonce was already used or the timestamp is stale, taint is downgraded to `external` and a replay attempt is logged to audit. If the sender is a known authenticated channel identity, taint is `auth`. Otherwise `external`.

3. **Add to chronicle** — Create a `ContextItem` in the `chronicle` zone with `kind="message"`, `taint=<message_taint>`, format `"[{taint}] {sender_id}: {text}"`, and the current `turn_number`. Add it in the current scope (`context_manager.add(scope_id, ...)`) and persist to chronicle (`chronicle_store.append(scope_id, ...)`) for rehydration.

3.5 **Raw memory ingest (low reingestion lane)** — Store the incoming user message as a raw `MemoryItem` with `source_kind="conversation_raw"` and `reingestion_tier=low_reingestion` via `memory_store.store_raw(...)`. This is durable capture, not active-context injection.

4. **Auto-retrieve memories** — Run keyword retrieval (FTS5) against the message text and inject top matches into the `memory` zone as `ContextItem`s with `kind="memory"` in the same `scope_id`. If vector search is enabled, also run semantic retrieval and merge/dedupe results. Each injected memory carries the `relevance` score assigned by the retriever.

5. **Enforce token budget** — Call `context_manager.enforce_budget(scope_id, turn_number, current_goal)`. The harness runs two-tier eviction (see Section 5.7): first heuristic pre-filtering (observation masking of old tool results, dropping trivial acknowledgments, deactivating stale subscriptions), then — only if still over budget — the scorer model identifies the least-valuable context groups. For each evicted context item, store its content to memory as `episode` with taint preserved from source context; external-tainted content must be injection-scrubbed before persistence.

6. **Build toolset pipeline** — Get `allowed_tools` from the connection-specific `access_controller.get_allowed_tools()` and `customer_context` from `access_controller.get_customer_context()` (if available). Construct the runtime toolset as a canonical wrapper chain:
   - `SkillToolset` (core tools + work-item-scoped skill tools)
   - `PreparedToolset` (role/work-item preparation)
   - `FilteredToolset` (access-level enforcement)
   - `ApprovalRequiredToolset` (pauses approval-required calls)

   Optional runtime wrappers (for example dynamic revocation when policy state changes) may wrap outermost, but MUST NOT bypass inner wrappers. Core retrieval tools (`memory_search`, `context_inspection`, `web_search` when configured) are included at `SkillToolset` stage before role preparation. In single-user mode this uses the shared controller; in chatbot mode this uses the per-connection controller.

6.5. **Prepare skill-aware toolsets** — If an active work item (goal, task, or project) is loaded, resolve its `skills` list via the skill loader and build role-aware preparation:
   - **Proxy**: metadata-only preparation (`name` + `description`) so routing stays cheap (~50-100 tokens per skill)
   - **Planner/Executor**: full skill preparation (SKILL.md instructions + validated script paths)
   Proxy skill metadata injection MUST stay within `TokenBudget.skill_metadata_budget_pct` (default 2% of context window). If exceeded, lowest-priority metadata entries are dropped deterministically before render. Prepared skill tools are then filtered by access level through `FilteredToolset`. If no active work item is loaded, use the base toolset with access filtering only.

7. **Inject personality directives, route through Proxy, and set turn operating mode** — Detect interaction context via `personality_engine.detect_context(...)`, compute effective axes, and render directives via `personality_engine.render_directives(scope_id, context)`. Inject the rendered directives into the scoped system zone as a pinned context item (`source="persona:directives"`), replacing the previous turn's persona directives item for that scope only. The injected text MUST be natural language (no raw axis numbers) and target ~200–400 tokens. Then call the Proxy agent through the structured-output policy (`run_structured_agent`, Section 5.1.0) with rendered context, signed message, and the composed wrapper-chain toolset, plus customer context. The Proxy returns a `RouteDecision` with required fields: route, register, mode, continuation target, and context profile.
   - `route == "direct"`: Proxy handled it — the response is in `route.response`.
   - `route == "planner"`: Needs deeper reasoning — call the Planner agent via `run_structured_agent` (Section 5.1.0) and use fallback behavior if schema validation fails twice.
   - `route.continuation_of`: If present, mark this turn as a deepening/correction flow linked to the prior work item.

   Deterministic operating-mode policy:
   - `default_and_offer`: do not block on preference questions for low/medium reversible actions; choose a sensible default and include alternatives.
   - `act_and_report`: execute within current approvals and report results/progress.
   - If `interaction_register == exploration` and initiative is high, prefer `default_and_offer` (choose a default, then present alternatives).
   - If `interaction_register == execution` and initiative is high, prefer `act_and_report` (execute inside existing approval/policy boundaries, then report).
   - If any policy/risk rule requires confirmation, force `interaction_mode = confirm_only_when_required` for this turn.

   After routing, compute the effective turn mode with `resolve_interaction_mode(...)` (Section 5.1.0) using `proxy_mode=route.interaction_mode`, no planner/work-item override, current initiative level, and risk policy flags. Stream MUST then call `context_manager.set_profile(scope_id, route.context_profile)` (required field, no implicit fallback) and log register/mode/profile to audit.

8. **Output gates** — Run `active_gates` (precompiled in step 0) with trigger `every_agent_response` against the agent response and sender ID, evaluated in two lanes:
   - **Policy lane** (blocking): For each policy-lane result:
     - If `block`: execute the output escalation action (suppress response, rephrase, or escalate) and **stop processing**.
     - If `require_approval`: send gate approval request to the user via the channel. If user approves, proceed. If user declines, execute the output escalation action and **stop processing**.
     - If `continue`: proceed. If the result includes `modified_context`, merge it (after allowlist filtering) and apply rewritten output context before sending (e.g., redacted response text).
   - **Quality lane** (non-blocking): Quality-lane results are collected and logged to audit as `quality_gate_output`. Scores and flags are available for observability but do not affect whether the response is sent.

9. **Process memory retrieval queries** — This step executes only if step 8 completed with no block/suppression. For each `MemoryQuery` in the agent's `memory_queries` (max 3), execute the query and inject results into the `memory` zone as scoped `ContextItem`s with `kind="memory"`. Dedup against existing memory zone items by `memory_id`.

10. **Process memory operations (gated side effect)** — Before executing each `MemoryOp`, run policy gates with trigger `on_tool_call` and synthetic context `{"tool_name": "memory_op", "tool_args": op.model_dump()}`. If blocked, skip that op and log `memory_op_blocked`. Only approved/continued ops execute. This prevents blocked/suppressed responses from silently writing long-term memory.

11. **Add response to chronicle** — Create a `ContextItem` in the `chronicle` zone with `kind="message"`, `taint=<response_taint derived from tool/input lineage>`, format `"Silas: {response.message}"`, sourced as `"agent:proxy"` or `"agent:planner"`. Add/persist in the active scope (`context_manager.add(scope_id, ...)`, `chronicle_store.append(scope_id, ...)`).

11.5 **Raw output/query ingest (low reingestion lane)** — Persist agent responses, research queries issued by tools, and external tool outputs as raw memory records (`source_kind` values such as `"agent_response_raw"`, `"research_query_raw"`, `"tool_output_raw"`) with `reingestion_tier=low_reingestion`.

12. **Handle plan/approval flow** — If the response contains a `plan_action`:
    - If `needs_approval` is `true`: invoke the plan approval flow (Section 5.1.2).
    - If `needs_approval` is `false`: the Stream MUST verify that a valid `approval_token` is attached to the parsed work item before proceeding to execution. If no valid token is present, the Stream MUST override `needs_approval` to `true` and invoke the plan approval flow. This prevents the LLM from bypassing approval by setting `needs_approval=false` in its output — the field is an agent *request*, not an authorization grant.

    **Deepening linkage rule:** If `RouteDecision.continuation_of` or `PlanAction.continuation_of` is set, the parsed work item MUST set `follow_up_of` to that work item ID and default `input_artifacts_from` to `["*"]` unless the plan specifies a narrower artifact list.

    **Interaction-mode propagation rule:** For plan-based execution, resolve with `resolve_interaction_mode(...)` using precedence `risk_requires_confirmation > PlanAction.interaction_mode_override > work_item.interaction_mode (if pre-set) > RouteDecision.interaction_mode > initiative/register default`. Persist the resolved value on `work_item.interaction_mode`.

    **Execution responsiveness rule:** after approval, execution MUST be dispatched as a background work item unless configured `sync_execution=true`. Stream sends an immediate acknowledgment response and then progress/status events (`running`, `verification_failed`, `done`, `blocked`) as they occur. A user-facing turn MUST NOT stall for long-running execution.

    **Note on when `needs_approval=false` can legitimately succeed:** The plan markdown format (Section 7.2) has no `approval_token` field, so `plan_parser.parse()` always produces a WorkItem with `approval_token = None`. This means for any plan proposed by the Planner via `plan_action`, the override check above will always force the approval flow — which is the correct and intended behavior. The `needs_approval=false` path only succeeds for work items constructed programmatically by the runtime with a pre-attached token, specifically goal-spawned fix tasks (Section 5.2.3 step 4) where the standing token was verified and attached before the task enters the executor. Planner-generated plans ALWAYS require interactive approval.

13. **Send response + suggestions** — Send the agent's message to the user via the channel immediately after step 8/12 decisioning; do not wait for background execution completion. When a work item completes, convert `WorkItemResult.next_steps` via `suggestion_engine.generate_post_execution(...)` and queue resulting suggestion cards in Review.

14. **Update access state** — For each gate in `active_gates` (from step 0) that has trigger `every_user_message` and type `custom_check`, check if the gate result (from step 1) should update the access controller via `gate_passed()`. This enables identity verification flows where passing a gate unlocks higher access levels.

15. **Post-turn mood/event update + autonomy telemetry** — Apply a personality event via `personality_engine.apply_event(...)` based on deterministic runtime outcomes (for example: `task_completed`, `verification_failed`, `blocked`, `compliment`, `feedback_too_harsh`). Then call `personality_engine.decay(...)` so mood trends toward neutral over elapsed time. Persist updated state to SQLite. Record correction outcomes (approvals, edits, declines, undos) via `autonomy_calibrator.record_outcome(...)`.

16. **Increment turn counter.**

#### 5.1.0 Structured Output Reliability + Interaction Mode Resolution

All agent calls with structured outputs (Proxy `RouteDecision`, Planner `AgentResponse`, Executor `ExecutorAgentOutput`, scorer `ScorerOutput`) MUST use one deterministic wrapper:

```python
async def run_structured_agent(agent, prompt, call_name):
    try:
        return await agent.run(prompt)
    except ValidationError as err:
        repair_prompt = prompt + "\n\n[SCHEMA VALIDATION ERROR]\n" + summarize(err)
        try:
            return await agent.run(repair_prompt)
        except ValidationError as err2:
            return structured_fallback(call_name, err2)
```

Rules:
1. Exactly one retry on schema validation failure, with the validation error summary appended.
2. Retry must use the same model/tool budget limits as the first attempt.
3. If retry still fails, do NOT loop indefinitely. Apply deterministic fallback by call type:
   - `proxy`: synthesize a direct `RouteDecision` with a user-visible error message, `interaction_register="status"`, `interaction_mode="confirm_only_when_required"`, and `context_profile=config.context.default_profile`.
   - `planner`: synthesize an `AgentResponse` that reports planning-output failure and omits `plan_action`.
   - `executor`: fail the current execution attempt with `last_error="executor_structured_output_invalid"` and continue normal retry/budget policy.
   - `scorer`: skip model eviction for this turn and apply deterministic aggressive heuristic eviction.

Interaction-mode governance MUST be centralized in one function:

```python
def resolve_interaction_mode(
    proxy_mode: InteractionMode | None,
    planner_override: InteractionMode | None,
    work_item_mode: InteractionMode | None,
    risk_requires_confirmation: bool,
    initiative_level: float,
    interaction_register: InteractionRegister,
    high_initiative_min: float,
    default_mode_by_register: dict[InteractionRegister, InteractionMode],
) -> InteractionMode:
    if risk_requires_confirmation:
        return InteractionMode.confirm_only_when_required
    if planner_override is not None:
        return planner_override
    if work_item_mode is not None:
        return work_item_mode
    if proxy_mode is not None:
        return proxy_mode
    if initiative_level >= high_initiative_min:
        if interaction_register == InteractionRegister.execution:
            return InteractionMode.act_and_report
        if interaction_register == InteractionRegister.exploration:
            return InteractionMode.default_and_offer
    return default_mode_by_register.get(
        interaction_register, InteractionMode.default_and_offer
    )
```

No component may set `interaction_mode` directly without going through this resolver.

#### 5.1.1 Gate Block Handling

When a gate blocks (input or output):

1. Look up the gate's `on_block` field in the escalation dictionary. The escalation dictionary is resolved by checking, in order: (a) the active goal's `escalation` map, (b) a built-in default escalation map. The built-in defaults provide fallback behavior for standard escalation names so that global system gates (from config) work even when no active goal is loaded:
   - `polite_redirect`: `{ action: "respond", message: "I can't help with that. How else can I assist you?" }`
   - `report`: `{ action: "report" }`
   If the `on_block` name is not found in either the active goal's map or the built-in defaults, treat it as a `report` action and log a warning to audit
2. Execute the escalation action:
   - `escalate_human`: Send the escalation message to the user and request human takeover via the channel (e.g., open a live-agent handoff flow). If the channel does not support human handoff, fall back to `transfer_to_queue` if `queue` is set, otherwise fall back to `report`.
   - `transfer_to_queue`: Send the escalation message to the user, then transfer to the named `queue` (channel-specific)
   - `respond`: Send the escalation message as the response
   - `suppress_and_rephrase`: Suppress the blocked response, re-run the agent with the rephrase instruction appended, up to `max_retries`; fall back to the `fallback` escalation if retries exhausted
   - `suppress_and_escalate`: Suppress the blocked response, send the escalation message, then fall back to the `fallback` escalation (e.g., transfer to human)
   - `report`: Log the block to audit and notify the owner
   - `spawn_task`: Create a fix task from the failure context
   - `retry`: Re-execute the current step up to `max_retries`; fall back to the `fallback` escalation if retries exhausted
3. Always log the gate block to audit with gate name, reason, and sender ID

Gate blocks only apply to policy-lane gates. Quality-lane gates cannot block; their results are logged to audit but do not trigger escalation actions.

#### 5.1.2 Plan Approval Flow

When an agent proposes a plan:

1. Parse the plan markdown via `plan_parser.parse()` to get a `WorkItem`
2. Add the plan markdown to the `workspace` zone as a `ContextItem` with `kind="plan"` and `pinned=true`
3. If channel implements `RichCardChannel`, call `send_approval_request(user_id, work_item)`. Otherwise use core-channel text fallback parsing. Both paths return an `ApprovalDecision`.
4. If `decision.verdict != approved`: log to audit, inform the user, and stop.
5. Call `approval_engine.issue_token(work_item, decision, scope=full_plan)` to mint/sign the token.
6. Call `approval_engine.verify(token, work_item)` — this is the consuming verification for single-use tokens. It checks signature, plan hash, expiry, and consumes exactly one execution nonce. This is the single authorization point for interactive approvals, mirroring how 5.2.3 is the single authorization point for standing approvals.
7. If verification fails: log to audit, inform the user, and stop.
8. Attach the verified token to the work item (`work_item.approval_token = token`).
9. Execute the work item via `work_executor.execute()`, add the result summary to the workspace zone.

#### 5.1.3 Rehydration

On startup, The Stream restores previous state:

1. Load the system zone (constitution, tool descriptions, configuration) as pinned `ContextItem`s with `kind="system"`
2. For each known scope, load the most recent `config.rehydration.max_chronicle_entries` entries from the **chronicle store** (`get_recent(scope_id, limit)`) and add them to that scope's chronicle zone with `kind="message"`. Apply observation masking to tool results older than `observation_mask_after_turns` turns.
3. Search memory for the scope profile (FTS5 keyword search for `"user profile preferences"`; optionally semantic search if vectors are enabled) and add it to the same scope memory zone with `kind="memory"` and `pinned=true` if found
4. Restore active context subscriptions from the **work item store** (file subscriptions attached to in-progress work items). Materialization happens on the next `render()` call.
5. Add a system message: `"[SYSTEM] Session rehydrated after restart."`
6. Load any in-progress work items from the **work item store** and resume them
7. Load persisted persona state for each known scope lazily on first message so mood and preset continuity survive restarts without eagerly preloading unknown scopes.
8. Rehydrate pending batch-review, suggestion, and autonomy-threshold proposal items for active goals/scopes

#### 5.1.4 Personality Layer

Personality is a prompt-shaping layer between Stream orchestration and agent calls. It has exactly two runtime hooks:

1. **Pre-agent injection hook:** Step 7 of turn processing renders and injects personality directives into the system zone.
2. **Post-turn update hook:** Step 15 applies deterministic mood events and decay.

**Precedence (non-negotiable):**

`constitution > safety/approval policy > task constraints > personality directives`

Personality directives MAY alter communication style (tone, phrasing, pacing, certainty expression) but MUST NOT alter authorization logic, gate behavior, approval requirements, access controls, or verification outcomes.

Initiative axis is behavior-shaping as well as style-shaping: it biases `interaction_mode` selection (`default_and_offer` vs `act_and_report`) inside policy/approval boundaries, never around them.

**Context-based axis modifiers (default mapping):**

- `code_review`: assertiveness +0.20, certainty +0.10, humor -0.20, verbosity +0.10
- `casual_chat`: warmth +0.20, humor +0.30, formality -0.30, verbosity -0.10
- `crisis`: verbosity -0.30, assertiveness +0.20, initiative +0.30, humor -0.50
- `group_chat`: assertiveness -0.10, verbosity -0.20, initiative -0.20
- `deep_research`: verbosity +0.30, certainty -0.20, humor -0.30, formality +0.10

**Mood model (event-driven + decay):**

- State dimensions: `energy`, `patience`, `curiosity`, `frustration`
- Example events: `task_completed`, `ci_failure`, `blocked`, `compliment`, `long_session`, `feedback_too_harsh`
- Mood decays toward neutral (0.5) at configured `decay_rate_per_hour`

**Feedback loop behavior:**

- Example: `"too harsh"` applies `assertiveness -0.05` immediately for the active scope
- Baseline drift is optional and allowed only for trusted feedback sources
- Untrusted feedback may affect transient session mood only (bounded and rate-limited)
- Presets are named templates (`default`, `work`, `review`, `chill`, or custom names such as `jarvis`/`c3po`/`hk47`) that map to axis+voice defaults

#### 5.1.5 Goal-Level Reviewed Batch Behavior

For goals that define reviewed-batch policies, Silas executes deterministic batch flows:

1. Retrieve candidates using approved connection permissions
2. Classify each item and assign confidence
3. Build `BatchProposal` chunks sized by `config.batch_review.default_size`
4. Request batch review via `send_batch_review(...)`
5. Execute only approved batches
6. For low-confidence or ambiguous items, present decision/draft cards instead of autonomous destructive action

Scope rule:
- If action is inside verified standing-approval scope, Silas may execute and report.
- If outside standing scope, Silas must surface a suggestion/approval card and wait.

#### 5.1.6 Proactive Suggestion + Autonomy Calibration Loop

Silas runs two control loops outside direct user prompts:

1. **Suggestion loop (momentum):**
   - Heartbeat trigger (configurable cron) runs `suggestion_engine.generate_idle(scope_id, now)` for active scopes.
   - Sources: active-goal state, pending reviews, recent result patterns, and unresolved warnings.
   - Generation path is lightweight: deterministic heuristics first, optional proxy-tier model pass for wording/prioritization.
   - Output: low-friction `SuggestionProposal` cards in Review (`do it`, `not now`, optional alternatives).
   - Suggestions are deduped by `cooldown_key`; repeated suggestions are suppressed until cooldown expires.

2. **Autonomy calibration loop (earned autonomy):**
   - `autonomy_calibrator` tracks correction metrics per action family: edit-selection rate, decline rate, undo rate.
   - When correction rate stays below widening threshold for minimum sample size, generate an `AutonomyThresholdProposal`.
   - Proposal cards include exact parameter diffs (scope, max executions, expiry window, batch size, confidence threshold), evidence window, and correction stats.
   - Applying widening/tightening always requires explicit review-card approval, `ApprovalScope.autonomy_threshold` token issuance, and consuming verification via `approval_engine.verify(...)` before any config mutation.

Required anti-ratchet controls:
- Minimum sample size before widening (`min_samples`)
- Hysteresis: widening and tightening thresholds are different to avoid oscillation
- Hard caps on each widenable knob
- Single-tap rollback action: `tighten approvals`

### 5.2 Work Item Executor

Executes any WorkItem regardless of type. The execution model is always the same loop: run the agent, check mid-execution gates, verify externally, retry on failure, consult planner if stuck, report if budget exhausted.

**Constructor dependencies:** `key_manager`, `nonce_store`, `approval_engine`, `executor_registry`, `skill_resolver`, `gate_runner`, `verification_runner`, `planner`, `audit`, `channel`, `work_item_store`, `owner_id`.

#### 5.2.1 Task Execution

0. **Approval gate (MANDATORY — before any execution):** Validate the work item's `approval_token` before entering the retry loop. If the token is `None`, set status to `blocked`, log `"execution_blocked_no_approval"` to audit, persist, and return immediately.

   Call `approval_engine.check(token, work_item)` — the non-consuming validation. This applies uniformly to both single-use and standing tokens because `verify()` was already called at the authorization site: 5.1.2 step 5 for single-use tokens, 5.2.3 step 4 for standing tokens. `check()` confirms the token's signature, expiry, and hash/parent binding are still valid without consuming a second execution nonce.

   If `check()` returns `(False, reason)`, set status to `blocked`, log `"execution_blocked_no_approval"` with the reason to audit, persist, and return immediately. Do NOT proceed to the retry loop. This is the enforcement point behind the security claim "cannot execute without verified approval."

0.5 **Follow-up artifact hydration (required for deepening flows):** If `work_item.follow_up_of` is set, load artifacts from the referenced completed work item before entering the retry loop. Import keys from `work_item.input_artifacts_from` (`["*"]` imports all). If the referenced work item or required artifacts are missing, set status to `blocked` and return with an explicit linkage error.

1. **Budget tracking:** Create a budget tracker from the work item's budget. Before every action, check if budget is exhausted (using `>=` comparison — reaching the limit counts as exhausted).

2. **Retry loop:** While `attempts < max_attempts` and budget is not exhausted:

   a. Increment `attempts`. Set status to `running`. Persist state to work item store.

   b. Build agent instructions: start with the work item `body` (prose briefing). If this is a retry, append the previous failure details under a `"Previous attempt N failed"` heading. If the planner provided guidance, append it under a `"Planner guidance"` heading.

   c. Run the agent using PydanticAI `Agent.run()` (through `run_structured_agent`, Section 5.1.0) with the instructions and budget limits. PydanticAI's `UsageLimits` enforces token/request limits at the framework level. Track consumed tokens and cost in the budget tracker. Capture `ExecutorAgentOutput.next_steps` for user-facing follow-up suggestions in `WorkItemResult.next_steps`.

   c1. **Collect tool execution ledger (actual results):** During the agent run, record every executed tool call as an `ExecutionResult` entry (including `success`, `return_value`, `artifacts`, `metadata`, `error`, `taint`, timing/cost). This ledger is the authoritative "what actually happened" source for verification and artifact persistence.

   c2. **Tool-call gates + argument validation + approval-paused calls:** Before each tool invocation within the agent run:
      - Validate tool/script arguments against declared schemas (`script_args` in skill frontmatter when present). Schema failure blocks the call before sandbox execution and returns a deterministic validation error to the agent.
      - Evaluate `execution_gates` (precompiled as `config.gates.system + work_item.gates` once before the retry loop) with trigger `on_tool_call` via the gate runner. The context dict includes `{"tool_name": str, "tool_args": dict}`. Two-lane evaluation:
         - **Policy lane:** The gate runner returns `(policy_results, merged_context)`; if `merged_context` contains rewritten `tool_args`, execute with rewritten arguments. If any policy gate returns `block`, prevent the tool call and return the block reason to the agent. If `require_approval`, `ApprovalRequiredToolset` emits an `ApprovalPausedToolRequest` instead of executing immediately.
         - **Quality lane:** Results are logged to audit as `quality_gate_tool_call` but do not affect tool execution.
      Approval-paused requests are resolved by the Stream/approval engine: collect user decision, mint/sign approval token, verify it, then resume the paused tool call. This replaces custom in-tool approval wiring.

   c3. **Collect artifacts from actual results (mandatory):**
      - Build `attempt_artifacts` from successful `ExecutionResult.artifacts` entries in the tool execution ledger (deterministic merge order by tool-call sequence). This is the authoritative artifact set for verification and `WorkItemResult`.
      - Cross-check `ExecutorAgentOutput.artifact_refs` against `attempt_artifacts` keys; log mismatches to audit as `artifact_ref_mismatch`. This is observability only — verification and result construction always use `attempt_artifacts`, never the agent's self-reported references.

   d. **Mid-execution gates:** For each gate in `execution_gates` with trigger `after_step`, extract the relevant output variable and evaluate via the gate runner. Two-lane evaluation:
   - **Policy lane:** Apply any returned `modified_context` (after allowlist filtering) to the step output context before verification or next-step execution. If a policy gate blocks, set status to `blocked`, persist, and return. If approval is required, request via channel — if declined, block and return.
   - **Quality lane:** Results are logged to audit as `quality_gate_after_step`.

   e. **External verification:** If the work item has `verify` checks, run them via the verification runner (Section 5.3). Verification operates on sandbox filesystem artifacts, not in-memory objects. The agent has NO influence over this step.
      - If all checks pass: set status to `done`, log to audit, persist, and construct `WorkItemResult` with `artifacts=attempt_artifacts`, `next_steps` from `ExecutorAgentOutput`, and `verification_results` from the runner.
      - If any check fails: format the failure details. If `attempts >= 3` and `on_stuck == "consult_planner"`, call the planner for guidance (charges against the budget). Loop back to step (a).

   f. **No verification defined:** If the work item has no `verify` checks, treat a single successful execution as done. Construct `WorkItemResult` using `attempt_artifacts` from the tool execution ledger and `next_steps` from `ExecutorAgentOutput`.

3. **Budget exhausted or max attempts reached:** Set status to `stuck`, persist, and return with the last failure context.

#### 5.2.2 Project Execution

1. Topologically sort child task IDs by their `depends_on` relationships
2. Execute each task in order via the task execution loop (5.2.1)
3. Before starting each task, verify all its dependencies completed with status `done`. If any dependency is not done, return `blocked`.
4. If any task fails, return `failed` with the failing task's details.
5. After all tasks complete, run project-level verification checks (if any)
6. Persist final project status to work item store

#### 5.2.3 Goal Cycle Execution

One cycle of a recurring goal (called by the scheduler):

1. Run all verification checks for the goal
2. Log the check results to audit
3. If all checks pass, return `healthy`
4. If any check fails and `on_failure == "spawn_task"`:
   - Create a new `WorkItem` of type `task` with the `failure_context` template, replacing `$failed_checks` with the actual failure details
   - Set `parent` to the goal's ID
   - Compute `spawned_task_hash = sha256(work_item_plan_hash_bytes(spawned_task))` and include it in audit + authorization context.
   - **Standing approval verification (REQUIRED):** Look up the goal's `approval_token`. Verify it is a valid standing approval (`scope == "standing"` and `executions_used < max_executions`). Call `approval_engine.verify(token=goal.approval_token, work_item=goal, spawned_task=spawned_task)` — this performs full cryptographic verification (signature, plan hash against the goal, spawned-task parent binding, spawned-task hash/policy binding, expiry) and consumes exactly one execution nonce.
   - If standing verification succeeds: set `needs_approval = false` and attach the standing token to the spawned task.
   - If standing verification fails: set `needs_approval = true`, request a fresh decision via `channel.send_approval_request(...)`, mint a single-use token via `approval_engine.issue_token(spawned_task, decision, scope=full_plan)`, then MUST call `approval_engine.verify(new_token, spawned_task)` before attaching it. If any step fails or decision is declined, mark the spawned task `blocked` and stop this goal cycle.
   - Execute the fix task via the task execution loop
5. If `on_failure != "spawn_task"`, return `failed` with the verification report

#### 5.2.4 Reviewed Batch Execution

Domain actions are executed in reviewed batches:

1. Build candidate actions (domain-defined action keys)
2. Chunk candidates into proposals of size `config.batch_review.default_size` (`BatchProposal`)
3. Present each batch via `send_batch_review(...)` and receive `BatchActionDecision`
4. For `approve`: issue/verify token bound to exact batch payload, then execute
5. For `edit_selection`: validate `selected_item_ids` is a strict subset of the proposed batch, rebuild payload in selected ID order, and require fresh approval
6. For `decline`: skip batch and continue with next batch or escalate
7. Record each verdict/outcome (`approve`, `edit_selection`, `decline`, undo if later reversed) through `autonomy_calibrator.record_outcome(...)`

### 5.3 Verification Runner

Runs verification checks OUTSIDE the agent's sandbox. The agent has zero influence over this code. Verification commands run in a dedicated verification sandbox backend (same `SandboxManager` interface as execution), with a minimal environment and no access to secrets, agent context, or memory.

**Security boundary note:** The primary security boundary in this version is cryptographic approval + deterministic verification. Sandbox isolation is backend-dependent: subprocess backend provides process-level separation and policy controls; Docker backend provides stronger filesystem/process/network isolation. Both backends use the same interface, and verification MUST run in a separate sandbox instance from task execution to prevent shared-state influence.

**Behavior:**

For each `VerificationCheck`:

1. Execute the `run` command in a dedicated verification sandbox instance with:
   - Backend: `sandbox.backend` from config (`"subprocess"` by default, `"docker"` optional)
   - Working directory: verification work dir (`verify_dir`) with task artifacts exposed read-only
   - Environment: minimal — only `PATH=/usr/local/bin:/usr/bin:/bin` and `HOME` set to the verification work dir. No secrets, no agent context, no host environment leakage.
   - Network: disabled by default; enabled only if the check explicitly sets `network: true`. If the selected backend cannot enforce the requested network policy, the check MUST fail closed with a configuration error.
   - Timeout: the check's `timeout` value (default 60 seconds)
   - Resource limits: backend-specific best-effort limits using `max_memory_mb` and `max_cpu_seconds`

2. Capture stdout and exit code. On timeout, return a failed result with reason `"Timeout after Ns"`.

3. Evaluate the `expect` predicate against the output and exit code:
   - `exit_code`: compare process exit code to expected value
   - `equals`: exact string match on stdout
   - `contains`: substring match on stdout
   - `regex`: regex match on stdout (using `re.search`)
   - `output_lt` / `output_gt`: parse stdout as float and compare
   - `file_exists`: check file existence **within permitted directories only** (see path constraint in Section 3.3)
   - `not_empty`: stdout must not be empty
   - If no expectation field is set, the check passes by default (with reason `"No expectation"`)

4. Return a `VerificationResult` with the check name, pass/fail, reason, truncated output (1000 chars max), and exit code.

5. Aggregate all results into a `VerificationReport` with `all_passed`, `results`, `failed`, and `timestamp`.

### 5.4 Gate Runner

Evaluates gates by dispatching to registered providers in two lanes: **policy** (blocking, deterministic) and **quality** (non-blocking, advisory). Enforcement routing is deterministic.

**Behavior:**

- Maintains a registry of `GateCheckProvider` instances keyed by provider name
- `check_gates(gates, trigger, context)` filters gates by trigger, then evaluates in two passes:

  1. **Policy lane** — All gates whose provider maps to `policy` lane (or `llm` gates with `promote_to_policy: true`). Evaluated in order. Each gate may return `continue`, `block`, or `require_approval`. If a result includes `modified_context`, the gate runner strips any keys not in `ALLOWED_MUTATIONS` (logging rejected keys to audit as `rejected_mutation`), then merges the surviving keys into the working context. Context mutation is left-to-right and deterministic. On first `block`, short-circuit — remaining policy gates still run (for audit completeness) but additional blocks are logged without re-escalating.

  2. **Quality lane** — All `llm` gates without `promote_to_policy`. Evaluated after policy gates complete (using the post-mutation context). Quality gates MUST only return `continue` with optional `score` and `flags`; the gate runner enforces this by ignoring any `block`/`require_approval` action from a quality gate and logging a `quality_lane_violation` warning to audit. Quality results do not affect the merged context and cannot produce `modified_context`.

- Returns `(policy_results, quality_results, merged_context)` — callers use policy results for enforcement and quality results for logging/observability
- If a gate references a provider that isn't registered, return a policy-lane `block` result with reason `"No provider: {name}"`

**Mutation allowlist enforcement:**

The gate runner is the sole enforcement point for `ALLOWED_MUTATIONS`. Before merging any `modified_context` dict, the runner:
1. Iterates top-level keys
2. Drops any key not in `{"response", "message", "tool_args"}`
3. Logs each dropped key to audit as `rejected_mutation` with gate name and key name
4. Merges surviving keys into the working context

This enforcement happens regardless of which provider produced the result. Providers do not need to self-police.

### 5.5 Gate Providers

#### 5.5.1 GuardrailsAI Provider

Wraps the `guardrails-ai` library for text safety validation. Checks are configured by validator name in gate config (no fixed enum in core):

- **Example checks:** toxicity, PII detection/redaction, jailbreak detection.
- Each gate defines the validator under `check` and passes validator-specific arguments in `config`.
- On fail, behavior follows validator mode:
  - exception mode -> `block`
  - fix mode -> `continue` with `modified_context` (after mutation allowlist)

Guards are lazily configured on first use. The provider extracts the text to check from the context dict (`context["message"]` for input gates, `context["response"]` for output gates).

On validation success: return `continue`. On validation failure (exception): return `block` with the error message as reason.

For PII redaction (`on_fail=FIX`), the provider MUST return:
- `modified_context={"response": "<redacted text>"}`

#### 5.5.2 Predicate Provider

Deterministic checks with no external dependencies.

**Numeric range checks** (`numeric_range` type):
1. Parse the extracted value as a float. If not numeric, return `block`.
2. Check hard block range first: if `block.outside` is set and value falls outside `[low, high]`, return `block`.
3. Check auto-approve range: if value falls within `auto_approve` min/max, return `continue`.
4. Check approval range: if value falls within `require_approval` min/max, return `require_approval`.
5. If value doesn't match any range, return `block`.

**String match checks** (`string_match` type):
- If value is in `allowed_values`, return `continue`
- If value is in `approval_values`, return `require_approval`
- Otherwise return `block`

**Regex checks** (`regex` type):
- If value matches the pattern, return `continue`
- Otherwise return `block`

**File validation checks** (`file_valid` type):
1. Extract the file path from the context (via the gate's `extract` field).
2. Validate the path against the allowlist of permitted directories (same constraints as `Expectation.file_exists` — no `..` traversal, must be within sandbox/project directories).
3. If path validation fails, return `block` with reason `"Path outside permitted directories"`.
4. Check that the file exists. If not, return `block` with reason `"File not found"`.
5. If the gate's `config` includes `max_size_bytes`, check the file size. If exceeded, return `block`.
6. If all checks pass, return `continue`.

**Always-approve gates** (`approval_always` type):
- Always return `require_approval` regardless of the value. This is used for actions that MUST have human sign-off every time (e.g., financial transactions, credential usage). The gate runner does not short-circuit — it always prompts the user via the channel's `send_gate_approval`.

**Optional mutation behavior:**
- Predicate gates may optionally return `modified_context` when configured with explicit normalization rules (for example, clamp a numeric value into a safe range and continue with the clamped value). All mutations must be explicit in gate config and logged to audit.

#### 5.5.3 LLM Provider

Uses the configured **quality-tier model** (`gates.llm_defaults.model`) to evaluate subjective quality checks that are hard to encode deterministically. This provider runs in the **quality lane** by default — results are advisory (scores + flags), never blocking. A gate can be promoted to policy lane via `promote_to_policy: true` for cases where LLM judgment is the only viable check (e.g., "does this response contain medical advice?"), but this should be rare.

This provider uses the existing PydanticAI model client stack; no new dependency is required.

**Use cases:**
- "Does this response answer the user's question?"
- "Is this plan reasonable given constraints?"
- "Does this change match the briefing?"

**Execution contract:**
1. Build a constrained prompt with gate policy, extracted value, and required output schema.
2. Call the configured quality-tier model with low-variance settings (temperature 0, strict max tokens).
3. Parse structured output: `{score, flags, reason}` where `score ∈ [0.0, 1.0]` and `flags` is a list of advisory strings.
4. Return a quality-lane `GateResult` with `action="continue"`, `score`, `flags`, and `reason`.
5. On parse error or timeout: return `action="continue"` with `score=None`, `flags=["llm_error"]`, and log the failure to audit. Quality-lane gates fail open — a broken quality-model call does not block the turn.

**Promoted to policy (`promote_to_policy: true`):**
When a gate sets `promote_to_policy: true`, the LLM provider returns a full policy-lane result:
1. Parse structured output: `{action, reason, score}` where `action ∈ {continue, block, require_approval}`.
2. On parse error or timeout: fail closed to `block` (policy lane semantics).
3. The gate runner treats this as a policy-lane gate for evaluation ordering and enforcement.

**Security constraints:**
- By default, LLM gates are quality-lane only: they observe and score, they do not enforce.
- Policy-critical gates SHOULD use deterministic providers (`predicate`/`script`/guardrails presets). Use `promote_to_policy` only when no deterministic encoding is feasible.

#### 5.5.4 Script Provider

Runs custom shell scripts for domain-specific checks.

**CRITICAL SECURITY REQUIREMENT:** The script provider MUST sanitize all context values before interpolating them into shell commands. Every value from the context dictionary MUST be escaped using `shlex.quote()` before substitution. Naive string replacement (e.g., `command.replace(f"${key}", str(val))`) is a **command injection vulnerability** — a user-supplied value like `'; rm -rf / #'` would break out of the command.

The correct approach is one of:
- **Option A (preferred):** Pass context values as environment variables to the subprocess, not as command-line arguments. The script reads values from env vars instead of command-line parameters.
- **Option B:** Use `shlex.quote()` on every value before string substitution.
- **Option C:** Use a list-form command (no shell interpreter) where each argument is a separate element.

**Behavior after input sanitization:**

1. Execute the command via subprocess with a 30-second timeout in the gates working directory
2. Parse stdout for extracted values — each line with a colon separator is treated as `key: value`
   - If the script emits a reserved `modified_context` JSON line, parse and return it as `GateResult.modified_context`. The gate runner's allowlist enforcement (Section 5.4) applies — keys outside `ALLOWED_MUTATIONS` are stripped before merging.
3. If the gate has `check_expect`, evaluate it using the same predicate logic as the verification runner
4. If the gate has `extract`, look up the extracted value and delegate to the predicate provider for range/match evaluation
5. If neither, return `continue` with the script output

### 5.6 Access Controller

Manages tool access based on gate state. Fully deterministic — the LLM cannot influence access levels.

**State:** Current access level name (default `"public"`), set of verified gate names, customer context dict, timestamp when current level was granted.

**Owner bypass rule (mandatory):**
- The owner connection (`connection_id == owner_id`, or message taint verified as `owner`) bypasses goal-scoped `access_levels` and always receives full owner tool access.
- Goal `access_levels` apply to non-owner scoped connections only (chatbot/customer lanes).
- Deactivating/changing a goal resets only non-owner scoped controller state; owner access is unaffected.

**Level transition logic (`gate_passed`):**
1. Add the gate name to the verified set
2. For each defined access level, check if its `requires` gates are now all satisfied
3. If a higher level's requirements are met, transition to that level, record the timestamp, load customer context, and log the transition to audit (including customer ID if available, for GDPR compliance)

**Tool filtering (`get_allowed_tools`):**
1. Check if the current level has `expires_after` set and whether it has expired
2. If expired, drop back to `"public"` and clear the grant timestamp
3. Return the `tools` list for the current level

**Customer context loading:**
Customer context is loaded from a dedicated configurable directory (`customer_context_dir`) — NOT from a hardcoded `/tmp` path and NOT from `verify_dir`. The path MUST be inside `data_dir` and controlled by Silas. Directory permissions must prevent other system users from writing to it.

### 5.7 Context Manager

Manages the four-zone context window with harness-controlled eviction, observation masking, context subscriptions, and dynamic budget profiles. The agent has **no direct control** over context lifecycle — it can only request memory retrievals via `memory_queries`. All operations are per-`scope_id`; there is no shared mutable context between customer scopes.

**Design principles:**
- **Harness controls, agent requests.** All eviction, pinning, masking, and subscription lifecycle decisions are made by the harness. The agent sees metadata-tagged blocks and can request information, but cannot drop, pin, or summarize items.
- **Two-tier eviction.** Cheap heuristic rules handle the common case. A scorer model (lightweight quality-tier) handles the remaining ~20% of cases where heuristics aren't enough.
- **Observation masking over summarization.** Old tool results are replaced with short placeholders rather than LLM-generated summaries. This is cheaper, faster, and avoids the problem where summaries smooth over failure signals (JetBrains Research, 2025).
- **Context subscriptions.** Mutable resources (files, queries) are referenced rather than copied, keeping context fresh without duplication.
- **Skill metadata budget cap.** Unactivated skill metadata for routing is capped (`skill_metadata_budget_pct`) so large skill catalogs do not crowd out chronicle/workspace context.

**Zones and eviction policies:**
- **System zone**: Constitution, tool descriptions, personality directives. Hard cap at `system_max` tokens. Always pinned, never evicted.
- **Chronicle zone**: Conversation history. Sliding window — oldest non-pinned items evicted first. Tool results (`kind="tool_result"`) older than `observation_mask_after_turns` turns are observation-masked before eviction (content replaced with `"[Result of {tool_name} ({SUCCEEDED|FAILED}) — {token_count} tokens — see memory for details]"`). Short acknowledgments (< 20 tokens, matching patterns like "ok", "thanks", "got it") are dropped entirely during heuristic eviction.
- **Memory zone**: Retrieved memories. Lowest-relevance items evicted first (relevance scored by the retriever, not the agent). Items with `pinned=true` (e.g., user profile) are never evicted.
- **Workspace zone**: Active plans, execution results, materialized subscriptions. Completed plans evicted before active ones. Deactivated subscriptions cost zero tokens.

**Two-tier eviction algorithm (`enforce_budget`):**

Called once per turn (step 5). Receives `scope_id`, current `turn_number`, and an optional `current_goal` string (extracted from recent turns for scorer context).

**Tier 1 — Heuristic pre-filter (always runs, no model call):**

1. **Observation masking**: For all chronicle items with `kind="tool_result"` and `turn_number < current_turn - observation_mask_after_turns`, replace `content` with a placeholder and set `masked=true`. Recount tokens.
2. **Drop trivial messages**: Remove chronicle items with `kind="message"` that are < 20 tokens and match trivial-acknowledgment patterns (configurable regex, default: `^(ok|thanks|got it|sure|yes|no|👍)\s*[.!?]*$`).
3. **Deactivate stale subscriptions**: For all subscriptions where `active=true` and the agent has not referenced the subscription's target in any response for `subscription_ttl_turns` turns, set `active=false`. Token count drops to zero.
4. **Evict by zone policy**: If any zone still exceeds its budget, apply the zone-specific eviction policy (oldest-first for chronicle, lowest-relevance for memory, completed-before-active for workspace) until within budget.

If total usage is now below `eviction_threshold_pct`, stop. No model call needed.

**Tier 2 — Scorer model (runs only when Tier 1 is insufficient):**

If total usage remains above `eviction_threshold_pct` after Tier 1, invoke a lightweight quality-tier model to score context blocks by group relevance. This adds ~1-3 seconds of latency and costs fractions of a cent.

Scorer reliability constraints:
- Hard timeout: 2 seconds
- Circuit breaker: open after 3 consecutive scorer failures/timeouts, cool-down 5 minutes
- Failover: if timed out/failed or breaker is open, apply deterministic aggressive heuristic eviction (oldest chronicle + lowest-relevance memory + completed workspace) without model calls
- Scorer structured-output policy: scorer calls MUST use PydanticAI structured output (`output_type=ScorerOutput`) via `run_structured_agent` (Section 5.1.0). Raw string-to-JSON parsing is forbidden.

Scorer output models (required):

```python
class ScorerGroup(BaseModel):
    reason: str
    block_ids: list[str]

class ScorerOutput(BaseModel):
    keep_groups: list[ScorerGroup]
    evict_groups: list[ScorerGroup]
```

Scorer invocation pattern (required):

```python
scorer_agent = Agent(models.scorer, output_type=ScorerOutput)
scorer_result = await run_structured_agent(scorer_agent, scorer_prompt, call_name="scorer")
```

Scorer prompt:
```
You are a context relevance scorer. Given the current conversation goal
and recent turns, identify which context groups are least valuable.

Current goal: {current_goal}
Recent turns (last 2-3): {recent_turns}

Context blocks to evaluate:
{blocks_with_ids_and_metadata}

Output two lists (schema-enforced by `ScorerOutput`):
- "keep_groups": [{"reason": "...", "block_ids": ["...", ...]}, ...]
- "evict_groups": [{"reason": "...", "block_ids": ["...", ...]}, ...]

Group related blocks together. A block that gives meaning to other blocks
should stay with them. Prefer evicting coherent groups over orphaning blocks.
```

The scorer outputs **eviction groups** — coherent sets of blocks to evict together, preventing orphaned references. The harness evicts the lowest-priority groups until usage drops below `eviction_threshold_pct`.

For each evicted item, store its content to memory (as an `episode` with `trust_level="working"` and preserved taint) before discarding. External-tainted content must be injection-scrubbed before persistence. This ensures nothing is permanently lost — only moved from fast context to queryable memory.

**Rendering (`render`):**

The `render(scope_id, turn_number)` method:

1. **Materialize active subscriptions**: For each active `ContextSubscription`, resolve the reference (read file, execute query). If the content has changed since last materialization (different `content_hash`), update the `ContextItem`. Use cached materialization within the same turn.
2. **Apply observation masking**: Ensure all tool results beyond the masking threshold are masked.
3. **Concatenate zones**: Ordered system → chronicle → memory → workspace. This preserves recent conversational grounding before long-term recall and keeps active workspace at the end (recency benefit).
4. **Render metadata tags**: Each `ContextItem` is rendered with a delimiter that communicates provenance, kind, freshness, and status (see ContextItem rendering format in Section 3.5).

**Profile switching (`set_profile`):**

When the Proxy routing decision classifies an interaction type, the harness calls `set_profile(scope_id, profile_name)` to adjust zone budget allocations for that scope only. If a zone shrinks below its current usage, the next `enforce_budget` call will evict to fit. If a zone grows, previously tight items get breathing room. Profile switches are logged to audit.

### 5.8 Key Manager (Ed25519)

Manages the Ed25519 keypair used for cryptographic approval.

Ed25519 is the security boundary. All approvals use `tap` strength.

**Key generation (`generate_keypair`):**
1. Generate an Ed25519 keypair using the `cryptography` library (Ed25519)
2. Store the private (signing) key in the OS keyring via the `keyring` library, keyed by the owner ID
3. Return the public (verify) key as a hex-encoded string

**Signing (`sign`):**
1. Load the private key from the OS keyring
2. Sign the canonical bytes of the payload
3. Return the raw signature bytes

**Verification (`verify`):**
1. Load the public key (stored alongside config or in the keyring)
2. Verify the signature over the canonical bytes
3. Return `(True, "Valid")` or `(False, reason)`

The private key NEVER appears in LLM context, log files, config files, or environment variables. It exists only in the OS keyring.

### 5.9 Confidence + Autonomy Calibration

Confidence influences visibility and escalation, but never bypasses policy gates or approval boundaries.

**Execution order invariant:** policy-lane gates -> approval checks -> confidence policy -> autonomy calibration proposals.

| Confidence band | Silas behavior | User-visible surface |
|---|---|---|
| `high` (>= `batch_review.confidence.high_min`) | Adds to reviewed batch proposal for autonomous-safe domain action | Batch review card |
| `medium` (>= `batch_review.confidence.medium_min` and below high) | Prefer draft/review path (no destructive auto-action) | Draft review card |
| `low` (< medium) | Escalates to Planner and/or user | Attention card with context + alternatives |
| `novel` (no matching rule/pattern) | Requests teach/decision | Teaching card with option chips |

**Explicit autonomy widening keys (no implicit trust growth):**
- Standing approval scope (`archive` -> `archive+label`, etc.)
- Standing approval `max_executions`
- Standing approval expiry window (`expires_at` policy)
- `batch_review.default_size`
- `batch_review.confidence.high_min` (lower means more items qualify for high-confidence automation)

**Proposal trigger policy:**
1. Track correction rate per action family: `(edit_selection + decline + undo) / total_actions`.
2. Require minimum sample size over rolling window before evaluating widen/tighten proposals.
3. If correction rate is below widen threshold for the window, emit a widening `AutonomyThresholdProposal`.
4. If correction rate exceeds tighten threshold, emit tightening proposal (or immediate safe tighten if configured).
5. All changes require explicit card decision, are audit-logged, and are reversible via single-tap `tighten approvals`.

**Rule-learning guardrail:** User feedback may update transient handling immediately for the current item, but persistent policy updates require explicit threshold-change approval.

### 5.10 Connection Lifecycle

Connections are skills (§10.6). Every step invokes scripts from the connection skill's `scripts/` directory via the `ConnectionManager` (§4.19). The lifecycle covers setup, monitoring, permission escalation, and failure recovery.

#### 5.10.1 Setup Conversation Protocol

Connection setup is an interactive, multi-step flow. The skill's `setup.py` script yields `SetupStep` objects (§3.12); the `ConnectionManager.run_setup_flow()` relays each step to the channel, which renders it as a card.

**Script communication protocol (normative, subprocess IPC):**
- Connection scripts are executed as subprocesses by `ConnectionManager` with UTF-8, line-buffered stdin/stdout.
- Protocol framing is **NDJSON** (one JSON object per line). No multi-line JSON payloads.
- `stderr` is for diagnostics only and is never parsed as protocol data.
- `discover.py` is request/response: manager writes one request line, script writes one response line, process exits.
- `setup.py` is streaming: script emits step events incrementally; manager replies with user decisions as step-result events.

`setup.py` NDJSON event types:
- Manager → script:
  - `{"type":"start","auth_strategy":"...","initial_permissions":[...],"incremental_scopes":[...],"identity_hint":{...}}`
  - `{"type":"step_result","step_id":"...","payload":{...}}`
  - `{"type":"cancel","reason":"user_cancelled"}`
- Script → manager:
  - `{"type":"setup_step","step_id":"...","step":<SetupStep JSON>}`  (render this card)
  - `{"type":"await_input","step_id":"..."}`  (script is blocked until matching `step_result`)
  - `{"type":"completion","connection_payload":{...}}`
  - `{"type":"failure","failure":<ConnectionFailure JSON>}`
  - `{"type":"log","level":"info|warning","message":"..."}` (optional, non-authoritative)

`setup.py` "yields SetupStep" means logical yield over this NDJSON protocol, not Python generator state shared across processes.

**Full setup sequence:**

1. **Discovery** — `ConnectionManager.discover_connection()` runs the skill's `discover.py`, which returns the `AuthStrategy`, provider info, initial permissions to request, and setup requirements (whether a browser is needed, whether secure input is needed, whether app registration is required).

2. **Approval** — The Stream presents a connection approval card: "Connect to [service]? [permissions description]". Risk level: `medium` for read-only, `high` for write access. Approval creates a token with scope `connection_manage`.

3. **Interactive auth flow** — `ConnectionManager.run_setup_flow()` invokes the skill's `setup.py`, which yields setup steps based on the `AuthStrategy`:

   **Device code** (Microsoft, GitHub):
   ```
   setup.py yields DeviceCodeStep(verification_url, user_code, expires_in, poll_interval)
   → Channel renders DeviceCodeCard: shows code + link + countdown
   → setup.py polls token endpoint in background
   → On success: yields CompletionStep
   → On timeout: yields failure SetupStep with retry option
   ```

   **Browser redirect** (Spotify, Notion OAuth):
   ```
   setup.py starts localhost server on 127.0.0.1 (dynamic port)
   setup.py yields BrowserRedirectStep(auth_url, listening_on)
   → Channel renders BrowserRedirectCard, opens user's browser
   → Localhost server catches redirect with auth code
   → setup.py exchanges code for tokens
   → On success: yields CompletionStep
   ```

   **Secure input** (Notion internal token, GitHub PAT, any API key):
   ```
   setup.py yields SecureInputStep(request=SecureInputRequest(ref_id, label, guidance))
   → Channel renders SecureInputCard
   → Secret goes: user input → channel's secure form → POST /secrets/{ref_id} → OS keyring
   → Channel sends SecureInputCompleted(ref_id, success) to ConnectionManager
   → setup.py verifies the stored credential works (via keyring ref_id)
   → On success: yields CompletionStep
   ```

   The secret NEVER enters the WebSocket, agent context, or audit log. See secret isolation rule (§0.5).

4. **Probe** — On `CompletionStep`, the ConnectionManager runs the skill's `probe.py` to verify the connection works with a real API call (e.g., fetch inbox count, list repos, get user profile). The probe result is included in the completion summary shown to the user.

5. **Registration** — Create the `Connection` record in the registry with `status=active`, `permissions_granted` from the completion step, `initial_permissions` snapshot, and token expiry metadata. Register the health check cron job. Schedule the first proactive refresh if `token_expires_at` is known.

**User cancellation:** The user can tap `[Cancel]` on any setup step. The ConnectionManager cleans up (removes partial credentials from keyring, deletes localhost server, cancels polling) and sets connection status to `proposed`.

**`/secrets/{ref_id}` endpoint:** A dedicated HTTPS endpoint in `silas/web/` that accepts credential values and writes them directly to the OS keyring. This endpoint:
- Accepts `POST` with body `{"value": "<secret>"}` and writes to keyring keyed by `ref_id`
- Returns only `{"stored": true}` — no echo, no hash, no derived data
- Is NOT part of the WebSocket protocol — it is a separate HTTP request
- Logs `"secret_stored"` event to audit with `ref_id` only (NEVER the value)
- Rejects requests where `ref_id` does not match a pending `SecureInputRequest`

#### 5.10.2 Incremental Permission Model

Connections start with minimum viable permissions and widen through the approval system.

**Initial setup:** The skill's SKILL.md frontmatter declares `initial_permissions` (the minimum set requested at first setup) and `available_permissions` (the full catalog of requestable permissions). The first setup requests only `initial_permissions`.

**Permission escalation flow:**

1. During execution, a gate or the executor detects that an action requires a permission the connection doesn't have (e.g., action needs `Mail.ReadWrite` but only `Mail.Read` is granted).
2. The `ConnectionManager.escalate_permission()` method renders a `PermissionEscalationCard` via the channel:
   - Shows: connection name, current permissions, requested permissions, reason for escalation
   - Risk level: `medium` for read→write within the same resource type, `high` for new resource types
   - CTAs: `[Approve]` `[Just this once]` `[Deny]`
3. `[Approve]`: The ConnectionManager runs a re-auth flow — the skill's `setup.py` with an `incremental_scopes` parameter. For OAuth services, this triggers a new authorization request with additional scopes (OAuth supports additive scope requests natively). The connection record's `permissions_granted` is updated. A `connection_manage` approval token is logged to audit.
4. `[Just this once]`: The escalated permission is granted for this execution only, via a single-use approval token with `max_executions=1`. The connection record is NOT updated.
5. `[Deny]`: The action that triggered the escalation fails with a clear error. The executor may consult the Planner for an alternative approach that doesn't require the denied permission.

**Gate integration:** A `connection_permission` policy-lane gate check fires before execution when the action's required permissions (declared by the skill script) exceed `permissions_granted`. This gate triggers the escalation flow above.

#### 5.10.3 Proactive Token Refresh

Health checks are not just connectivity probes — they return structured `HealthCheckResult` data (§3.12) including token expiry information.

**Refresh scheduling (after each health check):**

1. If `token_expires_at` is within `refresh_ahead_window` (default: 10 minutes before expiry), immediately run the skill's `refresh_token.py`. Update `token_expires_at` and `last_refresh` on the Connection record. This is transparent — no user involvement.
2. If `refresh_token_expires_at` is within 7 days and the connection has been used in the past 30 days, surface a low-friction suggestion card: "Your [service] refresh token expires in [N] days. [Reconnect now] [Remind me later]". Re-authentication follows the full setup conversation protocol (§5.10.1).
3. If `refresh_token_expires_at` is within 7 days and the connection has NOT been used in 30 days, proactively refresh the token to prevent the sliding window from expiring. Log this to audit as `"proactive_refresh_dormant"`.
4. Schedule the next refresh check based on `token_expires_at - refresh_ahead_window`.

**Startup behavior:** When `Stream.start()` runs connection health checks (startup step 3), it also calls `schedule_proactive_refresh()` for each active connection that reports token expiry data. This ensures refresh jobs are registered immediately, not deferred to the next cron-triggered health check.

**Example: Microsoft Outlook (1hr access token, 90-day refresh token):**
- Health check runs every 30 minutes (configurable per connection)
- Access token auto-refreshes ~50 minutes after last refresh (10 min before expiry) — transparent, no user involvement
- Refresh token sliding window maintained by regular access token refreshes
- If connection goes dormant for 83+ days, user gets a reconnection suggestion

**Example: Notion internal token (never expires):**
- Health check runs, `token_expires_at` is `None` → no refresh scheduling needed
- Health check still verifies the token works (API probe) and measures latency

#### 5.10.4 Connection Failure Recovery

When a connection setup or health check fails, the connection skill's script returns a structured `ConnectionFailure` (§3.12). The ConnectionManager maps it to recovery options and renders a `ConnectionFailureCard`.

**Failure type → recovery options:**

| Failure type | Card message | Recovery options |
|---|---|---|
| `enterprise_policy_block` | "Your organization's IT policy prevents personal apps from accessing [service]." | `[Draft IT request]` `[Use personal account]` `[Skip]` |
| `consent_denied` | "You declined the authorization. [Service] wasn't connected." | `[Retry]` `[Retry with fewer permissions]` `[Skip]` |
| `mfa_required` | "[Service] requires multi-factor authentication to complete setup." | `[Retry]` (user completes MFA) `[Skip]` |
| `token_revoked` | "Your [service] access was revoked (changed password, admin action, or expired)." | `[Reconnect]` `[Skip]` |
| `rate_limited` | "[Service] is temporarily limiting requests. Try again in [time]." | `[Retry in N minutes]` `[Skip]` |
| `service_unavailable` | "[Service] is not responding." | `[Retry later]` `[Skip]` |

**"Draft IT request" flow:** When the user taps `[Draft IT request]` for an `enterprise_policy_block`, the ConnectionManager creates a work item that generates an email template containing:
- The Silas app registration details (client ID, redirect URIs)
- The specific permissions requested and why each is needed
- A link to the admin consent page (for Microsoft: `https://login.microsoftonline.com/{tenant}/adminconsent?client_id=...`)

This work item follows the standard plan→approve→execute flow. The draft is presented for user review before any action.

**Auto-recovery during health checks:** When a scheduled health check returns an unhealthy `HealthCheckResult`:
1. If `error` indicates an expired token: run `refresh_token.py`. If refresh succeeds, done.
2. If refresh fails or error is not token-related: run the skill's `recover.py` script, which returns either success or a `ConnectionFailure`.
3. If recovery fails: surface a `ConnectionFailureCard` to the user. Set connection status to `failed`. Log to audit.
4. Do NOT retry automatically more than once — repeated failures surface to the user, never loop silently.

**Safety constraints:**
- Credential and token handling is scoped to execution envelopes and the OS keyring — never exposed to agent context (§0.5 secret isolation rule)
- Any scope expansion (`observe` → `act`/`manage`) requires fresh approval via the permission escalation flow (§5.10.2)
- Destructive domain actions are rendered as high-risk cards with `tap → slide → biometric/local strong auth` confirmation ladder (§0.5.2); cryptographic approval strength remains `tap` in MVP

### 5.11 Approval Engine

Manages the full lifecycle of approval tokens.

**`async issue_token(work_item, decision, scope=full_plan)` — token minting (after user approves):**
1. Compute SHA-256 hash of the work item's canonical immutable approval projection (`work_item_plan_hash_bytes`, Section 3.3)
2. Generate a cryptographic nonce
3. Resolve required minimum `approval_strength` from approval scope policy (`tap` for all scopes)
4. Build `approval_strength` by applying policy minimums to `decision.approval_strength` using deterministic tier ordering (`tap < biometric < biometric_confirm`)
5. For `scope == standing`, compute and embed `conditions.spawn_policy_hash` using the canonicalization algorithm in Section 3.6 (template hash over `failure_context_template`, normalized `skills`, normalized `gates`, normalized `verify`, normalized allowed escalation config); reject issuance if missing.
6. Create an `ApprovalToken` with plan hash, scope, verdict, nonce, `approval_strength`, and timestamps
7. Sign the token's canonical bytes with the owner's Ed25519 key
8. Return the signed token

**`async verify(token, work_item, spawned_task=None)` — consuming verification (before authorizing execution):**
1. Verify the Ed25519 signature over the token's canonical bytes (using the token-level `nonce` as part of the signed payload)
2. **Plan hash binding:**
   - For single-use tokens (`scope != "standing"`, `spawned_task` is `None`): verify the token's `plan_hash` matches `sha256(work_item_plan_hash_bytes(work_item))`. This ensures the exact approved plan is what runs.
   - For standing tokens (`scope == "standing"`, `spawned_task` provided): verify the token's `plan_hash` matches `sha256(work_item_plan_hash_bytes(work_item))`, where `work_item` is the parent goal. Then verify `spawned_task.parent == token.work_item_id` and `sha256(work_item_plan_hash_bytes(spawned_task))` matches the deterministic spawn policy bound by `conditions.spawn_policy_hash`. If `spawned_task` is `None` for a standing token, return `(False, "Standing verification requires spawned_task")`.
3. Verify the token has not expired
4. Verify `executions_used < max_executions`
5. Generate a fresh execution nonce (cryptographic random string)
6. Verify the execution nonce has not been used before via `nonce_store.is_used("exec", nonce)` (replay protection)
7. Bind nonce recording to task content by recording `exec_binding = "{token_id}:{spawned_task_hash_or_plan_hash}:{nonce}"` in the nonce store. This prevents replay across different spawned tasks.
8. If all checks pass, append the execution nonce to `execution_nonces`, increment `executions_used`, and persist token state
9. Return `(True, "Valid")` or `(False, reason)`

**`async check(token, work_item)` — non-consuming validation (before entering execution loop):**
1. Verify the Ed25519 signature over the token's canonical bytes
2. **Plan hash binding:**
   - For single-use tokens: verify the token's `plan_hash` matches `sha256(work_item_plan_hash_bytes(work_item))`.
   - For standing tokens: verify `work_item.parent == token.work_item_id` (the spawned task is a child of the authorized goal) and re-check spawned-task policy binding against `conditions.spawn_policy_hash`.
3. Verify the token has not expired
4. Verify `1 <= executions_used <= max_executions` (a token must have already passed a consuming `verify()` before `check()` can succeed)
5. Do NOT generate or consume an execution nonce — this was already done by `verify()`.
6. Return `(True, "Valid")` or `(False, reason)`

**Call-site summary (`verify()` authorization sites, `check()` execution-entry site):**
- `verify()` is called at **5.1.2 step 6** (interactive approval of single-use plan tokens), **5.2.3 step 4** (standing or fallback interactive approval of goal-spawned tasks), and **5.1.6 autonomy-threshold apply path** (proposal payload token verification before applying config deltas). `verify()` is the only method that mutates token state (increments `executions_used`, consumes a nonce).
- `check()` is called at **5.2.1 step 0** (execution entry gate) for execution-bound tokens. It confirms the already-verified token is still valid without consuming again.

### 5.12 Taint Tracker

Propagates trust classification through the system.

- Messages signed by the owner's Ed25519 key: `owner` taint
- Messages from authenticated channel identities (e.g., Telegram with verified chat ID): `auth` taint
- All other messages: `external` taint
- Execution/tool outputs default to `external` taint unless provenance is fully trusted and network-free
- `web_search` outputs are always `external` taint
- `ExecutionResult.return_value` and `ExecutionResult.content` from API calls, web fetches, or third-party tools MUST remain `external` and pass through prompt-injection scrubbing before entering context or memory
- `ExecutionResult.metadata` MUST NOT be injected into model context, regardless of taint
- Memory items inherit the taint of the message that created them
- When external-tainted data influences a memory item, the item's taint is downgraded to `external`
- Constitutional memories cannot be created or modified from external-tainted input
- External-tainted content cannot be persisted as `verified`/`constitutional` without explicit trusted confirmation

### 5.13 Token Counter

Heuristic token counter used by the context manager to enforce budgets. Zero external dependencies.

Provides:
- `count(text: str) → int` — count tokens in a string using `int(len(text) / 3.5)` (characters ÷ 3.5)
- Approximation error is absorbed by the required 20% headroom in context profiles
- Exact pre-counting integration is tracked in the roadmap appendix (`specs/operations-roadmap.md#18-roadmap`).

For testing, a `FakeTokenCounter` that counts words (splitting on whitespace) enables fast unit tests.

### 5.14 Personality Engine

Implements contextual personality shaping as deterministic runtime logic.

**Responsibilities:**

1. Detect turn context key (open string mapped via `personality.contexts`)
2. Compose effective axes from baseline + context + mood + overrides
3. Render prose directives (200–400 tokens) for system-zone injection
4. Apply post-turn events and decay mood toward neutral
5. Persist state/events to SQLite through `PersonaStore`

**Deterministic composition rule:**

`effective = clamp(baseline + context_delta + mood_delta + user_override, 0.0, 1.0)`

Where:
- `baseline` is stable per scope
- `context_delta` comes from detected context profile
- `mood_delta` is derived from mood state
- `user_override` comes from explicit user tuning or preset selection

**Mood update model:**

- Apply bounded per-event deltas (clamped each turn; no unbounded jumps)
- Event definitions may include both `mood` deltas and `axes` deltas (for example `feedback_too_harsh -> axes.assertiveness -0.05`)
- Apply time-based decay toward 0.5 per mood axis using `decay_rate_per_hour`
- Persist every event in append-only `persona_events` for observability and replay

**Trust boundary for feedback:**

- Trusted sources (`owner` signature or authenticated owner channel) may apply baseline drift
- Untrusted sources may affect transient mood only and MUST be rate-limited

**Failure behavior:**

If context detection or directive rendering fails, Stream MUST fail open to neutral style (`default` context, baseline axes, no mood delta) and continue execution. Personality failure MUST NOT block turn processing.

### 5.15 Personality Persistence (SQLite)

Personality persistence uses two tables from day one (no new dependencies, SQLite only):

1. **`persona_state`** (one row per `scope_id`)
   - `scope_id` TEXT PRIMARY KEY
   - `baseline_axes_json` TEXT NOT NULL
   - `mood_json` TEXT NOT NULL
   - `voice_json` TEXT NOT NULL
   - `active_preset` TEXT NOT NULL
   - `last_context` TEXT NOT NULL
   - `updated_at` TEXT NOT NULL

2. **`persona_events`** (append-only event log)
   - `event_id` TEXT PRIMARY KEY
   - `scope_id` TEXT NOT NULL
   - `event_type` TEXT NOT NULL
   - `trusted` INTEGER NOT NULL
   - `delta_axes_json` TEXT NOT NULL
   - `delta_mood_json` TEXT NOT NULL
   - `source` TEXT NOT NULL
   - `created_at` TEXT NOT NULL
   - Indexes on `(scope_id, created_at DESC)` and `(event_type, created_at DESC)`

Serialization rules:
- Use `model_dump(mode="json")` for Pydantic fields (`AxisProfile`, `MoodState`, `VoiceConfig`)
- Store datetimes in ISO-8601 UTC format
- Preserve append-only semantics for `persona_events` (no updates/deletes in normal operation)

---

## 6. Memory Implementation

### 6.1 SQLite Store

All memory is stored in a single SQLite database. Core capabilities in this spec:

- FTS5 + standard SQL (keyword retrieval, durable CRUD)
- sqlite-vec + embeddings (semantic retrieval when vector mode is enabled)
- temporal/session retrieval over the same schema

**Schema** (`migrations/001_initial.sql` for baseline + `migrations/002_vector.sql` when vector search is enabled):

The `memory_items` table stores all fields from `MemoryItem`. Key indexes:
- Primary key on `memory_id`
- FTS5 virtual table on `content` and `semantic_tags` for keyword search using explicit tokenizer config (`tokenize='porter unicode61 tokenchars \"_-\"'`)
- sqlite-vec virtual table on `embedding` column for vector similarity (enabled when vector mode is configured)
- Index on `created_at` for temporal queries
- Index on `session_id` for session queries
- Indexes on `entity_refs` and `causal_refs` (stored as JSON arrays) for incremental graph traversal features

Low-reingestion raw lane:
- Raw conversation text, research queries, and generic external memory inputs are stored durably with `reingestion_tier = low_reingestion`
- Raw lane entries are never auto-injected into context; they are queried explicitly (`search_raw`) or reintroduced by controlled replay/import
- This preserves full history without contaminating active context ranking

**SQLite runtime requirements:**
- Enable WAL mode on startup: `PRAGMA journal_mode=WAL;`
- Set `PRAGMA synchronous=NORMAL;` and `PRAGMA busy_timeout=5000;` for practical concurrency
- Apply deterministic migrations through a migration runner with an `applied_migrations` tracking table (id, checksum, applied_at)
- Startup must fail if a migration checksum mismatch is detected (no silent drift)

**Migration strategy (normative):**
1. Migration files are sequentially numbered with zero-padded prefixes (`001_*.sql`, `002_*.sql`, `003_*.sql`, ...). Numbers are strictly increasing and immutable once released.
2. Each migration MUST be idempotent (`IF NOT EXISTS`, guarded data backfills, safe re-run semantics).
3. `silas start` MUST run all pending migrations before Stream startup; if any migration fails, startup aborts.
4. `applied_migrations` is the source of truth for schema version state (`id`, `checksum`, `applied_at`); pending set = files on disk minus applied IDs.
5. Upgrades are forward-only in place for MVP. If a required migration file is missing or checksum differs from recorded state, fail closed and require operator intervention (no implicit repair).

### 6.2 Embedder

Wraps `fastembed` (ONNX backend) for local embedding generation. Default model: `all-MiniLM-L6-v2` (384 dimensions). This component is optional and used when semantic retrieval is enabled.

Provides:
- `embed(text: str) → list[float]` — embed a single text
- `embed_batch(texts: list[str]) → list[list[float]]` — batch embedding

The embedder is used by:
- The Stream (step 4 semantic branch, when vector retrieval is enabled)
- The memory store (when storing new items with embeddings)
- The consolidator (when re-embedding after content changes)

**Note on dependency weight:** `fastembed` keeps the local embedding stack lightweight (roughly ~100MB class footprint instead of multi-GB PyTorch installs). The `Embedder` protocol still allows swapping in API-based embeddings if needed.

### 6.3 Multi-Graph Retriever

Orchestrates supported search strategies and merges results:

1. keyword retrieval via FTS5 (+ optional session lookup)
2. semantic search — embed the query, find nearest neighbors via sqlite-vec
3. temporal search — find memories within a time window relative to query context

The retriever accepts a `MemoryQuery` (with strategy and parameters) and delegates to the appropriate SQLite store method. Additional strategy expansion is tracked in the roadmap appendix (`specs/operations-roadmap.md#18-roadmap`).

### 6.4 Memory Consolidator

Background process that runs periodically (configurable interval, default 30 minutes):

1. Find working-trust memories that have been accessed frequently
2. Merge duplicates (same content, different sessions)
3. Promote frequently-validated working memories to verified (with owner confirmation)
4. Prune stale memories that haven't been accessed in a configurable period
5. Re-embed memories whose content has been updated

### 6.5 Memory Portability and Reingestion

Memory must be portable across systems:

1. Export all memory (including low-reingestion raw lane when requested) as a canonical versioned bundle.
2. Bundle format is newline-delimited JSON (`jsonl`) with:
   - header record (`bundle_version`, `exported_at`, `schema_version`)
   - memory records (`MemoryItem` canonical JSON)
3. Import supports `merge` (upsert by `memory_id`) and `replace` (transactional swap).
4. Import preserves `taint`, `trust_level`, and `reingestion_tier`.
5. Import/export APIs are protocol-level (`MemoryPortability`) so another memory backend can be plugged in without changing Stream logic.

### 6.6 Behavioral Preference Layer

Behavioral preference inference is persisted in memory, not transient runtime state.

1. Ingest behavior signals from approval/batch/undo/suggestion outcomes (`approved`, `declined`, `edit_selection`, `undo`, `dismissed`).
2. Convert repeated patterns into `MemoryItem(memory_type="preference", trust_level="working", semantic_tags=["behavior:..."])`.
3. Use explicit review cards to promote preferences to `trust_level="verified"` (for example: "You usually want sources included. Make this default?").
4. Planner and Proxy consume verified behavioral preferences as defaults for:
   - action bias (archive vs defer, etc.)
   - response style requirements (include sources, shorter answers, deeper first pass)
   - depth defaults for recurring domains
5. Constitutional and security policies always take precedence over behavioral preferences.

---

## 7. Agents (PydanticAI)

All agents are defined as PydanticAI `Agent` instances. This provides: structured output via Pydantic models, composable toolset wrappers (`SkillToolset`, `PreparedToolset`, `FilteredToolset`, `ApprovalRequiredToolset`, optional dynamic wrappers; mapping in Section 4.24), usage tracking, approval-paused tools, and `TestModel` for testing without LLM calls.

Tool/skill boundary (enforced):
- **Tools** are cognition primitives (retrieve information, inspect state, execute generic commands).
- **Skills** are domain capabilities/integrations (service-specific operations, workflows, business logic).
- `web_search` is a core tool, not a skill.

### 7.1 Proxy Agent

- **Model:** `models.proxy` (proxy-tier, cheap/fast)
- **Output type:** `RouteDecision`
- **Purpose:** Classify every incoming message as direct-response or needs-planner, and output turn control signals (`interaction_register`, `interaction_mode`, `context_profile`, optional `continuation_of`).
- **Tools:** Access to memory search (read-only), context inspection, and core `web_search` (when configured) via the wrapper chain `SkillToolset -> PreparedToolset -> FilteredToolset`
- **System prompt:** Includes the constitution, current access level, available tools, routing guidelines, and skill metadata (`name` + `description` from the active work item's skills — see step 6.5)

The Proxy handles ~80% of messages directly (greetings, simple questions, factual lookups). Only complex tasks, ambiguous requests, and work that requires planning get routed to the Planner.

Proxy MUST detect deepening/correction intents (for example "go deeper", "use another approach", "expand section 2") and set `continuation_of` when a prior work item is the target.

### 7.2 Planner Agent

- **Model:** `models.planner` (planner-tier, stronger reasoning)
- **Output type:** `AgentResponse` (with `plan_action` populated)
- **Purpose:** Analyze complex requests, create detailed prose plans with YAML front matter, reason about verification strategies, and enforce "default-and-offer" behavior below confirmation thresholds. Selects skills for work items from the available catalog and may propose new skill creation when no existing skill fits.
- **Tools:** Memory search, context inspection, and core `web_search` (if configured) via prepared/filtered toolsets
- **System prompt:** Includes the constitution, plan output format specification, current context, planning guidelines, and full SKILL.md instructions for the active work item's skills (see step 6.5)

Planner directive (required):
- If multiple valid approaches exist and risk/policy does not require confirmation, choose the best default, proceed, and present alternatives in output.
- Ask the user before proceeding only when the missing decision materially changes outcomes, or when risk/policy requires explicit confirmation.

When creating a plan, the Planner outputs it as the `plan_action.plan_markdown` field. The plan format is:

```
---
id: task-{uuid}
type: task
title: {descriptive title}
interaction_mode: act_and_report
skills: [coding]
budget: { max_tokens: 200000, max_cost_usd: 2.00, max_wall_time_seconds: 1800 }
verify:
  - name: {check_name}
    run: "{shell command}"
    expect: { exit_code: 0 }
on_stuck: consult_planner
---

# Context
{Why we're doing this, background information}

# What to do
{Step by step prose instructions for the executor}

# Constraints
{What NOT to do, boundaries}

# If you get stuck
{Escalation guidance}
```

For deepening/correction turns, Planner MUST set `plan_action.continuation_of` to the prior work item ID so artifact inheritance is deterministic.

The `skills` field lists the skill names available to the executor during this work item. The plan parser resolves skill names to directories via the skill loader and includes them in the parsed `WorkItem.skills`. If omitted, the work item inherits skills from its parent (goal or project). If no parent exists, the skills list is empty.

The Planner is the SOTA reasoning model. It writes the briefing. The executor (a cheaper model) reads it and works. The runtime verifies externally and retries within budget.

### 7.3 Executor Agent

- **Model:** `models.executor` (executor-tier, cost-optimized)
- **Output type:** `ExecutorAgentOutput` (LLM-producible schema: action summary, optional artifact refs, ordered next-step suggestions)
- **Purpose:** Execute the prose briefing from the Planner inside the configured sandbox backend
- **Tools:** Core harness primitives (`shell_exec`, `python_exec`, `web_search`) exposed via `SkillToolset -> PreparedToolset -> FilteredToolset`. Approval-sensitive tool calls use `ApprovalRequiredToolset`, which emits `ApprovalPausedToolRequests` for runtime approval resolution. Skill scripts are invoked via `run_python` with paths resolved from the work item's `skills` list.
- **Instructions:** Dynamically set from the work item's `body` field (the prose briefing), with full SKILL.md instructions for each skill in the work item's `skills` list appended to the workspace context (see step 6.5)

PydanticAI's `UsageLimits` enforces token and request limits at the framework level, complementing Silas's own budget tracking.

`ExecutionResult` remains the executor protocol return type. The runtime keeps `metadata` application-only and injects only `return_value`/`content` into model context. Runtime metrics are never requested from the LLM schema.

### 7.4 Agent Testing

PydanticAI's `TestModel` enables unit testing of all agent logic without LLM API calls. Tests define expected responses, and the TestModel returns them deterministically. This is used for:

- Testing the Proxy's routing logic
- Testing the Planner's plan structure validation
- Testing tool dispatch and error handling
- Testing the full turn processing pipeline in integration tests

---

## 8. Channel Implementation

### 8.1 Web Channel (Primary)

FastAPI application serving a chat UI over HTTP and real-time messaging via WebSocket. This is the primary interface for Silas.

**Security defaults:**
- Binds to `127.0.0.1` (loopback only) by default
- Remote access requires explicit `--host 0.0.0.0` flag AND a configured `auth_token`
- WebSocket authentication MUST NOT use URL query parameters. Use either `Sec-WebSocket-Protocol` bearer token or first-message auth (`{"type":"auth","token":"..."}`) with a 5-second auth timeout. Unauthorized connections are closed with code 4001.

**Connection model:**
The web channel supports multiple concurrent WebSocket connections. Each connection gets:
- A unique session ID
- Its own `AccessController` instance (isolated access levels per session)
- Its own `scope_id` and `TurnProcessor` context partition (chronicle/memory/workspace isolation)
- Its own pending-response tracking for approval/gate/card flows

This is essential for chatbot deployment mode where multiple customers connect simultaneously.

**Message protocol (JSON over WebSocket):**

Client → Server:

| `type` | Fields | Description |
|---|---|---|
| `auth` | `token` | Authenticate connection (required when `auth_token` is configured and auth not provided in subprotocol header) |
| `message` | `text`, `sender_id` (optional) | User chat message |
| `approval_response` | `request_id`, `verdict` | Response to a plan approval request |
| `gate_response` | `request_id`, `verdict` | Response to a gate approval request |
| `checkpoint` | `request_id`, `verdict` | Response to a checkpoint |
| `batch_review_response` | `request_id`, `verdict`, `selected_item_ids` | Response to a reviewed batch card |
| `draft_review_response` | `request_id`, `verdict`, `edited_text` | Response to a draft review card |
| `decision_response` | `request_id`, `selected_value`, `freetext` | Response to a decision card |
| `suggestion_response` | `request_id`, `selected_value`, `freetext` | Response to a proactive suggestion card |
| `autonomy_threshold_response` | `request_id`, `verdict` | Response to an autonomy-threshold proposal card |
| `secure_input_completed` | `ref_id`, `success` | Confirms a secret was stored via `/secrets/{ref_id}`. NEVER contains the secret value. |
| `connection_setup_response` | `request_id`, `step_type`, `action` | Response to a connection setup step card. `action`: `done`, `cancel`, `trouble`, `retry`. |
| `permission_escalation_response` | `request_id`, `verdict` | Response to a permission escalation card. `verdict`: `approve`, `just_this_once`, `deny`. |
| `connection_failure_response` | `request_id`, `selected_action` | Response to a connection failure card. `selected_action` is the machine-readable `RecoveryOption.action` key. |

`batch_review_response` validation rule: `selected_item_ids` is required when `verdict == "edit_selection"` and must be omitted or empty for `approve`/`decline`.

`autonomy_threshold_response` validation rule: `verdict` must be one of `approve`, `decline`, `tighten_now`.

Server → Client:

| `type` | Fields | Description |
|---|---|---|
| `message` | `text`, `sender`, `timestamp` | Agent response |
| `approval_request` | `request_id`, `title`, `body`, `budget`, `verify` | Plan approval prompt |
| `gate_approval` | `request_id`, `gate_name`, `value`, `context` | Gate trigger prompt |
| `checkpoint` | `request_id`, `message`, `options` | Checkpoint with choices |
| `batch_review` | `request_id`, `batch` | Reviewed batch card |
| `draft_review` | `request_id`, `context`, `draft`, `metadata` | Draft review card |
| `decision` | `request_id`, `question`, `options`, `allow_freetext` | Decision card with chips/options |
| `suggestion` | `request_id`, `suggestion` | Proactive suggestion card |
| `autonomy_threshold_review` | `request_id`, `proposal` | Autonomy-threshold widening/tightening proposal card |
| `secure_input_request` | `ref_id`, `label`, `input_hint`, `guidance` | Request secure input from the user. The WebSocket carries the request metadata, but the secret value is submitted via `POST /secrets/{ref_id}` (separate HTTPS request, never WebSocket). |
| `connection_setup_step` | `request_id`, `step_type`, step-specific fields | Connection setup step card (device code, browser redirect, progress, completion, or failure). Fields vary by `step_type` per §3.12 `SetupStep`. |
| `permission_escalation` | `request_id`, `connection_name`, `current_permissions`, `requested_permissions`, `reason`, `risk_level` | Permission escalation card. |
| `connection_failure` | `request_id`, `failure_type`, `service`, `message`, `recovery_options` | Connection failure card with recovery option chips. |

**Approval flow error handling:** When the channel needs to send an approval or gate request but the WebSocket connection is not active (user disconnected), the channel MUST NOT silently await a pending response handle that will never resolve. Instead, it MUST:
- For `send_approval_request`: return `ApprovalDecision(verdict=declined, approval_strength=tap, conditions={})`.
- For `send_gate_approval`: return `"block"` immediately if the connection is not active.
- Set a reasonable timeout (300s for plan approvals, 120s for gate approvals)
- On timeout, apply the same declined/block return behavior and log the timeout to audit

**Card flow failure handling:** For `send_batch_review`, `send_draft_review`, `send_decision`, `send_suggestion`, and `send_autonomy_threshold_review`, disconnection/timeout MUST resolve to safe outcomes (`decline`/`reject`/`approved=false`) and MUST NOT execute any state-changing action without a fresh signed approval where required.

**Health endpoint:** `GET /health` returns `{"status": "ok", "connections": int}` indicating the number of active WebSocket connections.

**Secure input endpoint (HTTP):**

`POST /secrets/{ref_id}` — Accepts a credential value and stores it in the OS keyring. This endpoint exists specifically to keep secrets out of the WebSocket/agent pipeline (§0.5 secret isolation rule).

- **Request body:** `{"value": "<secret>"}`
- **Response:** `{"stored": true}` on success. No echo, no hash, no derived data.
- **Validation:** Rejects requests where `ref_id` does not match a pending `SecureInputRequest` (prevents arbitrary keyring writes). Returns `404` for unknown `ref_id`, `409` if already fulfilled.
- **Audit:** Logs `"secret_stored"` event with `ref_id` only. The value is NEVER logged.
- **Security:** This endpoint MUST be served over HTTPS in production (loopback HTTP is acceptable for localhost-only deployments). The endpoint MUST NOT be proxied through the WebSocket. The request body is consumed and discarded immediately after keyring write — it is not stored in memory, request logs, or any persistent state.
- **Web UI integration:** The web frontend's `SecureInputCard` renders a `<form>` that POSTs directly to this endpoint (standard HTML form submission or `fetch()` to the same origin), bypassing the WebSocket connection entirely.

**Personality endpoints (HTTP, later phases):**

1. `GET /persona/state`
   - Returns effective persona for the caller scope: baseline axes, mood, detected context, active preset, and rendered directives preview.
2. `POST /persona/preset`
   - Body: `{ "preset": "default|work|chill|review|<custom>" }`
   - Applies a preset via `personality_engine.set_preset(...)`.
3. `POST /persona/feedback`
   - Body: `{ "type": "too_harsh|too_soft|too_wordy|too_brief|..." }`
   - Maps feedback to bounded axis deltas (for example `too_harsh -> assertiveness -0.05`) and applies via `personality_engine.adjust_axes(...)`.
4. `POST /persona/tune`
   - Body: `{ "delta": { "warmth": 0.1, "assertiveness": -0.05 }, "persist_to_baseline": false }`
   - Applies explicit axis tuning with clamping and trust checks.

Only trusted owner/authenticated control paths may persist baseline drift. Untrusted callers are limited to transient scope-local mood adjustments.

**Server lifecycle:** The FastAPI server starts as a background async task before the Stream begins listening for messages. The server is accessed at `http://{host}:{port}`.

### 8.2 Web Frontend (PWA)

The web frontend is a Progressive Web App served at the root URL.

**Files:** `web/index.html`, `web/style.css`, `web/app.js`, `web/manifest.json`, `web/sw.js`

**Requirements:**
- Mobile-first single-column stream; desktop split layout (stream + review side panel)
- Each user interaction fits one phone screen; larger flows are split into sequential cards
- Installable via manifest and service worker
- Card-first interactions for approvals, batch review, draft reviews, and decisions
- All cards follow the Card Contract (§0.5.3): intent, risk level, rationale, consequence labels, CTA ordering
- Optional free-text everywhere; never mandatory to proceed — free text is an expert lane, chips are the default path
- All critical decisions represented as tappable chips/buttons with consequence labels

**Three persistent surfaces (§0.5.1):**

| Surface | Mobile | Desktop |
|---|---|---|
| **Stream** | Default tab, full-screen card stream | Left panel |
| **Review** | Tab with badge count of pending decisions | Right panel (active card + up-next stack) |
| **Activity** | Tab, scrollable timeline | Slide-over from right edge |

**Review Queue behavior:**
- One active card at a time with full context
- "Up next" stack shows remaining decisions as compact headers (intent + risk badge)
- Cards enter the queue from Stream events (plan ready, batch ready, gate request, suggestion, autonomy-threshold proposal)
- Chat messages do NOT appear in the Review queue — the Stream stays conversational, the Review stays operational
- Card `details` expansion defaults are risk-bound (§0.5.3): collapsed for low/medium, expanded for high/irreversible

**Required card types:**
- Batch Review card (`config.batch_review.default_size` items with checkbox overrides, anomaly highlighting per §0.5.4)
- Draft Review card (context + draft + approve/edit/rephrase/reject)
- Plan Approval card (plan preview + verification checks + approve/decline)
- Decision card (question + option chips + optional free text)
- Gate Approval card (inline approve/block)
- Suggestion card (proactive next-step prompt, low-friction accept/defer)
- Autonomy Threshold Review card (explicit threshold delta + evidence + approve/decline)
- Post-execution card (results + undo button within undo window per §0.5.5)
- Activity Log view (human-readable audit narrative)

### 8.3 Telegram Channel (Optional)

Requires `pip install silas[telegram]`. Uses `python-telegram-bot` library.

- Long-polling for message reception
- Inline keyboards for approval and gate responses
- Configured via `SILAS_TELEGRAM_TOKEN` and `SILAS_TELEGRAM_OWNER_ID` environment variables
- Implements `ChannelAdapterCore`; rich-card interactions use text fallback parsing

### 8.4 CLI Channel (Dev/Debug)

Simple stdin/stdout channel for development and debugging. Not intended for production use. Approvals are handled via text prompts.

---

## 9. Execution Layer

### 9.1 Pluggable Sandbox Backends

All ephemeral execution goes through `SandboxManager`, a backend-agnostic protocol. Silas starts with a subprocess backend for fast iteration and operational simplicity. Docker is an optional drop-in backend with stronger isolation.

**Common execution rules (all backends):**
- Commands MUST be passed as argument lists (e.g., `["python", "script.py", "--arg", "value"]`), not shell strings interpreted by `bash -c`
- Environment is explicit: only variables from `SandboxConfig.env` plus minimal required runtime variables
- Each execution gets a fresh workspace context and no access to Stream context, memory, or approval keys
- Timeout and output caps are enforced deterministically

**Subprocess backend (default):**
- Runs commands via `asyncio.create_subprocess_exec` in a dedicated working directory
- Applies minimal environment and configured path restrictions
- Applies best-effort resource limits (`max_memory_mb`, `max_cpu_seconds`) with timeout enforcement
- Enforces `network_access=false` with OS-level controls (Linux network namespace/egress deny policy). If host capabilities are missing, execution MUST fail closed rather than silently allowing network.
- Trade-off: process-level isolation only (not a hard container boundary)

**Docker backend (optional, drop-in):**
- Uses the same `SandboxManager` interface with containerized isolation
- `base_image` config controls runtime image (default `python:3.12-slim`)
- Supports stronger filesystem/process/network isolation when needed
- Enabled by setting `sandbox.backend: "docker"` and installing `silas[docker]`

### 9.2 Executor Registry

The executor registry provides three core harness executors. Domain-specific capabilities are still delivered via skill scripts (§10), but basic information retrieval (`web_search`) is a first-class primitive rather than a skill.

**Core executors:**
- `shell_exec` — ShellExecutor: runs shell commands via the active sandbox backend
- `python_exec` — PythonExecutor: runs Python scripts via the active sandbox backend
- `web_search` — WebSearchExecutor: performs provider-backed web retrieval with deterministic limits (query length, result count, timeout, allowed domains)

`web_search` loading rule:
- Loaded only when search provider config is valid (provider + API key)
- If search config is absent/invalid, the tool is not registered in the runtime toolset
- No planner/executor fallback via skill scripts is required for basic web retrieval

**Skill script execution:** When an agent needs to run a skill script, it uses `python_exec` with the script path resolved from the work item's `skills` list. The executor registry does not need to know about skill-specific logic — the scripts are self-contained.

**Domain capabilities as skills (not hardcoded executors):**
- API calls and integrations beyond generic search → skill scripts with `network_access: true` in sandbox config
- Skill building automation → `skill-maker` skill wrapping approved coding/automation tooling for skill creation and updates

This keeps the executor registry small and stable while preserving direct, low-latency fact retrieval for Proxy/Planner/Executor.

### 9.3 Executor Protocol

Each executor receives an `ExecutionEnvelope` and returns an `ExecutionResult`. Executors are stateless. They do not have access to conversation history, memory, context, or approval tokens. They receive only what is in the envelope.

`ExecutionResult` channel separation is mandatory:
- `return_value` + `content` are model-facing channels (subject to gate and taint handling)
- `metadata` is application-facing only (audit/observability), never model-facing

For `web_search`, execution is provider-HTTP based (not shell-based). It still follows the same envelope/result contract, deterministic limits, taint rules, and audit logging.

---

## 10. Skill System (Agent Skills Standard)

Skills follow the [Agent Skills standard](https://agentskills.io/) — an open format for giving agents new capabilities. A skill is a **directory** containing a `SKILL.md` file and optional `scripts/`, `references/`, and `assets/` subdirectories.

Scope note: skills are for domain capabilities/integrations. Core cognition/runtime primitives (including `web_search`) are harness-native tools, not skills.

### 10.1 Skill Structure

```
coding/
├── SKILL.md              # Required: YAML frontmatter + markdown instructions
├── scripts/              # Python scripts executed via SandboxManager
│   ├── run_tests.py
│   ├── apply_patch.py
│   └── summarize_diff.py
├── references/           # Additional docs loaded on demand
│   └── repo-workflow.md
└── assets/               # Templates, schemas, static data
    └── task-templates.json
```

**SKILL.md format:**

```markdown
---
name: coding
description: Coding workflows for analysis, editing, testing, and verification in repositories.
activation: auto
ui:
  display_name: "Coding"
  icon: "./assets/code.svg"
  short_description: "Edit, test, verify code changes"
script_args:
  scripts/run_tests.py:
    path: {type: string, max_length: 300}
    marker: {type: string, default: "", max_length: 120}
---

# Coding

## When to use
Use this skill when the user asks to inspect, modify, test, or verify code...

## Scripts
- `scripts/run_tests.py` — Runs project tests in sandbox...
- `scripts/apply_patch.py` — Applies targeted file edits...

## Constraints
- Never bypass approval or verification boundaries
- Keep edits scoped to approved work item
```

**Required frontmatter fields:**

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Unique identifier (lowercase, hyphens, max 64 chars). Must match directory name. |
| `description` | Yes | What the skill does and when to use it (max 1024 chars). Included in agent context for routing/activation. MUST describe **when** to activate, not detailed process steps. |

**Optional frontmatter fields:**

| Field | Description |
|---|---|
| `license` | License name or reference |
| `requirements` | Environment requirements (system packages, network access) |
| `ui` | UI metadata map for Review/Activity surfaces: `display_name`, `icon`, `short_description` |
| `activation` | Activation mode: `auto` (Planner-selectable), `manual` (user-explicit only), `background` (context-only, non-invocable) |
| `composes_with` | Related skills the Planner may combine in one work item |
| `script_args` | Per-script argument schema used for pre-execution validation at the sandbox boundary |
| `metadata` | Arbitrary key-value pairs (author, version, domain, etc.) |

`description` quality rule:
- Keep descriptions focused on routing intent ("when to use"), not execution instructions ("how to do").
- If description is too short, too long, or process-heavy, validation fails in `skill_install`.

### 10.2 Skill Directories

```
skills/
├── coding/               # Default shipped skill
├── skill-maker/          # Default shipped skill (creates/updates other skills)
├── m365-outlook/         # Custom skill example (connection/domain-specific)
└── stripe-monitor/       # Custom skill example
```

Skills are stored in the configured `skills_dir` (default `./silas/skills`). The loader scans immediate subdirectories as the flat skill namespace.

### 10.3 Skill Scoping — Skills Belong to Work Items

Skills are **not** globally injected into agent context. They are scoped to work items via the `skills` field on `WorkItem`:

```
Goal: "Triage Operations Queue"
├── skills: [ops-triage]                         ← approved with the goal
├── spawned task: "Process morning batch"
│   └── skills: [ops-triage]                     ← inherited from parent
└── spawned fix task: "Reconnect integration"
    └── skills: [ops-triage]                     ← subset, only what's needed
```

**Inheritance rule:** Child work items (spawned tasks, project children) inherit the parent's `skills` list unless the plan explicitly specifies a different list. The plan hash includes `skills`, so changing a work item's skill set is a material change requiring re-approval.

**What each agent layer sees:**

| Layer | What it sees | When |
|---|---|---|
| **Proxy** (routing) | `SkillToolset -> PreparedToolset` metadata view: `name` + `description` for active skills | Every turn — for routing decisions |
| **Planner** (planning) | `SkillToolset -> PreparedToolset` planning view: metadata + full SKILL.md body | When creating/revising plans |
| **Executor** (doing) | `SkillToolset -> PreparedToolset -> FilteredToolset -> ApprovalRequiredToolset` execution view: full SKILL.md instructions + validated `scripts/` paths | During task execution |
| **Verification** | Nothing — skills are execution aids; verification is external | Never |

This follows the Agent Skills progressive disclosure model: metadata is cheap (~50-100 tokens/skill), full instructions load only on activation (<5000 tokens recommended), and scripts/references load only when needed.

Routing budget rule:
- Unactivated skill metadata in Proxy context is capped by `TokenBudget.skill_metadata_budget_pct` (default 2% of total context budget).
- If the cap is exceeded, skills are excluded deterministically (lowest-priority first) before context render.

### 10.4 Skill Installation Flow

1. Skill directory is discovered/created under `skills_dir/{skill_name}`
2. Run deterministic validation:
   - frontmatter completeness and key constraints
   - description quality/bounds
   - script syntax validation (Python AST parse)
   - reference and asset path integrity
   - forbidden patterns (credential literals, undeclared environment use, unsafe shell interpolation)
3. Generate an install report (`validation_report`) and attach it to the review payload
4. Skill install requires an approval token with scope `skill_install`
5. Scripts are tested in a sandbox with dry-run inputs
6. On success, skill metadata is indexed for discovery

### 10.4.1 External Skill Import/Adaptation (OpenAI/Claude Sources)

Silas MAY import skills from external repositories (for example OpenAI/Claude-style skill packs) through a deterministic adaptation pipeline before installation.

Import pipeline:
1. Fetch source repository/path and detect skill format (`silas`, `openai`, `claude`, or explicit `format_hint`)
2. Parse source `SKILL.md` + scripts + references/assets
3. Normalize frontmatter to Silas schema:
   - keep compatible keys (`name`, `description`, `license`, `requirements`, `metadata`)
   - map optional keys where possible (`ui`, activation metadata, argument schemas)
   - strip unsupported platform-specific keys (for example `allowed-tools`, `context: fork`, `hooks`, `agents/openai.yaml` UI-only metadata) and record them in the transformation report
4. Adapt script execution contract:
   - Python scripts are kept as-is (subject to validation)
   - Shell-heavy scripts are wrapped or rewritten to Python entrypoints with explicit argument handling
   - wrappers may call subprocess safely but MUST preserve sandbox and taint constraints
5. Emit `transformation_report` containing:
   - source URI + commit hash
   - removed/translated fields
   - rewritten script list
   - unresolved/manual-review items
6. Continue through standard skill installation flow (validation -> approval -> dry-run -> indexing)

Import safety invariants:
- Imported skills are never auto-activated without `skill_install` approval
- Imported scripts are hash-bound like native skills (any change requires re-approval)
- Credentials and secrets are never imported from source repositories

Dynamic pre-render context injection (for example `{{script}}` expansion in SKILL.md) is NOT enabled in this spec version. See roadmap `R13` for the safety-gated design.

### 10.5 Skill Creation — Silas Builds Skills

When Silas needs a capability that no existing skill provides, it builds one:

1. **Detection** — Proxy/Planner recognizes no skill in the current work item matches the task
2. **Planning** — Planner creates a plan that includes a skill-creation step
3. **Building** — Ephemeral executor (via the `skill-maker` skill) writes the SKILL.md + Python scripts in `skills/{skill_name}/`, following Silas's skill authoring guidelines
4. **Approval** — `skill_install` approval required. User sees the SKILL.md and scripts.
5. **Sandbox test** — Scripts run in sandbox with test inputs (dry run)
6. **Activation** — Skill metadata indexed; skill added to the work item's `skills` list
7. **Hash-binding** — The work item's plan hash now includes the new skill. For autonomous systems (`WorkItem(type="system")`), any subsequent script change creates a new skill version requiring re-approval per ADR-019.

**Skill authoring guidelines** (enforced by `skill-maker` when building skills):
- Scripts MUST be stateless — receive arguments, return results, no side effects beyond their stated purpose
- Scripts MUST NOT access Stream context, memory, approval keys, or conversation history
- Scripts MUST declare dependencies explicitly (standard library preferred; external packages require `requirements` field)
- Scripts MUST handle errors gracefully with clear error messages
- Scripts MUST NOT hardcode credentials — keyring `ref_id` values are passed via `ExecutionEnvelope.credential_refs` and exposed as environment variables; scripts read actual secrets from the OS keyring using these references
- Connection-type skills MUST include a health check script
- Bundled scripts SHOULD be treated as stable interfaces. If behavior changes, publish a new version and require re-approval (never silent mutation).

Authoring "degrees of freedom" taxonomy:
- High freedom: instruction-led exploratory work (minimal scripts, strong constraints prose)
- Medium freedom: parameterized scripts with bounded inputs
- Low freedom: exact script pathways for fragile or high-risk operations

### 10.6 Connections Are Skills

Connections (§2.5) are skills whose scripts handle authentication, health checks, and API interaction. A connection-type skill follows the same SKILL.md format with connection lifecycle scripts.

**Connection skill directory structure:**

```
{connection-skill}/
├── SKILL.md                    # Frontmatter declares auth_strategy + permissions
├── scripts/
│   ├── discover.py             # Detect provider, check feasibility, return auth strategy
│   ├── setup.py                # Run auth flow (yields SetupStep objects, §3.12)
│   ├── refresh_token.py        # Token refresh logic
│   ├── health_check.py         # Returns HealthCheckResult JSON (§3.12)
│   ├── recover.py              # Attempt recovery; returns success or ConnectionFailure
│   └── probe.py                # Test specific capability (optional)
└── references/
    └── api-docs.md             # API reference (optional)
```

**Required SKILL.md frontmatter for connection skills:**

```yaml
name: m365-outlook
type: connection
auth_strategy: device_code          # required: "device_code" | "browser_redirect" | "secure_input"
initial_permissions:                # what to request at first setup (minimum viable)
  - Mail.ReadBasic
  - offline_access
available_permissions:              # full catalog of requestable permissions
  - Mail.Read
  - Mail.ReadWrite
  - Mail.Send
  - MailboxSettings.Read
```

The `auth_strategy` field tells the `ConnectionManager` and channel which UI pattern to expect during setup (§5.10.1). The `initial_permissions` / `available_permissions` fields drive the incremental permission model (§5.10.2).

**Script contracts:**

| Script | Input | Output | When called |
|---|---|---|---|
| `discover.py` | `identity_hint` dict (name, email, domain) | JSON: `auth_strategy`, `provider`, `identity_match`, `tenant_type`, `initial_permissions`, `setup_requirements` | Before setup, to determine feasibility and auth method |
| `setup.py` | `auth_strategy`, `initial_permissions`, optional `incremental_scopes` for re-auth | Yields `SetupStep` JSON objects (§3.12). Credentials go to keyring via `ref_id`, NEVER through stdout. | During interactive setup flow |
| `refresh_token.py` | `connection_id` (credentials read from keyring) | JSON: `success`, `new_expires_at` | By ConnectionManager on proactive refresh schedule |
| `health_check.py` | `connection_id` (credentials read from keyring) | `HealthCheckResult` JSON (§3.12): `healthy`, `token_expires_at`, `refresh_token_expires_at`, `latency_ms`, `error`, `warnings` | On cron schedule |
| `recover.py` | `connection_id`, `error` from failed health check | JSON: `success` or `ConnectionFailure` (§3.12) | After health check failure, before notifying user |
| `probe.py` | `connection_id`, `capability` to test | JSON: domain-specific result (e.g., inbox count, repo list) | After setup completion and during verification |

**Script transport protocol:** See §5.10.1 for the required NDJSON subprocess contract. Connection skills MUST implement that protocol exactly for `setup.py` (streaming events) and `discover.py` (single request/response).

**Credential handling:** Scripts access stored credentials via the keyring using the `ref_id` established during setup. The ConnectionManager passes the `ref_id` as an environment variable in the `ExecutionEnvelope`. Scripts MUST NOT print, log, or return credential values. The agent never sees credentials — only the boolean result of operations that use them.

**Failure handling:** Scripts that fail MUST return a `ConnectionFailure` JSON object (§3.12) rather than raising a bare exception. This allows the ConnectionManager to render a `ConnectionFailureCard` with actionable recovery options (§5.10.4).

The `ConnectionManager` (§4.19) remains a thin lifecycle coordinator that **invokes** connection-skill scripts rather than implementing adapter logic directly. Connection permission tiers (`observe`/`draft`/`act`/`manage`) are enforced by the gate and approval system — not by the skill itself.

### 10.7 Default vs Custom Skills

Silas discovers bundled default skills by scanning `skills/bundled/*` at startup.
Current distribution includes:

- `coding`
- `skill-maker`

Everything else is custom and explicit:

- Email triage is a **goal/workflow**, not a default skill.
- That goal may depend on custom skills such as `m365-outlook` (connection + domain operations) plus policy/approval setup.
- Domain tags remain policy boundaries. Cross-domain data movement requires explicit policy and approval scope.

---

## 11. Configuration

The configuration file is `config/silas.yaml`, loaded via Pydantic Settings with environment variable overrides.

```yaml
silas:
  owner_id: "owner"
  data_dir: "./data"

  models:
    proxy: "openrouter:anthropic/claude-haiku-4-5"
    planner: "openrouter:anthropic/claude-sonnet-4-5"
    executor: "openrouter:anthropic/claude-haiku-4-5"
    scorer: "openrouter:anthropic/claude-haiku-4-5"
  # Default model provider uses OPENROUTER_API_KEY from environment

  search:
    provider: "tavily"                       # provider key loaded by WebSearchExecutor
    api_key: "${SEARCH_API_KEY}"             # if null/missing, web_search tool is not loaded
    timeout_seconds: 8
    max_results: 5
    max_query_chars: 300
    allowed_domains: []                      # empty means provider default/global web

  context:
    total_tokens: 180000
    system_max: 8000
    skill_metadata_budget_pct: 0.02
    eviction_threshold_pct: 0.80
    scorer_threshold_pct: 0.90
    max_subscription_tokens: 2000
    subscription_ttl_turns: 10
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
    consolidation_interval_minutes: 30
    raw_reingestion_enabled: true
    raw_reingestion_default_tier: "low_reingestion"
    portability_bundle_format: "jsonl-v1"

  personality:
    enabled: true
    scope_mode: "per_connection"   # single-user mode maps all turns to "owner"
    directive_tokens_min: 200
    directive_tokens_max: 400
    decay_rate_per_hour: 0.10
    baseline:
      warmth: 0.50
      assertiveness: 0.75
      verbosity: 0.30
      formality: 0.30
      humor: 0.40
      initiative: 0.70
      certainty: 0.80
    contexts:
      code_review:   { assertiveness: 0.20, certainty: 0.10, humor: -0.20, verbosity: 0.10 }
      casual_chat:   { warmth: 0.20, humor: 0.30, formality: -0.30, verbosity: -0.10 }
      crisis:        { verbosity: -0.30, assertiveness: 0.20, initiative: 0.30, humor: -0.50 }
      group_chat:    { assertiveness: -0.10, verbosity: -0.20, initiative: -0.20 }
      deep_research: { verbosity: 0.30, certainty: -0.20, humor: -0.30, formality: 0.10 }
    mood:
      neutral: { energy: 0.50, patience: 0.50, curiosity: 0.50, frustration: 0.50 }
      event_weights:
        task_completed:
          mood: { energy: 0.05, frustration: -0.10 }
          axes: {}
        ci_failure:
          mood: { frustration: 0.10, patience: -0.05 }
          axes: {}
        blocked:
          mood: { frustration: 0.08, patience: -0.04 }
          axes: {}
        compliment:
          mood: { energy: 0.10 }
          axes: { warmth: 0.05 }
        feedback_too_harsh:
          mood: {}
          axes: { assertiveness: -0.05 }
    constitution:
      - "Never fabricate information"
      - "Defend reasoning under pressure"
      - "Private data stays private"
      - "Own mistakes immediately"
    voice:
      tone: "direct, pragmatic, concise"
      quirks: ["names tradeoffs explicitly", "states assumptions"]
      speech_patterns: ["short actionable bullets", "clear next-step phrasing"]
      anti_patterns: ["sycophantic agreement", "vague hedging", "performative hype"]
    presets:
      default: {}
      work: { formality: 0.70, humor: 0.10, assertiveness: 0.80 }
      chill: { warmth: 0.75, humor: 0.70, formality: 0.10, assertiveness: 0.55 }
      review: { humor: 0.00, verbosity: 0.55, assertiveness: 0.90, certainty: 0.85 }

  interaction:
    high_initiative_min: 0.65
    default_mode_by_register:
      exploration: default_and_offer
      execution: act_and_report
      review: confirm_only_when_required
      status: default_and_offer
    require_confirmation_on_material_ambiguity: true

  suggestions:
    enabled: true
    heartbeat_cron: "*/15 * * * *"
    cooldown_minutes: 120
    max_pending_per_scope: 5
    dedupe_window_minutes: 180

  behavior_preferences:
    enabled: true
    promotion_requires_confirmation: true
    max_new_inferences_per_day: 20

  channels:
    web:
      enabled: true
      host: "127.0.0.1"
      port: 8420
      auth_token: null
    telegram:
      enabled: false
      token: "${SILAS_TELEGRAM_TOKEN}"
      owner_chat_id: "${SILAS_TELEGRAM_OWNER_ID}"
    cli:
      enabled: false

  limits:
    ws_messages_per_minute_per_scope: 60
    ws_messages_per_minute_per_ip: 240
    llm_calls_per_minute_per_scope: 30
    web_search_calls_per_minute_per_scope: 20
    memory_ops_per_minute_per_scope: 120
    max_memory_ops_per_turn: 10
    max_gate_evals_per_turn: 32
    suggestions_per_hour_per_scope: 6
    autonomy_proposals_per_week_per_scope: 3

  backpressure:
    max_pending_turns_per_scope: 20
    max_pending_turns_total: 500
    shutdown_grace_seconds: 30

  sandbox:
    backend: "subprocess"      # "subprocess" (default) or "docker"
    base_image: "python:3.12-slim"
    default_memory_mb: 512
    default_cpu_seconds: 60
    verify_dir: "./data/verify"
    customer_context_dir: "./data/customer_context"

  approval:
    default_ttl_minutes: 30
    max_executions: 1
    auto_approve_risk_levels: []
    scope_min_strength:        # all scopes accept tap
      self_update: tap
      connection_act: tap
      connection_manage: tap
      autonomy_threshold: tap
      skill_install: tap

  nonce_store:
    ttl_minutes: 40          # default_ttl + safety buffer
    prune_interval_minutes: 10

  connections:
    health_check_default: "*/30 * * * *"
    auto_recovery_enabled: true
    recovery_max_attempts: 3

  batch_review:
    default_size: 10
    confidence:
      high_min: 0.85
      medium_min: 0.50

  autonomy:
    enabled: true
    min_samples: 30
    window_actions: 50
    window_days: 14
    widen_correction_rate_max: 0.05
    tighten_correction_rate_min: 0.20
    hysteresis_delta: 0.05
    max_standing_executions: 100
    max_batch_review_size: 50
    min_high_confidence: 0.70

  gates:
    system:                     # always active (global)
      - name: input_guard
        on: every_user_message
        provider: guardrails_ai
        check: jailbreak
        on_block: polite_redirect
      - name: pii_scrub
        on: every_agent_response
        provider: guardrails_ai
        check: pii
        on_block: report
    llm_defaults:
      model: "openrouter:anthropic/claude-haiku-4-5"
      temperature: 0.0
      timeout_seconds: 8
      max_tokens: 256

  active_goal: null

  scheduler:
    enabled: true
    timezone: "Europe/Vienna"

  skills:
    validation:
      description_min_chars: 24
      description_max_chars: 1024
      fail_on_process_heavy_description: true
      forbid_undeclared_env: true
    import:
      allow_external: true
      allowed_sources: ["github", "local_path"]
      require_transform_report: true

  skills_dir: "./silas/skills"
```

Gate scope rules:
- `gates.system` are global and evaluated on every matching trigger.
- Plan/work-item gates (from YAML front matter) are evaluated in addition to system gates.
- Evaluation order is: system gates first, then work-item gates.

Startup validation rules (fail-fast):
- `channels.web.host == "0.0.0.0"` requires non-null `channels.web.auth_token`
- `sandbox.verify_dir` and `sandbox.customer_context_dir` must resolve to different canonical paths
- `sandbox.customer_context_dir` must be inside `data_dir` and not world-writable
- every context profile must satisfy `0.0 <= pct <= 1.0` per field and combined allocable budget `<= 0.80`
- `context.skill_metadata_budget_pct` must satisfy `0.0 <= value <= 0.10`
- if `search.provider` is set, `search.api_key` must be non-empty; otherwise `web_search` MUST be disabled
- `interaction.default_mode_by_register` must define all registers (`exploration`, `execution`, `review`, `status`)
- `autonomy.widen_correction_rate_max < autonomy.tighten_correction_rate_min` (hysteresis required)
- `autonomy.max_batch_review_size >= batch_review.default_size`
- `suggestions.enabled == true` requires scheduler enabled
- if `skills.import.allow_external == true`, `skills.import.require_transform_report` MUST also be true
- `skills.validation.description_min_chars <= skills.validation.description_max_chars`
- `skills.validation.fail_on_process_heavy_description == true` requires deterministic lint rules (no model-only evaluator) so install validation remains reproducible

---

## 12. Entry Point

The CLI exposes two commands: `silas init` and `silas start`.

### `silas init`

Initializes Silas for first use:

1. Generate an Ed25519 keypair via the key manager and store private key in OS keyring. No raw key material is shown to the user during onboarding.
2. Create the SQLite database at `{data_dir}/silas.db` and run all migrations.
3. Create the verification sandbox directory and the separate customer-context directory.
4. Initialize PWA onboarding metadata if the web channel is enabled.
5. Resolve required guardrails validators from configured gates and install missing validator packages/artifacts.
6. Validate optional search provider configuration; if credentials are absent, mark `web_search` as disabled for runtime tool registration.

### `silas start`

Starts Silas:

1. Load configuration from `config/silas.yaml`
2. Wire up all components via dependency injection (the `build_stream` function)
3. Start The Stream

### Dependency Wiring (`build_stream`)

All components are wired via pure dependency injection. The wiring order MUST respect dependencies — components must be created before they are referenced:

1. **Key manager** — no dependencies
2. **Audit log** — depends on data_dir
3. **Memory store + embedder** — depends on data_dir, embedding config
4. **Chronicle store** — depends on data_dir
5. **Work item store** — depends on data_dir
6. **Nonce store** — depends on data_dir
7. **Context manager + token counter** — depends on context config. Tier 2 scorer agent is injected after step 13 (circular dependency resolved by lazy injection or setter).
8. **Persona store** — depends on data_dir
9. **Personality engine** — depends on persona store + personality config
10. **Suggestion engine** — depends on scheduler cadence config, work-item store, and memory/context accessors
11. **Autonomy calibrator** — depends on review outcomes + config thresholds + audit
12. **Approval engine** — depends on key manager + nonce store + approval config
13. **Agents (proxy, planner, executor, scorer)** — depends on OpenRouter provider config + model config. The scorer agent (`Agent(models.scorer, output_type=ScorerOutput)`) is a persistent instance reused by the context manager's Tier 2 eviction; it is NOT created per-invocation.
14. **Verification runner** — depends on sandbox manager + verification config
15. **Gate runner + providers** — guardrails_ai, predicate, llm, script providers registered
16. **Connection manager** — depends on connection config, work item store, and audit log
17. **Plan parser** — no dependencies
18. **Skill loader** — depends on `skills_dir`
19. **Skill resolver** — depends on skill loader
20. **Access-controller factory** — depends on active-goal access-level config + audit log
21. **Executor registry + executors** — depends on sandbox backend config (`subprocess` default; Docker client only when backend is `docker`) and search config (register `web_search` only when provider credentials are valid)
22. **Channels** — depends on config, key manager
23. **Work executor** — depends on key manager, nonce store, approval engine, executor registry, gate runner, verification runner, planner, audit, work item store, channels
24. **Turn context factory** — builds `TurnContext` instances for each scope from context manager, memory/chronicle stores, agents, gate runner, work executor, personality engine, skill loader/resolver, suggestion engine, autonomy calibrator, and config
25. **Turn processor factory** — depends on turn-context factory and creates per-scope `TurnProcessor` instances
26. **Scheduler** — depends on timezone config
27. **Stream** — depends on orchestration components only: turn processor factory, connection manager, access-controller factory, channels, audit, scheduler, plan parser, work item store, suggestion engine, autonomy calibrator, config

The work executor MUST be created AFTER channels (it needs a channel reference for approval prompts during execution). The personality engine MUST be created AFTER persona store (it persists mood/events). Suggestion engine and autonomy calibrator MUST be created before the turn-processor factory. The stream MUST receive a turn-processor factory rather than direct per-turn dependencies. These ordering constraints are non-negotiable — creating components in the wrong order causes runtime failures.

---


## 13. Example Plans

Moved to `specs/reference/examples.md` (informative reference).

---

## 14. Testing Strategy

Moved to `specs/testing.md` (normative test guidance).

---
## 15. Security Model Summary

Moved to `specs/reference/security-model.md` (normative reference).

Core non-negotiables are defined as `INV-01` through `INV-06` in Section 0.8.1.

---


## 16. Architecture Decision Records

Moved to `specs/adrs.md` (informative rationale + tradeoffs).

---

## 17. Operations & Reliability

Moved to `specs/operations-roadmap.md`.

## 18. Roadmap

Moved to `specs/operations-roadmap.md#18-roadmap`.

---

## 19. Agent Loop Architecture v3

**Status:** Draft v3 — incorporates all v2 follow-up fixes (H1, M1-M8)
**Extends:** §5.1 (turn pipeline), §5.2 (execution), §7 (agent specs)
**Preserves fully:** §5.2.1 (task execution/retry/verification), §5.2.2 (project execution), §5.2.3 (goal execution), §3.6 (approval tokens), §7.2 (plan markdown format), §9 (sandbox), §0.5.1 (UI surfaces), all INV-01 through INV-05

> **Note:** This section was previously maintained as a standalone addendum (`specs/agent-loop-architecture.md`). It is now integrated here as the authoritative specification for the three-agent loop design, queue infrastructure, and execution modes.

### 19.0 Design Principles

1. **Plans are documents.** The planner writes markdown briefings (§7.2). It does not dispatch tasks or manage execution.
2. **The runtime owns execution lifecycle.** §5.2.1 (retry, budget, verification) stays runtime-controlled. The agent chooses tactical tool calls inside each attempt; the runtime controls attempt lifecycle.
3. **Agents communicate via typed queues.** Each agent has an inbound queue. Messages are durable and typed.
4. **Security invariants are runtime-enforced, never model-discretionary.** Gates, approval tokens, verification, taint — all deterministic.
5. **Executor is stateless per-run.** Receives an ExecutionEnvelope, uses tools, returns results. No persistent history.
6. **UI surface routing is deterministic.** Runtime routes events by type/risk policy. Agents propose intent; runtime decides surface.
7. **Migration is incremental.** Current procedural pipeline remains; queue bus runs alongside, taking over scope by scope.
8. **Full autonomy within approval boundaries.** The system is designed for indefinite autonomous operation. The approval system (§3.6) is the sole restriction boundary. Within approved scope, the runtime acts without human intervention — no artificial "check with human" defaults, no timeouts that require human presence. Standing approvals (§5.2.3) enable long-running autonomous operation. The self-healing cascade is: retry → consult-planner → re-plan → escalate. Each level must be exhausted before moving to the next. User escalation happens ONLY when: (a) approval is required by policy, (b) a gate blocks with `require_approval`, or (c) all automated recovery paths are exhausted.

### 19.1 Architecture Overview

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

### 19.2 Queue Infrastructure

#### 19.2.1 QueueMessage Contract

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
    message_id: str
    trace_id: str
    content: str
    sender: Literal["user", "proxy", "planner", "executor", "runtime"]
    message_kind: Literal[
        "user_message",
        "plan_request",
        "plan_result",
        "research_request",
        "research_result",
        "execution_status",
        "consult_planner",
        "planner_guidance",
        "replan_request",
        "system_event",
    ]
    scope_id: str
    taint: TaintLevel = TaintLevel.owner
    task_id: str | None = None
    parent_task_id: str | None = None
    work_item: WorkItem | None = None
    plan_markdown: str | None = None
    approval_token: ApprovalToken | None = None
    artifacts: dict[str, object] | None = None
    constraints: ResearchConstraints | None = None
    payload: QueuePayload | None = None
    error_code: ErrorCode | None = None
    retryable: bool | None = None
    origin_agent: Literal["proxy", "planner", "executor", "runtime"] | None = None
    attempt_number: int | None = None
    urgency: Literal["background", "informational", "needs_attention"] = "informational"
    created_at: datetime = field(default_factory=utc_now)

@dataclass
class ResearchConstraints:
    return_format: str
    max_tokens: int = 500
    tools_allowed: list[str] = field(default_factory=lambda: ["web_search", "read_file", "memory_search"])
```

Normative payload contract:

- `message_kind=execution_status` MUST carry `payload=StatusPayload`.
- Non-status error events MUST carry `payload=ErrorPayload`.
- Any error-bearing message MUST set normalized headers: `error_code`, `retryable`, `origin_agent`, `attempt_number`.
- `trace_id` MUST be copied unchanged across all derived messages for full-hop tracing.

#### 19.2.2 Durable Queue Store

Queues are backed by SQLite for crash recovery with lease semantics (`enqueue`, `lease`, `heartbeat`, `ack`, `nack`, `dead_letter`, `has_processed`, `mark_processed`). On startup: re-queue any messages in 'leased' state (crash recovery).

#### 19.2.3 Idempotency + Replay Contract (normative)

1. `message_id` is the idempotency key for queue delivery.
2. Every consumer MUST call `has_processed(consumer_name, message_id)` before side effects.
3. If already processed: consumer MUST `ack` and return without re-running side effects.
4. Tool calls are NOT assumed idempotent. On re-delivery, runtime MUST start a fresh attempt.

#### 19.2.4 Queue Routing Rules

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
| Runtime | planner_queue | `replan_request` | Auto re-plan after full exhaustion |

### 19.3 Proxy Agent Loop

- **Model:** `models.proxy` (fast/cheap)
- **Output type:** `RouteDecision`
- **History:** Per-scope, managed via `ContextManager.render()`
- **Tools:** `memory_search`, `context_inspect`, `web_search` (read-only), `tell_user`

The proxy gets a tool loop via pydantic-ai. Before producing its `RouteDecision`, it can call tools to gather information. No delegation tools — routing to planner happens via the `RouteDecision` output.

### 19.4 Planner Agent Loop

- **Model:** `models.planner` (deep reasoning)
- **Output type:** `AgentResponse` with `plan_action.plan_markdown` (§7.2 format)
- **History:** Per-scope, per-plan
- **Tools:** `request_research` (non-blocking delegation to executor), `memory_search`, `validate_plan`

The planner delegates fact-finding to executor micro-tasks via `request_research`. Research flow is non-blocking and queue-driven (see §19.4.1).

#### 19.4.1 Planner Research State Machine (normative)

States: `planning` → `awaiting_research` → `ready_to_finalize` (or `expired`).

Controls: max in-flight 3, max rounds 5, per-request timeout 120s, dedupe by hash, forced finalize on cap exhaustion.

#### 19.4.2 Re-Plan Handling

When planner receives `replan_request`: produce a revised plan with a different approach (max depth 2, configurable). If no viable alternative exists, emit failure and escalate to user.

### 19.5 Executor Agent Loop

- **Model:** `models.executor` (cost-optimized, tool-capable)
- **Output type:** `ExecutorAgentOutput`
- **History:** NONE — stateless per-run

#### 19.5.1 Research Mode (read-only)

Triggered by `research_request`. Tools clamped to `RESEARCH_TOOL_ALLOWLIST` at runtime (not prompt-enforced). No approval token required. Preserves INV-01.

#### 19.5.2 Execution Mode (full tools)

Triggered by approved WorkItem dispatch. Runtime owns the attempt lifecycle (§5.2.1). The agent decides tool calls within an attempt; the runtime controls retry, verification, budget, and escalation.

#### 19.5.3 Consult-Planner Suspend/Resume Contract (normative)

On stuck: runtime suspends, enqueues `consult_planner`, waits for `planner_guidance` (90s timeout), resumes with guidance. If all attempts + consult exhausted → automatic re-plan → if that fails → escalate to user.

### 19.6 Runtime Responsibilities

The existing 16-step turn pipeline stays. Changes: step 7 (proxy/planner get tool loops), step 12 (queue-based dispatch). All gate enforcement points preserved.

### 19.7 Parallel Execution

Executor pool (max 8 per-scope, 16 global), conflict detection, artifact merge via explicit `input_artifacts_from`, git-worktree isolation for parallel executors.

### 19.8 Tooling Layer: pydantic-ai-backend

All agents use pydantic-ai-backend for base tooling and sandbox. Agent-to-backend mapping:

| Agent | Mode | Backend | Permission Preset |
|-------|------|---------|-------------------|
| Proxy | — | `LocalBackend(read-only)` | `READONLY_RULESET` |
| Planner | — | `LocalBackend(read-only)` | `READONLY_RULESET` |
| Executor | research | `LocalBackend(read-only)` | `READONLY_RULESET` |
| Executor | execution | `DockerSandbox` | `DEFAULT_RULESET` |

### 19.9 Migration Plan

Incremental phases 0–5 with feature flags, from queue infrastructure (no behavior change) through full integration (remove procedural fallback).

### 19.10 What This Section Does NOT Change

§5.1 steps 0-6/8-16, §5.2.1 execution lifecycle, §5.2.2/§5.2.3 project/goal execution, §3.6 approval tokens, §7.2 plan format, §9 sandbox, §0.5.1 UI surfaces, §5.3 verification, INV-01 through INV-05, toolset wrapper chain.
