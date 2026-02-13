# AGENTS.md — Coding Agent Instructions

## Project: Silas AI Runtime

Python 3.12+ project using `uv` for package management. Strict linting enforced.

## Before You Start

1. Read the relevant section of `specs.md` for what you're building
2. Check existing code in the target module — don't duplicate
3. Look at `silas/protocols/` for the interface you need to implement
4. Look at `silas/models/` for the data types you'll work with

## Code Standards

### Must Follow
- **Type hints on everything** — all function signatures, all variables where not obvious
- **Docstrings on all public methods** — explain *why*, not just *what*
- **Inline comments for non-obvious logic** — if you had to think about it, comment it
- **No bare `except`** — always catch specific exceptions (use `BLE001` noqa only when intentional)
- **No `print()`** — use `logging.getLogger(__name__)` instead
- **Timezone-aware datetimes** — always `datetime.now(datetime.UTC)`, never naive
- **Cyclomatic complexity < 12** — extract helpers if a function gets complex

### Lint Check (run before finishing)
```bash
uv run ruff check silas tests
```

Must pass with zero errors. Rules enforced: E, F, I, B, N, C901, UP, SIM, RUF, PT, S, DTZ, PIE, T20.

### Tests (run before finishing)
```bash
uv run pytest
```

All tests must pass. Write tests for new code in `tests/test_<module>.py`.

## Architecture Patterns

### Protocols over inheritance
All major components have a Protocol in `silas/protocols/`. Implement the protocol, don't subclass.

### Models are Pydantic
All data types are Pydantic BaseModels in `silas/models/`. Use `model_validate()` for parsing.

### Agents use pydantic-ai
LLM agents use `pydantic_ai.Agent` with structured output. Wrap calls in `run_structured_agent()` from `silas/agents/structured.py`. Always provide a deterministic fallback when LLM fails.

### Execution is sandboxed
Code execution goes through `silas/execution/sandbox.py`. Never run user code in the main process.

### Gates are fail-safe
Quality gates fail-open (degrade gracefully). Policy gates fail-closed (block on error). See `silas/gates/`.

## Key Files

| What | Where |
|------|-------|
| Full spec | `specs.md` |
| All protocols | `silas/protocols/` |
| All models | `silas/models/` |
| Test helpers/fakes | `tests/fakes.py` |
| Lint config | `pyproject.toml` [tool.ruff] |

## Git Rules

- Work on a feature branch (`feat/*`, `fix/*`, `refactor/*`)
- Commit messages: `feat:`, `fix:`, `chore:`, `refactor:` prefix
- Never commit to `main` or `dev` directly
- Never suppress lint errors — fix the code

## When Done

Run both lint and tests to confirm clean:
```bash
uv run ruff check silas tests && uv run pytest
```
