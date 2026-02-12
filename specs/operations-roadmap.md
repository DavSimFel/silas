## 17. Operations & Reliability

### 17.1 Error Handling Strategy

Silas uses a unified error taxonomy with deterministic handling:

- `E_CFG_*` (configuration/startup): fail fast, refuse start
- `E_LLM_*` (model API, parse, timeout): retry with bounded backoff, then degrade/fallback route
- `E_DB_*` (SQLite lock/corruption/query): retry transient locks, fail closed on corruption and mark service degraded
- `E_SANDBOX_*` (executor/verification sandbox): fail current work item as `blocked` or `failed`, never bypass verification
- `E_GATE_*` (provider failures): policy lane fails closed, quality lane fails open with audit flag
- `E_CHANNEL_*` (disconnect/timeouts): safe defaults (`decline`/`block`) for pending approvals and card decisions
- `E_APPROVAL_*` (signature/hash/nonce mismatch): hard deny with audit event

All raised errors must map to one taxonomy code, include correlation IDs (`turn_id`, `work_item_id`, `scope_id`), and emit structured audit + metrics events.

### 17.2 Graceful Shutdown

On `SIGTERM`/`SIGINT`:

1. Stop accepting new channel messages.
2. Close approval/card intake with safe declines for unresolved prompts.
3. Let in-flight turns finish up to `shutdown_grace_seconds` (default 30s); then cancel.
4. Persist in-memory state (`work_items`, scoped context cursors, scheduler state) and write audit checkpoint.
5. Drain and close WebSocket connections with close code 1001.
6. Stop scheduler and sandbox workers, then exit.

Crash recovery relies on rehydration from SQLite stores and idempotent approval/execution checks.

### 17.3 Deployment

Supported deployment targets:

- Local/systemd service (single binary + SQLite data dir)
- Docker Compose (app + optional reverse proxy)

Baseline requirements:

- TLS termination at reverse proxy for remote access
- periodic backups of `silas.db` + config + skills directories
- rolling upgrade path: run migrations, verify checksum table, restart with graceful drain
- explicit host/auth policy: remote bind requires auth token

### 17.4 Monitoring

Expose metrics and structured logs for:

- turn latency (p50/p95/p99), queue depth, active scopes
- gate actions (`continue`/`block`/`require_approval`) and quality flags
- approval outcomes and verification failures
- budget consumption (tokens/cost/time) per work item
- sandbox failures/timeouts by backend
- memory growth, chronicle retention, nonce prune counts
- suggestion acceptance/defer/dismiss rates and cooldown suppressions
- autonomy calibration stats (correction rates, widen/tighten proposals, applied deltas)

Alerts:

- sustained gate block spikes
- approval verification failures
- scorer circuit breaker open
- queue depth/backpressure thresholds exceeded
- autonomy proposal spikes or oscillation (repeated widen/tighten churn)
- DB migration/checksum failures

### 17.5 Rate Limiting

Required limits (configurable):

- inbound WebSocket messages per scope and per IP
- approval/card submissions per scope
- LLM calls per minute per scope + global cap
- web-search calls per minute per scope
- memory ops per turn and per minute
- gate evaluations per turn (hard cap)
- proactive suggestion cards per hour per scope
- autonomy-threshold proposals per week per scope

Limit violations return deterministic safe responses and are logged as security events.

### 17.6 Backpressure

Backpressure policy protects availability under load:

- per-scope bounded turn queue (`max_pending_turns_per_scope`)
- global bounded queue (`max_pending_turns_total`)
- if per-scope queue is full: reject newest message with `busy_retry` response
- if global queue is full: reject lowest-priority non-owner traffic first
- long-running background executions publish status without blocking foreground message handling

Queue pressure state is surfaced in `/health` and metrics for autoscaling/operator action.

## 18. Roadmap

Roadmap items intentionally excluded from current implementation scope:

- `R1`: Additional approval strength integrations (for example WebAuthn/passkey enrollment and policy mapping)
- `R2`: Exact token pre-counting integration path (SDK/provider-backed precise counters)
- `R3`: Side-session routing and dedicated side-session execution surface
- `R4`: Memory strategy expansion beyond current set (entity/causal graph traversal)
- `R5`: Context subscription semantic anchors and enhanced stale-detection semantics
- `R6`: Optional async gate lane for non-blocking telemetry-only checks
- `R7`: `WorkItemType.system` orchestration and broader autonomous proposal framework
- `R8`: Non-essential PWA offline queue/push orchestration enhancements
- `R9`: Gate model refactor (`Gate`) into discriminated unions for stricter schema ergonomics
- `R10`: Key-rotation lifecycle (active + next key, rollover window, audit policy)
- `R11`: Re-evaluate `AgentResponse.needs_approval`; remove if redundant after runtime override hardening
- `R12`: Personality simplification pass after production telemetry
- `R13`: Safety-gated dynamic skill context injection (`{{script}}`/command expansion) with read-only execution, strict output caps, taint enforcement, and full audit traces
- `R14`: Context eviction feedback loop — exclusion-based scorer calibration with ablation validation (§19)
- `R15`: Agent and skill benchmarking harness — eval suites, regression detection, ablation protocol (§20)
