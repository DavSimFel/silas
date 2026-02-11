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

Both milestones preserve all existing security invariants (signed approvals, plan-hash binding, nonce replay protection, policy gates before execution). Additional roadmap items are tracked in Section 19.

## 0.8 Amendment Guardrail (Interoperability + Tooling)

The interoperability and skill-tooling additions in this revision are additive and MUST NOT weaken existing security or execution guarantees.

Non-regression requirements:
- Cryptographic approval remains mandatory for executable actions
- Plan-hash binding and nonce replay protection remain unchanged
- External deterministic verification remains the source of truth for completion
- Sandbox isolation and taint propagation rules remain unchanged
- Imported or adapted skills use the same `skill_install` approval, dry-run, and hash-bound versioning path as native skills

---

## 1. Project Structure

```
silas/
├── pyproject.toml
├── silas/
│   ├── __init__.py
│   ├── main.py                     # Entry point, CLI
│   ├── config.py                   # Pydantic Settings, YAML config loading
│   │
│   ├── protocols/                  # ALL interfaces (Protocol classes)
│   │   ├── __init__.py
│   │   ├── channels.py             # ChannelAdapterCore, RichCardChannel, ChannelMessage
│   │   ├── agents.py               # Router, Planner, AgentResponse
│   │   ├── memory.py               # MemoryStore, MemoryRetriever, MemoryConsolidator, MemoryPortability
│   │   ├── context.py              # ContextManager, ContextSubscription
│   │   ├── approval.py             # ApprovalVerifier, NonceStore
│   │   ├── execution.py            # EphemeralExecutor, SandboxManager
│   │   ├── gates.py                # Gate, GateRunner, GateCheckProvider
│   │   ├── work.py                 # WorkItemExecutor, VerificationRunner, WorkItemStore
│   │   ├── scheduler.py            # TaskScheduler, ScheduledTask
│   │   ├── audit.py                # AuditLog
│   │   ├── personality.py          # PersonalityEngine, PersonaStore
│   │   ├── proactivity.py          # SuggestionEngine, AutonomyCalibrator
│   │   ├── connections.py          # ConnectionManager (thin coordinator over connection skills)
│   │   └── skills.py              # SkillLoader, SkillResolver
│   │
│   ├── models/                     # Pydantic data models (no logic)
│   │   ├── __init__.py
│   │   ├── messages.py             # SignedMessage, ChannelMessage, TaintLevel
│   │   ├── agents.py               # AgentResponse, PlanAction, RouteDecision
│   │   ├── memory.py               # MemoryItem, MemoryQuery, MemoryOp
│   │   ├── context.py              # ContextItem, ContextZone, ContextSubscription, ContextProfile, TokenBudget
│   │   ├── approval.py             # ApprovalToken, ApprovalVerdict, ApprovalScope
│   │   ├── execution.py            # ExecutionEnvelope, ExecutionResult
│   │   ├── work.py                 # WorkItem, WorkItemType, StepContract, Budget
│   │   ├── gates.py                # Gate, GateType, Expectation, AccessLevel
│   │   ├── sessions.py             # Session, SessionType
│   │   ├── skills.py               # SkillMetadata (parsed SKILL.md frontmatter), SkillRef
│   │   ├── personality.py          # PersonalityAxis, VoiceConfig, PersonaState
│   │   ├── review.py               # BatchProposal, DraftReview, Suggestion, AutonomyThresholdProposal models
│   │   └── connections.py          # AuthStrategy, SetupStep, SecureInputRequest, HealthCheckResult, ConnectionFailure
│   │
│   ├── core/                       # Core implementations
│   │   ├── __init__.py
│   │   ├── stream.py               # The Stream — main loop, persistent session
│   │   ├── work_executor.py        # WorkItemExecutor — universal task runner
│   │   ├── plan_parser.py          # Parse markdown plans into WorkItems
│   │   ├── verification_runner.py  # External verification — agent cannot touch
│   │   ├── gate_runner.py          # Gate evaluation — dispatches to providers
│   │   ├── access_controller.py    # Access level state machine
│   │   ├── context_manager.py      # ContextManager — zones, profiles, two-tier eviction, subscriptions
│   │   ├── key_manager.py          # Ed25519 key generation, signing, verification
│   │   ├── approval_engine.py      # Approval lifecycle, verification, nonce tracking
│   │   ├── turn_context.py         # TurnContext dependency container for TurnProcessor
│   │   ├── taint_tracker.py        # Input taint propagation
│   │   ├── token_counter.py        # Heuristic token counter (chars ÷ 3.5)
│   │   ├── personality_engine.py   # Axes composition, context detection, mood drift
│   │   └── suggestion_engine.py    # Proactive next-step suggestions + autonomy threshold proposals
│   │
│   ├── agents/                     # PydanticAI Agent definitions
│   │   ├── __init__.py
│   │   ├── proxy.py                # ProxyAgent — proxy-tier routing, direct response
│   │   ├── planner.py              # PlannerAgent — planner-tier planning, reasoning
│   │   ├── executor.py             # ExecutorAgent — cheapest, runs briefings
│   │   └── prompts/
│   │       ├── proxy_system.md
│   │       ├── planner_system.md
│   │       └── constitution.md
│   │
│   ├── memory/                     # Memory implementations
│   │   ├── __init__.py
│   │   ├── sqlite_store.py         # SQLite + FTS5 (sqlite-vec enabled in Phase 3)
│   │   ├── consolidator.py         # Background memory consolidation
│   │   ├── retriever.py            # Multi-graph retrieval
│   │   ├── embedder.py             # fastembed wrapper
│   │   └── migrations/
│   │       ├── 001_initial.sql
│   │       └── 002_vector.sql      # Adds sqlite-vec indexes
│   │
│   ├── execution/                  # Ephemeral executor implementations
│   │   ├── __init__.py
│   │   ├── registry.py             # ExecutorRegistry (core primitives only)
│   │   ├── subprocess_sandbox.py   # SubprocessSandboxManager (default backend)
│   │   ├── docker_sandbox.py       # DockerSandboxManager (optional backend)
│   │   ├── shell.py                # ShellExecutor (runs via configured sandbox backend)
│   │   ├── python_exec.py          # PythonExecutor (runs via configured sandbox backend)
│   │   └── web_search.py           # WebSearchExecutor (provider-backed retrieval, key-gated)
│   │
│   ├── gates/                      # Gate check providers
│   │   ├── __init__.py
│   │   ├── guardrails_ai.py        # GuardrailsAIChecker (validator hub adapter)
│   │   ├── predicate.py            # PredicateChecker (numeric, regex, exit code)
│   │   ├── llm.py                  # LLMChecker (subjective quality checks via configured quality-tier model)
│   │   ├── script.py               # ScriptChecker (custom shell scripts)
│   │   └── guards/                 # Custom domain-specific check scripts
│   │       ├── content_policy.py
│   │       ├── data_redaction.py
│   │       └── verify_customer.py
│   │
│   ├── channels/
│   │   ├── __init__.py
│   │   ├── web.py                  # WebChannel (FastAPI + WebSocket, primary)
│   │   ├── telegram.py             # TelegramChannel (optional)
│   │   └── cli.py                  # CLIChannel (dev/debug)
│   │
│   ├── persistence/                # Durable storage for runtime state
│   │   ├── __init__.py
│   │   ├── work_item_store.py      # SQLite WorkItem persistence
│   │   ├── chronicle_store.py      # SQLite chronicle persistence
│   │   ├── memory_portability.py   # Memory export/import bundle adapter
│   │   └── persona_store.py        # SQLite personality state/event persistence
│   │
│   ├── skills/
│   │   ├── __init__.py
│   │   ├── loader.py               # SKILL.md frontmatter parser + directory scanner
│   │   ├── resolver.py             # Resolves skill names to paths, validates script access
│   │   ├── validator.py            # Deterministic skill validation checks
│   │   ├── importer.py             # External skill adaptation + transform report
│   │   ├── coding/                 # Shipped default skill: coding/build/test workflows
│   │   ├── skill-maker/            # Shipped default skill: build/install new skills
│   │   └── {custom-skill}/         # Flat skill namespace (user-installed or generated)
│   │
│   ├── connections/
│   │   ├── __init__.py
│   │   ├── manager.py              # Connection lifecycle coordinator (invokes connection-skill scripts)
│   │   └── registry.py             # Connection state persistence + lookup
│   │
│   ├── audit/
│   │   └── sqlite_audit.py         # Hash-chained SQLite audit log
│   │
│   └── scheduler/
│       └── ap_scheduler.py         # APScheduler wrapper
│
├── config/
│   └── silas.yaml                   # Default configuration
│
├── web/                            # Web frontend (served by FastAPI)
│   ├── index.html                  # PWA shell
│   ├── style.css
│   ├── app.js                      # WebSocket + card renderer
│   ├── manifest.json               # Home-screen install metadata
│   ├── sw.js                       # Install/runtime caching hooks
│
├── tests/
│   ├── conftest.py                 # Shared fixtures
│   ├── fakes.py                    # In-memory implementations for testing
│   ├── test_context.py
│   ├── test_gates.py
│   ├── test_work_executor.py
│   ├── test_verification.py
│   ├── test_approval.py
│   ├── test_memory.py
│   ├── test_personality.py
│   ├── test_proactivity.py
│   └── evals/                      # Pydantic Evals
│       ├── eval_routing.py
│       ├── eval_planning.py
│       └── eval_memory.py
│
└── plans/                          # Example plan templates
    ├── fix-bug.md
    ├── customer-support-bot.md
    └── health-monitor.md
```

---

### 1.5 Onboarding Flow (First Run)

The first-run flow is phone-first, PWA-compatible, and requires no desktop-only steps:

1. **Welcome card**
   - Copy: "I'm Silas. Let's get you set up."
   - CTA: `[Get Started]`
2. **LLM provider selection**
   - Options: OpenRouter, local
   - Single API-key field (`OPENROUTER_API_KEY`) with immediate validation
3. **Identity bootstrap**
   - Name, primary email, primary phone
   - Used for connection discovery and domain policy defaults
4. **Completion**
   - Redirect to The Stream
   - First message: "I'm ready. Tell me what to connect first, or I'll figure it out."

### 1.6 Web Frontend (PWA)

The web frontend is a Progressive Web App:

- `web/manifest.json` enables install-to-home-screen
- `web/sw.js` provides install/runtime caching hooks
- Responsive layouts:
  - Phone: single-column card stream
  - Desktop: chat stream + preview/side panel

Required interactive views/components:

- Chat stream (primary surface)
- Batch Review card (configurable-size approve/decline/edit-selection)
- Draft Review card (context + draft + approve/edit/rephrase/reject)
- Decision card (question + option chips + optional free text)
- Activity Log view (human-readable audit timeline)

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
| `pynacl` | Ed25519 signing/verification (libsodium) | `>=1.5,<2` |
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

Exact pre-counting roadmap options are tracked in Section 19.

### CLI Entry Point

The package exposes a single CLI command: `silas`, mapped to `silas.main:cli`.

---

## 3. Data Models

All models are Pydantic `BaseModel` subclasses with no business logic. Every `datetime` field throughout the system MUST use timezone-aware datetimes via `datetime.now(timezone.utc)`. The deprecated `datetime.utcnow()` MUST NOT be used anywhere — it returns naive datetimes that cause comparison bugs with timezone-aware datetimes.

### 3.1 Messages (`models/messages.py`)

**TaintLevel** — String enum classifying message trust:

| Value | Meaning |
|---|---|
| `owner` | Signed by owner's Ed25519 key |
| `auth` | From known channel identity, not cryptographically signed |
| `external` | Unknown source, untrusted |

**ChannelMessage** — Raw message from any channel:

| Field | Type | Description |
|---|---|---|
| `channel` | `str` | Source channel identifier: `"web"`, `"telegram"`, `"cli"`, `"webhook"` |
| `sender_id` | `str` | Sender identifier within the channel |
| `text` | `str` | Message content |
| `timestamp` | `datetime` | When the message was received (timezone-aware, defaults to UTC now) |
| `attachments` | `list[str]` | Paths or URLs to attachments |
| `reply_to` | `str \| None` | ID of message being replied to |

**SignedMessage** — Message with cryptographic owner signature:

| Field | Type | Description |
|---|---|---|
| `message` | `ChannelMessage` | The underlying message |
| `signature` | `bytes` | Ed25519 signature over canonical bytes |
| `nonce` | `str` | Single-use nonce for replay protection |
| `taint` | `TaintLevel` | Trust classification (defaults to `external`) |

The canonical bytes for signature verification MUST be JSON canonicalization, not delimiter concatenation:

1. Build an object with exactly `{"text": ..., "timestamp": ..., "nonce": ...}` where `timestamp` is timezone-aware ISO-8601.
2. Serialize with sorted keys and no insignificant whitespace (`separators=(",", ":")`).
3. Encode as UTF-8 bytes.

Delimiter-based concatenation is forbidden because it is ambiguous when values contain delimiter characters.

### 3.2 Agent Response (`models/agents.py`)

**AgentResponse** — Every LLM response from Proxy or Planner MUST conform to this schema. It is the structured output type for all agent calls.

| Field | Type | Description |
|---|---|---|
| `message` | `str` | The text response to send to the user |
| `memory_queries` | `list[MemoryQuery]` | Memory retrieval queries to execute and inject into context (max 3 per response). This is the agent's **only** context lever — it can request information but cannot drop, pin, or summarize context items. The harness controls all eviction and lifecycle. |
| `memory_ops` | `list[MemoryOp]` | Memory store/update/delete/link operations |
| `plan_action` | `PlanAction \| None` | Plan proposal, revision, or execution request |
| `needs_approval` | `bool` | Agent's request for whether the plan requires human approval. The runtime treats this as advisory — if `false` but no valid approval token exists on the work item, the runtime overrides to `true` and enters the approval flow. Only pre-verified standing approvals can legitimately skip interactive approval. |

A `model_validator` MUST enforce `len(memory_queries) <= 3`. This is a fixed structural constraint (not config-dependent) that prevents the agent from flooding the memory zone with retrieval results. Three queries cover the common retrieval pattern of "recall + current state + related precedent" without cognitive overhead.

The `memory_ops` list has no model-level bound. Instead, the Stream enforces `max_memory_ops_per_turn` (default 10, from `config.limits`) at turn processing time (step 10): if `len(memory_ops)` exceeds the limit, the Stream truncates to the first N ops, logs `memory_ops_truncated` to audit, and proceeds. This keeps data models free of runtime config coupling.

**PlanActionType** — Enum: `propose`, `revise`, `execute_next`, `abort`.

**InteractionRegister** — Enum: `exploration`, `execution`, `review`, `status`.

**InteractionMode** — Enum:

| Value | Meaning |
|---|---|
| `default_and_offer` | Pick best default action/answer, then present alternatives |
| `act_and_report` | Execute within policy/approval boundaries and report outcomes |
| `confirm_only_when_required` | Ask only when policy/risk/irreversibility requires user confirmation |

**PlanAction:**

| Field | Type | Description |
|---|---|---|
| `action` | `PlanActionType` | What the agent wants to do with the plan |
| `plan_markdown` | `str \| None` | Markdown plan with YAML front matter |
| `continuation_of` | `str \| None` | Work item ID this plan deepens/corrects; used to inherit prior artifacts |
| `interaction_mode_override` | `InteractionMode \| None` | Planner override for the active work item; if set, must be honored unless policy/risk forces confirmation |

**MemoryOpType** — Enum: `store`, `update`, `delete`, `link`.

**MemoryOp:**

| Field | Type | Description |
|---|---|---|
| `op` | `MemoryOpType` | Operation type |
| `content` | `str \| None` | Content to store or update |
| `memory_id` | `str \| None` | Target memory item ID (for update/delete/link) |
| `memory_type` | `MemoryType` | Type classification (defaults to `episode`) |
| `tags` | `list[str]` | Semantic tags for retrieval |
| `entity_refs` | `list[str]` | Referenced entity IDs |
| `link_to` | `str \| None` | Memory ID to create a causal link to |
| `link_type` | `str \| None` | Type of causal link |

`MemoryOp` MUST use a `model_validator` enforcing operation-specific required fields:
- `store`: `content` is required
- `update`: `memory_id` and `content` are required
- `delete`: `memory_id` is required
- `link`: `memory_id`, `link_to`, and `link_type` are required

The implementation may use either a discriminated union or a single model with this validator; behavior must be equivalent.

**MemoryQueryStrategy** — Enum: `semantic`, `temporal`, `session`, `keyword`.

**MemoryQuery:**

| Field | Type | Description |
|---|---|---|
| `strategy` | `MemoryQueryStrategy` | Retrieval strategy |
| `query` | `str` | Search query text |
| `max_results` | `int` | Maximum items to return (default 5) |
| `max_tokens` | `int` | Maximum total tokens for results (default 2000) |

**RouteDecision:**

| Field | Type | Description |
|---|---|---|
| `route` | `str` | One of: `"direct"`, `"planner"` |
| `reason` | `str` | Why this route was chosen |
| `response` | `AgentResponse \| None` | For direct routes, the immediate response |
| `interaction_register` | `InteractionRegister` | Register classification for this turn (`exploration`, `execution`, `review`, `status`) |
| `interaction_mode` | `InteractionMode` | Default operating mode for this turn; consumed by Stream, personality, and channel rendering |
| `continuation_of` | `str \| None` | Work item ID that this user turn deepens/corrects, if detected |
| `context_profile` | `str` | Required context budget profile key (e.g., `"coding"`, `"research"`). Stream MUST call `context_manager.set_profile(scope_id, ...)` after routing. |

`RouteDecision` MUST enforce:
- `context_profile` is non-empty and exists in configured profile registry
- if `route == "direct"`, `response` is required
- if `route == "planner"`, `response` is `None`

### 3.3 Work Items (`models/work.py`)

Work Items are the universal structure for all work — from one-shot tasks to indefinite goals. The `type` field determines execution behavior.

**WorkItemType** — Enum:

| Value | Behavior |
|---|---|
| `task` | Bounded, single-agent execution with retry loop |
| `project` | Collection of tasks with dependency ordering |
| `goal` | Indefinite, scheduled verification that spawns fix tasks on failure |

**WorkItemStatus** — Enum: `pending`, `running`, `healthy`, `done`, `failed`, `stuck`, `blocked`, `paused`.

**Budget** — Resource constraints enforced deterministically by the runtime:

| Field | Type | Default | Description |
|---|---|---|---|
| `max_tokens` | `int` | 200,000 | Maximum total tokens consumed |
| `max_cost_usd` | `float` | 2.00 | Maximum total API cost |
| `max_wall_time_seconds` | `int` | 1800 | Maximum wall clock time |
| `max_attempts` | `int` | 5 | Maximum execution retry attempts |
| `max_planner_calls` | `int` | 3 | Maximum planner consultations |

**BudgetUsed** — Tracks consumed resources. Checked BEFORE every action.

| Field | Type | Description |
|---|---|---|
| `tokens` | `int` | Total tokens consumed |
| `cost_usd` | `float` | Total cost consumed |
| `wall_time_seconds` | `float` | Total wall time elapsed |
| `attempts` | `int` | Number of execution attempts |
| `planner_calls` | `int` | Number of planner consultations |
| `executor_runs` | `int` | Number of executor invocations |

The `exceeds(budget)` method MUST use `>=` (greater-than-or-equal), not `>`. Reaching the exact budget limit counts as exhausted. This prevents a final action from consuming resources beyond the stated limit.

The `merge(child)` method MUST aggregate ALL fields from a child BudgetUsed, including `attempts` and `executor_runs`. This ensures parent work items (projects) accurately reflect total resource consumption across child tasks.

**Expectation** — Deterministic success predicate for verification. Exactly one field MUST be set:

| Field | Type | Description |
|---|---|---|
| `exit_code` | `int \| None` | Expected process exit code |
| `equals` | `str \| None` | Output must exactly equal this string |
| `contains` | `str \| None` | Output must contain this substring |
| `regex` | `str \| None` | Output must match this regular expression |
| `output_lt` | `float \| None` | Output (parsed as float) must be less than this value |
| `output_gt` | `float \| None` | Output (parsed as float) must be greater than this value |
| `file_exists` | `str \| None` | This file path must exist (see path constraints below) |
| `not_empty` | `bool \| None` | Output must not be empty |

`Expectation` MUST define a `model_validator` that enforces mutual exclusivity: exactly one predicate field is non-`None`/true. Zero predicates or multiple predicates are schema errors.

**Path constraint for `file_exists`:** The path MUST be validated against an allowlist of permitted directories (the verification sandbox work directory and configured project directories). Paths containing `..` MUST be rejected. The verification runner MUST NOT check arbitrary filesystem paths — this prevents the agent from probing the host filesystem (e.g., `/etc/shadow`, `~/.ssh/`) via crafted verification checks.

**VerificationCheck** — External check the agent cannot modify:

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Human-readable check name |
| `run` | `str` | Shell command to execute inside the verification sandbox runtime |
| `expect` | `Expectation` | Success criteria |
| `timeout` | `int` | Maximum seconds (default 60) |
| `network` | `bool` | Whether the verification container needs network access (default `false`) |

**EscalationAction** — What happens when a gate blocks or verification fails:

| Field | Type | Description |
|---|---|---|
| `action` | `str` | One of: `"report"`, `"escalate_human"`, `"transfer_to_queue"`, `"suppress_and_rephrase"`, `"suppress_and_escalate"`, `"respond"`, `"spawn_task"`, `"retry"` |
| `queue` | `str \| None` | Target queue for transfers |
| `message` | `str \| None` | Message to send to the user |
| `instruction` | `str \| None` | Instructions for rephrase actions |
| `max_retries` | `int` | Maximum retry attempts (default 2) |
| `fallback` | `str \| None` | Fallback escalation name if retries exhausted |

**WorkItem** — The universal work structure:

| Field | Type | Description |
|---|---|---|
| **Identity** | | |
| `id` | `str` | Unique identifier |
| `type` | `WorkItemType` | Determines execution behavior |
| `title` | `str` | Human-readable title |
| `parent` | `str \| None` | Parent work item ID (for project children, spawned fix tasks) |
| `spawned_by` | `str \| None` | Work item ID that triggered creation of this item |
| `follow_up_of` | `str \| None` | Prior work item ID this item deepens/corrects (for "go deeper" / "try another approach" flows) |
| `domain` | `str \| None` | Domain policy boundary (`personal`, `business:*`, etc.) |
| **Scope** | | |
| `agent` | `Literal["ephemeral", "stream"]` | Execution lane: `"ephemeral"` (configured sandbox backend) or `"stream"` (main session) |
| `budget` | `Budget` | Resource constraints |
| `needs_approval` | `bool` | Whether human approval is required (default true) |
| `approval_token` | `ApprovalToken \| None` | Structured approval token after approval is granted. Stored as a full `ApprovalToken` object (not a bare string) because the runtime needs access to mutable fields (`executions_used`, `execution_nonces`) for standing approval tracking. Serialized as JSON when persisted to the work item store; deserialized on load. |
| **Briefing** | | |
| `body` | `str` | Markdown prose briefing written by Planner for the executor |
| `interaction_mode` | `InteractionMode` | Execution interaction policy for this item (`default_and_offer`, `act_and_report`, `confirm_only_when_required`; default `confirm_only_when_required`) |
| `input_artifacts_from` | `list[str]` | Artifact keys imported from `follow_up_of` before execution. `["*"]` means import all available artifacts. |
| **Verification** | | |
| `verify` | `list[VerificationCheck]` | External checks — agent CANNOT modify these |
| **Gates** | | |
| `gates` | `list[Gate]` | Mid-execution and per-turn checks |
| **Skills** | | |
| `skills` | `list[str]` | Skill names available to agents executing this work item. Skills follow the Agent Skills standard (agentskills.io): each is a directory with `SKILL.md` + `scripts/`. Agents only see skills listed here — not the full catalog. Child tasks inherit parent skills unless explicitly overridden. Changing this list changes the plan hash and requires re-approval. |
| **Access Control** | | |
| `access_levels` | `dict[str, AccessLevel]` | Tool access tiers for chatbot deployments |
| **Escalation** | | |
| `escalation` | `dict[str, EscalationAction]` | Named escalation actions |
| **Lifecycle** | | |
| `schedule` | `str \| None` | Schedule for goals: a cron expression (e.g., `"*/30 * * * *"`) for periodic verification, or the literal `"always_on"` for continuously-running goals (e.g., chatbot deployments that run as long as the Stream is active). `None` means no schedule. |
| `on_failure` | `str` | Escalation name on failure (default `"report"`) |
| `on_stuck` | `str` | What to do when stuck (default `"consult_planner"`) |
| `failure_context` | `str \| None` | Template for spawned fix tasks; `$failed_checks` is replaced with failure details |
| **Hierarchy** | | |
| `tasks` | `list[str]` | Child task IDs (for projects) |
| `depends_on` | `list[str]` | Task IDs that must complete before this starts |
| **State (managed by runtime, NOT agent)** | | |
| `status` | `WorkItemStatus` | Current status (default `pending`) |
| `attempts` | `int` | Number of attempts so far |
| `budget_used` | `BudgetUsed` | Resources consumed so far |
| `verification_results` | `list[dict]` | History of verification run results |
| `created_at` | `datetime` | Creation timestamp (timezone-aware UTC) |

`WorkItem.agent` execution semantics (normative):
- `agent == "ephemeral"`: The item is executable work and is dispatched through `WorkItemExecutor.execute()` (task/project/goal cycle spawned tasks).
- `agent == "stream"`: The item configures the long-lived Stream lane (for example chatbot `always_on` goals: gates, access levels, interaction defaults, personality context). It is loaded as active Stream configuration and is NOT dispatched to `WorkItemExecutor.execute()` as an ephemeral task body.
- Validation rule: `task` and `project` work items MUST use `agent="ephemeral"`. `goal` may use either lane; `goal + schedule="always_on"` defaults to `agent="stream"` unless explicitly overridden.

**WorkItemResult** — Deterministic executor return contract:

| Field | Type | Description |
|---|---|---|
| `work_item_id` | `str` | ID of the executed work item |
| `status` | `WorkItemStatus` | Final status (`done`, `healthy`, `failed`, `stuck`, or `blocked`) |
| `summary` | `str` | Human-readable outcome summary |
| `last_error` | `str \| None` | Last error/reason when not successful |
| `verification_results` | `list[dict]` | Verification results collected during execution |
| `budget_used` | `BudgetUsed` | Final budget consumption snapshot |
| `artifacts` | `dict[str, str]` | Named outputs from execution (if any) |
| `next_steps` | `list[str]` | Suggested follow-up actions for user-facing suggestion cards |

**Plan hash canonicalization (`work_item_plan_hash_bytes`)**:

`ApprovalToken.plan_hash` MUST be computed from a canonical JSON projection of immutable WorkItem fields, NOT from a full model serialization. This prevents runtime-managed or mutable fields (e.g., approval token attachment, execution counters, status updates) from breaking hash verification after approval.

- **Included fields** (canonical approval projection):
  - `id`, `type`, `title`, `parent`, `spawned_by`, `follow_up_of`, `domain`
  - `agent`, `budget`, `body`, `interaction_mode`, `input_artifacts_from`, `verify`, `gates`, `skills`
  - `access_levels`, `escalation`, `schedule`, `on_failure`, `on_stuck`, `failure_context`
  - `tasks`, `depends_on`
- **Excluded fields** (runtime/mutable, never part of plan hash):
  - `approval_token`, `needs_approval`
  - `status`, `attempts`, `budget_used`, `verification_results`, `created_at`

Serialization rules for canonical bytes:
1. Build a dict containing only included fields above.
2. Serialize as JSON with sorted keys and no insignificant whitespace (`separators=(",", ":")`).
3. Encode UTF-8 bytes.
4. Compute SHA-256 over those bytes.

### 3.4 Gates (`models/gates.py`)

Gates are the unified primitive for guardrails, approval flows, mid-execution checks, and access control. A gate checks a value against criteria and decides: **continue**, **block**, or **require approval**.

**GateType** — Enum:

| Value | Description |
|---|---|
| `numeric_range` | Value falls within defined ranges |
| `string_match` | Value matches allowed/approval/blocked string sets |
| `regex` | Value matches a regular expression |
| `file_valid` | File exists and meets criteria |
| `approval_always` | Always requires human approval |
| `custom_check` | Evaluated by running a script or callable |

**GateLane** — Enum:

| Value | Description |
|---|---|
| `policy` | Blocking, deterministic. May return `continue`, `block`, or `require_approval`. Providers: `predicate`, `guardrails_ai`, `script`, `custom`. Always synchronous. |
| `quality` | Non-blocking, advisory. May only return `continue` with optional scores/flags. Provider: `llm`. Results are reported via audit, never block execution. |

The lane is derived from the provider: `llm` → `quality`, all others → `policy`. A gate can be explicitly promoted from quality to policy via `promote_to_policy: true` in its config, which makes the LLM gate synchronous and blocking (use sparingly — see ADR-013).

**GateProvider** — Enum:

| Value | Lane | Description |
|---|---|---|
| `guardrails_ai` | `policy` | Guardrails validator execution by configured validator name |
| `predicate` | `policy` | Deterministic checks (numeric ranges, regex, string match) |
| `llm` | `quality` | Structured LLM quality checks (default model from `gates.llm_defaults`). Advisory-only unless `promote_to_policy` is set. |
| `script` | `policy` | Custom shell scripts with input sanitization |
| `custom` | `policy` | Python callable |

**GateTrigger** — Enum:

| Value | When it fires |
|---|---|
| `every_user_message` | Before processing any user input |
| `every_agent_response` | Before sending any agent response to the user |
| `after_step` | After a specific plan step completes |
| `on_tool_call` | Before a tool is executed |

**Gate:**

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Unique gate identifier |
| `on` | `GateTrigger` | When this gate fires |
| `after_step` | `int \| None` | Step index for `after_step` trigger |
| `provider` | `GateProvider` | Which provider evaluates this gate (default `predicate`) |
| `type` | `GateType` | What kind of check (default `string_match`) |
| `check` | `str \| None` | Provider-specific check name or script command |
| `config` | `dict` | Provider-specific configuration |
| `extract` | `str \| None` | Variable name to extract from agent output |
| `auto_approve` | `dict \| None` | Numeric range for auto-approval: `{"min": float, "max": float}` |
| `require_approval` | `dict \| None` | Numeric range requiring human approval: `{"min": float, "max": float}` |
| `block` | `dict \| None` | Values outside this range are blocked: `{"outside": [float, float]}` |
| `allowed_values` | `list[str] \| None` | String values that auto-approve |
| `approval_values` | `list[str] \| None` | String values requiring human approval |
| `on_block` | `str` | Escalation name from `WorkItem.escalation` (default `"report"`) |
| `check_command` | `str \| None` | Shell command for `custom_check` type |
| `check_expect` | `Expectation \| None` | Success criteria for `custom_check` |
| `promote_to_policy` | `bool` | If true, an `llm` provider gate is promoted to the policy lane (synchronous, blocking). Default false. Ignored for non-`llm` providers. |

**AccessLevel:**

| Field | Type | Description |
|---|---|---|
| `description` | `str` | Human-readable description of this level |
| `tools` | `list[str]` | Tool names available at this level |
| `requires` | `list[str]` | Gate names that must all pass to reach this level |
| `expires_after` | `int \| None` | Seconds until access drops back to default; `None` means no expiry |

**GateResult:**

| Field | Type | Description |
|---|---|---|
| `gate_name` | `str` | Which gate produced this result |
| `lane` | `str` | `"policy"` or `"quality"` — which lane produced this result |
| `action` | `str` | One of: `"continue"`, `"block"`, `"require_approval"`. Quality-lane gates MUST only return `"continue"`. |
| `reason` | `str` | Human-readable explanation |
| `value` | `str \| float \| None` | The value that was checked |
| `score` | `float \| None` | Quality-lane only: numeric score from the LLM check (0.0–1.0). `None` for policy-lane gates. |
| `flags` | `list[str]` | Quality-lane only: advisory flags (e.g., `"off_topic"`, `"low_confidence"`, `"verbose"`). Empty list for policy-lane gates. |
| `modified_context` | `dict \| None` | Optional context rewrite. Keys MUST be in the mutation allowlist (see below). Disallowed keys are silently dropped and logged to audit as `rejected_mutation`. |

**Mutation allowlist (`ALLOWED_MUTATIONS`):**

Only the following top-level keys are permitted in `modified_context`. The gate runner strips any key not in this set before merging, and logs each rejected key to audit as `rejected_mutation`.

| Key | Type | Purpose |
|---|---|---|
| `response` | `str` | Rewrite agent response text (e.g., PII redaction) |
| `message` | `str` | Rewrite input message text (e.g., normalization) |
| `tool_args` | `dict` | Shallow-merge into tool call arguments (e.g., clamp position size, redirect endpoint, strip flags) |

Example mutations:
- Tool input rewrite: `{"tool_args": {"command": "npm test --no-coverage"}}`
- Risk clamp: `{"tool_args": {"position_usd": 25.0}}`
- Endpoint redirect: `{"tool_args": {"base_url": "https://staging.api.example.com"}}`
- PII redaction: `{"response": "Your account ending in [REDACTED] has been updated."}`

**Gate mutation semantics:** When `modified_context` is present (after allowlist filtering), the gate runner merges it into the evaluation context and uses the merged context for subsequent execution steps. Mutations MUST be logged to audit with both original and modified values (redacted where sensitive). Only policy-lane gates may produce `modified_context`; quality-lane mutations are dropped.

### 3.5 Context (`models/context.py`)

**ContextZone** — Enum defining the four zones of the context window:

| Zone | Purpose | Eviction Policy |
|---|---|---|
| `system` | System prompt, constitution, tool descriptions | Never evicted |
| `chronicle` | Conversation history (user messages + agent responses) | Sliding window: oldest-first with observation masking |
| `memory` | Retrieved memories (semantic, temporal, entity, causal) | Lowest-relevance-first (scored by retriever, not agent) |
| `workspace` | Active plans, execution results, subscriptions | Completed-before-active; deactivated subscriptions cost zero tokens |

Zones are an **internal organizational concept** for the harness. The agent does not manage zones — it sees metadata-tagged blocks in the rendered context and can request memory retrieval, but all eviction, pinning, and lifecycle decisions are harness-controlled.

**Context budget model:** Zone budgets are selected from **task-type profiles** rather than fixed percentages. The Proxy's routing decision (step 7) classifies the interaction, and the harness selects the matching profile. This allows a coding task to allocate more to workspace while a conversation allocates more to chronicle.

**ContextProfile** — Named budget allocation profiles loaded from config:

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Profile identifier (open string key, e.g., `"conversation"`, `"coding"`, `"research"`, `"support"`) |
| `chronicle_pct` | `float` | Chronicle zone share of allocable budget |
| `memory_pct` | `float` | Memory zone share of allocable budget |
| `workspace_pct` | `float` | Workspace zone share of allocable budget |

Profiles are a registry in config (`context.profiles`). The harness treats profile names as opaque keys and does not hardcode specific profile IDs.

`ContextProfile` MUST enforce:
- A `field_validator` on each percentage (`chronicle_pct`, `memory_pct`, `workspace_pct`) with inclusive range `0.0 <= value <= 1.0`
- A `model_validator` requiring `chronicle_pct + memory_pct + workspace_pct <= 0.80`

Headroom math is computed at render time against the **allocable budget**, not raw total window:
1. `system_actual = min(system_zone_tokens, TokenBudget.system_max)`
2. `allocable_budget = max(TokenBudget.total - system_actual, 0)`
3. `zone_budget(zone) = floor(allocable_budget * zone_pct)`
4. `headroom = allocable_budget - (chronicle_budget + memory_budget + workspace_budget)`

Because profile percentages are capped at 0.80, minimum headroom is 20% of `allocable_budget` (after system-zone occupancy), not 20% of `TokenBudget.total`.

**ContextItem:**

| Field | Type | Description |
|---|---|---|
| `ctx_id` | `str` | Unique identifier (internal, for harness tracking) |
| `zone` | `ContextZone` | Which zone this item belongs to |
| `content` | `str` | The text content (or masked placeholder after observation masking) |
| `token_count` | `int` | Pre-counted token size |
| `created_at` | `datetime` | When this item was added (timezone-aware UTC) |
| `turn_number` | `int` | The turn in which this item was created |
| `source` | `str` | Origin label (e.g., `"channel:web"`, `"memory:profile"`, `"agent:planner"`) |
| `taint` | `TaintLevel` | Trust classification propagated from source (`owner`, `auth`, `external`) |
| `kind` | `str` | Content type: `"message"`, `"tool_result"`, `"memory"`, `"plan"`, `"execution_result"`, `"subscription"`, `"system"` |
| `relevance` | `float` | Relevance score (default 1.0). Set by retriever for memory items, 1.0 for live content. |
| `masked` | `bool` | Whether this item has been observation-masked (content replaced with placeholder) |
| `pinned` | `bool` | Whether this item is protected from eviction (harness-controlled, never agent-controlled) |

The `kind` field drives the metadata tag rendered in the context window. Each block is rendered with a delimiter that communicates provenance to the agent:

```
--- memory | 2026-01-15 | "auth module bug fix pattern" ---
We fixed the auth module by extracting the validation into a middleware...
--- end ---

--- turn 14 | user | live ---
Apply the same fix pattern to the payment module
--- end ---

--- subscription:file | auth.py:10-45 | live | last_changed: turn 8 ---
def validate_token(token: str) -> bool:
    ...
--- end ---

--- tool_result | turn 12 | masked ---
[Result of shell_exec("npm test") — 847 tokens — see memory for details]
--- end ---
```

**ContextSubscription** — A reference to a live resource that is materialized (resolved to current content) on each LLM call:

| Field | Type | Description |
|---|---|---|
| `sub_id` | `str` | Unique subscription identifier |
| `sub_type` | `str` | One of: `"file"`, `"file_lines"`, `"memory_query"` |
| `target` | `str` | Resource locator: file path, `"path:start-end"` for line ranges, or memory query string |
| `zone` | `ContextZone` | Zone where materialized content is placed (typically `workspace`) |
| `created_at` | `datetime` | When the subscription was created |
| `turn_created` | `int` | Turn number when created |
| `content_hash` | `str` | SHA-256 of last materialized content (for change detection) |
| `active` | `bool` | Whether to materialize on next render (deactivated subs cost zero tokens) |
| `token_count` | `int` | Token count of last materialization |

**Subscription semantics:**
- **File subscriptions** (`"file"`): Materializes the full file content. If the file exceeds a configurable `max_subscription_tokens` (default 2,000), the subscription is automatically deactivated and a warning is logged.
- **File line subscriptions** (`"file_lines"`): Materializes a line range. If the content at those lines no longer matches `content_hash`, the harness flags the block as `[stale — file changed since turn N]` in the rendered metadata. Future phases will add semantic anchors (function/class names) as stable references.
- **Memory query subscriptions** (`"memory_query"`): Re-executes the query on each render. Results are deduped against existing memory zone items.
- **Deduplication**: A whole-file subscription supersedes all line-range subscriptions for the same file. Overlapping line ranges are merged.
- **Lifecycle**: Subscriptions are deactivated (not deleted) by the eviction system. Deactivated subscriptions cost zero tokens but can be reactivated instantly by the harness if the scorer determines they're relevant again. The harness deactivates subscriptions that haven't been referenced in the agent's responses for `subscription_ttl_turns` turns (default 10).
- **Materialization caching**: Within the same turn, materialized content is cached. If the underlying resource hasn't changed (same `content_hash`), the cached version is reused. Memory query subscriptions always re-query (the memory store may have been updated by `memory_ops` in the same turn).

**TokenBudget:**

| Field | Type | Default | Description |
|---|---|---|---|
| `total` | `int` | 180,000 | Total context window token budget |
| `system_max` | `int` | 8,000 | Fixed cap for system zone |
| `skill_metadata_budget_pct` | `float` | 0.02 | Maximum share of `total` allocated to unactivated skill metadata in routing context. If exceeded, lowest-priority skill metadata is excluded deterministically. |
| `eviction_threshold_pct` | `float` | 0.80 | Trigger two-tier eviction when total usage exceeds this percentage |
| `scorer_threshold_pct` | `float` | 0.90 | Trigger scorer-model eviction when heuristic eviction alone doesn't bring usage below `eviction_threshold_pct` |
| `max_subscription_tokens` | `int` | 2,000 | Maximum tokens for a single subscription materialization |
| `subscription_ttl_turns` | `int` | 10 | Turns of non-reference before a subscription is deactivated |
| `observation_mask_after_turns` | `int` | 5 | Tool results older than this many turns are observation-masked |
| `profiles` | `dict[str, ContextProfile]` | (see defaults above) | Named budget allocation profiles |
| `default_profile` | `str` | `"conversation"` | Profile used when routing doesn't specify one |

### 3.6 Approval (`models/approval.py`)

**ApprovalScope** — Enum:

| Value | Description |
|---|---|
| `full_plan` | Approves the entire plan |
| `single_step` | Approves one specific step |
| `step_range` | Approves a range of steps |
| `tool_type` | Approves use of a tool category |
| `skill_install` | Approves skill installation |
| `credential_use` | Approves credential access |
| `budget` | Approves a budget allocation |
| `self_update` | Approves updates to Silas runtime/code/configuration; always high-risk |
| `connection_act` | Approves state-changing actions on connected services |
| `connection_manage` | Approves connection lifecycle/configuration changes |
| `autonomy_threshold` | Approves widening/tightening autonomy threshold parameters |
| `standing` | Pre-approved for recurring goals (higher `max_executions`); each use MUST be verified via the approval engine (signature, plan hash, expiry, execution nonce) — standing does not mean unchecked |

**ApprovalVerdict** — Enum: `approved`, `declined`, `edit_requested`, `conditional`.

**ApprovalDecision** — User decision collected by a channel before token minting:

| Field | Type | Description |
|---|---|---|
| `verdict` | `ApprovalVerdict` | User decision |
| `approval_strength` | `Literal["tap"]` | User-verification strength for this spec (`tap` only) |
| `conditions` | `dict` | Optional user-provided conditions |

**ApprovalToken:**

| Field | Type | Description |
|---|---|---|
| `token_id` | `str` | Unique token identifier |
| `plan_hash` | `str` | SHA-256 hash of the canonical immutable WorkItem approval projection (see Section 3.3 plan hash canonicalization), not the full runtime model serialization |
| `work_item_id` | `str` | ID of the approved work item |
| `scope` | `ApprovalScope` | What scope is approved |
| `verdict` | `ApprovalVerdict` | The approval decision |
| `signature` | `Base64Bytes` | Ed25519 signature by owner over canonical bytes. `Base64Bytes` is a custom Pydantic type (defined once in `models/approval.py`) that stores raw `bytes` in memory but serializes to/from base64 strings in JSON. This ensures deterministic round-tripping through SQLite JSON columns and audit logs. |
| `issued_at` | `datetime` | When the token was issued (timezone-aware UTC) |
| `expires_at` | `datetime` | When the token expires (timezone-aware UTC) |
| `nonce` | `str` | Unique token nonce — included in the signature to bind the token's identity, but NOT consumed via the nonce store (see execution nonces below) |
| `approval_strength` | `Literal["tap"]` | Signed user-verification metadata (`tap` only in this spec) |
| `conditions` | `dict` | Optional conditions on the approval |
| `executions_used` | `int` | How many times this token has been consumed |
| `max_executions` | `int` | Maximum allowed uses (default 1; higher for standing approvals) |
| `execution_nonces` | `list[str]` | Per-execution nonces consumed so far (managed by runtime, not included in signature) |

For `scope == standing`, `conditions` MUST include `spawn_policy_hash` (SHA-256 over the goal's deterministic spawn policy projection: `failure_context`, `skills`, `gates`, `verify`, and allowed escalation config). This binds standing approval to a specific class of spawned tasks.

`spawn_policy_hash` canonicalization (normative):
- Hash the parent goal's **spawn policy template**, not per-cycle substituted content.
- `failure_context` is hashed as the raw template string exactly as stored on the goal (including `$failed_checks` placeholder where present).
- Build this canonical projection:

```python
canonical = {
  "failure_context_template": goal.failure_context or "",
  "skills": sorted(set(goal.skills or [])),
  "gates": sorted(
    [canonical_json(gate.model_dump(mode="json")) for gate in (goal.gates or [])]
  ),
  "verify": sorted(
    [canonical_json(check.model_dump(mode="json")) for check in (goal.verify or [])]
  ),
  "escalation_config": {
    name: goal.escalation[name].model_dump(mode="json")
    for name in sorted(
      set([goal.on_failure] + [g.on_block for g in (goal.gates or []) if g.on_block])
      .intersection(set((goal.escalation or {}).keys()))
    )
  },
}
spawn_policy_hash = sha256(
  json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
```

Where `canonical_json(obj)` means:
`json.dumps(obj, sort_keys=True, separators=(",", ":"))`.

All list/object normalization above is required. Implementations that hash equivalent data with different ordering are non-compliant.

`Base64Bytes` implementation requirement:
- Define as an annotated type with `PlainSerializer` (bytes→base64 str) and `PlainValidator` (base64 str→bytes).
- Plain implicit bytes JSON handling is forbidden for approval tokens.

**Scope minimum verification levels:**
- All scopes: `tap`

`approval_strength` controls UX friction, not authorization semantics. All issued tokens use `tap`.

**Replay protection model:** The token's `nonce` field uniquely identifies the token itself and is included in the signed canonical bytes, but it is NOT recorded in the nonce store at creation or consumed at verification. Instead, each *use* of the token generates a fresh **execution nonce** (a cryptographic random string). The execution nonce is checked against the nonce store for uniqueness using a content-bound key (`token_id + plan_or_spawn_hash + execution_nonce`), then recorded as consumed. This allows multi-use standing approval tokens (`max_executions > 1`) to be used repeatedly without token-level nonce conflicts while preventing replay across different spawned tasks. For single-use tokens (`max_executions == 1`), the behavior is equivalent — one execution nonce is generated and consumed.

Canonical bytes for token signing MUST use canonical JSON, not delimiter concatenation:

1. Build an object containing all signed fields: `plan_hash`, `work_item_id`, `scope`, `verdict`, `nonce`, `approval_strength`, `issued_at`, `expires_at`, `max_executions`, `conditions`.
2. Serialize with sorted keys and no insignificant whitespace (`separators=(",", ":")`).
3. Encode as UTF-8.

All security-critical fields MUST be included in the signed payload — omitting a field allows it to be altered without breaking signature verification.

### 3.7 Execution (`models/execution.py`)

**SandboxConfig:**

| Field | Type | Default | Description |
|---|---|---|---|
| `backend` | `str \| None` | `None` | Optional backend override: `"subprocess"` or `"docker"`. `None` uses global config |
| `work_dir` | `str` | `"./data/sandbox/work"` | Working directory in the sandbox runtime (host path for subprocess, mapped path for Docker) |
| `network_access` | `bool` | `False` | Whether outbound network is requested. If `false`, backend MUST enforce network isolation; if enforcement is unavailable, execution MUST fail closed. |
| `filesystem_read` | `list[str]` | `[]` | Paths exposed read-only to the sandbox runtime |
| `filesystem_write` | `list[str]` | `[]` | Paths exposed read-write to the sandbox runtime |
| `max_memory_mb` | `int` | 512 | Best-effort memory limit |
| `max_cpu_seconds` | `int` | 60 | Best-effort CPU time limit |
| `env` | `dict[str, str]` | `{}` | Environment variables exposed to sandboxed execution |

**ExecutionEnvelope** — Everything an ephemeral executor receives. Nothing more.

| Field | Type | Description |
|---|---|---|
| `execution_id` | `str` | Unique execution run ID |
| `step_index` | `int` | Current step index within the plan |
| `task_description` | `str` | The prose briefing from the work item |
| `action` | `str` | Executor type to use (e.g., `"shell_exec"`, `"python_exec"`, `"web_search"`) |
| `args` | `dict` | Action-specific arguments |
| `input_artifacts` | `dict[str, str]` | Named input files/data from previous steps |
| `credential_refs` | `dict[str, str]` | Opaque keyring reference IDs for credentials this action needs (e.g., `{"outlook": "ref_abc123"}`). Scripts read actual secrets from the OS keyring using these `ref_id` values. The envelope NEVER contains raw secret values — only opaque references. This preserves the secret isolation rule (§0.5). |
| `timeout_seconds` | `int` | Maximum execution time (default 300) |
| `max_output_bytes` | `int` | Maximum output size (default 100,000) |
| `sandbox_config` | `SandboxConfig` | Sandbox runtime configuration (backend-agnostic) |

The envelope deliberately DOES NOT contain: conversation history, memory, context, approval tokens, or any state from The Stream. Executors are stateless and ephemeral.

**ExecutionResult** — Everything an ephemeral executor returns. Then it is destroyed.

| Field | Type | Description |
|---|---|---|
| `execution_id` | `str` | Matches the envelope |
| `step_index` | `int` | Matches the envelope |
| `success` | `bool` | Whether execution completed without errors |
| `return_value` | `str` | Primary textual result (eligible for LLM context injection) |
| `content` | `list[dict]` | Optional rich content blocks (eligible for LLM context injection) |
| `metadata` | `dict[str, str \| int \| float \| bool \| list \| dict]` | Application-only metadata (audit headers, provider diagnostics, raw protocol details). MUST NEVER be injected into LLM context. |
| `artifacts` | `dict[str, str]` | Named output files/data |
| `taint` | `TaintLevel` | Trust classification of execution output (`external` by default for any network/tool-derived data) |
| `error` | `str \| None` | Error message if failed |
| `duration_seconds` | `float` | Wall time elapsed |
| `tokens_used` | `int` | LLM tokens consumed (if any) |
| `cost_usd` | `float` | LLM cost incurred (if any) |

**ExecutorAgentOutput** — Structured LLM output for executor-agent reasoning (runtime metrics excluded):

| Field | Type | Description |
|---|---|---|
| `summary` | `str` | What the agent attempted/completed |
| `artifact_refs` | `list[str]` | Optional artifact keys/paths emitted by tools |
| `next_steps` | `list[str]` | Ordered follow-up suggestions to surface in Review/Stream after completion |

`ExecutionResult` output-channel rule:
- `return_value` and `content` may be injected into model context subject to gates and taint handling.
- `metadata` is application-only and MUST be excluded from model context.

### 3.8 Memory (`models/memory.py`)

**MemoryType** — Enum: `fact`, `episode`, `preference`, `skill_note`, `decision`, `foresight`, `profile`.

**ReingestionTier** — Enum:

| Value | Description |
|---|---|
| `active` | Eligible for normal retrieval/ranking and context injection |
| `low_reingestion` | Raw log/archive lane. Stored durably but only reintroduced on explicit replay/import workflows or direct query |

**TrustLevel** — Enum:

| Value | Description |
|---|---|
| `working` | Unverified — from conversation or agent inference. Can be overwritten. |
| `verified` | Confirmed by owner or external verification. Requires owner action to modify. |
| `constitutional` | Core identity/rules. Cannot be modified at runtime. |

**MemoryItem:**

| Field | Type | Description |
|---|---|---|
| `memory_id` | `str` | Unique identifier |
| `content` | `str` | The memory content |
| `memory_type` | `MemoryType` | Classification |
| `reingestion_tier` | `ReingestionTier` | Ingestion lane (default `active`) |
| `trust_level` | `TrustLevel` | Trust tier (default `working`) |
| `taint` | `TaintLevel` | Source taint (default `owner`) |
| `created_at` | `datetime` | Creation timestamp (timezone-aware UTC) |
| `updated_at` | `datetime` | Last modification timestamp (timezone-aware UTC) |
| `valid_from` | `datetime \| None` | Temporal validity start |
| `valid_until` | `datetime \| None` | Temporal validity end |
| `access_count` | `int` | Number of times retrieved |
| `last_accessed` | `datetime \| None` | Last retrieval timestamp |
| `semantic_tags` | `list[str]` | Tags for keyword/faceted search |
| `entity_refs` | `list[str]` | Referenced entity IDs for entity graph traversal |
| `causal_refs` | `list[str]` | IDs of causally related memories |
| `temporal_next` | `str \| None` | Next memory in temporal chain |
| `temporal_prev` | `str \| None` | Previous memory in temporal chain |
| `session_id` | `str \| None` | Session this memory was created in |
| `embedding` | `list[float] \| None` | Vector embedding for semantic search |
| `source_kind` | `str` | Input origin: `"conversation_raw"`, `"research_query_raw"`, `"tool_output_raw"`, `"memory_op"`, etc. |

**Behavioral preference inference (required):**
- Behavioral defaults are stored as `MemoryItem` records with `memory_type="preference"` and semantic tags prefixed with `behavior:` (for example `behavior:approval/archive`, `behavior:response/include_sources`, `behavior:depth/geopolitics`).
- Inferred preferences are written with `trust_level="working"` first.
- Promotion to `trust_level="verified"` requires explicit user confirmation via a decision/review card.
- Planner and Proxy consume verified behavioral preferences as default policy hints (for example default depth, source citation preference, default reviewed-batch action bias).

### 3.9 Sessions (`models/sessions.py`)

**SessionType** — Enum: `stream` (permanent main session), `side` (ephemeral focused session).

**Session:**

| Field | Type | Description |
|---|---|---|
| `session_id` | `str` | Unique identifier |
| `session_type` | `SessionType` | Stream or side |
| `title` | `str` | Human-readable title |
| `created_at` | `datetime` | Creation timestamp (timezone-aware UTC) |
| `last_active` | `datetime` | Last activity timestamp (timezone-aware UTC) |
| `turn_count` | `int` | Number of turns in this session |
| `active` | `bool` | Whether the session is currently active |
| `pinned_ctx_ids` | `list[str]` | Context items pinned in this session |

### 3.10 Personality (`models/personality.py`)

Personality is represented as numeric control axes plus qualitative voice settings. The LLM never receives raw axis values; runtime renders prose directives from the effective state.

**PersonalityAxis** — Enum:

| Axis | Range | Meaning |
|---|---|---|
| `warmth` | 0.0–1.0 | Clinical ↔ Caring |
| `assertiveness` | 0.0–1.0 | Deferential ↔ Confrontational |
| `verbosity` | 0.0–1.0 | Terse ↔ Elaborate |
| `formality` | 0.0–1.0 | Casual ↔ Professional |
| `humor` | 0.0–1.0 | Dry/none ↔ Playful |
| `initiative` | 0.0–1.0 | Reactive ↔ Proactive |
| `certainty` | 0.0–1.0 | Hedging ↔ Declarative |

**PersonaContextKey** — String key:

| Field | Type | Description |
|---|---|---|
| `context_key` | `str` | Open registry key resolved against `personality.contexts` config map |

**AxisProfile:**

| Field | Type | Description |
|---|---|---|
| `warmth` | `float` | 0..1 |
| `assertiveness` | `float` | 0..1 |
| `verbosity` | `float` | 0..1 |
| `formality` | `float` | 0..1 |
| `humor` | `float` | 0..1 |
| `initiative` | `float` | 0..1 |
| `certainty` | `float` | 0..1 |

**MoodState:**

| Field | Type | Description |
|---|---|---|
| `energy` | `float` | 0..1, session drive |
| `patience` | `float` | 0..1, tolerance for repetition/failure |
| `curiosity` | `float` | 0..1, novelty-seeking tendency |
| `frustration` | `float` | 0..1, pressure accumulated from blockers |

**VoiceConfig:**

| Field | Type | Description |
|---|---|---|
| `tone` | `str` | Qualitative tone descriptor |
| `quirks` | `list[str]` | Signature micro-behaviors |
| `speech_patterns` | `list[str]` | Preferred phrasings/rhythm |
| `anti_patterns` | `list[str]` | Forbidden style habits |

**PersonaPreset:**

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Preset ID (e.g., `default`, `work`, `review`) |
| `axes` | `AxisProfile` | Baseline axis values for this preset |
| `voice` | `VoiceConfig` | Voice profile for this preset |

**PersonaState** (persisted in SQLite):

| Field | Type | Description |
|---|---|---|
| `scope_id` | `str` | Personality scope (`owner` or connection ID) |
| `baseline_axes` | `AxisProfile` | Slow-changing baseline |
| `mood` | `MoodState` | Fast-changing session state |
| `active_preset` | `str` | Current preset name |
| `voice` | `VoiceConfig` | Active voice config |
| `last_context` | `str` | Last detected context key |
| `updated_at` | `datetime` | Last state update (timezone-aware UTC) |

**PersonaEvent** (append-only):

| Field | Type | Description |
|---|---|---|
| `event_id` | `str` | Unique ID |
| `scope_id` | `str` | Same scope as persona state |
| `event_type` | `str` | Runtime event (`task_completed`, `ci_failure`, `feedback_too_harsh`, etc.) |
| `trusted` | `bool` | Whether event source is trusted for baseline drift |
| `delta_axes` | `dict[str, float]` | Axis adjustments from this event |
| `delta_mood` | `dict[str, float]` | Mood adjustments from this event |
| `source` | `str` | Event source (`runtime`, `owner_feedback`, `channel`) |
| `created_at` | `datetime` | Event time (timezone-aware UTC) |

**Effective axis composition (deterministic):**

`effective_axes = clamp(baseline_axes + context_delta + mood_delta + user_override, 0.0, 1.0)`

The personality layer MUST apply composition deterministically and keep constitution rules immutable. Personality can shape wording, pacing, and tone, but MUST NOT alter security/approval/policy decisions.

### 3.11 Batch Review Actions (`models/review.py`)

This is the core data model for domain-agnostic reviewed batch execution.

**BatchActionType** — String key:

| Field | Type | Description |
|---|---|---|
| `action` | `str` | Domain-defined action key (for example `archive`, `mark_read`, `close_ticket`) |

**BatchActionItem:**

| Field | Type | Description |
|---|---|---|
| `item_id` | `str` | Domain-specific item identifier |
| `title` | `str` | Item title/subject preview |
| `actor` | `str` | Source actor (sender, service account, queue, etc.) |
| `occurred_at` | `datetime` | Item timestamp |
| `reason` | `str` | Why this item is in the batch |
| `confidence` | `float` | Confidence score for this item |

**BatchProposal:**

| Field | Type | Description |
|---|---|---|
| `batch_id` | `str` | Unique batch proposal ID |
| `goal_id` | `str` | Parent goal work item ID |
| `action` | `str` | Proposed domain action key |
| `items` | `list[BatchActionItem]` | Items in this batch (default size from config) |
| `reason_summary` | `str` | Human-readable rationale for batch |
| `confidence_min` | `float` | Minimum confidence in this batch |
| `created_at` | `datetime` | Proposal creation time |

**BatchActionVerdict** — Enum: `approve`, `decline`, `edit_selection`.

**BatchActionDecision:**

| Field | Type | Description |
|---|---|---|
| `verdict` | `BatchActionVerdict` | User decision for this batch |
| `selected_item_ids` | `list[str]` | Optional subset override. Required when `verdict == edit_selection`; ignored otherwise. |

**DraftVerdict** — Enum: `approve`, `edit`, `rephrase`, `reject`.

**DecisionOption:**

| Field | Type | Description |
|---|---|---|
| `label` | `str` | Chip/button label |
| `value` | `str` | Machine-readable value |
| `approval_tier` | `Literal["tap"]` | Verification tier (`tap` only in this spec) |

**DecisionResult:**

| Field | Type | Description |
|---|---|---|
| `selected_value` | `str \| None` | Selected option value |
| `freetext` | `str \| None` | Optional free-text |
| `approved` | `bool` | Whether decision is accepted |

**SuggestionProposal** — Proactive "what next" card payload:

| Field | Type | Description |
|---|---|---|
| `suggestion_id` | `str` | Unique suggestion ID |
| `scope_id` | `str` | Scope the suggestion belongs to |
| `source` | `str` | Origin (`idle_heartbeat`, `post_execution`, `goal_monitor`) |
| `intent` | `str` | One-line action intent |
| `rationale` | `str` | Why this suggestion is timely |
| `risk_level` | `Literal["low","medium","high","irreversible"]` | Deterministic risk classification |
| `proposed_work_item_id` | `str \| None` | Optional linked work item prepared for approval/execution |
| `cooldown_key` | `str` | Dedupe/cooldown key to prevent repetitive suggestions |
| `created_at` | `datetime` | Proposal creation time |

**AutonomyThresholdChange:**

| Field | Type | Description |
|---|---|---|
| `key` | `str` | Threshold key (`standing.scope`, `standing.max_executions`, `standing.expires_at`, `batch_review.default_size`, `batch_review.confidence.high_min`) |
| `current_value` | `str` | Current canonical value |
| `proposed_value` | `str` | Proposed canonical value |
| `direction` | `Literal["widen","tighten"]` | Whether autonomy increases or decreases |

**AutonomyThresholdProposal** — Explicit autonomy-tuning change request:

| Field | Type | Description |
|---|---|---|
| `proposal_id` | `str` | Unique proposal ID |
| `scope_id` | `str` | Scope receiving the proposal |
| `goal_id` | `str \| None` | Associated goal, if any |
| `changes` | `list[AutonomyThresholdChange]` | Proposed threshold changes |
| `sample_size` | `int` | Number of observed decisions/actions in the evidence window |
| `correction_rate` | `float` | Rolling correction rate (`edits + declines + undos`) / total |
| `evidence_window` | `str` | Human-readable window summary (for example `"last 50 batches / 14 days"`) |
| `rationale` | `str` | Why this widening/tightening is proposed |
| `risk_level` | `Literal["medium","high"]` | Risk of changing autonomy boundaries |
| `created_at` | `datetime` | Proposal creation time |

**AutonomyThresholdDecision** — Enum: `approve`, `decline`, `tighten_now`.

**Batch security binding rule:** approval token binding for a batch MUST include exact `action` + ordered `item_id` list + item count. Changing any item requires fresh approval.

Batch token binding is implemented as:
1. Build canonical batch projection: `{"goal_id": ..., "action": ..., "item_ids": [...ordered...], "count": N}`
2. Serialize as canonical JSON (sorted keys, compact separators)
3. Compute `batch_hash = sha256(bytes)`
4. Issue `ApprovalToken` for batch execution with `plan_hash = batch_hash`

`BatchProposal.batch_id` is metadata only and MUST NOT be the authorization hash.

**Autonomy-threshold security binding rule:** approval token binding for an autonomy-threshold proposal MUST include exact ordered `changes` and evidence metadata (`scope_id`, `goal_id`, `sample_size`, `correction_rate`, `evidence_window`). Changing any proposed parameter requires fresh approval.

### 3.12 Connection Models (`models/connections.py`)

Data models for the connection framework's setup conversation, auth strategies, health monitoring, and failure handling.

**AuthStrategy** — Literal: `"device_code"`, `"browser_redirect"`, `"secure_input"`.

Each strategy maps to a different setup UX pattern:
- `device_code`: User enters a code on the service's website (no browser redirect, no secret in Silas). Best for local/CLI agents. Supported by Microsoft, GitHub.
- `browser_redirect`: Silas opens the user's browser and catches the OAuth redirect on a localhost port. Requires a browser. Supported by Spotify, Notion (public OAuth).
- `secure_input`: User pastes a static token/API key via the secure input mechanism (§0.5.3, §5.10.1). The secret never enters the agent pipeline. Used for Notion internal tokens, GitHub PATs, any API-key-based service.

**SecureInputRequest:**

| Field | Type | Description |
|---|---|---|
| `ref_id` | `str` | Opaque reference ID (generated by setup script, used as keyring key) |
| `label` | `str` | Human-readable label (e.g., "Notion integration token") |
| `input_hint` | `str \| None` | Prefix hint for the input (e.g., `"ntn_..."`) — visual only, NOT validation |
| `guidance` | `dict` | Rendered by channel: `instructions` (str), `help_url` (str\|None) |

**SecureInputCompleted** — What the agent receives after the user submits a secret:

| Field | Type | Description |
|---|---|---|
| `ref_id` | `str` | Same opaque reference ID |
| `success` | `bool` | Whether the secret was stored successfully |

The agent NEVER receives the secret value, a hash of the secret, or any information derived from the secret. Only the boolean `success` signal.

**SetupStep** — Discriminated union emitted by connection setup scripts. The `type` field determines the variant:

| Type | Fields | Description |
|---|---|---|
| `device_code` | `verification_url`, `user_code`, `expires_in` (seconds), `poll_interval` (seconds) | Display code for user to enter on service's site |
| `browser_redirect` | `auth_url`, `listening_on` (host:port of localhost callback server) | Open user's browser to auth URL |
| `secure_input` | `request: SecureInputRequest` | Collect a secret via the secure input mechanism |
| `progress` | `message`, `progress_pct` (float\|None) | Status update during async auth flow |
| `completion` | `success` (bool), `summary` (str), `permissions_granted` (list[str]) | Final result of setup |
| `failure` | `failure: ConnectionFailure` | Setup failed — present recovery options |

**SetupStepResponse** — User response to a setup step:

| Field | Type | Description |
|---|---|---|
| `step_type` | `str` | Echoes the step type |
| `action` | `str` | User action: `"done"`, `"cancel"`, `"trouble"`, `"retry"` |

**HealthCheckResult** — Structured response from `health_check.py` scripts:

| Field | Type | Description |
|---|---|---|
| `healthy` | `bool` | Whether the connection is functional |
| `token_expires_at` | `datetime \| None` | When the current access token expires |
| `refresh_token_expires_at` | `datetime \| None` | When the refresh token expires (for sliding-window services) |
| `latency_ms` | `int` | API probe response time |
| `error` | `str \| None` | Error details if unhealthy |
| `warnings` | `list[str]` | Non-critical issues (e.g., "rate limit at 80%") |

**ConnectionFailure** — Structured failure with recovery options:

| Field | Type | Description |
|---|---|---|
| `failure_type` | `str` | One of: `enterprise_policy_block`, `consent_denied`, `mfa_required`, `rate_limited`, `service_unavailable`, `token_revoked`, `unknown` |
| `service` | `str` | Service display name (e.g., "Microsoft 365") |
| `message` | `str` | Human-readable explanation |
| `recovery_options` | `list[RecoveryOption]` | Actionable recovery paths |

**RecoveryOption:**

| Field | Type | Description |
|---|---|---|
| `action` | `str` | Machine-readable action key (e.g., `request_admin_approval`, `use_personal_account`, `retry_fewer_permissions`, `retry_later`, `skip`) |
| `label` | `str` | Chip label (e.g., "Ask your IT admin") |
| `description` | `str` | One-line explanation of what happens |
| `risk_level` | `Literal["low","medium","high"]` | For the risk ladder |

Connection scripts that fail MUST return a `ConnectionFailure` JSON object rather than raising a bare exception. The ConnectionManager parses the structured failure and renders the appropriate `ConnectionFailureCard` (§0.5.3).

---

## 4. Protocols

Every component is behind a `typing.Protocol`. Non-negotiable for testability, swappability, and benchmarking. Implementations can be swapped via dependency injection without changing any calling code.

### 4.1 ChannelAdapterCore

Represents the minimal message transport contract (web, Telegram, CLI).

| Method | Signature | Description |
|---|---|---|
| `listen` | `async () → AsyncIterator[tuple[ChannelMessage, str]]` | Yields incoming messages with a connection ID. The connection ID is a channel-specific identifier (e.g., WebSocket session ID, Telegram chat ID, `"cli"` for CLI). The Stream uses this ID to look up or create the per-connection `AccessController` and per-connection context scope. |
| `send` | `async (recipient_id: str, text: str, reply_to: str \| None) → None` | Send a text message to a recipient |

Also exposes `channel_name: str` as a property.

### 4.1.1 RichCardChannel (Optional Capability)

Channels that support interactive cards implement this additional protocol:

| Method | Signature | Description |
|---|---|---|
| `send_approval_request` | `async (recipient_id: str, work_item: WorkItem) → ApprovalDecision` | Present a plan to the user and collect approval/decline + optional conditions. Channels do NOT sign tokens. |
| `send_gate_approval` | `async (recipient_id: str, gate_name: str, value: str \| float, context: str) → str` | Present a gate trigger to the user and collect `"approve"` or `"block"`. The `value` parameter accepts both numeric values (from `numeric_range` gates) and string values (from `string_match` gates), matching `GateResult.value: str \| float \| None`. |
| `send_checkpoint` | `async (message: str, options: list[dict]) → dict` | Present a checkpoint with options and collect the user's choice |
| `send_batch_review` | `async (recipient_id: str, batch: BatchProposal) → BatchActionDecision` | Present a reviewed batch card for approve/decline/edit-selection. Returns verdict + optional selected subset. |
| `send_draft_review` | `async (recipient_id: str, context: str, draft: str, metadata: dict) → DraftVerdict` | Present a draft for review with approve/edit/rephrase/reject outcomes. |
| `send_decision` | `async (recipient_id: str, question: str, options: list[DecisionOption], allow_freetext: bool) → DecisionResult` | Present a decision card with tappable options and optional free text. |
| `send_suggestion` | `async (recipient_id: str, suggestion: SuggestionProposal) → DecisionResult` | Present a low-friction proactive suggestion card (`do it`, `not now`, optional alternatives). |
| `send_autonomy_threshold_review` | `async (recipient_id: str, proposal: AutonomyThresholdProposal) → AutonomyThresholdDecision` | Present an autonomy-threshold widening/tightening proposal card with explicit evidence and consequences. |
| `send_secure_input` | `async (recipient_id: str, request: SecureInputRequest) → SecureInputCompleted` | Present a secure input card. Web: password field POSTs to `/secrets/{ref_id}` (bypasses WebSocket). CLI: `getpass`. Messaging: redirect link to web UI. Returns only `ref_id` + `success` — NEVER the secret value. |
| `send_connection_setup_step` | `async (recipient_id: str, step: SetupStep) → SetupStepResponse` | Present a connection setup step card (device code, browser redirect, progress, or completion). Collects user action (`done`, `cancel`, `trouble`). |
| `send_permission_escalation` | `async (recipient_id: str, connection_name: str, current: list[str], requested: list[str], reason: str) → DecisionResult` | Present a permission escalation card. Risk level assigned deterministically: `medium` for same-resource read→write, `high` for new resource types. |
| `send_connection_failure` | `async (recipient_id: str, failure: ConnectionFailure) → DecisionResult` | Present a connection failure card with recovery option chips. Always includes `Skip`. |

**Channel capability: `supports_secure_input: bool`** — Channels declare whether they can render secure input natively. Web and CLI support it; messaging channels (Telegram, Discord) MUST redirect to the web UI secure form. The `send_secure_input` implementation checks this flag and falls back to sending a web link if `False`.

CLI MUST implement `ChannelAdapterCore` only. Stream behavior for channels without `RichCardChannel` is deterministic fallback:
- approval/gate/checkpoint/suggestion/autonomy-threshold prompts are sent as plain text via `send(...)`
- replies are parsed from subsequent `message` events
- on parse timeout, fail closed (`declined`/`block`)
- for suggestion/autonomy-threshold prompts, timeout maps to `not now`/`decline` (never implicit widen/apply)

**Important:** Channels collect user decisions; `ApprovalEngine` mints and signs `ApprovalToken`s. This keeps private-key operations in one component and removes channel/signing ambiguity.

### 4.2 MemoryStore

| Method | Signature | Description |
|---|---|---|
| `store` | `async (item: MemoryItem) → str` | Store a memory item, return its ID |
| `get` | `async (memory_id: str) → MemoryItem \| None` | Retrieve by ID |
| `update` | `async (memory_id: str, **kwargs) → None` | Update specific fields |
| `delete` | `async (memory_id: str) → None` | Delete a memory item |
| `search_keyword` | `async (query: str, limit: int) → list[MemoryItem]` | FTS5 keyword retrieval (MVP baseline) |
| `search_session` | `async (session_id: str) → list[MemoryItem]` | All memories from a session |
| `store_raw` | `async (item: MemoryItem) → str` | Append raw memory input into `low_reingestion` lane (conversation/tool/research logs) |
| `search_raw` | `async (query: str, limit: int) → list[MemoryItem]` | Query raw low-reingestion lane explicitly |

Phase-gated extensions (`search_semantic`, `search_temporal`, `search_entity`, `search_causal`) are defined only when implemented to avoid dead protocol surface.

### 4.2.1 MemoryRetriever

| Method | Signature | Description |
|---|---|---|
| `retrieve` | `async (query: MemoryQuery, scope_id: str \| None = None, session_id: str \| None = None) → list[MemoryItem]` | Execute a retrieval strategy and return ranked memory items |

### 4.2.2 MemoryConsolidator

| Method | Signature | Description |
|---|---|---|
| `run_once` | `async () → dict` | Execute one consolidation cycle and return stats (merged/promoted/pruned/reembedded counts) |

### 4.2.3 MemoryPortability

| Method | Signature | Description |
|---|---|---|
| `export_bundle` | `async (since: datetime \| None = None, include_raw: bool = True) → bytes` | Export canonical portable memory bundle (JSONL + metadata/version) for external systems |
| `import_bundle` | `async (bundle: bytes, mode: str = "merge") → dict` | Reingest exported memory bundle into this system (`merge` or `replace`) |

Memory portability is mandatory: the canonical bundle format must be versioned, self-describing, and independent of runtime internals so memory can be reingested into a new system.

### 4.3 ContextManager

| Method | Signature | Description |
|---|---|---|
| `add` | `(scope_id: str, item: ContextItem) → str` | Add an item to a zone in a connection scope, return its ctx_id |
| `drop` | `(scope_id: str, ctx_id: str) → None` | Remove an item (harness-only, never agent-called) |
| `get_zone` | `(scope_id: str, zone: ContextZone) → list[ContextItem]` | Get all items in a zone for one scope |
| `subscribe` | `(scope_id: str, sub: ContextSubscription) → str` | Register a context subscription for a scope, return sub_id |
| `unsubscribe` | `(scope_id: str, sub_id: str) → None` | Deactivate a subscription |
| `set_profile` | `(scope_id: str, profile_name: str) → None` | Switch the active budget profile for a scope |
| `render` | `(scope_id: str, turn_number: int) → str` | Materialize subscriptions, apply observation masking, render all zones as a metadata-tagged string for LLM consumption. Rendering order: system → chronicle → memory → workspace. |
| `enforce_budget` | `(scope_id: str, turn_number: int, current_goal: str \| None) → list[str]` | Two-tier eviction: (1) heuristic pre-filter, (2) scorer model if still over budget. Returns evicted ctx_ids. See Section 5.7 for algorithm. |
| `token_usage` | `(scope_id: str) → dict[str, int]` | Return current token usage per zone (internal diagnostics, not rendered to agent) |

All context operations are scope-isolated by `scope_id`. No API exists for cross-scope reads; this is a hard boundary for multi-customer deployments.

### 4.4 ApprovalVerifier

| Method | Signature | Description |
|---|---|---|
| `issue_token` | `async (work_item: WorkItem, decision: ApprovalDecision, scope: ApprovalScope = full_plan) → ApprovalToken` | Create and sign a token after user approval. Computes plan hash, applies strength policy, signs canonical bytes, and returns a token with `executions_used=0`. Non-plan approvals (batch/autonomy-threshold) are represented as canonical payload wrappers so the same hash/signature path applies. |
| `verify` | `async (token: ApprovalToken, work_item: WorkItem, spawned_task: WorkItem \| None = None) → tuple[bool, str]` | Full consuming verification: checks signature, plan hash binding, expiry, execution count, and consumes a fresh execution nonce. For standing tokens (`scope == "standing"`), `work_item` is the parent goal (hash checked against this) and `spawned_task` is the task being authorized. Verification MUST check both `spawned_task.parent == token.work_item_id` and spawned-task hash/policy binding (`conditions.spawn_policy_hash`). For single-use tokens, `spawned_task` is `None` and `work_item` is the task being executed. Returns (valid, reason). Must be async because it calls the async `NonceStore`. |
| `check` | `async (token: ApprovalToken, work_item: WorkItem) → tuple[bool, str]` | Non-consuming validation: checks signature, expiry, and `1 <= executions_used <= max_executions` (must have been consumed by a prior `verify()`), but does NOT generate or consume an execution nonce. For single-use tokens, also checks plan hash against `work_item`. For standing tokens, checks both `work_item.parent == token.work_item_id` and spawned-task policy/hash binding. Used by 5.2.1 step 0 for all token types. |

### 4.5 NonceStore

| Method | Signature | Description |
|---|---|---|
| `is_used` | `async (domain: str, nonce: str) → bool` | Check if a nonce has been consumed within a domain |
| `record` | `async (domain: str, nonce: str) → None` | Mark a nonce as consumed within a domain |
| `prune_expired` | `async (older_than: datetime) → int` | Remove expired nonce records and return rows pruned |

**Domain prefixing:** Nonces are scoped by domain to prevent accidental collisions between unrelated subsystems. Defined domains: `"msg"` (message replay nonces from signed messages), `"exec"` (approval execution nonces). The store key is `"{domain}:{binding}"`, where `binding = nonce` for `"msg"` and `binding = "{token_id}:{plan_or_spawn_hash}:{nonce}"` for `"exec"`. This ensures message nonces cannot collide with approval execution nonces and binds execution replay checks to task content.

**Retention rule:** Nonce records MUST carry `recorded_at` and expire after `max_token_ttl + safety_buffer` (default buffer: 10 minutes). Expired nonces are unreplayable by definition and must be pruned on a schedule to prevent unbounded growth.

### 4.6 EphemeralExecutor

| Method | Signature | Description |
|---|---|---|
| `execute` | `async (envelope: ExecutionEnvelope) → ExecutionResult` | Execute a single action inside the configured sandbox backend. The executor is stateless and destroyed after returning. |

### 4.7 SandboxManager

| Method | Signature | Description |
|---|---|---|
| `create` | `async (config: SandboxConfig) → Sandbox` | Create a new sandbox runtime instance with the specified constraints (subprocess or Docker) |
| `destroy` | `async (sandbox_id: str) → None` | Destroy the sandbox instance and clean up all resources |

### 4.8 GateCheckProvider

| Method | Signature | Description |
|---|---|---|
| `check` | `async (gate: Gate, context: dict) → GateResult` | Evaluate a single gate against the provided context |

### 4.9 GateRunner

| Method | Signature | Description |
|---|---|---|
| `check_gates` | `async (gates: list[Gate], trigger: GateTrigger, context: dict) → tuple[list[GateResult], list[GateResult], dict]` | Evaluate matching gates in two lanes and return `(policy_results, quality_results, merged_context)`. Policy-lane gates are evaluated first in order; `modified_context` is filtered through `ALLOWED_MUTATIONS` and merged left-to-right. Quality-lane gates run after policy gates using the post-mutation context. |
| `check_gate` | `async (gate: Gate, context: dict) → GateResult` | Evaluate a single gate. Policy-lane gates may return `modified_context` (subject to allowlist). Quality-lane gates return scores/flags only. |

### 4.10 VerificationRunner

| Method | Signature | Description |
|---|---|---|
| `run_checks` | `async (checks: list[VerificationCheck]) → VerificationReport` | Run all checks OUTSIDE the agent's sandbox. Agent cannot influence this. |

**VerificationReport** contains:
- `all_passed: bool` — whether every check passed
- `results: list[VerificationResult]` — per-check results
- `failed: list[VerificationResult]` — only the failures
- `timestamp: datetime` — when checks were run (timezone-aware UTC)

**VerificationResult** contains: `name`, `passed`, `reason`, `output` (truncated to 1000 chars), `exit_code`.

### 4.11 AccessController

| Method | Signature | Description |
|---|---|---|
| `gate_passed` | `(gate_name: str) → None` | Record that a gate has been satisfied. If all required gates for a higher access level are now met, transition to that level. |
| `get_allowed_tools` | `() → list[str]` | Return tools available at the current access level. Checks expiry — if the current level has expired, drops back to the default level first. |
| `get_customer_context` | `() → dict \| None` | Return customer context if identity has been verified |

### 4.12 WorkItemExecutor

| Method | Signature | Description |
|---|---|---|
| `execute` | `async (item: WorkItem) → WorkItemResult` | Execute any WorkItem (task, project, or goal). |

### 4.13 WorkItemStore (NEW)

Durable persistence for work item state. Without this, a process crash loses all running work item state — unacceptable for long-lived goals and scheduled verification.

| Method | Signature | Description |
|---|---|---|
| `save` | `async (item: WorkItem) → None` | Persist current work item state |
| `get` | `async (work_item_id: str) → WorkItem \| None` | Load a work item by ID |
| `list_by_status` | `async (status: WorkItemStatus) → list[WorkItem]` | Find work items by status |
| `list_by_parent` | `async (parent_id: str) → list[WorkItem]` | Find child work items |
| `update_status` | `async (work_item_id: str, status: WorkItemStatus, budget_used: BudgetUsed) → None` | Update status and budget atomically |

Implementation: SQLite table (`work_items`) with JSON-serialized fields for `budget`, `budget_used`, `verify`, `gates`, `escalation`, `verification_results`, `approval_token`, `interaction_mode`, `input_artifacts_from`. The `approval_token` field stores the full `ApprovalToken` as JSON, including mutable state (`executions_used`, `execution_nonces`). On save, the token is serialized via Pydantic's `.model_dump(mode="json")`; on load, it is deserialized back to an `ApprovalToken` instance. The `signature` field (bytes) is stored as base64-encoded string. Indexed on `id`, `status`, `parent`, `follow_up_of`.

### 4.14 ChronicleStore (NEW)

Durable persistence for conversation history, enabling Stream rehydration after restart.

| Method | Signature | Description |
|---|---|---|
| `append` | `async (scope_id: str, item: ContextItem) → None` | Persist a chronicle entry for one connection/customer scope |
| `get_recent` | `async (scope_id: str, limit: int) → list[ContextItem]` | Load the most recent N chronicle entries for one scope |
| `prune_before` | `async (cutoff: datetime) → int` | Delete or archive chronicle entries older than retention cutoff; returns rows affected |

Implementation: SQLite table (`chronicle`) keyed by `(scope_id, timestamp)` with a configurable retention policy (default 90 days) and optional archival sink.

### 4.15 PlanParser

| Method | Signature | Description |
|---|---|---|
| `parse` | `(markdown: str) → WorkItem` | Parse a markdown plan with YAML front matter into a WorkItem |

`PlanParser` MUST be pure parsing. File I/O is the caller's responsibility (`Path.read_text()` then `parse(markdown)`).

### 4.16 AuditLog

| Method | Signature | Description |
|---|---|---|
| `log` | `async (event: str, **data) → str` | Append a hash-chained audit entry. Returns the entry ID. |
| `verify_chain` | `async () → tuple[bool, int]` | Verify the hash chain integrity. Returns (valid, entries_checked). |
| `write_checkpoint` | `async () → str` | Persist a checkpoint hash for the current chain head and return checkpoint ID |
| `verify_from_checkpoint` | `async (checkpoint_id: str \| None = None) → tuple[bool, int]` | Verify from the latest or specified checkpoint to current head |

Checkpointing reduces routine verification cost from full `O(n)` scans to incremental verification between checkpoints.

### 4.17 PersonalityEngine

| Method | Signature | Description |
|---|---|---|
| `detect_context` | `async (message: ChannelMessage, route_hint: str \| None = None) → str` | Classify the turn context key (config-driven registry lookup) |
| `get_effective_axes` | `async (scope_id: str, context_key: str) → AxisProfile` | Compose baseline + context delta + mood + overrides into clamped effective axes |
| `render_directives` | `async (scope_id: str, context_key: str) → str` | Render natural-language style directives from effective axes + voice; target 200–400 tokens |
| `apply_event` | `async (scope_id: str, event_type: str, trusted: bool, source: str, metadata: dict \| None = None) → PersonaState` | Apply event-driven mood/baseline adjustments and persist an event |
| `decay` | `async (scope_id: str, now: datetime) → PersonaState` | Decay mood toward neutral over elapsed time |
| `set_preset` | `async (scope_id: str, preset_name: str) → PersonaState` | Activate a named preset |
| `adjust_axes` | `async (scope_id: str, delta: dict[str, float], trusted: bool, persist_to_baseline: bool = False) → PersonaState` | Explicit tuning from user commands or feedback |

### 4.18 PersonaStore

| Method | Signature | Description |
|---|---|---|
| `get_state` | `async (scope_id: str) → PersonaState \| None` | Load persisted personality state |
| `save_state` | `async (state: PersonaState) → None` | Persist personality state |
| `append_event` | `async (event: PersonaEvent) → None` | Append an event record |
| `list_events` | `async (scope_id: str, limit: int = 100) → list[PersonaEvent]` | Retrieve recent events for diagnostics |

### 4.19 ConnectionManager (NEW)

Thin lifecycle coordinator that invokes connection-skill scripts (§10.6) rather than implementing adapter logic directly. All credential handling flows through the OS keyring — the ConnectionManager never holds secret values in memory beyond the script execution boundary.

| Method | Signature | Description |
|---|---|---|
| `discover_connection` | `async (skill_name: str, identity_hint: dict) → dict` | Run the skill's `discover.py` — returns auth strategy, provider info, initial permissions, and setup requirements (§3.12) |
| `run_setup_flow` | `async (skill_name: str, identity_hint: dict, channel: RichCardChannel, recipient_id: str) → Connection` | Run the interactive setup conversation. Invokes the skill's `setup.py`, which yields `SetupStep` objects (§3.12). For each step, renders via channel and collects user input. On `device_code`: shows code card, polls in background. On `browser_redirect`: opens browser, catches redirect. On `secure_input`: delegates to `channel.send_secure_input()`. On `completion`: stores credentials in keyring, creates Connection record. On `failure`: renders ConnectionFailureCard with recovery options. |
| `activate_connection` | `async (skill_name: str, provider: str, auth_payload: dict, approval: ApprovalToken) → str` | Non-interactive activation (for programmatic setup or re-activation after credential refresh). Runs skill setup script with pre-collected auth payload. |
| `escalate_permission` | `async (connection_id: str, requested_permissions: list[str], reason: str, channel: RichCardChannel, recipient_id: str) → bool` | Request additional service-level permissions. Renders a PermissionEscalationCard (§0.5.3). If approved, runs a re-auth flow (skill's `setup.py` with incremental scope parameter). Updates `permissions_granted` on the Connection record. Returns `True` if permissions were granted. |
| `run_health_checks` | `async () → list[HealthCheckResult]` | Run each active connection skill's `health_check.py` script. Returns structured `HealthCheckResult` (§3.12) with token expiry info, latency, and warnings. |
| `schedule_proactive_refresh` | `async (connection_id: str, health: HealthCheckResult) → None` | After a health check, schedule pre-emptive token refresh if `token_expires_at` is within `refresh_ahead_window` (default 10 min). If `refresh_token_expires_at` is within 7 days, surface a re-authentication suggestion card via the suggestion engine. |
| `refresh_token` | `async (connection_id: str) → bool` | Run the skill's `refresh_token.py` script. Update `token_expires_at` and `last_refresh` on the Connection record. Returns `True` on success. |
| `recover` | `async (connection_id: str) → tuple[bool, str]` | Run the connection skill's recovery script. If recovery fails, return structured `ConnectionFailure` for the caller to render. |
| `list_connections` | `async (domain: str \| None = None) → list[Connection]` | Enumerate managed connections from the registry |

### 4.20 SkillLoader (NEW)

| Method | Signature | Description |
|---|---|---|
| `scan` | `() → list[SkillMetadata]` | Scan flat skill directories under `skills_dir/` and parse SKILL.md frontmatter |
| `load_metadata` | `(skill_name: str) → SkillMetadata` | Load validated frontmatter for a single skill (`name`, `description`, optional `ui`, `activation`, `composes_with`, script arg schemas) |
| `load_full` | `(skill_name: str) → str` | Load the full SKILL.md body (frontmatter + instructions) for role-aware toolset preparation |
| `resolve_script` | `(skill_name: str, script_path: str) → str` | Resolve a relative script path to an absolute path, validating it exists within the skill directory |
| `validate` | `(skill_name: str) → dict` | Run deterministic validation checks (frontmatter completeness, description bounds, script syntax, reference integrity, forbidden patterns). Returns a report. |
| `import_external` | `(source: str, format_hint: str \| None = None) → dict` | Import/adapt external skills (OpenAI/Claude-style repositories) into Silas format and return a transformation report before installation approval. |

### 4.21 SkillResolver (NEW)

| Method | Signature | Description |
|---|---|---|
| `resolve_for_work_item` | `(work_item: WorkItem) → list[SkillMetadata]` | Resolve the work item's `skills` list to loaded metadata, applying inheritance from parent if `skills` is empty |
| `prepare_toolset` | `(work_item: WorkItem, agent_role: str, base_toolset: Toolset, allowed_tools: list[str]) → Toolset` | Build the canonical wrapper chain: `SkillToolset -> PreparedToolset -> FilteredToolset -> ApprovalRequiredToolset`. Optional runtime-only wrappers (for example dynamic revocation) may wrap the chain outermost without bypassing inner controls. |

`SkillToolset` is the inner capability wrapper that exposes core harness tools plus work-item-scoped skill tools before role preparation and access filtering are applied.

### 4.22 SuggestionEngine (NEW)

Generates proactive "what next" suggestions both between turns and after completed work.

| Method | Signature | Description |
|---|---|---|
| `generate_idle` | `async (scope_id: str, now: datetime) → list[SuggestionProposal]` | Produce idle-time suggestion cards from active goals, pending reviews, and recent context patterns |
| `generate_post_execution` | `async (scope_id: str, result: WorkItemResult) → list[SuggestionProposal]` | Convert execution outcomes + `next_steps` into user-facing suggestion cards |
| `mark_handled` | `async (scope_id: str, suggestion_id: str, outcome: str) → None` | Record suggestion outcome (`accepted`, `dismissed`, `deferred`) for cooldown + learning |

### 4.23 AutonomyCalibrator (NEW)

Tracks correction behavior and proposes explicit autonomy threshold changes.

| Method | Signature | Description |
|---|---|---|
| `record_outcome` | `async (scope_id: str, action_family: str, outcome: str) → None` | Record correction outcomes (`approved`, `edit_selection`, `declined`, `undo`) |
| `evaluate` | `async (scope_id: str, now: datetime) → list[AutonomyThresholdProposal]` | Evaluate rolling metrics and emit widening/tightening proposals when thresholds are met |
| `apply` | `async (proposal: AutonomyThresholdProposal, decision: AutonomyThresholdDecision) → dict` | Apply approved threshold changes atomically and emit audit records |

### 4.24 Toolset Pipeline Mapping (PydanticAI)

This section binds the spec's logical wrapper chain to **actual PydanticAI v1.x APIs**. The chain is implemented using native toolset primitives, not hypothetical abstractions.

PydanticAI v1.x primitives used:
- Tool registration: `Agent(..., tools=[...], toolsets=[...])`, plus run-time `toolsets=` on `agent.run()/run_sync()/run_stream()/iter()`, and contextual `agent.override(toolsets=...)`.
- Toolset composition: `FunctionToolset`, `CombinedToolset`, and wrapper composition helpers on any toolset (`prepared(...)`, `filtered(...)`, `approval_required(...)`).
- Dynamic tool definition hooks:
  - Per-tool `prepare` hook (`Tool(..., prepare=...)` or `@agent.tool(prepare=...)`) to mutate or omit a tool per step.
  - Agent-wide `prepare_tools` hook to mutate/filter full tool-definition lists per step.
- Approval-paused calls: `ApprovalRequiredToolset` + deferred tool approvals/results flow.

Logical-to-API mapping (normative):

| Logical stage | PydanticAI v1.x mapping | Responsibility |
|---|---|---|
| `SkillToolset` | `FunctionToolset`/external toolsets combined via `CombinedToolset` | Register core harness tools + work-item skill tools |
| `PreparedToolset` | `base_toolset.prepared(prepare_func)` and optional per-tool `prepare` | Role/work-item-specific tool descriptions/availability |
| `FilteredToolset` | `prepared_toolset.filtered(filter_func)` | Access-level and policy-based tool exposure before model step |
| `ApprovalRequiredToolset` | `filtered_toolset.approval_required(needs_approval_func)` | Pause approval-sensitive calls; resume after explicit decision |

Pipeline assembly order:
1. Build `skill_toolset` from core + skill-resolved tools.
2. Apply role/work-item preparation (`prepared` + optional per-tool `prepare`).
3. Apply access filtering (`filtered`) using `AccessController.get_allowed_tools()`.
4. Wrap with approval requirement (`approval_required`) for sensitive calls.
5. Optionally wrap outermost with runtime-only `WrapperToolset` derivatives (telemetry/revocation), never bypassing inner wrappers.

Run-time registration pattern:
- Baseline shared tools are registered at agent construction.
- Work-item/turn-specific toolsets are passed at run time (`agent.run(..., toolsets=[runtime_chain])`) or with `agent.override(toolsets=[runtime_chain])` in scoped contexts/tests.
- This keeps static agent definitions small while allowing deterministic per-turn scope and access controls.

Pre-call filtering/approval order for each tool call:
1. Tool definitions are prepared (`prepare` / `prepare_tools` / `PreparedToolset`).
2. Candidate tools are filtered (`FilteredToolset`) before exposure to the model.
3. If model requests a tool, `ApprovalRequiredToolset` decides execute-now vs defer-for-approval.
4. Deferred approvals are resolved by Stream + ApprovalEngine; run resumes with provided `deferred_tool_results`.

This mapping is normative. The wrapper chain uses only these PydanticAI v1.x primitives — no non-existent framework internals.

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
1. Generate an Ed25519 keypair using libsodium (via PyNaCl)
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
- Exact pre-counting integration is tracked in Section 19 roadmap.

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

The retriever accepts a `MemoryQuery` (with strategy and parameters) and delegates to the appropriate SQLite store method. Additional strategy expansion is tracked in Section 19 roadmap.

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

### 13.1 Simple Task — Fix a Bug

```markdown
---
id: task-fix-tz-bug
type: task
title: Fix timezone bug in shift scheduler
interaction_mode: act_and_report
budget: { max_tokens: 200000, max_cost_usd: 2.00, max_wall_time_seconds: 1800 }
verify:
  - name: tests_pass
    run: "pytest tests/test_shifts.py -v"
    expect: { exit_code: 0 }
  - name: lint_clean
    run: "ruff check ."
    expect: { exit_code: 0 }
on_stuck: consult_planner
---

# Context

The shift roster generator produces overlapping shifts when employees
span multiple timezones. Root cause is likely in
`scheduler/slot_allocator.py` — collision detection uses local time
instead of UTC.

# What to do

1. Read `scheduler/slot_allocator.py` and `tests/test_shifts.py`
2. Fix timezone handling — all comparisons in UTC, only convert for display
3. Run tests. Add test_cross_timezone_collision if missing
4. Run `ruff check . --fix` and `pytest tests/ -v`
5. Commit: "fix: timezone collision detection in slot allocator"
6. Push to branch `fix/timezone-slots` and create PR

# Constraints

- Do NOT change the Slot data model
- Do NOT modify existing test fixtures — only add new test cases
- If bug is NOT in slot_allocator.py, stop and consult
```

### 13.2 Chatbot Deployment — Customer Support with Access Control

```markdown
---
id: goal-insurance-support
type: goal
title: Insurance customer support chatbot
agent: stream
interaction_mode: confirm_only_when_required
schedule: always_on

gates:
  - name: toxicity_in
    on: every_user_message
    provider: guardrails_ai
    check: toxicity
    config: { threshold: 0.7 }
    on_block: escalate_human

  - name: toxicity_out
    on: every_agent_response
    provider: guardrails_ai
    check: toxicity
    config: { threshold: 0.3 }
    on_block: suppress_and_escalate

  - name: pii_out
    on: every_agent_response
    provider: guardrails_ai
    check: pii
    config: { entities: ["CREDIT_CARD", "SSN"] }
    on_block: suppress_and_rephrase

  - name: jailbreak_in
    on: every_user_message
    provider: guardrails_ai
    check: jailbreak
    on_block: polite_redirect

  - name: identity_verified
    on: every_user_message
    provider: script
    type: custom_check
    check: "verify_customer"
    config:
      script: "guards/verify_customer.py"
      args_from_env: ["CUSTOMER_NAME", "CUSTOMER_DOB"]
    check_expect: { equals: "verified" }
    on_block: retry_verification

access_levels:
  public:
    description: "General info only"
    tools: [faq_search, product_info]
  verified:
    description: "Customer-specific data"
    tools: [faq_search, product_info, policy_lookup, claim_status]
    requires: [identity_verified]
    expires_after: 900

escalation:
  escalate_human:
    action: transfer_to_queue
    queue: support_l2
    message: "Let me connect you with a colleague."
  suppress_and_escalate:
    action: suppress_and_escalate
    message: "I apologize, let me connect you with a team member."
    fallback: escalate_human
  suppress_and_rephrase:
    action: suppress_and_rephrase
    instruction: "Rephrase without credit card numbers or SSNs. Use masked format."
    max_retries: 2
    fallback: escalate_human
  polite_redirect:
    action: respond
    message: "I'm here to help with insurance questions. How can I assist you?"
  retry_verification:
    action: respond
    message: "I couldn't verify those details. Could you double-check?"
---

# Insurance Support Bot

You are a friendly support agent for ACME Insurance.

## Before verification
Answer general questions about products, coverage types, pricing.
If a customer asks about their policy or claims, ask for their
full name and date of birth first.

## After verification
Address the customer by name. Look up policies, claims, coverage.
Never read out full account numbers, SSNs, or payment details.
Use masked formats like "ending in 4821."
```

Note: The `identity_verified` gate uses the `config.args_from_env` pattern rather than command-line interpolation. The script checker passes customer name and date of birth as environment variables (`CUSTOMER_NAME`, `CUSTOMER_DOB`) to the verification script, avoiding shell injection.

### 13.3 Trading Bot — Prediction Market with Sentiment Gates

```markdown
---
id: task-trade-weather
type: task
title: Execute weather prediction trade on Kalshi
interaction_mode: confirm_only_when_required
budget: { max_tokens: 100000, max_cost_usd: 1.50, max_wall_time_seconds: 600 }

gates:
  - name: confidence_check
    on: after_step
    after_step: 1
    provider: predicate
    type: numeric_range
    extract: confidence_score
    auto_approve: { min: 0.75, max: 1.0 }
    require_approval: { min: 0.5, max: 0.75 }
    block: { outside: [0.0, 1.0] }
    on_block: abort_trade

  - name: position_size
    on: after_step
    after_step: 2
    provider: predicate
    type: numeric_range
    extract: position_usd
    auto_approve: { min: 0.0, max: 25.0 }
    require_approval: { min: 25.0, max: 100.0 }
    block: { outside: [0.0, 100.0] }
    on_block: abort_trade

verify:
  - name: order_placed
    run: "python scripts/check_kalshi_order.py --order-id '$order_id'"
    expect: { equals: "filled" }

escalation:
  abort_trade:
    action: report
    message: "Trade aborted — value outside safe range."
---

# Weather Prediction Trade

## Step 1: Analyze
Query the ECMWF and GFS weather models for tomorrow's high
temperature forecast for the target city. Compare with the
Kalshi market price. Output your confidence_score (0.0-1.0).

## Step 2: Size
If confident, calculate position size using Kelly criterion
with half-Kelly sizing. Output position_usd.

## Step 3: Execute
Place the order on Kalshi via API. Output order_id.

## Constraints
- Never exceed $100 per trade
- Only trade weather markets, not politics or entertainment
- If models disagree by more than 5 degrees F, reduce confidence by 0.2
```

### 13.4 Recurring Goal — Health Monitor

```markdown
---
id: goal-health-monitor
type: goal
title: Prediction bot health monitor
interaction_mode: act_and_report
schedule: "*/30 * * * *"
budget: { max_tokens: 10000, max_cost_usd: 0.10, max_wall_time_seconds: 120 }

verify:
  - name: api_responding
    run: "curl -sf https://api.kalshi.com/v1/health | jq -r .status"
    expect: { equals: "ok" }
    network: true
  - name: bot_process_alive
    run: "pgrep -f prediction_bot"
    expect: { exit_code: 0 }
  - name: last_trade_recent
    run: "python scripts/check_last_trade_age.py"
    expect: { output_lt: 3600 }

on_failure: spawn_task
failure_context: |
  The prediction bot health check failed.

  Failed checks:
  $failed_checks

  Investigate and fix. If the process is dead, restart it.
  If the API is down, wait and re-check in 5 minutes.
  If no trades in >1 hour, check if markets are open.
---

Health monitor for the prediction market trading bot.
Runs every 30 minutes. If any check fails, spawns a fix task.
Standing approval (verified per-execution) covers spawned tasks within budget.
```

### 13.5 Project — Multi-Task Deployment

```markdown
---
id: project-deploy-v2
type: project
title: Deploy v2.0 to production
interaction_mode: act_and_report
budget: { max_tokens: 500000, max_cost_usd: 10.00, max_wall_time_seconds: 7200 }

tasks:
  - task-run-tests
  - task-build-image
  - task-deploy-staging
  - task-smoke-test
  - task-deploy-prod

verify:
  - name: prod_healthy
    run: "curl -sf https://api.example.com/health"
    expect: { equals: "ok" }
    network: true
---

Deploy version 2.0. Tasks execute in dependency order.
Each task has its own verification. Project-level check
confirms production is healthy after all tasks complete.
```

---

## 14. Testing Strategy

### Unit Tests

| Area | What to Test |
|---|---|
| Verification predicates | All `Expectation` types: exit_code, equals, contains, regex, output_lt, output_gt, file_exists (including path validation rejection), not_empty |
| Gate evaluation | Numeric ranges (auto/approval/block boundaries), string match (allowed/approval/unknown), regex match/miss, **`file_valid` (path validation, existence, size)**, **`approval_always` (always returns require_approval)**, **`on_tool_call` trigger (gate checked before tool dispatch)**, **`modified_context` merge with `ALLOWED_MUTATIONS` enforcement (including tool-arg rewrites, rejected key logging)**, **two-lane evaluation: policy gates block, quality gates produce scores/flags only**, **LLM provider quality-lane output validation (score + flags)**, **`promote_to_policy` promotes LLM gate to policy lane with fail-closed semantics**, **quality-lane violation enforcement (block/require_approval from quality gate is ignored and logged)**, **precompiled active gate set reused across input/output/tool checks** |
| Budget tracking | Exhaustion detection with `>=` semantics, `merge()` aggregating all fields, budget display formatting |
| Web search executor | Query/result caps, timeout behavior, domain allowlist filtering, API error mapping, and disabled-by-missing-key behavior |
| Plan parser | YAML front matter extraction, gate/verify/escalation parsing, **`skills` list parsing and inheritance from parent**, `interaction_mode` parsing/defaulting, follow-up linkage fields (`follow_up_of`, `input_artifacts_from`), missing fields get defaults |
| Routing interaction contract | `RouteDecision` always includes `interaction_register` + `interaction_mode` + `context_profile`; deepening intent detection populates `continuation_of` |
| Skill loader | SKILL.md frontmatter validation (`name`, `description`, optional `ui`/`activation`/`composes_with`/`script_args`), flat directory scanning (`skills_dir/*`), metadata extraction, deterministic import-adaptation report generation |
| Skill resolver | Skill name to path resolution, script path validation, work-item scoping (only skills in `WorkItem.skills` are accessible), wrapper-chain ordering invariants |
| Turn context DI | `TurnContext` construction completeness, per-scope isolation, and test fixture ergonomics (single mockable dependency container) |
| Script argument schemas | Schema validation failures at sandbox boundary, defaulting behavior, max-length/range enforcement, rejection of undeclared arguments |
| Execution result channels | `return_value`/`content` model-facing handling vs `metadata` application-only handling (never context-injected) |
| Access controller | Level transitions when all required gates pass, expiry-based demotion, tool list accuracy |
| Context manager | Zone budget enforcement per profile, profile switching, heuristic eviction (observation masking, trivial message dropping, subscription deactivation), scorer-model eviction with group output parsing, subscription materialization and caching, subscription deduplication (whole-file supersedes line-ranges), metadata-tagged rendering format, pinned item protection |
| Memory portability | Export/import bundle round-trip, schema version checks, `merge` vs `replace`, preservation of `taint` + `trust_level` + `reingestion_tier` |
| Approval engine | Plan hash verification, nonce replay detection, expiry enforcement, signature validation, execution count limits, **standing approval verification for goal-spawned tasks (must fail without valid standing token)**, **`needs_approval=false` override when no token present**, **standing token hash checked against parent goal + spawned-task policy/hash binding**, **spawned task parent-ID must match token work_item_id**, **`verify()` consumes exactly one execution nonce; `check()` consumes zero**, **standing token path: `verify()` in 5.2.3 + `check()` in 5.2.1 = one nonce total**, **`verify()` with standing token and `spawned_task=None` returns error**, **autonomy-threshold proposal tokens (`scope=autonomy_threshold`) must verify before config mutation**, **`approval_strength` included in signed payload**, **scope minimum strength policy defaults to `tap` for all scopes in MVP (`scope_min_strength`)** |
| Batch review models | `BatchProposal` schema validation, config-driven batch sizing, token-binding payload validation (`action + item_ids + count`), edit-selection flow requiring re-approval |
| Suggestion + autonomy models | `SuggestionProposal` dedupe/cooldown keys, `AutonomyThresholdProposal` schema, widen/tighten diff validation, risk-level enforcement |
| Confidence escalation | Confidence band routing (`high`/`medium`/`low`/`novel`), policy-gate precedence over confidence actions, persistent rule changes requiring approval |
| Autonomy calibrator | Correction-rate math, minimum sample gating, hysteresis behavior, hard caps, single-tap rollback path |
| Behavioral preference inference | Signal ingestion to `preference` memories, working->verified promotion gate, planner/proxy consumption of verified defaults |
| Personality engine | Context detection mapping, deterministic axis composition (`clamp`), directive rendering token bounds, event-driven mood updates, decay behavior, feedback trust rules, baseline drift opt-in |
| Taint tracker | Owner/auth/external classification, taint propagation to memory, constitutional memory protection |
| Script checker | Input sanitization (verify shell metacharacters are escaped), env-var passing, timeout handling |
| Nonce store | **Domain-prefixed nonce isolation (same nonce string in different domains must not collide)**, replay detection within domain, content-bound execution nonce keys (`token_id + plan/spawn hash + nonce`), TTL pruning |

### Integration Tests

| Area | What to Test |
|---|---|
| SQLite memory | CRUD operations, FTS5 search, vector similarity search via sqlite-vec, raw low-reingestion lane storage/retrieval (`store_raw`, `search_raw`) |
| Chronicle store | Append and retrieve recent entries, ordering guarantee |
| Work item store | Save/load round-trip, status updates, list by status/parent/follow-up, **approval_token JSON serialization round-trip (including mutable fields and base64 signature)**, `interaction_mode` + `input_artifacts_from` persistence |
| Audit chain | Log entries, verify chain integrity, detect tampering |
| GuardrailsAI | Configured validator execution, fix-mode rewrites, and block behavior with real validators |
| Script checker | Actual subprocess execution with sanitized inputs |
| Sandbox backends | Subprocess backend execution + cleanup; Docker backend parity tests (when installed); consistent behavior through same `SandboxManager` interface |
| Web search integration | Provider request/response normalization, taint tagging as `external`, and tool registration toggling from search config |
| Work executor | Full retry loop: fail → retry → consult planner → succeed, **`plan_action` with `needs_approval=false` and no token triggers approval flow override**, **step 0 blocks execution when approval_token is None/invalid/expired** |
| Follow-up execution | `follow_up_of` artifact inheritance (`input_artifacts_from`), missing-link blocking behavior, continuation chain audit records |
| Web channel | WebSocket connect + auth handshake (subprotocol or first-message `auth`), message exchange, approval flow, disconnect handling, **`send_approval_request` returns declined `ApprovalDecision` on disconnect/timeout**, **`send_gate_approval` returns `"block"` on disconnect**, card protocol round-trips (`batch_review`, `draft_review`, `decision`), safe timeout defaults for card flows |
| Suggestion/autonomy cards | Card protocol round-trips (`suggestion`, `autonomy_threshold_review`), timeout-safe outcomes, risk-based details expansion defaults |
| System + plan gates | Global `gates.system` merge order before work-item gates, no duplication required across plans, **precompiled gate set frozen for turn duration**, **two-lane separation: policy results enforce, quality results logged** |
| Persona store + API | `persona_state`/`persona_events` persistence across restart, preset/tune/feedback endpoints, trusted vs untrusted feedback enforcement, per-connection scope isolation |
| Connections | Setup conversation protocol (device code, browser redirect, secure input flows), incremental permission escalation, proactive token refresh scheduling, structured failure recovery with recovery options, `POST /secrets/{ref_id}` endpoint (secret never in WebSocket), connection-ID namespace separation from channel session IDs |
| Skill system | Skill install flow (deterministic validation, `skill_install` approval, sandbox dry-run, metadata indexing), skill-aware toolset preparation per work item (Proxy metadata under budget cap, full skill prep for Planner/Executor), wrapper-chain enforcement, skill inheritance from parent work item, plan hash change on skill list modification, skill creation flow (plan + build + approve + activate) |
| External skill adaptation | Import from GitHub/local source, OpenAI/Claude frontmatter normalization, script adaptation report, deterministic transform output, install blocked on unresolved high-risk items |
| Toolset wrappers | End-to-end chain behavior (`SkillToolset -> PreparedToolset -> FilteredToolset -> ApprovalRequiredToolset`), optional dynamic outer wrapper revocation behavior, no bypass of inner wrappers |

### Evals (Pydantic Evals)

| Area | What to Evaluate |
|---|---|
| Proxy routing | Accuracy of direct vs planner classification across message types |
| Interaction mode | Accuracy of register/mode selection and default-and-offer behavior under low-risk ambiguity |
| No-management UX | Rate of unnecessary workflow/mode questions when safe defaults exist (target near zero) |
| Planner output | Plan quality: correct verify checks, reasonable budget, clear briefing |
| Memory recall | Precision@k for semantic retrieval across topics |
| Gate coverage | Percentage of harmful content caught by guardrails gates |
| Proactivity quality | Suggestion acceptance vs dismissal rates, duplicate-suggestion suppression effectiveness |

### End-to-End Tests

- Full web flow: connect -> authenticate -> send message -> planner approval -> background execution progress -> verification result
- Goal cycle flow: scheduled verification failure -> spawned fix task -> standing approval verification -> execution -> status update
- Proactivity flow: idle heartbeat -> suggestion card -> accept/defer outcome -> cooldown/dedupe behavior
- Autonomy flow: low correction-rate window -> threshold proposal card -> approve change -> widened behavior + rollback path
- Restart flow: crash/restart -> rehydration by scope -> pending work resumes with no cross-scope leakage

### Load Tests

- Sustained concurrent scopes (chatbot mode): verify per-scope locks, queue behavior, and latency SLOs
- Gate/scorer stress: high-turn-rate sessions triggering repeated budget enforcement with scorer fallback
- WebSocket churn: connect/disconnect bursts while approval/card prompts are active

### Chaos Tests

- Kill process during active execution and verify deterministic recovery
- Inject SQLite lock contention and transient I/O failures
- Simulate LLM provider timeouts and scorer-provider outages
- Simulate sandbox backend capability loss (network isolation unavailable) and verify fail-closed behavior

### Test Infrastructure

- **PydanticAI `TestModel`**: Used for all agent unit tests. No LLM API calls in CI.
- **`FakeTokenCounter`**: Counts words instead of tokens for fast context manager tests.
- **`InMemoryMemoryStore`**, **`InMemoryChronicleStore`**, **`InMemoryWorkItemStore`**: Dict-based implementations of all store protocols for unit testing.
- **`FakeKeyManager`**: Generates deterministic test keys for approval flow testing.
- **`FakeVerificationRunner`**: Returns configurable pass/fail results for work executor testing.
- **`FakePersonalityEngine`**: Deterministic context/axes renderer for Stream tests without style nondeterminism.
- **`FakeContextScorer`**: Returns configurable eviction group outputs for two-tier eviction testing without live model calls.
- **`FakeLLMGateProvider`**: Deterministic quality-lane gate results (scores + flags) for testing `llm` provider paths without live model calls. Also supports `promote_to_policy` mode for testing promoted gate behavior.
- **`FakeWebSearchExecutor`**: Deterministic web-search responses/errors for toolset and executor tests without external API calls.
- **`FakeSuggestionEngine`**: Deterministic suggestion outputs for idle/post-execution tests, including cooldown behavior.
- **`FakeAutonomyCalibrator`**: Deterministic correction-rate windows and threshold proposals for widen/tighten tests.

---

## 15. Security Model Summary

| Layer | Enforcement | Mechanism |
|---|---|---|
| Input | Deterministic | Gates (predicate/script/guardrails validators), taint tagging, signed messages |
| Output | Deterministic | Gates (predicate/script/guardrails validators), suppress-and-rephrase escalation |
| Routing | LLM (proxy-tier) | Proxy decides route, but cannot execute |
| Core retrieval tools | Deterministic runtime controls | `memory_search`, `context_inspection`, and `web_search` (key-gated, access-filtered, audited) |
| Planning | LLM (planner-tier) | Creates plans, but cannot execute without approval; `needs_approval=false` is overridden by runtime unless a verified token exists |
| Interaction mode | Deterministic + LLM-classified | Proxy classifies register/mode each turn; runtime enforces risk/policy overrides (`confirm_only_when_required`) |
| Approval | Cryptographic | Ed25519 signed tokens, plan hash binding, standing-token spawned-task policy/hash binding, nonce replay protection; `approval_strength` is signed metadata, not unsigned bypass |
| Verification | Deterministic | External checks in separate sandbox, agent cannot influence |
| Gates | Two-lane | Policy lane (predicate/guardrails/script): blocking, deterministic. Quality lane (llm): advisory scores/flags, non-blocking. Mutation allowlist restricts `modified_context` to `response`, `message`, `tool_args`. |
| Access Control | Deterministic | State machine with gate-driven transitions, tool filtering |
| Session isolation | Deterministic | Per-connection `scope_id` partitioning for chronicle/memory/workspace + per-connection turn locks |
| Execution | Deterministic | WorkItemExecutor verifies approval token at entry (Section 5.2.1 step 0) before any execution begins; standing tokens verified per-execution in goal cycles (Section 5.2.3 step 4) |
| Skill import/adaptation | Deterministic + approved | External skill sources are normalized through deterministic transforms, produce a transformation report, pass validator checks, then require `skill_install` approval |
| UX verification | Deterministic + cryptographic | `tap` strength with explicit interaction ladder (`tap -> slide -> biometric/local auth when available`). All executable actions still require valid signed approval tokens |
| Personality | Deterministic | PersonalityEngine computes style directives from bounded axes/mood; constitution and security policies take precedence; only trusted paths may persist baseline drift |
| Autonomy calibration | Deterministic + approved deltas | Threshold widening/tightening only via explicit reviewed proposals, with hysteresis, caps, and audit trail |
| Isolation | Architecture | Ephemeral sandbox backend instances (subprocess default, Docker optional): no context, no memory, credential access via opaque keyring refs only |
| Audit | Cryptographic | Hash-chained log, GDPR-compliant access level transitions |
| Memory | Trust levels | working/verified/constitutional, taint tracking on external data |
| Credentials | OS-level | Private keys in OS keyring, never in LLM context |
| Script inputs | Sanitized + schema-validated | All user-controlled values passed via env vars or shlex.quote(), never raw shell interpolation; script arguments are validated against declared `script_args` schemas before execution |

**What the LLM can NEVER do:**

- Forge approval tokens (no private key access)
- Execute actions without verified approval (task execution is gated on token verification at step 0; standing approvals consume an execution nonce per use)
- Self-report success (external verification only)
- Access credentials directly (scoped by Orchestrator)
- Modify the audit log (hash chain breaks)
- See other executors' contexts (per-run sandbox instances and stateless envelopes)
- Grant itself higher access levels (deterministic state machine)
- Self-register disabled tools (for example enable `web_search` without configured credentials)
- Bypass policy gates (runtime enforces before/after every turn; quality-lane checks are advisory and logged to audit)
- Influence verification checks (runs in separate sandbox)
- Persist state between executor runs
- Override constitutional memories
- Override constitution or security policy via personality tuning
- Self-widen autonomy thresholds or standing-approval scope without explicit approved proposal
- Activate externally imported skills without deterministic adaptation + `skill_install` approval
- Enable dynamic skill context injection (`{{script}}` expansion) in this version
- Inject shell commands via gate script inputs (sanitized)

---

## 16. Architecture Decision Records

### ADR-001: Plans as Prose Briefings, Not State Machines

**Decision:** Plans are markdown documents with YAML front matter, not structured ActionPlan objects with step arrays.

**Rationale:** Most plans execute in a single pass — agent reads briefing, does the work, tests pass. Heavy step-by-step structures add complexity without benefit. Prose briefings let the SOTA reasoning model (Planner) write natural instructions for the cheaper execution model.

### ADR-002: Verification is External and Deterministic

**Decision:** The agent cannot verify its own work. All verification runs outside the agent's sandbox via shell commands with deterministic expectation checks.

**Rationale:** An agent that self-reports success has a systematic incentive to claim success. External verification eliminates this. The Expectation model (exit_code, equals, contains, regex, numeric comparisons, file_exists) covers all common verification patterns without LLM judgment.

### ADR-003: Gates and Guardrails are the Same Primitive

**Decision:** Both mid-execution checks and per-turn guardrails use the same Gate model.

**Rationale:** A gate checks a value against criteria and decides: continue, block, or require approval. This applies equally to "is this response toxic?" and "is this trading position too large?" Unifying the primitive reduces code, simplifies reasoning, and means every safety feature uses the same tested machinery. Trigger (`every_user_message`, `every_agent_response`, `after_step`, `on_tool_call`), scope (`system` vs work-item), and lane (`policy` vs `quality`) are configuration dimensions of the same primitive.

### ADR-004: Provider-Based Gate Architecture

**Decision:** Gates dispatch to providers (guardrails_ai, predicate, llm, script, custom) rather than implementing checks inline.

**Rationale:** Guardrails-ai provides a reusable validator framework. Predicate handles deterministic numeric/string/regex logic. Script handles domain-specific policies. The LLM provider (configured quality-tier model) covers subjective quality checks that are otherwise awkward to encode as predicates. Provider boundaries keep orchestration uniform while allowing each check to use the right mechanism. Security-critical gates remain deterministic and synchronous.

### ADR-005: Access Levels via Gate State Machine

**Decision:** Tool access is controlled by a deterministic state machine driven by gate results. Passing identity verification gates unlocks higher access levels with more tools.

**Rationale:** The LLM cannot grant itself access to customer data. The runtime tracks which gates have passed and deterministically controls which tools the agent can use. This provides GDPR-compliant access control with audit logging.

### ADR-006: WorkItem Scales from One-Shot to Indefinite

**Decision:** Tasks, projects, and goals share the WorkItem model. The type field determines behavior.

**Rationale:** A task is a bounded single-agent execution. A project is ordered tasks with dependencies. A goal is scheduled verification that spawns fix tasks. The same retry/verify/escalate machinery handles all three. Configuration, not code, determines complexity.

### ADR-007: Web-First Channel Architecture

**Decision:** The primary channel is a FastAPI + WebSocket web interface, not Telegram or CLI.

**Rationale:** Web gives direct browser access, no third-party dependency, easy to extend with admin dashboards, audit log viewers, and memory browsers. Loopback-only by default for security. For multi-user chatbot deployment, each WebSocket connection gets its own AccessController/context scope. Telegram and other channels are optional additions. The split channel protocol (`ChannelAdapterCore` + optional `RichCardChannel`) keeps minimal transports simple while preserving rich UX where available.

### ADR-008: PydanticAI as Agent Execution Layer

**Decision:** Use PydanticAI as the framework for all LLM interactions rather than calling provider SDKs directly.

**Rationale:** PydanticAI provides structured output via Pydantic models, tool registration and dispatch, usage tracking, and TestModel for testing without LLM calls. This eliminates the need to build custom agent execution infrastructure. Silas's unique value — the security model, trust-leveled memory, gate unification, verification runner, and WorkItem lifecycle — remains custom code in the orchestration layer on top. PydanticAI handles "talking to LLMs"; Silas handles "governing what agents do."

**Trade-off acknowledged:** This is a deep structural coupling. If the PydanticAI project is abandoned, replacing it would require rewriting the agent layer. The Protocol-based architecture mitigates this at the boundary (EphemeralExecutor, MemoryStore, etc. are all framework-agnostic) but the agent definitions themselves are PydanticAI-specific. The bet is that the Pydantic team (backed by significant resources and a large user base) will maintain it.

### ADR-009: Pluggable Sandbox Backend (Subprocess First, Docker Optional)

**Decision:** Execution is abstracted behind `SandboxManager` with a pluggable backend. The initial backend is subprocess-based for simplicity and velocity; Docker is an optional drop-in backend for stronger isolation.

**Rationale:** The highest-priority boundary for this phase is cryptographic approval and deterministic verification, not maximal container isolation. A subprocess backend behind a strict interface lets us ship faster while preserving the call-site contract (`EphemeralExecutor` + `SandboxManager`). When stronger isolation is needed, Docker can be enabled without changing orchestrator logic, approval flow, or executor tool contracts. Commands are always passed as argument lists (not shell strings) in both backends.

### ADR-010: Work Item Persistence

**Decision:** Runtime state is durably persisted to SQLite (work items + chronicle), not held only in memory. This is a DBOS-style durability baseline without taking a hard dependency on the DBOS runtime in v1.

**Rationale:** Silas runs long-lived goals on cron schedules and projects with multi-step execution. A process crash during execution would lose all state — attempts count, budget consumed, verification history, task status, and recent dialogue context. Without persistence, crashed goals restart from zero, potentially re-executing already-completed work or exceeding budgets. The `WorkItemStore` and `ChronicleStore` protocols with SQLite implementations provide crash recovery and deterministic resume semantics now; the protocol boundary keeps the door open to swap in DBOS later if we need full workflow orchestration primitives.

### ADR-011: Personality as Runtime Shaping Layer

**Decision:** Personality is implemented as a deterministic runtime layer between Stream orchestration and agent calls, using numeric axes + voice config + mood state. It has one pre-agent injection point (turn step 7) and one post-turn update hook (turn step 15).

**Rationale:** Static persona prose is single-mode and cannot adapt cleanly across contexts (code review, crisis, casual chat, research). A numeric interface gives stable control primitives for presets, context shifts, and feedback loops while remaining testable. Rendering prose directives (instead of exposing raw numbers to the LLM) preserves model fluency. The layer is intentionally constrained: constitution, approval, gates, and verification remain higher-precedence controls and are never overridden by personality tuning.

### ADR-012: Gate Scope + Mutation

**Decision:** Gates are layered as global `gates.system` (config) plus work-item gates (plan/front matter). Gate results can optionally mutate context (`modified_context`) subject to a strict allowlist (`ALLOWED_MUTATIONS`). The active gate set is precompiled once per turn (or per execution) and reused.

**Rationale:** Global safety gates should not require duplication across plans. Context mutation enables policy-preserving rewrites (e.g., redact output, clamp risky tool input) without forcing hard blocks. The mutation allowlist (`response`, `message`, `tool_args`) prevents gates from accidentally or maliciously modifying security-critical state (approval tokens, budgets, access levels). Precompiling the active gate set once avoids subtle mid-turn inconsistencies and simplifies the runtime.

### ADR-013: Two-Lane Gate Architecture (v1 Simplification)

**Decision:** Gates are evaluated in two lanes: **policy** (blocking, deterministic, synchronous) and **quality** (non-blocking, advisory, LLM-based). Async gates are cut from v1. LLM gates default to quality lane unless explicitly promoted via `promote_to_policy`.

**Rationale:** The v1 gate system had three complexity vectors that provided marginal value:

1. **Async gates** added queueing, late escalation, restart semantics, and next-turn delivery for checks that are explicitly non-critical. This machinery touched the Stream (step 0 polling), gate runner (frozen context snapshots, enqueue, correlation metadata), and every gate evaluation path (is_async branching). For v1, non-blocking quality signals are better served by synchronous quality-lane evaluation with audit logging — simpler and equally useful for observability.

2. **LLM gates with blocking authority** created edge cases: model hallucinations could block legitimate turns, parse errors required fail-closed semantics that were overly aggressive for quality checks, and the boundary between "advisory" and "enforcing" was configuration-dependent rather than architecturally clear.

3. **Unrestricted `modified_context`** meant any gate provider could theoretically mutate any field in the evaluation context, including security-critical state. The allowlist makes the gate runner a hard enforcement boundary.

The two-lane model makes the separation explicit: policy gates (predicate, guardrails_ai, script) are the only gates that can block or require approval. Quality gates (llm) observe, score, and flag. The `promote_to_policy` escape hatch exists for cases where LLM judgment is the only feasible check (e.g., "does this contain medical advice?"), but the default is safe.

**Trade-off:** Async gates are intentionally excluded from this spec to preserve deterministic runtime behavior. Possible extensions are tracked in Section 19 roadmap.

### ADR-014: Harness-Controlled Context, Not Agent-Controlled

**Decision:** The harness (Stream + ContextManager) controls all context lifecycle — eviction, pinning, masking, subscription management. The agent's only context lever is `memory_queries` (max 3 retrieval requests per response). The agent cannot drop, pin, or summarize context items.

**Rationale:** The original design (v2) gave agents explicit context control via `ctx_drop`, `ctx_pin`, `ctx_summ_request`, and `ctx_fetch` fields on every `AgentResponse`, inspired by MemGPT (Packer et al., 2023). Industry experience since then has decisively moved against this pattern:

1. **Letta (née MemGPT) abandoned agent-controlled context in V1** (2025), removing the tool-heavy memory management that was their core innovation. Modern models have agentic patterns in post-training, making tool-layered context management unnecessary overhead.
2. **Cognitive load tax**: Six meta-cognitive fields on every response (drop, pin, summarize, fetch, memory_ops, plan_action) competes with actual task performance. Every token spent deciding "should I drop ctx_abc123?" is a token not spent on the user's task.
3. **Summarization hurts**: JetBrains Research (Dec 2025) showed observation masking outperforms LLM summarization — 52% cheaper, +2.6% solve rate — because summaries smooth over failure signals.
4. **The reliability paradox**: An agent reliable enough to correctly manage its own context doesn't need the complex management system; an unreliable agent makes bad context decisions.
5. **Industry consensus**: Claude (server-side compaction), Google ADK (processor pipeline), AutoGen (TransformMessages), and LangGraph (checkpointer) all use harness-controlled context. Zero major production frameworks give agents explicit drop/pin/summarize commands.

**What the agent retains:** Memory retrieval via `memory_queries`. This is the one context operation agents do well — requesting information is a search task (LLMs are good at this), unlike deletion/prioritization which requires metacognitive reasoning about downstream needs.

**Trade-off:** The harness may occasionally evict something the agent would have kept. The two-tier eviction system (heuristic + scorer model) and the memory-before-discard guarantee mitigate this: nothing is permanently lost, only moved from fast context to queryable memory. If the agent needs it back, it uses `memory_queries`.

### ADR-015: Two-Tier Eviction (Heuristic + Scorer Model)

**Decision:** Context eviction uses a two-tier approach: cheap heuristic rules always run (observation masking, trivial message dropping, subscription deactivation), and a lightweight quality-tier scorer model runs only when heuristics don't free enough space.

**Rationale:** Simple oldest-first eviction (used by Claude, AutoGen) works for most cases but can discard relevant older items while keeping trivial recent ones. Full model-based scoring on every turn adds unnecessary latency and cost. The two-tier approach gives the best of both: ~80% of eviction decisions are handled by near-zero-cost heuristics, with the scorer model handling the ~20% of cases where semantic relevance matters.

The scorer outputs **eviction groups** rather than individual scores, preventing orphaned references (e.g., evicting a variable declaration while keeping its usages). Group-based eviction is more natural for the scorer — it explains why groups should stay or go — and more auditable.

**Trade-off:** The scorer model adds ~1-3 seconds of latency when triggered. Setting `eviction_threshold_pct` to 0.80 ensures the scorer rarely fires during normal conversation, only during dense sessions that rapidly fill context.

### ADR-016: Context Subscriptions for Live Resources

**Decision:** Mutable resources (files, memory queries) can be registered as context subscriptions — references that are materialized (resolved to current content) on each `render()` call. Subscriptions replace static content copies for resources that change during a session.

**Rationale:** In agentic coding sessions, the agent reads a file at turn 5 and modifies it at turn 8. Without subscriptions, the context contains both the stale turn-5 version and the turn-8 diff, forcing the agent to mentally reconcile them. With subscriptions, the turn-5 read is a reference that auto-updates, so by turn 8 the context shows the current file state. This eliminates staleness and deduplication (a whole-file subscription supersedes line-range subscriptions for the same file).

Subscriptions also transform eviction: deactivating a subscription costs zero tokens and is instantly reversible (just re-materialize), making the scorer model's eviction decisions lower-stakes.

**Trade-off:** Materialization adds per-render cost (file reads, query execution). Within-turn caching and content-hash change detection keep this manageable. Additional subscription enhancements are tracked in Section 19 roadmap.

### ADR-017: Dynamic Budget Profiles Over Fixed Percentages

**Decision:** Zone budget allocations are selected from named profiles in a config registry rather than fixed percentages. The Proxy's routing decision determines which profile key is active.

**Rationale:** Different interaction types need different budget mixes. Fixed percentages force a one-size-fits-all compromise. Industry benchmarks show dynamic allocation delivers 40-60% cost reduction while maintaining quality. Since Silas already classifies interactions via the Proxy routing step, profile selection adds zero extra inference cost — it piggybacks on an existing decision.

**Trade-off:** Profile switching mid-conversation can cause a zone to shrink below its current usage, triggering eviction. This is acceptable — the eviction system handles it gracefully, and the alternative (never switching) wastes budget on the wrong zone for the rest of the conversation.

### ADR-018: Verification Strength Is UX Metadata, Not Authorization Semantics

**Decision:** `approval_strength` is signed UX metadata, not authorization semantics. This spec uses `tap` only.

**Rationale:** Mobile-first UX needs friction tuning, but unsigned approvals would break replay protection, auditability, and deterministic execution guards. Keeping signatures mandatory preserves security invariants.

### ADR-019: Two-Stage Delivery

**Decision:** v4 delivers in two stages: task execution loop (MVP-1), then custom goal packs with reviewed batch actions (MVP-2).

**Rationale:** MVP-1 validates the core security + execution loop without requiring domain integrations. MVP-2 then extends it with connections and reviewed batch workflows. Security remains unchanged because approvals stay cryptographic and each batch action is bound to exact payload.

### ADR-020: Agent Skills Standard as Universal Capability Format

**Decision:** All **domain** capabilities (connections, integrations, business actions) use the [Agent Skills standard](https://agentskills.io/) — a directory with `SKILL.md` (YAML frontmatter + markdown instructions) and optional `scripts/`, `references/`, `assets/` subdirectories. Core cognition/runtime primitives (`memory_search`, `context_inspection`, `web_search`, `shell_exec`, `python_exec`) remain harness-native tools. Skills are scoped to work items via the `skills` field, not globally injected. Silas can build new skills: the Planner plans the skill, an ephemeral executor (via `skill-maker`) writes the SKILL.md + Python scripts following Silas's authoring guidelines, and `skill_install` approval activates it.

**Rationale:**

1. **Universal building block.** Connections, integrations, and domain capabilities largely reduce to skills with scripts. This eliminates the need for separate connection adapter code and custom manifest schemas. Core harness primitives remain minimal (`shell_exec`, `python_exec`, `web_search`), while domain actions/integrations are implemented as skills.

2. **Self-extending.** Silas can create any capability it lacks by writing SKILL.md + Python scripts. The Planner plans the skill; an executor builds it. The existing security model governs this entirely: `skill_install` approval, sandbox dry-run testing, and hash-bound versioning (changing a script = plan hash change = re-approval). No new security machinery is needed.

3. **Work-item scoping.** Skills are attached to goals, plans, and tasks — not dumped into a global system prompt. This provides token efficiency (only relevant skills in context), security (skill access is part of the approved work item hash), and auditability (the audit log records exactly which skills were available for each action). The progressive disclosure model keeps routing cheap (~50-100 tokens of metadata per skill) while giving executors full instructions on activation.

4. **Open standard.** The Agent Skills format is adopted by Claude Code, Cursor, Gemini CLI, and others. Skills built for Silas are portable; skills built for other agents can be installed in Silas (with `skill_install` approval). This is interoperability without protocol coupling.

5. **Connections collapse.** A connection (for example `m365-outlook`) is just a skill with `discover.py`, `setup.py`, `health_check.py`, `refresh_token.py`, `recover.py`, and domain probe scripts. The `ConnectionManager` becomes a thin lifecycle coordinator that invokes connection-skill scripts rather than maintaining a separate adapter architecture. Connection permission tiers (`observe`/`draft`/`act`/`manage`) are enforced by the existing gate and approval system. The setup conversation protocol (§5.10.1) supports three auth strategies (device code, browser redirect, secure input) with credential isolation guaranteed by the secret isolation rule (§0.5).

**Trade-off:** Skills are more opaque than typed executor classes — a Python script can do anything the sandbox allows. This is mitigated by: (a) sandbox isolation constraining what scripts can access, (b) approval gates ensuring the user reviews scripts before activation, (c) hash-bound versioning detecting any script modification, and (d) the skill authoring guidelines enforcing statelessness and explicit dependencies.

### ADR-021: Authentication Strength Scope

**Decision:** This specification standardizes approval-strength policy on `tap` while preserving cryptographic signing and replay protection for all executable actions.

**Rationale:** This keeps the authorization boundary deterministic and fully testable while using explicit UI friction controls.

### ADR-022: Default-and-Offer Interaction Policy

**Decision:** Below confirmation thresholds, Silas defaults to acting on the best available approach and presents alternatives after acting instead of blocking on optional questions.

**Rationale:** Unnecessary clarification prompts create interaction drag and user-management burden. Risk and policy boundaries already determine when confirmation is mandatory. Separating cognitive defaulting ("choose and proceed") from authorization ("must approve") preserves safety while reducing latency.

### ADR-023: Explicit Autonomy Widening via Reviewed Threshold Deltas

**Decision:** Autonomy changes are explicit threshold proposals, never implicit trust drift. Widening/tightening is represented as auditable parameter deltas and requires review-card approval.

**Rationale:** Standing approvals and batch defaults already exist as deterministic controls. The missing governance primitive is controlled evolution based on observed correction rates. Proposal-driven threshold changes with hysteresis, sample-size minimums, caps, and single-tap rollback provide adaptive autonomy without hidden policy mutation.

### ADR-024: Wrapper-Chain Toolset Composition

**Decision:** Tool access composition uses an explicit wrapper chain (`SkillToolset -> PreparedToolset -> FilteredToolset -> ApprovalRequiredToolset`) rather than ad-hoc assembly logic at call sites.

**Rationale:** Wrappers isolate one responsibility each: capability exposure, role preparation, access filtering, and runtime approval pauses. This reduces coupling in turn orchestration, makes ordering testable, and prevents accidental bypasses when new behaviors are added (for example dynamic revocation wrappers).

### ADR-025: Deterministic External Skill Adaptation

**Decision:** Skills imported from external ecosystems are normalized through a deterministic adaptation pipeline that emits a transformation report and then enters the standard `skill_install` path.

**Rationale:** Cross-ecosystem skill reuse is useful, but importing heterogeneous frontmatter/script conventions directly is unsafe and hard to audit. Deterministic normalization plus explicit transform reporting preserves interoperability while keeping approval/audit semantics identical to native skills.

### ADR-026: Dynamic Context Injection Is Deferred and Safety-Gated

**Decision:** Pre-render dynamic skill-context injection (for example command/template expansion inside `SKILL.md`) is excluded from default runtime behavior and tracked as a roadmap item.

**Rationale:** While useful for freshness, dynamic pre-injection can expand attack surface and unpredictably inflate context. Deferring it keeps v1 deterministic. Future enablement requires strict read-only execution, output caps, tainting as `external`, and audit traceability.

---

## 17. Operations & Reliability

### 18.1 Error Handling Strategy

Silas uses a unified error taxonomy with deterministic handling:

- `E_CFG_*` (configuration/startup): fail fast, refuse start
- `E_LLM_*` (model API, parse, timeout): retry with bounded backoff, then degrade/fallback route
- `E_DB_*` (SQLite lock/corruption/query): retry transient locks, fail closed on corruption and mark service degraded
- `E_SANDBOX_*` (executor/verification sandbox): fail current work item as `blocked` or `failed`, never bypass verification
- `E_GATE_*` (provider failures): policy lane fails closed, quality lane fails open with audit flag
- `E_CHANNEL_*` (disconnect/timeouts): safe defaults (`decline`/`block`) for pending approvals and card decisions
- `E_APPROVAL_*` (signature/hash/nonce mismatch): hard deny with audit event

All raised errors must map to one taxonomy code, include correlation IDs (`turn_id`, `work_item_id`, `scope_id`), and emit structured audit + metrics events.

### 18.2 Graceful Shutdown

On `SIGTERM`/`SIGINT`:

1. Stop accepting new channel messages.
2. Close approval/card intake with safe declines for unresolved prompts.
3. Let in-flight turns finish up to `shutdown_grace_seconds` (default 30s); then cancel.
4. Persist in-memory state (`work_items`, scoped context cursors, scheduler state) and write audit checkpoint.
5. Drain and close WebSocket connections with close code 1001.
6. Stop scheduler and sandbox workers, then exit.

Crash recovery relies on rehydration from SQLite stores and idempotent approval/execution checks.

### 18.3 Deployment

Supported deployment targets:

- Local/systemd service (single binary + SQLite data dir)
- Docker Compose (app + optional reverse proxy)

Baseline requirements:

- TLS termination at reverse proxy for remote access
- periodic backups of `silas.db` + config + skills directories
- rolling upgrade path: run migrations, verify checksum table, restart with graceful drain
- explicit host/auth policy: remote bind requires auth token

### 18.4 Monitoring

Expose metrics and structured logs for:

- turn latency (p50/p95/p99), queue depth, active scopes
- gate actions (`continue`/`block`/`require_approval`) and quality flags
- approval outcomes and verification failures
- budget consumption (tokens/cost/time) per work item
- sandbox failures/timeouts by backend
- memory growth, chronicle retention, nonce prune counts
- suggestion acceptance/defer/dismiss rates and cooldown suppressions
- autonomy calibration stats (correction rates, widen/tighten proposals, applied deltas)

Alerts:

- sustained gate block spikes
- approval verification failures
- scorer circuit breaker open
- queue depth/backpressure thresholds exceeded
- autonomy proposal spikes or oscillation (repeated widen/tighten churn)
- DB migration/checksum failures

### 18.5 Rate Limiting

Required limits (configurable):

- inbound WebSocket messages per scope and per IP
- approval/card submissions per scope
- LLM calls per minute per scope + global cap
- web-search calls per minute per scope
- memory ops per turn and per minute
- gate evaluations per turn (hard cap)
- proactive suggestion cards per hour per scope
- autonomy-threshold proposals per week per scope

Limit violations return deterministic safe responses and are logged as security events.

### 18.6 Backpressure

Backpressure policy protects availability under load:

- per-scope bounded turn queue (`max_pending_turns_per_scope`)
- global bounded queue (`max_pending_turns_total`)
- if per-scope queue is full: reject newest message with `busy_retry` response
- if global queue is full: reject lowest-priority non-owner traffic first
- long-running background executions publish status without blocking foreground message handling

Queue pressure state is surfaced in `/health` and metrics for autoscaling/operator action.

## 18. Roadmap

Roadmap items intentionally excluded from current implementation scope:

- `R1`: Additional approval strength integrations (for example WebAuthn/passkey enrollment and policy mapping)
- `R2`: Exact token pre-counting integration path (SDK/provider-backed precise counters)
- `R3`: Side-session routing and dedicated side-session execution surface
- `R4`: Memory strategy expansion beyond current set (entity/causal graph traversal)
- `R5`: Context subscription semantic anchors and enhanced stale-detection semantics
- `R6`: Optional async gate lane for non-blocking telemetry-only checks
- `R7`: `WorkItemType.system` orchestration and broader autonomous proposal framework
- `R8`: Non-essential PWA offline queue/push orchestration enhancements
- `R9`: Gate model refactor (`Gate`) into discriminated unions for stricter schema ergonomics
- `R10`: Key-rotation lifecycle (active + next key, rollover window, audit policy)
- `R11`: Re-evaluate `AgentResponse.needs_approval`; remove if redundant after runtime override hardening
- `R12`: Personality simplification pass after production telemetry
- `R13`: Safety-gated dynamic skill context injection (`{{script}}`/command expansion) with read-only execution, strict output caps, taint enforcement, and full audit traces
