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
