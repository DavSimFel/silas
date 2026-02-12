# Implementation Status

Last updated: 2026-02-12 (post-gap-analysis, second pass + companion docs)

## Summary

This file now separates:

- **Implemented component**: class/module exists and has tests.
- **Runtime spec-compliant**: integrated end-to-end in Stream/Executor flows with spec-required enforcement.

Current state: many components are implemented, with `INV-01`, `INV-03`, standing-approval spawn verification, planner-route handoff, and step-0/1 input-gate flow now enforced in runtime execution, but there are still critical runtime gaps against `specs.md`.

---

## ✅ Implemented Components (Exist + Tested)

### Protocols / Core Components

| Component | Implementation | Notes |
|----------|---------------|------|
| ChannelAdapterCore / RichCardChannel | `WebChannel` | Implemented |
| MemoryStore | `SQLiteMemoryStore` | Implemented |
| MemoryRetriever | `SilasMemoryRetriever` | Implemented |
| MemoryConsolidator | `SilasMemoryConsolidator` | Implemented |
| ContextManager | `LiveContextManager` | Implemented |
| ApprovalVerifier | `SilasApprovalVerifier` (Ed25519) | Implemented as component |
| NonceStore | `SQLiteNonceStore` | Implemented |
| GateRunner | `SilasGateRunner` | Implemented |
| Gate providers | `PredicateChecker`, `ScriptChecker`, `LLMChecker` | Implemented |
| VerificationRunner | `SilasVerificationRunner` | Implemented |
| AccessController | `SilasAccessController` | Implemented |
| WorkItemExecutor | `LiveWorkItemExecutor` | Implemented, but missing required runtime checks (see gaps) |
| WorkItemStore | `SQLiteWorkItemStore` | Implemented |
| ChronicleStore | `SQLiteChronicleStore` | Implemented |
| PlanParser | `MarkdownPlanParser` | Implemented |
| AuditLog | `SQLiteAuditLog` | Implemented |
| PersonalityEngine | `SilasPersonalityEngine` | Component exists, partial Stream integration |
| PersonaStore | `SQLitePersonaStore` | Implemented |
| SkillLoader | `SilasSkillLoader` | Implemented |
| SkillResolver | `LiveSkillResolver` | Implemented |
| SuggestionEngine | `SimpleSuggestionEngine` | Implemented |
| AutonomyCalibrator | `SimpleAutonomyCalibrator` | Implemented |
| TaskScheduler | `SilasScheduler` (APScheduler) | Implemented |

### Security & Keys (Component-Level)

| Component | Status |
|-----------|--------|
| Secret isolation (Tier 1 + Tier 2) | Implemented |
| Ed25519 approval signer/verifier | Implemented |
| Secure input endpoint (`POST /secrets/{ref_id}`) | Implemented |
| `data_dir` wiring from settings | Implemented |

### Models

Pydantic model constraints implemented:

- `AgentResponse`: `len(memory_queries) <= 3`
- `RouteDecision`: profile validation + route/response shape checks
- `Expectation`: exactly one predicate field
- `ContextProfile`: pct bounds and sum constraint
- `BudgetUsed.exceeds()`: `>=` semantics
- `MemoryOp`: op-specific required fields

---

## ⚠️ Critical Runtime Spec Gaps (Open)

These are the highest-priority gaps between code and `specs.md`.

| Severity | Gap | Spec Reference | Current Runtime Behavior |
|----------|-----|----------------|--------------------------|
| High | **Step-5 budget enforcement semantics diverge** | §5.1 step 5 | Budget enforcement is deferred until after response generation, and evicted context is not persisted back to memory per spec |
| High | **Message trust/signing flow mismatch** | §5.1 step 2 | Runtime uses per-process HMAC and no Ed25519 inbound verification/nonced freshness replay flow |
| High | **Stream startup sequence incomplete** | §5.1 `start()` steps 2-7 | Runtime starts rehydration + listen loop only; no `stream_started` audit, connection health/recovery, active-goal scheduling, or heartbeat registration |
| High | **Rehydration is partial vs required lifecycle state** | §5.1.3 steps 1,4-8 | Missing system-zone restore, subscription restore, rehydration system message, in-progress work resume, persona lazy load, and pending review/suggestion/autonomy queue restore |
| High | **Secure-input endpoint contract incomplete** | §5.10.1, §5.9 secure input endpoint | `POST /secrets/{ref_id}` response/validation/audit behavior does not match spec (`{"stored": true}`, pending-ref validation, `secret_stored` audit event) |
| High | **ConnectionManager protocol/runtime drift** | §5.10.1-§5.10.2, `specs/protocols.md` §4.19 | Setup flow is not channel-driven interactive lifecycle, escalation path auto-merges permissions without decision card flow, and activation approval token is ignored |
| High | **Per-connection isolation model incomplete** | §5.1 connection lock/processor model | Stream currently uses a single `TurnContext` scope path, not scoped processor/lock maps |
| High | **Interaction mode resolver not centralized/used** | §5.1.0 `resolve_interaction_mode` | No single resolver function enforced in turn pipeline |
| High | **Sandbox/verification execution policy incomplete** | §9.1, §5.3 | Subprocess sandbox does not enforce spec-level network fail-closed controls/resource limits; verification runner still shells via `bash -lc` |
| High | **Execution layer remains disconnected from work execution path** | §5.2.1(c-e), §9.2 | `ShellExecutor`/`PythonExecutor`/sandbox execution envelopes are implemented but not used by `WorkItemExecutor` runtime flow |
| High | **Operations/reliability controls from §17 are largely unimplemented** | `specs/operations-roadmap.md` §17.1-§17.6 | No unified runtime error taxonomy wiring, no graceful-drain shutdown path with safe card-resolution defaults, and no configured rate-limit/backpressure queue controls |
| Medium | **Output gate escalation model incomplete** | §5.1 step 8 + §5.1.1 | Blocked output is hardcoded to `"I cannot share that"` rather than escalation-map execution |
| Medium | **Quality/policy lane behavior differs on output path** | §5.4/§5.5 | `OutputGateRunner` is a custom path and not full two-lane gate-runner policy/quality flow |
| Medium | **Gate-provider feature coverage is incomplete** | §5.5.2, §5.5.4 | Predicate provider does not implement `file_valid`; script provider does not implement reserved `modified_context` parsing + `check_expect`/`extract` delegation behavior |
| Medium | **Proactivity model/protocol contracts diverge from companion specs** | `specs/models.md` §3.11, `specs/protocols.md` §4.22-§4.23 | Suggestion/autonomy payloads are simplified (`dict`/`str`) and do not match typed `SuggestionProposal` / `AutonomyThresholdProposal` / decision contracts |
| Medium | **Proactivity/autonomy loops are turn-coupled, not heartbeat-driven** | §5.1.6 | Suggestion polling runs during turn handling; autonomy `evaluate()` loop is not scheduler-driven and heartbeat jobs are not wired into runtime start |
| Medium | **Core `web_search` executor parity is missing** | §9.2, `specs/security-model.md` (core retrieval tools) | Runtime currently exposes mock `web_search` via skill handler, not provider-backed `WebSearchExecutor` with deterministic registration/limits |
| Medium | **Memory portability contract is not implemented** | `specs/protocols.md` §4.2.3 | `MemoryPortability` protocol exists, but no `export_bundle`/`import_bundle` implementation is wired in runtime components |
| Medium | **Tier-2 signing key load is not wired into runtime auth path** | §0.8.1 `INV-01`/`INV-02`, §5.11 | Startup loads signing key but does not inject it into Stream/approval verification path |
| Medium | **Proxy fallback behavior differs from spec fallback** | §5.1.0 fallback rules | Proxy local fallback echoes input with `default_and_offer` instead of spec fallback messaging/mode |
| Medium | **Tests codify non-spec behavior** | §5.1 + §5.2 | Some tests assert current stub/deferred behavior (planner stub path, direct proxy plan-actions execution) |

---

## ✅ Recently Closed Runtime Gaps

| Closed On | Gap | Spec Reference | Runtime Fix |
|-----------|-----|----------------|-------------|
| 2026-02-12 | **INV-01 enforced at execution entry** | §0.8.1 `INV-01`, §5.2.1 step 0 | `LiveWorkItemExecutor` now requires `approval_token` + `approval_verifier.check(...)`; missing/invalid approval blocks execution and is auditable |
| 2026-02-12 | **INV-03 enforced for completion truth** | §0.8.1 `INV-03`, §5.2.1(e) | `LiveWorkItemExecutor` now runs external verification for `work_item.verify` and only returns `done` when checks pass |
| 2026-02-12 | **Standing-approval spawn path requires verification** | §5.2.3 step 4 | `SilasGoalManager` now only clears `needs_approval` when a standing token exists and `approval_engine.verify(goal_token, goal_work_item, spawned_task)` succeeds; verified token is attached to spawned task |
| 2026-02-12 | **Planner route handoff now invokes planner agent** | §5.1 step 7 | `Stream` now calls `turn_context.planner` on `route="planner"` and executes planner-produced actions (with legacy proxy-action fallback only when planner output has no actions) |
| 2026-02-12 | **Turn pipeline step-0/step-1 gate path wired** | §5.1 steps 0-1 | `Stream` now precompiles active gates once per turn and runs two-lane input gate evaluation (block/require-approval/continue + quality audit) before routing |

---

## ⚠️ Status Document Corrections Applied

- Replaced prior “Implemented & Spec-Compliant” framing with split status (component-level vs runtime compliance).
- Removed inaccurate implication that all listed components are fully integrated in the runtime enforcement path.
- Fixed protocol-section framing mismatch (previously labeled `18/22` while listing more entries).
- Added missing deferred/core-runtime items previously omitted from the deferred list.

---

## ❌ Deferred / Not Yet Fully Integrated

### Previously listed deferred items

- GuardrailsAI gate provider
- Memory portability (`export_bundle` / `import_bundle`)
- Slide-to-confirm UX and WebAuthn/biometric ladder
- Benchmarking framework (§19-20)
- Optional Docker sandbox backend
- Telegram/CLI channels
- Evals (Pydantic Evals)
- Dynamic skill context injection (ADR-020 disabled)
- Connection auto-discovery shipping path

### Additional deferred core-runtime items (newly documented)

- Full step-0 active gate precompile and reuse across turn
- Input gate enforcement flow (policy and quality lanes)
- Spec-complete plan approval flow (parse -> approval -> token issue+verify -> execute)
- Full startup lifecycle wiring (`stream_started`, connection health/recovery, active-goal registration, scheduler heartbeats)
- Rehydration completeness (system zone, subscriptions, pending cards, in-progress work resume, persona continuity hooks)
- Secure-input pending-request registry + audit event parity for `/secrets/{ref_id}`
- Goal standing-approval verification + token attachment in spawn flow
- ConnectionManager interactive setup and permission-escalation card flow
- Step-5-compliant budget timing + evicted-context persistence
- Two-tier context eviction parity (trivial-ack drop, stale subscription deactivate, scorer fallback path)
- Memory query (`step 9`) and memory-op gated writes (`step 10`) in Stream
- Raw output/query ingest (`step 11.5`)
- Access-state updates from gate pass results (`step 14`)
- Personality pre-agent directive injection and post-turn event/decay hooks (`steps 7/15`)
- Per-connection turn processors + lock map isolation model
- Centralized `resolve_interaction_mode(...)` governance
- Planner-agent invocation on planner route in main Stream path
- Sandbox policy parity (resource/network/path enforcement + verification command execution contract)
- Wire execution envelopes/executor registry into runtime `WorkItemExecutor`
- Scheduler-driven suggestion/autonomy loops + proposal review flow
- Gate-provider parity for `file_valid` and script `check_expect`/`extract`/`modified_context`
- Provider-backed `WebSearchExecutor` integration (no mock-skill fallback for core retrieval)
- Full `MemoryPortability` implementation (`export_bundle` / `import_bundle`) with canonical bundle format
- Proactivity contract alignment to typed `SuggestionProposal` / `AutonomyThresholdProposal` models
- Operations hardening: taxonomy-coded error handling, graceful shutdown drain, rate limiting, and queue backpressure controls

---

## 📊 Test/Lint Snapshot

- Test suite is large and active (~670+ tests).
- Lint/type quality is generally strong.
- Some current tests intentionally validate deferred/stubbed behavior; these should be updated as runtime gaps close.

---

## 🏗️ Build History

| PR | Description | Tests Added |
|----|-------------|-------------|
| #27 | Remaining tests (medium priority) | +20 |
| #28 | Scorer agent, two-tier eviction | +models |
| #29 | Planner, executor, key manager, sandbox | +16 |
| #30 | LLM + script gate providers | +tests |
| #31 | SkillResolver, SilasScheduler | +31 |
| #32 | C901 complexity fixes | — |
| #33 | 317 lint violations fixed | — |
| #34 | Integration tests | +20 |
| #35 | Remove all `type: ignore` | — |
| #36 | Execution layer + agent fallback tests | +21 |
| #37 | Code quality + API key support | — |
| #38 | WorkItemRunner + zombie cleanup | +tests |
| #39 | Split stream.py (966→572 lines) | — |
| #40 | WebSocket auth enforcement | +tests |
| #42 | Benchmarking spec (§19-20) | — |
| #43 | Security batch (6 findings) | — |
| #44 | Security regression tests | +12 |
| #45 | Protocol drift fixes | — |
| #46 | TYPE_CHECKING guards | — |
| #47 | Structured logging | +3 |
| #48 | Onboarding flow (CLI + web + PWA) | +6 |
| #49 | SecretStore (two-tier) | +12 |
| #50 | RichCardChannel (12 methods) | +12 |
| #51 | ApprovalVerifier + Ed25519 | +tests |
| #52 | Two-tier key storage | +tests |
| #53 | MemoryRetriever | +11 |
| #54 | Compliance batch (gaps 5,7,8,12) | +7 |
