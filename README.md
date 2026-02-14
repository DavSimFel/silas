# Silas

AI agent runtime with structured execution, approval gates, and persistent memory.

## What It Does

Silas is the brain behind an always-on AI assistant. It handles:

- **Goal-driven execution** — break complex objectives into tasks, execute in sandboxes, verify results
- **Approval gates** — policy and quality checks before and after actions (LLM + script-based)
- **Persistent memory** — SQLite-backed episodic memory with keyword/entity recall and consolidation
- **Context management** — zone-based context windowing with token budgets and scorer-driven eviction
- **Skill system** — Agent Skills standard (agentskills.io) with resolver, loader, and toolset wrappers
- **Personality engine** — axis-based personality with mood tracking and voice adaptation
- **Proactive suggestions** — idle-time and post-execution suggestion generation with fatigue tracking

## Architecture

```
Stream (persistent main loop)
  → Proxy Agent (routes input to responder/planner)
    → Planner Agent (breaks goals into tasks)
      → Executor Agent (produces tool-call plans)
        → Sandbox Manager (isolated subprocess execution)
          → Verification Runner (checks results against expectations)
            → Gate Runner (approve/block based on policy + quality)
```

## Project Structure

```
silas/
├── agents/          # Proxy, planner, executor agents
├── approval/        # Approval token management
├── audit/           # SQLite audit logging
├── channels/        # WebSocket + HTTP channel (FastAPI)
├── connections/     # External service connection manager
├── core/            # Stream, context manager, plan parser, key manager
├── execution/       # Sandbox, shell, Python executors
├── gates/           # Output gates, LLM checker, script checker
├── goals/           # Goal lifecycle manager
├── memory/          # SQLite memory store + consolidator
├── models/          # Pydantic models (all domain objects)
├── persistence/     # SQLite stores (chronicle, connections, work items)
├── personality/     # Personality engine + axis profiles
├── proactivity/     # Suggestions, autonomy calibrator, UX metrics
├── protocols/       # Typed Protocol interfaces
├── scheduler/       # APScheduler wrapper for cron + heartbeats
├── skills/          # Skill loader, resolver, validator, importer
├── tools/           # Toolset wrappers (filtered, prepared, approval-required)
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

**Phase**: Pre-migration (core runtime built, integration testing next)
**Tests**: 504 passed, 1 xfailed
**Lint**: 0 errors (strict mode)

## License

Private — not open source.
