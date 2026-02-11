# AGENTS.md — Codex Instructions

## Project: Silas Runtime — Phase 1a (Bare Skeleton)

You are building Phase 1a of the Silas AI agent runtime. Read `specs.md` and `PLAN.md` for the full specification.

## What to Build (Phase 1a ONLY)

### 1. Project Bootstrap
- `pyproject.toml` with uv, all Phase 1 deps (pydantic, pydantic-ai-slim[openrouter,logfire], pydantic-settings, pyyaml, click, httpx, fastapi, uvicorn[standard], pynacl, keyring, pytest, pytest-asyncio, ruff)
- Package layout: `silas/` with `__init__.py`
- CI: `.github/workflows/ci.yml` (ruff lint + pytest)

### 2. Pydantic Models (skeleton path — `silas/models/`)
Implement these models exactly per spec Section 3:
- `messages.py`: TaintLevel, ChannelMessage, SignedMessage
- `agents.py`: MemoryOpType, MemoryOp, MemoryQueryStrategy, MemoryQuery, InteractionRegister, InteractionMode, PlanActionType, PlanAction, AgentResponse (with model_validator for memory_queries <= 3), RouteDecision (with validators)
- `memory.py`: MemoryType (enum: episode, fact, preference, skill, entity, profile), MemoryItem
- `context.py`: ContextZone, ContextProfile (with validators for pct ranges and sum <= 0.80), ContextItem, ContextSubscription, TokenBudget
- `work.py`: WorkItemType, WorkItemStatus, Budget, BudgetUsed (with exceeds() using >= and merge()), Expectation (with mutual exclusivity validator), VerificationCheck, EscalationAction, WorkItem, WorkItemResult
- `gates.py`: GateType, GateLane, GateProvider, GateTrigger, Gate, AccessLevel, GateResult
- `approval.py`: ApprovalScope, ApprovalVerdict, ApprovalDecision, Base64Bytes (annotated type), ApprovalToken
- `sessions.py`: Session, SessionType

### 3. Protocol Definitions (`silas/protocols/`)
Define all Protocol classes per spec Section 4. For Phase 1a, these are just interface definitions:
- `channels.py`: ChannelAdapterCore, RichCardChannel
- `agents.py`: (no protocol needed — PydanticAI agents are concrete)
- `memory.py`: MemoryStore, MemoryRetriever, MemoryConsolidator, MemoryPortability
- `context.py`: ContextManager
- `approval.py`: ApprovalVerifier, NonceStore
- `execution.py`: EphemeralExecutor, SandboxManager
- `gates.py`: GateCheckProvider, GateRunner
- `work.py`: WorkItemExecutor, VerificationRunner, WorkItemStore
- `scheduler.py`: TaskScheduler
- `audit.py`: AuditLog
- `personality.py`: PersonalityEngine, PersonaStore
- `proactivity.py`: SuggestionEngine, AutonomyCalibrator
- `skills.py`: SkillLoader, SkillResolver

### 4. Basic WebChannel (`silas/channels/web.py`)
- FastAPI app with a single WebSocket endpoint at `/ws`
- Implements `ChannelAdapterCore` protocol
- Serves static files from `web/` directory
- Single-scope (no multi-connection yet)

### 5. Minimal Chat UI (`web/`)
- `index.html` — simple chat interface
- `app.js` — WebSocket connection, send/receive messages
- `style.css` — clean, dark theme

### 6. HeuristicTokenCounter (`silas/core/token_counter.py`)
- `count(text: str) -> int` = `ceil(len(text) / 3.5)`

### 7. TurnContext (`silas/core/turn_context.py`)
- Dependency container dataclass holding all per-turn dependencies
- For Phase 1a, most fields are Optional/None

### 8. Stream stub (`silas/core/stream.py`)
- `_process_turn()` that takes a message, calls the Proxy agent, returns response
- Steps 2-4 and 7 (routing only) and 13 from spec. Everything else = no-op stub with audit log comment.
- No persistence, no gates, no approval — just route through Proxy and respond

### 9. PydanticAI Proxy Agent (`silas/agents/proxy.py`)
- Agent with `RouteDecision` output type
- System prompt from `silas/agents/prompts/proxy_system.md`
- For Phase 1a: always routes "direct" (no planner yet)

### 10. AgentResponse parsing from structured output
- `run_structured_agent` wrapper per spec Section 5.1.0

### 11. YAML Config (`silas/config.py` + `config/silas.yaml`)
- Pydantic Settings model matching spec Section 11
- Load from YAML with env var overrides
- For Phase 1a: only load what's needed (models, channels.web, context basics, owner_id, data_dir)

### 12. CLI (`silas/main.py`)
- `silas init` — create data dir, create DB placeholder
- `silas start` — load config, wire deps, start Stream + web server

### 13. Test Infrastructure
- `tests/conftest.py` with shared fixtures
- `tests/fakes.py` with `TestModel`, `FakeTokenCounter`, in-memory store stubs
- At least one test per: models validation, token counter, stream turn processing

## Rules
- Follow the spec precisely — field names, types, enums, validators must match
- Use `datetime.now(timezone.utc)` everywhere, NEVER `datetime.utcnow()`
- All datetime fields must be timezone-aware
- Type everything properly — no `Any` unless truly needed
- Keep imports clean, use `__all__` exports
- Run `ruff check` and `pytest` before considering yourself done
- Commit with conventional commits (`feat:`, `fix:`, `chore:`)

## What NOT to Build
- No SQLite persistence (that's Phase 1b)
- No memory store implementation (Phase 1b)
- No gates/approval/verification (Phase 3+)
- No personality engine (Phase 5)
- No Telegram/CLI channels (Phase 8)
- No Docker sandbox (Phase 8)

When completely finished, run this command to notify me:
openclaw gateway wake --text "Done: Phase 1a bare skeleton built — models, protocols, WebChannel, Proxy agent, Stream stub, CLI, tests" --mode now
