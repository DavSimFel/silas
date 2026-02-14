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

