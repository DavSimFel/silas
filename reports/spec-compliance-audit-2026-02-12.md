# Spec Compliance Audit — 2026-02-12

## Methodology
Compared every protocol method, model validator, and behavioral requirement in `specs/` against actual implementations in `silas/`.

---

## ❌ Non-Compliant

### 1. WebChannel — Missing 12 RichCardChannel methods
**Spec:** §4.1.1 requires `send_approval_request`, `send_gate_approval`, `send_checkpoint`, `send_batch_review`, `send_draft_review`, `send_decision`, `send_suggestion`, `send_autonomy_threshold_review`, `send_secure_input`, `send_connection_setup_step`, `send_permission_escalation`, `send_connection_failure`.
**Code:** Only `send`, `send_stream_*`, and `send_approval_card` exist. None of the 12 RichCardChannel methods are implemented.
**Impact:** All interactive card flows (approvals, gates, connections, suggestions) can't render in the PWA.

### 2. ApprovalVerifier — No `issue_token` implementation
**Spec:** §4.4 requires `issue_token(work_item, decision, scope) → ApprovalToken` with Ed25519 signing.
**Code:** `LiveApprovalManager` has `request_approval` and `resolve` but no `issue_token`. No Ed25519 signing anywhere. Approval tokens are created without cryptographic binding.
**Impact:** Approval tokens are not tamper-proof. Plan hash verification is not enforced.

### 3. MemoryRetriever — No implementation
**Spec:** §4.2.1 requires `retrieve(query, scope_id, session_id) → list[MemoryItem]`.
**Code:** Protocol defined, no implementing class exists. `SQLiteMemoryStore` has `search_keyword` and `search_session` but no unified retriever with strategy dispatch.
**Impact:** Agent `memory_queries` in `AgentResponse` have no execution path.

### 4. MemoryPortability — No implementation
**Spec:** §4.2.3 requires `export_bundle` and `import_bundle` for portable JSONL memory bundles.
**Code:** Protocol defined, no implementing class.
**Impact:** Memory cannot be exported or migrated between instances.

### 5. MemoryConsolidator — Wrong method name
**Spec:** §4.2.2 requires `run_once() → dict`.
**Code:** `SilasMemoryConsolidator` has `consolidate(scope_id)` — different name and signature.
**Impact:** Won't satisfy the protocol contract.

### 6. TaskScheduler — Missing `schedule` and `cancel`
**Spec:** §4.x requires `schedule` and `cancel` methods.
**Code:** `SilasScheduler` has `add_goal_schedule`, `add_cron_job`, `remove_schedule` — different names.
**Impact:** Protocol mismatch.

### 7. AutonomyCalibrator — Missing `apply` method
**Spec:** §4.23 requires `apply(proposal, decision) → dict`.
**Code:** `SimpleAutonomyCalibrator` has `record_outcome`, `evaluate`, `rollback`, `get_metrics` — no `apply`.
**Impact:** Approved threshold changes can't be applied.

### 8. GuardrailsAI gate provider — Enum only, no code
**Spec:** §3.4 defines `guardrails_ai` provider for Guardrails validator execution.
**Code:** Only `GateProvider.guardrails_ai = "guardrails_ai"` enum value. No check implementation.
**Impact:** Gate configs referencing `guardrails_ai` provider would crash at runtime.

### 9. `POST /secrets/{ref_id}` — Hardcoded `data_dir`
**Spec:** §0.5 — secret endpoint should use configured data directory.
**Code:** `data_dir = Path("./data")  # TODO: wire from settings`
**Impact:** Wrong secret store location if `data_dir` is configured differently.

### 10. Onboarding CSS — Missing
**Code:** `web/index.html` has onboarding HTML, `web/app.js` has `initOnboarding()`, but:
- No CSS for `.onboarding-overlay`, `.onboarding-card`, `.glass-overlay`, etc.
- `initOnboarding()` is never called from anywhere in app.js.
**Impact:** Onboarding overlay would be unstyled and invisible.

### 11. Model validators — Incomplete coverage
**Spec requires specific validators:**
- `AgentResponse`: `len(memory_queries) <= 3` ← needs check
- `MemoryOp`: op-specific required fields ← needs check
- `RouteDecision`: `context_profile` non-empty + in registry; `direct` requires `response` ← needs check
- `Expectation`: exactly one predicate field set ← needs check
- `ContextProfile`: each pct 0-1, sum ≤ 0.80 ← needs check
- `BudgetUsed.exceeds()`: must use `>=` not `>` ← needs check
- `ApprovalToken` canonical JSON signing ← not implemented (no Ed25519)

### 12. PWA onboarding — `initOnboarding()` never invoked
**Code:** Function defined in `app.js` but never called. The onboarding flow is dead code on the frontend.

---

## ✅ Compliant (18 protocols)

| Protocol | Implementation | Status |
|----------|---------------|--------|
| MemoryStore | SQLiteMemoryStore | ✅ |
| ContextManager | LiveContextManager | ✅ |
| NonceStore | SQLiteNonceStore | ✅ |
| GateRunner | SilasGateRunner | ✅ |
| GateCheckProvider | PredicateChecker, ScriptChecker, LLMChecker | ✅ |
| VerificationRunner | SilasVerificationRunner | ✅ |
| AccessController | SilasAccessController | ✅ |
| WorkItemExecutor | LiveWorkItemExecutor | ✅ |
| WorkItemStore | SQLiteWorkItemStore | ✅ |
| ChronicleStore | SQLiteChronicleStore | ✅ |
| PlanParser | MarkdownPlanParser | ✅ |
| AuditLog | SQLiteAuditLog | ✅ |
| PersonalityEngine | SilasPersonalityEngine | ✅ |
| PersonaStore | SQLitePersonaStore | ✅ |
| ConnectionManager | SilasConnectionManager | ✅ |
| SkillLoader | SilasSkillLoader | ✅ |
| SkillResolver | LiveSkillResolver | ✅ |
| SuggestionEngine | SimpleSuggestionEngine | ✅ |
| SecretStore | NEW (PR #49) | ✅ |

---

## Priority for Migration

**Must fix (blocks basic operation):**
1. WebChannel RichCardChannel methods (at minimum: `send_approval_request`, `send_secure_input`)
2. ApprovalVerifier `issue_token` + Ed25519 signing
3. MemoryRetriever implementation
4. PWA onboarding wiring (CSS + `initOnboarding()` call)

**Should fix (protocol compliance):**
5. MemoryConsolidator method rename
6. TaskScheduler method rename
7. AutonomyCalibrator `apply`
8. `/secrets/{ref_id}` data_dir from settings
9. Model validators audit

**Can defer:**
10. GuardrailsAI gate provider (other providers work)
11. MemoryPortability (not needed until multi-instance)
