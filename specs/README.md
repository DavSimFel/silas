# Specs

Authoritative behavioral specifications for the Silas runtime.

These files define **what the system must do** — contracts between design and implementation.
For **what is currently implemented**, see `STATUS.md` in the repo root.

## Files

| File | Content |
|------|---------|
| `adrs.md` | Architecture Decision Records — historical rationale |
| `security-model.md` | Cross-cutting security model (layers, enforcement, LLM boundaries) |
| `protocols.md` | Protocol signatures + behavioral requirements spanning multiple components |
| `models.md` | Pydantic model schemas, field constraints, canonical formats |
| `benchmarking.md` | Eviction calibration + benchmarking framework (§19-20, not yet implemented) |
| `operations-roadmap.md` | Release phases and migration targets |
| `examples.md` | Example plans and configuration |

## What moved to source

- **Project structure** → deleted (use `find` or your IDE)
- **Testing strategy** → `tests/README.md` (lives with the tests)
- **Protocol behavioral docs** → being migrated to docstrings on `silas/protocols/*.py`
- **Model field docs** → being migrated to docstrings on `silas/models/*.py`
