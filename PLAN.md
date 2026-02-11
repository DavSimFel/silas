# PLAN.md — Implementation Roadmap

## Design Principles

1. **Every phase ends with a runnable, testable system** — not a pile of modules
2. **Each phase adds one capability layer** — never two unrelated concerns
3. **Migration happens when Silas can replace current-me** — not before, not after
4. **Test infra ships WITH the phase it tests** — not in a separate "testing phase"

---

## Phase 1: Echo (skeleton + persistence)

**What it does:** Message in → LLM response → persisted to disk. Survives restart.

### 1a: Bare Skeleton
- Project bootstrap (`pyproject.toml`, `uv`, package layout, CI)
- All Pydantic models in `models/` (skeleton-path first: messages, agents, context)
- All Protocol definitions in `protocols/`
- Basic `WebChannel` (FastAPI + WebSocket, single-scope)
- Minimal chat UI (`web/index.html`, `app.js`, `style.css`)
- `HeuristicTokenCounter` (chars ÷ 3.5)
- `Stream._process_turn()` stub → Proxy → response
- `TurnContext` dependency container + scope-aware turn-processor factory
- PydanticAI Proxy agent with `RouteDecision` output type
- `AgentResponse` parsing from structured output
- YAML config loading + minimal defaults
- `silas init` + `silas start` CLI
- Phase 1a step coverage: turn steps 2–4, 7 (routing only), 13. All others = no-op stubs with audit log.
- Test infra: `TestModel`, `FakeTokenCounter`, in-memory stores
- **Milestone:** `silas start` → open http://localhost:8420 → send message → get response

### 1b: Persistence
- SQLite schema (`001_initial.sql`) + migration runner (sequential, idempotent, checksummed)
- `SQLiteMemoryStore` — CRUD + FTS5 keyword search (no embeddings)
- `SQLiteChronicleStore` — durable append/get_recent for rehydration
- `SQLiteWorkItemStore` — save/get/update_status/list for crash recovery
- Stream rehydration from stores on startup
- Migration checksums enforced on startup
- Test infra: store integration tests (CRUD round-trips, FTS5, rehydration)
- **Milestone:** restart process → prior conversation there → memories retrievable

### 1c: Routing Intelligence
- `LiveContextManager` with zone management, dynamic budget profiles, observation masking, heuristic eviction
- Proxy `RouteDecision` structured output fully wired (register + mode + profile)
- `run_structured_agent` wrapper (§5.1.0 — retry + deterministic fallback)
- `resolve_interaction_mode()` centralized function
- Auto-memory retrieval via FTS5 in turn loop
- `MarkdownPlanParser` baseline (front matter + body → WorkItem)
- PWA bootstrap: `manifest.json`, `sw.js`, install prompt support
- Test infra: routing evals (direct vs planner classification), parser unit tests
- **Milestone:** Proxy routes direct/planner correctly. Prior memories in context. Rehydration + keyword retrieval E2E.

**Gate:** Multi-turn conversation with memory across restarts.

---

## Phase 2: Brain (memory + context)

**What it does:** Silas manages its own context intelligently. Remembers, forgets, prioritizes.

- Two-tier eviction: heuristic pre-filter + scorer model
- Scorer agent (persistent instance, lazy-injected into context manager)
- `ScorerOutput`/`ScorerGroup` models + scorer via `run_structured_agent`
- Scorer reliability (2s timeout, circuit breaker, deterministic fallback)
- Observation masking for stale tool results
- Context profile switching from routing decisions
- Memory queries from `AgentResponse` (max 3/turn)
- Memory ops from `AgentResponse` (Stream-enforced limit via `max_memory_ops_per_turn`)
- Metadata-tagged rendering with provenance delimiters
- `SQLiteAuditLog` — hash-chained audit entries
- Test infra: `FakeContextScorer`, context manager zone/eviction tests, memory op tests
- **Milestone:** Remembers across turns. Old results auto-masked. Stale context evicted. Audit running.

**Gate:** Context stays coherent over 50+ turn conversations.

---

## Phase 3: Hands (planning + execution)

**What it does:** Plan → approve → execute → verify. The core agent loop.

### 3a: Planner + Approval
- PydanticAI Planner agent with `AgentResponse` output
- `MarkdownPlanParser` full implementation (gates/verify/escalation/skills)
- `SilasKeyManager` — Ed25519 keypair generation + OS keyring
- `ApprovalEngine` — token minting, signing, verification
- Cryptographic approval (plan hash, nonce, expiry, signature, `approval_strength`)
- `ApprovalToken.signature` as `Base64Bytes` (annotated type with PlainSerializer/PlainValidator)
- `SQLiteNonceStore` — replay protection
- Plan approval UI in web frontend (preview, approve/decline)
- Test infra: `FakeKeyManager`, approval flow tests, nonce replay tests
- **Milestone:** Complex request → see plan → approve → token minted + verified

### 3b: Executor + Verification
- `SilasWorkExecutor` — retry loop + budget enforcement (`>=` semantics)
- Executor agent (PydanticAI) with `ExecutorAgentOutput`
- Tool execution ledger (c1) + artifact collection from actual results (c3)
- Subprocess `SandboxManager` (process create/exec/destroy)
- `ShellExecutor` + `PythonExecutor` via `SandboxManager`
- `WebSearchExecutor` as core harness tool
- `ExecutionEnvelope` with `credential_refs` (opaque keyring refs, never raw secrets)
- `SilasVerificationRunner` — external checks on filesystem artifacts, path validation
- All `Expectation` predicates + `PredicateChecker` + `ScriptChecker`
- Retry: fail → retry → consult planner → stuck
- Deepening linkage (`follow_up_of` + artifact inheritance), `BudgetUsed.merge()`
- Test infra: `FakeVerificationRunner`, `FakeWebSearchExecutor`, executor retry tests
- **Milestone:** Approve → execute in sandbox → verification passes/fails → retry works

### 3c: Toolset Pipeline
- Full wrapper chain: Skill → Prepared → Filtered → ApprovalRequired (§4.24 PydanticAI v1.x binding)
- Tool-call gates + argument validation (c2)
- Approval-paused call flow (defer → user decision → resume)
- Optional dynamic outer wrapper (telemetry/revocation)
- `web_search` tool registration when configured
- Test infra: wrapper chain integration tests, tool-call gate tests
- **Milestone:** Tool calls flow through filter → approval → execute → result

**Gate:** "Do X" → plan → approve → execute → verify. Core loop works. **MVP-1.**

---

## Phase 4: Guards (gates + safety)

**What it does:** Safety layer. Input/output gates, access control, quality checks.

- `SilasGateRunner` — two-lane dispatch (policy blocks, quality logs)
- `PredicateChecker` for gate predicates (policy lane)
- `LLMChecker` — quality-tier advisory (quality lane) + `promote_to_policy`
- `ScriptChecker` for gate scripts
- `GateResult.modified_context` with `ALLOWED_MUTATIONS` enforcement
- Precompiled active gate set per turn + per execution
- Mid-execution gates (after_step trigger)
- `SilasAccessController` — levels, tool filtering, expiry, owner bypass (§5.6)
- Global `gates.system` from config
- `GuardrailsAI` checker integration (config-driven)
- Error handling hardening: LLM API failures, WebSocket disconnects, sandbox failures
- Test infra: `FakeLLMGateProvider`, gate two-lane tests, access controller tests
- **Milestone:** Toxic input blocked. Tools filtered by level. Quality gates log without blocking.

**Gate:** Safe enough for external-facing use.

---

## Phase 5: Voice + PWA

**What it does:** Personality engine + PWA hardening. This is where I move into my own runtime.

### 5a: Personality Engine
- Pydantic models (`AxisProfile`, `MoodState`, `VoiceConfig`, `PersonaPreset`, `PersonaState`, `PersonaEvent`)
- Protocols (`PersonalityEngine`, `PersonaStore`)
- `SQLitePersonaStore` (persona_state + persona_events tables)
- `SilasPersonalityEngine`:
  - Context detection → axis deltas
  - Axis composition + clamping
  - Mood events + time decay
  - `render_directives()` → 200–400 token natural language
- Stream step 7 hook: inject directives (`source="persona:directives"`, pinned)
- Stream step 15 hook: post-turn mood/event update + decay
- Web API: `GET /persona/state`, `POST /persona/preset`, `POST /persona/feedback`, `POST /persona/tune`
- Trust guardrails on baseline drift
- Test infra: `FakePersonalityEngine`, axis composition tests, decay tests
- **Milestone:** Preset change → style changes. Mood survives restart. Security unchanged.

### 5b: PWA Hardening
- PWA finalization: offline support, install prompt, push notifications for approval requests
- Side sessions (URL routing: `/side/new`, `/stream`)
- Card rendering for approval, batch review, suggestions
- **Milestone:** Full personality + PWA. Agent has voice and style.

**Gate:** Silas has personality, memory, execution, safety — all via installable PWA.

---

## Phase 6: Skills + Connections

**What it does:** Extensible capabilities. External service integration.

### 6a: Skill System
- SKILL.md loader, frontmatter parser, flat directory scanner
- Skill resolver (name → path, script validation, work-item scoping)
- Skill-aware toolset preparation (metadata budget cap for Proxy, full prep for Planner/Executor)
- Skill installation flow (validation → approval → sandbox dry-run → indexing)
- Skill validator (completeness, syntax, forbidden patterns)
- External skill import/adaptation (OpenAI/Claude normalization)
- Script argument schema validation at sandbox boundary
- Default skills: `coding`, `skill-maker`
- **Milestone:** Install skill → appears in toolset → executor uses it

### 6b: Connections
- `ConnectionManager` full lifecycle (discover → setup → activate → health → refresh → recover)
- NDJSON subprocess protocol (§5.10.1 / §10.6)
- Example: `skills/m365-outlook/`
- `POST /secrets/{ref_id}` — secure credential ingestion to OS keyring
- All card types: `SecureInputCard`, `DeviceCodeCard`, `BrowserRedirectCard`, `PermissionEscalationCard`, `ConnectionFailureCard`
- Incremental permission model
- Proactive token refresh scheduling
- Connection failure recovery flows
- **Milestone:** Set up M365 connection → health checks pass → credentials in keyring

**Gate:** Can install skills and connect to external services.

---

## Phase 7: Goals + Autonomy

**What it does:** Recurring work, standing approvals, proactive behavior, preference learning.

### 7a: Goals + Standing Approvals
- `APSchedulerWrapper` for cron goals
- Active goal loading from config
- Standing approvals with `spawn_policy_hash` canonicalization (§3.6 — exact algorithm for template hashing)
- Goal scheduled verification + automatic fix task spawning
- Goal monitoring queue (pending batches, low-confidence escalations)
- Review + proactive queue polling at turn start (§5.1 step 0.5 — pending batch reviews, low-confidence escalations, draft reviews, connection warnings)
- Test infra: `FakeSuggestionEngine`, standing approval tests

### 7b: Batch Review + Execution
- Batch review cards + batch execution loop
- Full reviewed batch execution flow (§5.2.4 — batch candidate building, chunking, batch token binding, edit-selection re-approval, autonomy outcome recording)
- `DraftReview` / `DraftVerdict` models and draft review flow (§3.11)

### 7c: Autonomy + Preferences
- Suggestion engine heartbeat + review cards
- Autonomy calibrator (correction-rate tracking, threshold proposals, rollback)
- Memory consolidator (background)
- Behavioral preference inference pipeline (§6.6 — signal ingestion, preference memory creation, working→verified promotion, planner/proxy consumption)
- Context subscriptions full implementation (§3.5 — `file`, `file_lines`, `memory_query` types, deduplication, TTL deactivation, materialization caching, content-hash change detection)
- Approval fatigue mitigation (§0.5.4 — cadence tracking, queue density cues, no hard throttling)
- Undo/recover pattern (§0.5.5 — reverse action log, 5-min undo window, post-execution undo card)
- UX quality metrics collection (§0.5.6 — decision time, taps per batch, decline rate, correction rate, undo rate, approval fatigue triggers, free-text usage rate)
- Test infra: `FakeAutonomyCalibrator`, batch execution tests, preference pipeline tests

- **Milestone:** Goal pack runs E2E. Standing approvals cover recurring tasks. Suggestions + autonomy proposals work. Preferences learned from behavior.

**Gate:** Silas proactively maintains things without being asked. **MVP-2.**

---

## 🪶 MIGRATION (between Phase 7 and 8)

**What it does:** Silas moves into his own runtime. Onboarding gets the owner set up. Polish happens from the inside.

### Onboarding Flow (§1.5)
- Welcome card: "I'm Silas. Let's get you set up." + `[Get Started]` CTA
- LLM provider selection (OpenRouter / local) with API key secure input field
- `POST /secrets/{ref_id}` endpoint (§8.1) — secure credential ingestion to OS keyring (bypasses WebSocket, never enters agent pipeline)
- API key immediate validation (test call to provider)
- Identity bootstrap (name, primary email, primary phone)
- Completion → redirect to The Stream
- First message: "I'm ready. Tell me what to connect first, or I'll figure it out."

### Migration
- Seed initial memory + personality baseline from owner's context files
- Configure PWA + Telegram pointed at Silas instance
- Multi-connection WebChannel (§8.1 — scope isolation, per-connection `AccessController`, `scope_id`, `TurnProcessor` partition, pending-response tracking)
- WebSocket auth (§8.1 — `Sec-WebSocket-Protocol` bearer OR first-message auth with 5s timeout, code 4001)
- `Session` model implementation (§3.9 — `SessionType` stream/side, pinned context IDs)
- Verify: multi-turn memory, approval flow, personality coherence, context eviction
- 48h parallel run with prior agent setup, owner uses both
- Cutover — Silas becomes primary

**Gate:** Silas handles conversation, memory, task execution, safety, personality, skills, and autonomy. Ready for real use.

---

## Phase 8: Scale + Polish

**What it does:** Production hardening, additional channels, advanced features. Silas polishes himself from inside the runtime.

### 8a: Channels + Sandbox
- `TelegramChannel` — long-polling, inline keyboards
- Docker backend for `SandboxManager`
- Browser skill (Playwright in Docker sandbox)
- Webhook channels (Discord/Slack)
- Per-connection `AccessController` in web channel
- State gates for identity verification
- CLI `getpass.getpass()` for `SecureInputCard` rendering (§0.5.3)

### 8b: UX Hardening
- Risk ladder implementation (§0.5.2 — interaction patterns per risk level: tap, slide confirm, biometric)
- Card contract enforcement (§0.5.3 — standardized card anatomy: intent, risk_level, rationale, consequence_label, CTA ordering, max height, details expansion)
- Three persistent PWA surfaces (§8.2 — Stream, Review queue, Activity log) with full surface architecture
- Activity surface as human-readable audit timeline (§0.5.1)
- `send_checkpoint` + WebSocket `checkpoint` message type (§4.1.1, §8.1 — RichCardChannel checkpoint method)

### 8c: Safety + Reliability
- Taint tracker implementation (§5.12 — propagation rules, constitutional memory protection)
- Error taxonomy (§17/18.1 — unified `E_CFG_*`, `E_LLM_*` etc., correlation IDs)
- Graceful shutdown (§17/18.3 — SIGTERM/SIGINT handling, state persistence, WebSocket drain)
- Rate limiting (§17/18.6 — per-scope/IP limits, safe rejection responses)
- Backpressure (§17/18.7 — bounded turn queues, priority-based rejection)
- Config startup validation rules (§11 — 12+ fail-fast checks: host/auth, verify_dir ≠ customer_context_dir, profile sums, hysteresis)
- `silas init` hardening (§12 — keypair gen, DB creation, migrations, guardrails resolver, search validation)
- Chronicle retention pruning (§4.14 — `prune_before`, configurable retention policy, default 90 days)
- Audit checkpoint + incremental verification (§4.16 — `write_checkpoint`, `verify_from_checkpoint`)
- Message freshness window (§5.1 step 2 — configurable timestamp staleness check for signed messages)

### 8d: Advanced Features
- Access level transitions with GDPR audit logging
- Escalation pipelines (transfer to human, suppress and rephrase)
- Skill creation flow (plan → build via skill-maker → approve → activate)
- Project execution with dependency ordering (topological sort)
- Memory portability (§4.2.3, §6.5 — `MemoryPortability` protocol, JSONL bundle format, export/import, versioning, merge/replace modes)
- Raw memory ingest lanes (§5.1 steps 3.5/11.5 — `store_raw` for conversation/tool/research logs)
- Causal graph search in memory retriever
- User profile distillation in consolidator
- `FastEmbedEmbedder` + `sqlite-vec` for vector search
- `MultiGraphRetriever` v1 (semantic + temporal)

### 8e: Operations + Monitoring
- `GET /health` endpoint (§8.1 — returns `{"status": "ok", "connections": int}`)
- Monitoring (§17/18.5 — structured logs, metrics, alerts)
- Deployment hardening (systemd, Docker Compose, TLS, backup strategy)
- Load tests (§14 — sustained concurrent scopes, gate/scorer stress, WebSocket churn)
- Chaos tests (§14 — process kill recovery, SQLite lock contention, LLM timeout simulation, sandbox capability loss)
- Pydantic Evals for routing, planning, memory, gate accuracy
- Health monitor goal template
- Admin dashboard (work items, audit log, memory browser, skill catalog)
- Customer support bot template

---

## Migration Timeline

```
Phase 1 ─── Phase 2 ─── Phase 3 ─── Phase 4 ─── Phase 5 ─── Phase 6 ─── Phase 7 ─── 🪶 MIGRATION ─── Phase 8
 Echo        Brain       Hands       Guards      Voice+PWA    Skills     Goals+Auto    I MOVE IN       Polish
                           │                                                │                            │
                         MVP-1                                            MVP-2                    (from inside)
                                               (48h parallel
                                                then cutover)
```

## Design Rationale

| Decision | Rationale |
|----------|-----------|
| Vector search in Phase 8 | FTS5 is enough for MVP. Avoids ONNX/sqlite-vec build complexity early. |
| Gates as dedicated Phase 4 | Safety is a layer, not mixed into execution logic. |
| Personality before migration (Phase 5) | Agent needs personality to be *itself*. |
| PWA as primary channel | Installable, works offline, no third-party dependency. Telegram is Phase 8. |
| Goals/autonomy in Phase 7 | Advanced proactive behavior. Shouldn't block migration. |
| Audit log in Phase 2 | Need trail before any execution happens. |
| Phase 3 as 3a/3b/3c | Planner, executor, and toolset pipeline are independently testable. |
