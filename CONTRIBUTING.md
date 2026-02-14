# Contributing to Silas

## Reading Order

Before diving into the code, read the docs in this order:

1. **[README.md](README.md)** — what Silas does, project structure, setup
2. **[ARCHITECTURE.md](ARCHITECTURE.md)** — how the three-agent system works
3. **[specs/README.md](specs/README.md)** — index of all specification documents

## Dev Setup

See the [README](README.md#development) for prerequisites and setup instructions. In short:

```bash
uv sync --group dev
```

## Running Tests

```bash
uv sync --group dev && uv run pytest
```

Linting and type checking:

```bash
uv run ruff check silas tests    # lint (strict mode)
uv run ruff format --check .     # format check
```

## Git Workflow

1. **Never commit directly to `main` or `dev`** — always use feature branches
2. Branch naming: `feat/*`, `fix/*`, `chore/*`, `refactor/*`, `docs/*`
3. **Every change goes through a PR** — even small ones
4. `feature → dev`: CI must be green before merge
5. `dev → main`: requires human approval on GitHub
6. Delete feature branches after merge
7. Tag releases on `main` with SemVer

## Code Standards

| Tool | Purpose |
|------|---------|
| **ruff** | Linting + formatting (strict config in `pyproject.toml`) |
| **pyright** | Static type checking |
| **pytest** | Test runner |

Key ruff rules enforced: C901 (complexity), UP (modern Python), SIM (simplification), S (security/bandit), DTZ (timezone-aware datetimes), PT (pytest best practices), T20 (no stray `print()`).

## Where Specs Live

The `specs/` directory contains the authoritative behavioral specifications — contracts between design and implementation. The main spec document is `specs.md` in the repo root.

- **`specs.md`** — the normative implementation core (all numbered sections)
- **`specs/`** — companion documents split out for navigability
- **`specs/reference/`** — lookup-only reference material (models, protocols, examples, security model)

Specs define *what the system must do*. Code implements the spec. When they diverge, the spec wins (and should be updated if the divergence is intentional).
