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

