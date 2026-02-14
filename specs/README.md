# Specs

Authoritative behavioral specifications for the Silas runtime.

These files define **what the system must do** — contracts between design and implementation.

## Normative Documents

These define required behavior. Code must conform to them.

| File | Content |
|------|---------|
| [`../specs.md`](../specs.md) | **Primary spec** — all numbered sections, including the agent loop architecture (§19) |
| [`adrs.md`](adrs.md) | Architecture Decision Records — historical rationale for key design choices |
| [`design-language.md`](design-language.md) | UX design language and interaction patterns |
| [`benchmarking.md`](benchmarking.md) | Eviction calibration + benchmarking framework (§19-20) — **Future / not yet implemented** |
| [`operations-roadmap.md`](operations-roadmap.md) | Release phases, migration targets, and operational requirements |

## Reference Documents

Lookup-only material extracted from the main spec for navigability. Located in [`reference/`](reference/).

| File | Content |
|------|---------|
| [`reference/models.md`](reference/models.md) | Pydantic model schemas, field constraints, canonical formats (§3) |
| [`reference/protocols.md`](reference/protocols.md) | Protocol signatures + behavioral requirements (§4) |
| [`reference/examples.md`](reference/examples.md) | Example plans and configuration (§13) |
| [`reference/security-model.md`](reference/security-model.md) | Security model matrix and prohibited-capability list (§15) |

## What moved to source

- **Project structure** → deleted (use `find` or your IDE)
- **Testing strategy** → `tests/README.md` (lives with the tests)
- **Protocol behavioral docs** → being migrated to docstrings on `silas/protocols/*.py`
- **Model field docs** → being migrated to docstrings on `silas/models/*.py`
