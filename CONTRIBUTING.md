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

CI runs lint + 3 parallel test shards. Some tests require privileged Linux (namespaces) and are excluded from CI — run the full suite locally if needed.

Linting, formatting, and type checking:

```bash
uv run ruff check silas tests    # lint (strict mode)
uv run ruff format --check .     # format check
uv run pyright silas             # type checking (~75 errors, tracked)
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

## Key Architectural Concepts

- **Topics** — Activated context containers (not executed jobs). Hold triggers, instructions, plans, state. See [ARCHITECTURE.md](ARCHITECTURE.md#topics-system).
- **WorkItem ≠ Task** — WorkItem is an internal execution unit; Task is a Notion record. Never conflate them.
- **Approval tokens** — Ed25519-signed, consuming (one-use) or non-consuming (standing). Required before any executor action.
- **Toolset wrapper chain** — Raw Tool → SkillToolset → PreparedToolset → FilteredToolset → ApprovalRequiredToolset. Enforced in code, not prompts.

## CI Pipeline

CI runs on every PR to `dev`:

| Job | What |
|-----|------|
| **lint** | ruff check + ruff format --check |
| **test (1-3)** | 3 parallel pytest shards (pytest-split) |
| **pyright** | Type checking (continue-on-error) |

All checks must be green before merge (except pyright, which is advisory).
