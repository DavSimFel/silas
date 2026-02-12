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

### Manual Acceptance Harness

For post-implementation validation against the spec, run the interactive harness:

```bash
uv run silas manual-harness --profile core
```

Run the full matrix (core + extended):

```bash
uv run silas manual-harness --profile full
```

Optional flags:

- `--base-url` to target a non-default web runtime endpoint
- `--output-dir` to store reports outside `reports/manual-harness`

Each run writes both JSON and Markdown reports with per-scenario pass/fail/skip outcomes.

---
