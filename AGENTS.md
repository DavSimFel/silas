# AGENTS.md — Codex Instructions

## Current Task: Phase 1b — Persistence

Read `specs.md` and `PLAN.md` for the full specification. You are building on top of Phase 1a (already complete).

## What to Build

### 1. SQLite Schema (`silas/memory/migrations/001_initial.sql`)
Per spec, create tables for:
- `memories` — with FTS5 virtual table for keyword search
- `chronicle` — keyed by `(scope_id, timestamp)`, configurable retention
- `work_items` — JSON-serialized complex fields, indexed on `id`, `status`, `parent`, `follow_up_of`
- `audit_log` — hash-chained entries
- `nonces` — domain-scoped replay protection with `recorded_at`

### 2. Migration Runner (`silas/persistence/migrations.py`)
- Sequential, idempotent migrations
- Checksums enforced on startup (SHA-256 of each .sql file)
- Stores applied migrations in a `_migrations` table
- Fails fast if a previously applied migration's checksum changed

### 3. SQLiteMemoryStore (`silas/memory/sqlite_store.py`)
Implements the `MemoryStore` protocol from `silas/protocols/memory.py`:
- CRUD operations (store, get, update, delete)
- FTS5 keyword search via `search_keyword`
- Session search via `search_session`
- Raw memory ingest via `store_raw` and `search_raw` (low_reingestion lane)
- Use `aiosqlite` for async access

### 4. SQLiteChronicleStore (`silas/persistence/chronicle_store.py`)
Implements the `ChronicleStore` protocol (define if not in protocols yet):
- `append(scope_id, item: ContextItem)` — persist chronicle entry
- `get_recent(scope_id, limit)` — load most recent N entries
- `prune_before(cutoff)` — delete entries older than retention cutoff
- Use `aiosqlite`

### 5. SQLiteWorkItemStore (`silas/persistence/work_item_store.py`)
Implements the `WorkItemStore` protocol from `silas/protocols/work.py`:
- `save(item)` — persist work item (JSON serialize complex fields)
- `get(work_item_id)` — load by ID
- `list_by_status(status)` — find by status
- `list_by_parent(parent_id)` — find children
- `update_status(work_item_id, status, budget_used)` — atomic update
- `approval_token` stored as full JSON (including Base64Bytes signature)
- Use `aiosqlite`

### 6. SQLiteAuditLog (`silas/audit/sqlite_audit.py`)
Implements the `AuditLog` protocol:
- Hash-chained entries (each entry includes SHA-256 of previous entry)
- `log(event, **data)` — append entry
- `verify_chain()` — verify full chain integrity
- `write_checkpoint()` — persist checkpoint hash
- `verify_from_checkpoint(checkpoint_id)` — verify from checkpoint to head
- Use `aiosqlite`

### 7. SQLiteNonceStore (`silas/persistence/nonce_store.py`)
Implements the `NonceStore` protocol:
- `is_used(domain, nonce)` — check if consumed
- `record(domain, nonce)` — mark as consumed
- `prune_expired(older_than)` — remove old entries
- Key format: `"{domain}:{nonce}"`
- Use `aiosqlite`

### 8. Stream Rehydration (`silas/core/stream.py` update)
Update the Stream to:
- On startup, call `_rehydrate()` per spec §5.1.3
- Load recent chronicle entries from ChronicleStore
- Apply observation masking to old tool results
- Load user profile from memory search
- Load in-progress work items from WorkItemStore
- Add system message "[SYSTEM] Session rehydrated after restart."

### 9. Wire persistence into main.py
- Update `build_stream()` to create SQLite stores
- Run migrations on `silas init`
- Pass stores to TurnContext

## Dependencies to Add
- `aiosqlite` (>=0.20,<1) — async SQLite access

## Rules
- All datetime fields use `datetime.now(timezone.utc)`, NEVER `datetime.utcnow()`
- Use parameterized queries, never string formatting for SQL
- JSON serialization via Pydantic's `.model_dump(mode="json")` / `.model_validate()`
- Migration checksums are SHA-256 hex digests
- Run `ruff check` and `pytest` before finishing
- Commit with conventional commits

## What NOT to Build
- No vector search / embeddings (Phase 8)
- No consolidator implementation (Phase 2)
- No retriever beyond FTS5 keyword (Phase 2)

When completely finished, run:
openclaw gateway wake --text "Done: Phase 1b persistence — SQLite stores, migrations, rehydration" --mode now
