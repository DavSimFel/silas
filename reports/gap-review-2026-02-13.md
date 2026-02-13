# Silas Runtime — Gap Review 2026-02-13

**Reviewer:** Silas (self-audit)  
**Scope:** specs.md, specs/*.md, ARCHITECTURE.md, STATUS.md vs actual code in silas/, tests/, config/  
**Verdict:** The project has solid foundations but STATUS.md significantly overstates completion. The queue system exists but isn't wired end-to-end. Several security invariants are partially enforced. Multiple spec sections have zero implementation.

---

## 1. Executive Summary

**Overall health: 55-60% of spec implemented. Core scaffolding is real; the autonomous loop is not.**

The project has ~20K lines of Python across ~130 modules and ~14K lines of tests (~690 tests). The approval engine, gate system, work item executor, context manager, memory store, and basic agents are implemented and tested. The queue infrastructure (WI-1 through WI-4) exists as code but the integration is shallow — the `QueueBridge.collect_response()` polls proxy_queue by leasing *any* message and nacking non-matches, which is O(n) in queue depth and will break under concurrent traces.

**Biggest risks:**
1. **Queue integration is a facade.** STATUS.md marks WI-1 through WI-4 as "✅ Done" but agents still run as one-shot structured output in the main path. The queue bridge is a thin wrapper that doesn't actually run agents through the queue loop in production.
2. **Memory steps 9-10 are no-ops.** The Stream logs `phase1a_noop` for memory query processing and memory op processing. The agent's `memory_queries` and `memory_ops` are never executed post-response.
3. **No taint propagation system.** INV-05 claims "taint propagation remains outside agent control" but there is no taint tracker in the codebase. Taint is set on messages and memory items but never propagated through tool call chains.
4. **Spec uses `pynacl` but code uses `cryptography` library.** The Ed25519 implementation works but diverges from the stated dependency.

---

## 2. Spec vs Code Matrix

### 2.1 Core Turn Pipeline (specs.md §5.1)

| Step | Spec | Code Status | Notes |
|------|------|-------------|-------|
| 0. Precompile gates | Required | ✅ Implemented | `_precompile_active_gates()` |
| 0.5 Review/proactive queue | Required | ⚠️ Partial | Suggestions collected, but batch reviews, draft reviews, connection warnings not polled |
| 1. Input gates | Two-lane | ✅ Implemented | Policy + quality lanes, approval flow |
| 2. Sign/taint | Ed25519 + nonce | ✅ Implemented | But Stream self-signs inbound messages (signs its own input), which is architecturally wrong — the spec envisions the *client* signing |
| 3. Chronicle | Required | ✅ Implemented | |
| 3.5 Raw memory ingest | Required | ✅ Implemented | |
| 4. Auto-retrieve memories | Required | ✅ Implemented | Keyword + entity mention matching |
| 5. Budget enforcement | Two-tier eviction | ⚠️ Partial | Heuristic eviction exists, scorer model (tier 2) not implemented |
| 6. Build toolset pipeline | Wrapper chain | ⚠️ Partial | Tools exist but full SkillToolset→PreparedToolset→FilteredToolset→ApprovalRequired chain is declared, not fully wired in Stream |
| 6.5 Skill-aware toolsets | Required | 🔴 Stub | Logged as `phase1a_noop` |
| 7. Proxy + personality | Required | ⚠️ Partial | Proxy runs, personality engine exists, but directive injection into system zone not confirmed in turn pipeline |
| 8. Output gates | Two-lane | ⚠️ Partial | Output gate runner exists but uses a separate `OutputGateRunner` class, not the same two-lane `SilasGateRunner` |
| 9. Memory queries | Required | 🔴 No-op | Logged as `phase1a_noop` |
| 10. Memory ops (gated) | Required | 🔴 No-op | Logged as `phase1a_noop` |
| 11. Response chronicle | Required | ✅ Implemented | |
| 11.5 Raw output ingest | Required | 🔴 No-op | Logged as `phase1a_noop` |
| 12. Plan/approval flow | Required | ✅ Implemented | Planner route + skill execution + approval |

### 2.2 Agent Loop Architecture (specs/agent-loop-architecture.md)

| Component | Spec | Code Status | Notes |
|-----------|------|-------------|-------|
| DurableQueueStore | §2.2 | ✅ Implemented | SQLite-backed, lease/ack/nack/dead-letter, heartbeat |
| QueueMessage types | §2.1 | ⚠️ Divergent | Code uses Pydantic `QueueMessage` with `dict[str,object]` payload, not the typed `StatusPayload`/`ErrorPayload` from spec. Missing fields: `scope_id`, `taint`, `task_id`, `parent_task_id`, `work_item`, `plan_markdown`, `approval_token`, `artifacts`, `constraints`, `urgency` |
| Queue routing | §2.3 | ✅ Implemented | `QueueRouter` with routing table |
| Proxy consumer | §3 | ⚠️ Partial | `ProxyConsumer` exists but doesn't use `ContextManager.render()` for history — it passes raw prompt to agent |
| Planner consumer | §4 | ⚠️ Partial | `PlannerConsumer` exists but research state machine (§4.8: planning→awaiting_research→ready_to_finalize→expired) not implemented |
| Executor consumer | §5 | ⚠️ Partial | `ExecutorConsumer` exists but research mode enforcement is prompt-based ("RESEARCH MODE" prefix) not toolset-level (`RESEARCH_TOOL_ALLOWLIST` clamping) |
| Consult-planner | §5.2.1 on_stuck | ⚠️ Exists | `ConsultManager` in queue/consult.py, but not wired into executor retry loop |
| Replan cascade | §4.6.1 | ⚠️ Exists | `ReplanManager` in queue/replan.py, but not wired into execution failure path |
| Telemetry events | §2.4 | ⚠️ Exists | `QueueTelemetryEvent` schema defined but telemetry emission points not wired |
| Audit events | §2.5 | ⚠️ Partial | Audit events logged throughout Stream but not using `RuntimeAuditEvent` schema from spec |
| QueueOrchestrator | Needed | ✅ Implemented | Consumer lifecycle management with backoff |
| QueueBridge | Integration | ⚠️ Fragile | `collect_response` leases any proxy_queue message, nacks non-matches — O(n) and racy |

### 2.3 Data Models (specs/models.md)

| Model | Spec | Code Status |
|-------|------|-------------|
| ChannelMessage, SignedMessage, TaintLevel | §3.1 | ✅ |
| AgentResponse, RouteDecision, PlanAction | §3.2 | ✅ |
| WorkItem, Budget, BudgetUsed, Expectation | §3.3 | ✅ |
| VerificationCheck, EscalationAction | §3.3 | ✅ |
| ApprovalToken, ApprovalDecision | §3.6 | ✅ |
| ContextItem, ContextZone, ContextSubscription | §3.7 | ✅ |
| MemoryItem, MemoryQuery, MemoryOp | §3.4 | ✅ |
| Gate, GateResult, GateConfig | §3.5 | ✅ |
| PersonalityAxes, MoodState | §3.8 | ✅ |
| BatchProposal, DraftVerdict, DecisionOption | §3.9-3.10 | ✅ |
| SuggestionProposal, AutonomyThresholdProposal | §3.11 | ✅ |
| ConnectionPermission, AuthStrategy | §3.12 | ✅ |
| UndoAction | §0.5.5 | ✅ (model exists) |

### 2.4 Protocols (specs/protocols.md)

| Protocol | Spec | Implementation | Notes |
|----------|------|----------------|-------|
| ChannelAdapterCore | §4.1 | ✅ WebChannel | |
| RichCardChannel | §4.1.1 | ✅ WebChannel | Most methods implemented |
| MemoryStore | §4.2 | ✅ SQLiteMemoryStore | |
| MemoryRetriever | §4.2.1 | ✅ SilasMemoryRetriever | |
| MemoryConsolidator | §4.2.2 | ✅ SilasMemoryConsolidator | |
| MemoryPortability | §4.2.3 | 🔴 Protocol only | No implementation of export_bundle/import_bundle |
| ContextManager | §4.3 | ✅ LiveContextManager | |
| ApprovalVerifier | §4.4 | ✅ SilasApprovalVerifier | |
| NonceStore | §4.5 | ✅ SQLiteNonceStore | |
| EphemeralExecutor | §4.6 | ✅ ShellExecutor, PythonExecutor | |
| SandboxManager | §4.7 | ✅ SubprocessSandboxManager | No Docker backend |
| GateCheckProvider | §4.8 | ✅ PredicateChecker, ScriptChecker | |
| GateRunner | §4.9 | ✅ SilasGateRunner | |
| VerificationRunner | §4.10 | ✅ | |
| AccessController | §4.11 | ✅ SilasAccessController | |
| WorkItemExecutor | §4.12 | ✅ LiveWorkItemExecutor | |
| WorkItemStore | §4.13 | ✅ SQLiteWorkItemStore | |
| ChronicleStore | §4.14 | ✅ SQLiteChronicleStore | |
| PlanParser | §4.15 | ✅ MarkdownPlanParser | |
| PersonalityEngine | §4.16 | ✅ SilasPersonalityEngine | |
| Scheduler | §4.17 | ✅ SilasScheduler (APScheduler) | |

---

## 3. Security Invariants — Code Path Tracing

### INV-01: Executable actions require cryptographically verified approval tokens (Ed25519)

**Status: ✅ Enforced**

Code path: `LiveWorkItemExecutor._execute_single()` → `_check_execution_approval()` → `ApprovalVerifier.check()` → signature verification via `cryptography.hazmat.primitives.asymmetric.ed25519`. Without a valid token, execution returns `blocked` status.

**Caveat:** The spec says `pynacl` (libsodium) but code uses `cryptography` library. Functionally equivalent but a dependency divergence.

### INV-02: Approval tokens are content-bound and replay-protected

**Status: ✅ Enforced**

`SilasApprovalVerifier.verify()` checks: signature validity → plan_hash binding → expiry → execution count → nonce domain binding (`exec:{token_id}:{plan_hash}:{nonce}`). Standing token verification checks `spawned_task.parent == token.work_item_id`.

`SilasApprovalVerifier.check()` (non-consuming) validates: signature → plan_hash → expiry → `1 <= executions_used <= max_executions`.

### INV-03: Completion truth is external deterministic verification

**Status: ✅ Enforced**

`LiveWorkItemExecutor._execute_single()` runs `_run_external_verification()` after successful execution attempt. Uses `VerificationRunner.run_checks()` which runs checks outside the agent's sandbox. Agent cannot influence verification.

**Caveat:** If no verification checks are defined (`work_item.verify` is empty), verification is skipped and execution is marked as done. This is by design per spec but worth noting — work items without verification checks bypass INV-03.

### INV-04: Policy gates run deterministically; quality gates are advisory

**Status: ✅ Enforced**

`SilasGateRunner.check_gates()` evaluates policy-lane gates first (can block/require_approval), then quality-lane gates (always return `action="continue"`, mutations ignored). Quality gate actions are overridden in `_normalize_quality_result()`. Mutation allowlist (`ALLOWED_MUTATIONS`) enforced in `_sanitize_policy_mutation()`.

### INV-05: Execution isolation and taint propagation remain outside agent control

**Status: ⚠️ Partially enforced**

- **Execution isolation:** ✅ `SubprocessSandboxManager` creates isolated working directories, sets resource limits (memory, CPU), blocks network via `unshare -n`, blocks `bash -c` shell injection. Each sandbox gets a tempdir that's destroyed on cleanup.
- **Taint propagation:** 🔴 **Not implemented.** There is no `TaintTracker` class. Taint is assigned at message ingestion (`owner`/`auth`/`external`) and stored on `MemoryItem` and `ContextItem`, but there is no mechanism to propagate taint through tool call chains or data flows. If an external-tainted message produces a tool result, that result's taint is not automatically set to `external`.

### INV-06: Skill activation requires deterministic validation, approval, and hash-bound versioning

**Status: ⚠️ Partial**

- Skill validation: `SkillValidator` exists in `silas/skills/validator.py`
- Skill approval: Wired in Stream via `ApprovalFlow.request_skill_approval()`
- Hash-bound versioning: Not confirmed — no evidence of content-hash tracking on installed skills

---

## 4. Dead Specs (Zero Code Backing)

| Spec Section | Description | Status |
|---|---|---|
| §0.5.1 Three persistent UI surfaces (Stream/Review/Activity) | Frontend surfaces described in detail | Frontend exists but the Review and Activity surfaces as distinct decision cockpit surfaces are not confirmed as separate implementations |
| §0.5.2 Risk ladder (low/medium/high/irreversible interaction patterns) | Slide-to-confirm, biometric auth | No UI interaction pattern enforcement — approval is binary tap |
| §0.5.3 Card contract (max height, details expansion rules) | Enforced card anatomy | No card rendering enforcement in code |
| §0.5.4 Approval fatigue mitigation (cadence tracking, queue density cues) | Tracking median decision time | No implementation of approval cadence tracking |
| §0.5.5 Undo/recover pattern | 5-minute undo window, reverse action log | `UndoAction` model exists, but no undo execution logic found |
| §0.5.6 UX quality metrics | Decision time, taps per batch, decline rate etc. | No metrics collection |
| §2.5 Connection framework lifecycle | Connections as skills, health checks, token refresh | `ConnectionManager` protocol exists, no concrete lifecycle implementation found in code |
| §4.2.3 MemoryPortability | export_bundle/import_bundle | Protocol defined, zero implementation |
| §5.7 Two-tier eviction (scorer model) | Heuristic + scorer model eviction | Heuristic tier exists, scorer model tier not implemented |
| §9 Docker sandbox backend | Optional Docker isolation | No Docker integration in codebase |
| §10.4/10.4.1 Skill import/adaptation | External skill normalization, transformation report | No implementation |
| §5.10.1 Secure-input endpoint | `POST /secrets/{ref_id}` bypassing WebSocket | `_SecretPayload` model in web.py suggests partial, but full flow unconfirmed |
| Guardrails-AI integration | `guardrails-ai` as gate provider | Only referenced in `models/gates.py` as a `GateProvider` enum value; no actual guardrails-ai library integration |
| Planner research state machine | §4.8: planning→awaiting_research→ready_to_finalize→expired | Not implemented |
| Autonomy threshold proposals | §5.1.6 | Model + protocol exists, no calibration logic |

---

## 5. Undocumented Code

| Code | Description | Spec Coverage |
|------|-------------|---------------|
| `silas/manual_harness.py` (602 lines) | Manual testing harness | Not in any spec |
| `silas/stubs.py` | In-memory stubs for testing | Not spec'd (reasonable) |
| `silas/core/plan_executor.py` | Plan action execution, skill work item building | Implements spec logic but file not mentioned in project structure |
| `silas/core/approval_flow.py` | Approval flow management | Not in project structure spec |
| `silas/queue/bridge.py` | Queue-to-Stream integration bridge | Not in agent-loop-architecture spec |
| `silas/queue/status_router.py` | Status event routing to surfaces | Implements §6.3 but file not in spec project structure |
| `silas/queue/consult.py` | Consult-planner manager | Implements spec concept but file not in project structure |
| `silas/gates/output.py` | OutputGateRunner | Separate from SilasGateRunner — spec says one runner for both |
| `silas/proactivity/ux_metrics.py` | UX metrics collection stubs | Exists but likely empty/minimal |
| `silas/tools/backends.py` | Tool backend abstractions | Not spec'd |

---

## 6. Test Coverage Gaps

**Overall: ~690 tests, most test happy paths with some edge cases.**

| Area | Test File | Gap |
|------|-----------|-----|
| Stream turn pipeline | `test_stream.py` (855 lines) | Good coverage of main flow but steps 9, 10, 11.5 are no-ops so untestable. No tests for concurrent multi-connection turns |
| Queue consumers | `test_queue_consumers.py` (715 lines) | Tests consumer dispatch but not the full proxy→planner→executor→proxy loop end-to-end through queues |
| QueueBridge | `test_integration_queue.py` (379 lines) | Tests dispatch/collect but not concurrent trace isolation or the nack-storm problem |
| Taint propagation | — | **Zero tests** because feature doesn't exist |
| Memory portability | — | **Zero tests** because feature doesn't exist |
| Docker sandbox | — | **Zero tests** because feature doesn't exist |
| Undo flow | — | **Zero tests** beyond model validation |
| Research state machine | — | **Zero tests** because feature doesn't exist |
| Replan cascade (wired) | `test_queue_consumers.py` | ReplanManager unit tested but not integration-tested in execution failure path |
| Output gates | `test_output_gates.py` | Tests OutputGateRunner but not the two-lane policy/quality model |
| Security edge cases | `test_security.py`, `test_security_fixes.py` | Good but no test for: expired nonce pruning under load, concurrent approval token races, taint escalation via memory injection |
| Sandbox escape | `test_sandbox.py` | Tests resource limits and network isolation but no adversarial escape tests (symlinks, path traversal, signal abuse) |

---

## 7. Architecture Drift

### 7.1 Queue Integration is Overlay, Not Core

ARCHITECTURE.md describes three agents connected by a message bus as the fundamental architecture. But the actual execution path in `Stream._process_turn_with_active_context()` is a direct procedural pipeline: proxy → planner → executor, all via `run_structured_agent()`. The queue path only activates when `queue_bridge is not None`, and even then, `collect_response` uses a fragile poll-and-nack pattern.

**The code is a monolithic turn processor with a queue system bolted on the side.** STATUS.md marking WI-1 through WI-4 as "Done" is misleading — the queue *components* exist but the *integration* into the main execution path is incomplete.

### 7.2 Two Output Gate Implementations

The spec describes one `GateRunner` for both input and output gates. The code has:
- `SilasGateRunner` — used for input gates (two-lane, policy/quality)
- `OutputGateRunner` — separate class in `silas/gates/output.py` used for output gates

This means output gates may not follow the same two-lane evaluation model as input gates.

### 7.3 Ed25519 Library Mismatch

Spec §2 lists `pynacl` (libsodium) for Ed25519. Code uses `cryptography` library (`cryptography.hazmat.primitives.asymmetric.ed25519`). Both are installed (`pynacl` in venv, `cryptography` in pyproject.toml). The actual signing code uses `cryptography`, not `pynacl`.

### 7.4 QueueMessage Schema Divergence

The spec's `QueueMessage` (dataclass in agent-loop-architecture.md §2.1) has 20+ fields including `scope_id`, `taint`, `task_id`, `work_item`, `approval_token`, `artifacts`, `constraints`, `urgency`. The code's `QueueMessage` (Pydantic model in queue/types.py) has 10 fields with a generic `payload: dict[str, object]`. Critical metadata like `scope_id` and `taint` are not first-class fields.

### 7.5 Stream Self-Signs Messages

The spec (§5.1 step 2) envisions inbound messages being signed by the *client* (owner's Ed25519 key) and the Stream *verifying* that signature. Instead, `Stream._sign_inbound_message()` signs the message *itself* with its own key and then verifies its own signature. This makes the inbound signing step a no-op for trust — it will always pass because the Stream is signing and verifying with the same key.

### 7.6 Memory Operations Are No-Ops

Steps 9 (memory queries) and 10 (memory ops) in the turn pipeline are logged as `phase1a_noop`. The agent's `AgentResponse` includes `memory_queries` and `memory_ops` fields, but the Stream never processes them after receiving the response. This means the agent cannot request memory retrieval or persist memories through the designed mechanism.

---

## 8. Prioritized Action Items

### Critical (Security/Correctness)

| # | Item | Risk | Effort |
|---|------|------|--------|
| 1 | **Implement taint propagation** (INV-05) | External data can be stored as `owner`-tainted in memory, enabling prompt injection persistence | High — needs a TaintTracker threaded through tool calls |
| 2 | **Fix inbound message signing** | Stream self-signs then self-verifies, making trust classification meaningless for non-WebSocket channels | Medium |
| 3 | **Implement memory steps 9-10** | Agent's memory queries and ops are silently dropped every turn | Medium |
| 4 | **Fix QueueBridge.collect_response** | Poll-and-nack pattern is O(n) and causes message reordering under concurrent traces | Medium |

### High (Functionality)

| # | Item | Risk | Effort |
|---|------|------|--------|
| 5 | **Wire queue consumers into main execution path** | The "autonomous runtime" claimed in STATUS.md doesn't actually run through queues | High |
| 6 | **Implement scorer model (tier-2 eviction)** | Context eviction is heuristic-only; long conversations will lose important context | Medium |
| 7 | **Unify output gate runner** | Two gate runner implementations may enforce different policies | Low |
| 8 | **Implement research state machine** | Planner can't delegate research to executor through the queue | High |
| 9 | **Wire consult-planner and replan cascade** | Self-healing cascade (Design Principle #8) exists as code but isn't connected | Medium |

### Medium (Completeness)

| # | Item | Risk | Effort |
|---|------|------|--------|
| 10 | **Align QueueMessage schema with spec** | Missing `scope_id`, `taint` causes lost context across queue hops | Medium |
| 11 | **Implement MemoryPortability** | No way to export/import agent memory | Medium |
| 12 | **Implement skill hash-bound versioning** (INV-06) | Installed skills can be modified without detection | Low |
| 13 | **Add Docker sandbox backend** | Only subprocess sandbox; no container isolation option | Medium |
| 14 | **Update STATUS.md to reflect reality** | Claims WI-1–WI-4 "Done" when integration is incomplete | Trivial |

### Low (Polish)

| # | Item | Risk | Effort |
|---|------|------|--------|
| 15 | Implement UX quality metrics | No observability into approval patterns | Low |
| 16 | Implement undo/recover pattern | No undo for reversible actions | Medium |
| 17 | Guardrails-AI gate provider | Only predicate and script gate providers exist | Medium |
| 18 | Telegram/CLI channels | Only web channel implemented | Medium |
| 19 | Approval fatigue mitigation | No cadence tracking | Low |
| 20 | Connection lifecycle management | Connections as skills not implemented | High |

---

## Appendix: Test Run Verification

```
Total test files: 57
Total tests: ~690
Lint status: 0 errors (ruff strict)
Code lines: ~20,304 (silas/)
Test lines: ~13,948 (tests/)
Test:code ratio: 0.69:1
```

STATUS.md claims are mostly directionally correct for *component existence* but overstate *integration completeness*. The queue system components work individually (well-tested), but the end-to-end autonomous loop through queues is not the production path.
