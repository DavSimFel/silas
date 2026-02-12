# Implementation Status

Last updated: 2026-02-12 (PR #54)

## ✅ Implemented & Spec-Compliant

### Protocols (18/22)

| Protocol | Implementation | Spec |
|----------|---------------|------|
| ChannelAdapterCore | `WebChannel` | §4.1 |
| RichCardChannel | `WebChannel` (12 methods) | §4.1.1 |
| MemoryStore | `SQLiteMemoryStore` | §4.2 |
| MemoryRetriever | `SilasMemoryRetriever` | §4.2.1 |
| MemoryConsolidator | `SilasMemoryConsolidator` | §4.2.2 |
| ContextManager | `LiveContextManager` | §4.3 |
| ApprovalVerifier | `SilasApprovalVerifier` (Ed25519) | §4.4 |
| NonceStore | `SQLiteNonceStore` | §4.5 |
| GateRunner | `SilasGateRunner` | §4.9 |
| GateCheckProvider | `PredicateChecker`, `ScriptChecker`, `LLMChecker` | §4.8 |
| VerificationRunner | `SilasVerificationRunner` | §4.10 |
| AccessController | `SilasAccessController` | §4.11 |
| WorkItemExecutor | `LiveWorkItemExecutor` | §4.12 |
| WorkItemStore | `SQLiteWorkItemStore` | §4.13 |
| ChronicleStore | `SQLiteChronicleStore` | §4.14 |
| PlanParser | `MarkdownPlanParser` | §4.15 |
| AuditLog | `SQLiteAuditLog` | §4.16 |
| PersonalityEngine | `SilasPersonalityEngine` | §4.17 |
| PersonaStore | `SQLitePersonaStore` | §4.18 |
| SkillLoader | `SilasSkillLoader` | §4.20 |
| SkillResolver | `LiveSkillResolver` | §4.21 |
| SuggestionEngine | `SimpleSuggestionEngine` | §4.22 |
| AutonomyCalibrator | `SimpleAutonomyCalibrator` | §4.23 |
| TaskScheduler | `SilasScheduler` (APScheduler) | §4.x |

### Security & Keys

| Component | Status | Spec |
|-----------|--------|------|
| Secret isolation (§0.5) | ✅ Tier 1 (keyring/encrypted file) + Tier 2 (passphrase) | §0.5 |
| Ed25519 approval signing | ✅ Issue, verify, check with nonce replay protection | §4.4 |
| Secure input endpoint | ✅ `POST /secrets/{ref_id}` bypasses WebSocket | §0.5.3 |
| `data_dir` wired from settings | ✅ | §0.5 |

### Models

All pydantic models match spec field constraints:
- `AgentResponse`: `len(memory_queries) <= 3` ✅
- `RouteDecision`: `context_profile` non-empty + in registry; `direct` requires `response` ✅
- `Expectation`: exactly one predicate field ✅
- `ContextProfile`: each pct 0-1, sum ≤ 0.80 ✅
- `BudgetUsed.exceeds()`: uses `>=` ✅
- `MemoryOp`: op-specific required fields ✅

### Frontend (PWA)

| Component | Status |
|-----------|--------|
| WebSocket stream | ✅ |
| Card request-response protocol | ✅ |
| Onboarding overlay + CSS | ✅ |
| `initOnboarding()` wired | ✅ |
| Service worker | ✅ |
| Manifest (installable) | ✅ |

---

## ❌ Not Implemented (speced, deferred)

These are explicitly deferred — either by spec roadmap or by design decision.

| Feature | Spec | Reason |
|---------|------|--------|
| **GuardrailsAI gate provider** | §3.4 | Enum value exists, no checker implementation. Other providers (predicate, script, LLM) cover all current use cases. |
| **MemoryPortability** | §4.2.3 | `export_bundle`/`import_bundle` — not needed until multi-instance deployment. |
| **Slide-to-confirm UX** | §15 | Spec defines interaction ladder (tap → slide → biometric). Currently tap-only. |
| **WebAuthn / biometric** | R1 roadmap | Passkey enrollment + fingerprint approval. Post-migration. |
| **Benchmarking framework** | §19-20 | Eviction calibration data collection. Spec-only until after migration. |
| **EphemeralExecutor Docker backend** | §4.7 | Subprocess backend works. Docker is optional enhancement. |
| **TelegramChannel** | §4.1 | Placeholder exists. WebChannel is primary. |
| **CLIChannel** | §4.1 | Dev/debug only. Not prioritized. |
| **Evals (Pydantic Evals)** | §14 | Routing, planning, memory recall evals. Post-migration. |
| **Dynamic skill context injection** | ADR-020 | `{{script}}` expansion disabled in this version per spec. |
| **Multi-instance memory sync** | — | Not speced. Future architecture. |
| **Connection auto-discovery** | §4.19 | `ConnectionManager.discover_connection` exists but no connection skills ship yet. |

---

## 📊 Test Coverage

- **Total tests:** ~670+
- **Lint:** 0 errors (ruff, 10 rule categories, C901 max=12)
- **Type ignores:** 0
- **Bare `except Exception`:** 3 remaining (all with logging)

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
