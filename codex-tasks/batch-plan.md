# Codex Batch Plan — Runtime Gap Closures

## Round 1 (parallel)
### 1A: Stream Startup + Rehydration
- Stream startup sequence: `stream_started` audit, connection health/recovery, active-goal scheduling, heartbeat registration
- Rehydration: system-zone restore, subscription restore, rehydration system message, in-progress work resume, persona lazy load, pending review/suggestion/autonomy queue restore
- Files: `silas/core/stream.py`, tests

### 1B: Execution Wiring + Sandbox Hardening
- Wire ShellExecutor/PythonExecutor into WorkItemExecutor runtime flow
- Sandbox: network fail-closed, resource limits (memory, time, PID), verification command contract
- Files: `silas/work/executor.py`, `silas/execution/sandbox.py`, `silas/execution/shell.py`, `silas/execution/python_exec.py`, tests

## Round 2 (parallel)
### 2A: Message Trust + Signing Key Wiring
- Replace per-process HMAC with Ed25519 inbound verification + nonce freshness
- Inject Tier-2 signing key into Stream auth path from startup
- Files: `silas/core/stream.py`, `silas/secrets.py`, tests

### 2B: Gate Improvements
- Output gate escalation map (replace hardcoded "I cannot share that")
- Two-lane quality/policy gate flow on output path
- Gate provider parity: `file_valid` on PredicateChecker, `modified_context`/`check_expect`/`extract` on ScriptChecker
- Files: `silas/gates.py`, `silas/gates/providers/`, tests

## Round 3 (parallel)
### 3A: Connection Isolation + Interaction Mode
- Per-connection turn processors + lock map isolation
- Centralized `resolve_interaction_mode()` in turn pipeline
- ConnectionManager interactive setup + escalation card flow
- Files: `silas/core/stream.py`, `silas/core/connection_manager.py`, tests

### 3B: Proactivity + Scheduling
- Typed SuggestionProposal/AutonomyThresholdProposal models (replace dict/str)
- Heartbeat-driven suggestion/autonomy loops (decouple from turn)
- Real WebSearchExecutor (provider-backed, not mock skill)
- Files: `silas/proactivity/`, `silas/models/proactivity.py`, `silas/execution/web_search.py`, tests

## Round 4 (parallel)
### 4A: Secure Input + Proxy Fallback
- Secure-input pending-ref registry + `{"stored": true}` response + `secret_stored` audit event
- Proxy fallback: spec-compliant messaging/mode instead of `default_and_offer` echo
- Files: `silas/channels/web.py`, `silas/core/stream.py`, tests

### 4B: Operations Hardening
- Unified runtime error taxonomy wiring
- Graceful-drain shutdown path with safe card-resolution defaults
- Rate-limit/backpressure queue controls
- Files: `silas/core/stream.py`, `silas/core/operations.py` (new), tests
