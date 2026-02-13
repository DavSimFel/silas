# Contributing to Silas

Thanks for your interest in contributing! Silas is an AI agent runtime with structured execution, approval gates, and persistent memory.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for package management

### Setup

```bash
git clone https://github.com/DavSimFel/silas.git
cd silas
git checkout dev
uv sync --dev
```

### Run Tests

```bash
uv run pytest tests/ -v --tb=short
```

### Lint & Type Check

```bash
uv run ruff check silas/ tests/
uv run pyright silas/
```

All three must pass before submitting a PR.

## Development Workflow

### Branch Strategy

- **`main`** — stable releases only. Requires 1 approving review.
- **`dev`** — integration branch. All feature work targets `dev`.
- **Feature branches** — `feat/*`, `fix/*`, `chore/*`, `refactor/*` off `dev`.

### Pull Requests

1. Fork the repo and create a feature branch from `dev`
2. Make your changes with tests
3. Ensure CI is green (lint + types + tests)
4. Open a PR targeting `dev`
5. PRs to `main` are opened by maintainers only

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Telegram channel adapter
fix: timezone collision detection in slot allocator
chore: update dependencies
refactor: split stream.py into focused modules
test: add concurrent turn isolation tests
docs: update architecture overview
```

## Code Standards

- **Type hints on everything** — all function signatures, all variables where not obvious
- **Docstrings on all public methods** — explain *why*, not just *what*
- **Inline comments for non-obvious logic** — if you had to think about it, comment it
- **No bare `except:`** — always catch specific exceptions
- **No `print()`** — use `logging.getLogger(__name__)`
- **Timezone-aware datetimes** — `datetime.now(datetime.UTC)`, never naive
- **Cyclomatic complexity < 12** — extract helpers if needed

Enforced rules: `E, F, I, B, N, C901, UP, SIM, RUF, PT, S, DTZ, PIE, T20`

## Architecture

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) for an overview of how the system fits together. The full specification lives in [`specs.md`](specs.md).

Key directories:

| Directory | Purpose |
|-----------|---------|
| `silas/agents/` | Proxy, planner, executor agents (PydanticAI) |
| `silas/approval/` | Ed25519 approval token system |
| `silas/core/` | Stream (main loop), context manager, plan parser |
| `silas/execution/` | Sandbox backends, shell/Python executors |
| `silas/gates/` | Policy + quality gate providers |
| `silas/memory/` | SQLite memory store + FTS5 retrieval |
| `silas/models/` | All Pydantic domain models |
| `silas/protocols/` | Typed Protocol interfaces |
| `silas/skills/` | Skill loader, resolver, validator |
| `tests/` | Unit + integration tests |

## What to Work On

Check the [issues](https://github.com/DavSimFel/silas/issues) for open tasks. Issues labeled `good first issue` are a good starting point.

If you want to work on something not yet filed, open an issue first to discuss the approach.

## Spec Compliance

Silas is built against a detailed specification (`specs.md`). When implementing features:

1. Read the relevant spec section first
2. Check `STATUS.md` for current implementation state
3. Follow the spec's contracts — don't improvise interfaces
4. If the spec is wrong or ambiguous, open an issue to discuss before diverging

## Security

If you find a security vulnerability, **do not open a public issue**. Email [david@feldhofer.cc](mailto:david@feldhofer.cc) instead.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
