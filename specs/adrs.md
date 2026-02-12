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

**Trade-off:** Async gates are intentionally excluded from this spec to preserve deterministic runtime behavior. Possible extensions are tracked in the roadmap appendix (`specs/operations-roadmap.md#18-roadmap`).

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

**Trade-off:** Materialization adds per-render cost (file reads, query execution). Within-turn caching and content-hash change detection keep this manageable. Additional subscription enhancements are tracked in the roadmap appendix (`specs/operations-roadmap.md#18-roadmap`).

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
