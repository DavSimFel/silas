# Silas

AI agent runtime with structured execution, approval gates, and persistent memory.

## What It Does

Silas is the brain behind an always-on AI assistant. It handles:

- **Three-agent architecture** — Proxy (fast routing), Planner (strategy), Executor (sandboxed work) connected by a durable message bus
- **Topics system** — AGENTS.md-pattern context containers with hard triggers (webhook events) and soft triggers (keyword matching) for autonomous activation
- **Queue-based orchestration** — SQLite-backed durable queues with lease-based delivery, dead-letter handling, and configurable timeouts
- **Approval gates** — Ed25519-signed tokens + policy/quality checks before and after actions (LLM + script-based)
- **Persistent memory** — SQLite-backed episodic memory with keyword/entity recall and consolidation
- **Context management** — zone-based context windowing with token budgets and scorer-driven eviction
- **Skill system** — Agent Skills standard (agentskills.io) with resolver, loader, and toolset wrappers
- **Personality engine** — axis-based personality with mood tracking and voice adaptation

## Architecture

```
Stream (persistent main loop)
  → Proxy Agent (routes input, activates Topics by soft triggers)
    → Planner Agent (decomposes into plans, researches via executor)
      → Executor Agent (sandboxed tool execution with approval tokens)
        → Gate Runner (approve/block based on policy + quality)

Topics (activated context containers)
  → Hard triggers (webhook events: GitHub, n8n, cron)
  → Soft triggers (keyword/entity matching in conversation)
  → Procedural memory (instructions, plans, state per topic)
```

## Project Structure

```
silas/
├── agents/          # Proxy, planner, executor agents
├── approval/        # Ed25519 approval token management
├── audit/           # SQLite audit logging
├── channels/        # WebSocket + HTTP channel (FastAPI)
├── connections/     # External service connection manager
├── context/         # Zone-based context windowing
├── core/            # Stream (split into modules), context manager, key manager
├── execution/       # Sandbox, shell, Python executors
├── gates/           # Output gates, LLM checker, script checker
├── goals/           # Goal lifecycle manager
├── memory/          # SQLite memory store + consolidator
├── models/          # Pydantic models (all domain objects)
├── persistence/     # SQLite stores (chronicle, connections, work items)
├── personality/     # Personality engine + axis profiles
├── proactivity/     # Suggestions, autonomy calibrator, UX metrics
├── protocols/       # Typed Protocol interfaces
├── queue/           # Durable SQLite message bus (store, router, bridge, types)
├── scheduler/       # APScheduler wrapper for cron + heartbeats
├── security/        # Security guards and access control
├── skills/          # Skill loader, resolver, validator, importer
├── tools/           # Toolset wrappers (filtered, prepared, approval-required)
├── topics/          # Topics system (model, parser, registry, trigger matcher)
└── work/            # Work item executor + batch processor
```

## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv sync
```

### Tests

```bash
uv run pytest           # 504 tests
uv run ruff check silas tests   # lint (strict mode)
```

### Lint Rules

Strict ruff configuration with:
- **C901** — cyclomatic complexity max 12
- **UP** — modern Python patterns (pyupgrade)
- **SIM** — code simplification
- **S** — security checks (bandit)
- **DTZ** — timezone-aware datetimes enforced
- **PT** — pytest best practices
- **T20** — no stray print() statements

### Git Workflow

1. Never commit directly to `main` or `dev`
2. Feature branches: `feat/*`, `fix/*`, `chore/*`, `refactor/*`
3. All changes go through PRs
4. `feature → dev`: CI must be green
5. `dev → main`: requires human approval
6. Delete feature branches after merge

## Understanding Silas

New here? Read in this order:

1. **This README** — overview and setup
2. **[ARCHITECTURE.md](ARCHITECTURE.md)** — how the three-agent system works
3. **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to contribute
4. **[specs/](specs/README.md)** — detailed behavioral specifications and reference material

## Status

**Phase**: Core runtime built — Topics Phase 1 merged, queue orchestration wired, observability deployed
**Tests**: 1,100+ passed, 0 failures
**Lint**: 0 ruff errors (strict mode), ~75 pyright errors remaining
**CI**: lint + 3 test shards in GitHub Actions

## License

Private — not open source.
