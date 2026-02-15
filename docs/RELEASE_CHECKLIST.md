# Release Checklist — dev → main

Use this checklist before every `dev → main` merge. All sections must pass.

---

## 1. CI Green

- [ ] Latest commit on `dev` has passing CI (`gh run list --branch dev --limit 1`)
- [ ] No open PRs targeting `dev` with failing checks
- [ ] All stale feature branches deleted

## 2. Test Suite

- [ ] `pytest` passes locally with 0 failures
- [ ] Coverage threshold met (configured in CI)
- [ ] No skipped tests without documented reason
- [ ] Inspect AI harness runs clean (if available): `python -m silas inspect-harness --dry-run`

## 3. Lint & Type Checks

- [ ] `ruff check .` — zero errors
- [ ] `ruff format --check .` — zero diffs
- [ ] `pyright` — zero errors (or only pre-existing baseline)

## 4. Security Review

- [ ] No secrets in code (`grep -r "sk-" --include="*.py" src/`)
- [ ] No `# type: ignore` without justification
- [ ] No bare `except:` blocks
- [ ] Auth token required when binding 0.0.0.0
- [ ] Ed25519 signing intact (approval tokens, inbound trust)

## 5. Documentation

- [ ] `ARCHITECTURE.md` reflects current module structure
- [ ] `README.md` install/run instructions work on clean checkout
- [ ] `CONTRIBUTING.md` up to date
- [ ] Changelog entry drafted (what changed since last release)

## 6. Smoke Test

- [ ] App starts: `python -m silas` boots without errors
- [ ] Web channel responds on configured port
- [ ] At least one turn completes (CLI or web)
- [ ] Observability endpoints live: `/metrics`, Loki receiving logs, Tempo receiving traces

## 7. Release Process

- [ ] Create PR: `dev → main` with changelog as description
- [ ] David approves on GitHub
- [ ] Merge (no squash — preserve history)
- [ ] Tag release: `git tag v0.X.Y && git push --tags`
- [ ] Verify CI passes on `main`

---

## Version-Specific Additions

For each release, add version-specific smoke tests below based on the changelog.
Features introduced since last release should each have at least one manual verification step.

### v0.1.0 (Initial Release)

- [ ] Topics system loads and matches triggers
- [ ] Queue path processes a turn end-to-end
- [ ] Approval gate blocks and resumes correctly
- [ ] Skills load from `shipped/` directory
- [ ] OTel traces appear in Tempo
- [ ] Prometheus scrapes `/metrics` successfully
- [ ] Grafana dashboards render (Silas Health, LLM Economics)
