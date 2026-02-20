-- Phase 1b: Initial schema
-- Memory, chronicle, work items, audit log, nonces

-- Applied migrations tracking
CREATE TABLE IF NOT EXISTS _migrations (
    name TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

-- Memory items
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    reingestion_tier TEXT NOT NULL DEFAULT 'active',
    trust_level TEXT NOT NULL DEFAULT 'working',
    taint TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed TEXT,
    semantic_tags TEXT NOT NULL DEFAULT '[]',  -- JSON array
    entity_refs TEXT NOT NULL DEFAULT '[]',    -- JSON array
    causal_refs TEXT NOT NULL DEFAULT '[]',    -- JSON array
    temporal_next TEXT,
    temporal_prev TEXT,
    session_id TEXT,
    embedding BLOB,  -- reserved for Phase 8 vector search
    source_kind TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_memories_reingestion ON memories(reingestion_tier);

-- FTS5 virtual table for keyword search
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    memory_id,
    content,
    semantic_tags,
    content='memories',
    content_rowid='rowid'
);

-- Triggers to keep FTS5 in sync
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, memory_id, content, semantic_tags)
    VALUES (new.rowid, new.memory_id, new.content, new.semantic_tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, memory_id, content, semantic_tags)
    VALUES ('delete', old.rowid, old.memory_id, old.content, old.semantic_tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, memory_id, content, semantic_tags)
    VALUES ('delete', old.rowid, old.memory_id, old.content, old.semantic_tags);
    INSERT INTO memories_fts(rowid, memory_id, content, semantic_tags)
    VALUES (new.rowid, new.memory_id, new.content, new.semantic_tags);
END;

-- Chronicle (conversation history for rehydration)
CREATE TABLE IF NOT EXISTS chronicle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id TEXT NOT NULL,
    ctx_id TEXT NOT NULL,
    zone TEXT NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    source TEXT NOT NULL,
    taint TEXT NOT NULL DEFAULT 'external',
    kind TEXT NOT NULL,
    relevance REAL NOT NULL DEFAULT 1.0,
    masked INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_chronicle_scope_time ON chronicle(scope_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chronicle_scope_turn ON chronicle(scope_id, turn_number);

-- Work items
CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    parent TEXT,
    spawned_by TEXT,
    follow_up_of TEXT,
    domain TEXT,
    agent TEXT NOT NULL DEFAULT 'ephemeral',
    budget TEXT NOT NULL DEFAULT '{}',           -- JSON
    needs_approval INTEGER NOT NULL DEFAULT 1,
    approval_token TEXT,                         -- JSON (full ApprovalToken)
    body TEXT NOT NULL,
    interaction_mode TEXT NOT NULL DEFAULT 'confirm_only_when_required',
    input_artifacts_from TEXT NOT NULL DEFAULT '[]',  -- JSON array
    verify TEXT NOT NULL DEFAULT '[]',           -- JSON array
    gates TEXT NOT NULL DEFAULT '[]',            -- JSON array
    skills TEXT NOT NULL DEFAULT '[]',           -- JSON array
    access_levels TEXT NOT NULL DEFAULT '{}',    -- JSON
    escalation TEXT NOT NULL DEFAULT '{}',       -- JSON
    schedule TEXT,
    on_failure TEXT NOT NULL DEFAULT 'report',
    on_stuck TEXT NOT NULL DEFAULT 'consult_planner',
    failure_context TEXT,
    tasks TEXT NOT NULL DEFAULT '[]',            -- JSON array
    depends_on TEXT NOT NULL DEFAULT '[]',       -- JSON array
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    budget_used TEXT NOT NULL DEFAULT '{}',      -- JSON
    verification_results TEXT NOT NULL DEFAULT '[]',  -- JSON array
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_items_status ON work_items(status);
CREATE INDEX IF NOT EXISTS idx_work_items_parent ON work_items(parent);
CREATE INDEX IF NOT EXISTS idx_work_items_follow_up ON work_items(follow_up_of);

-- Audit log (hash-chained)
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE,
    event TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',  -- JSON
    timestamp TEXT NOT NULL,
    prev_hash TEXT NOT NULL,          -- SHA-256 of previous entry (or "genesis")
    entry_hash TEXT NOT NULL          -- SHA-256 of this entry
);

CREATE INDEX IF NOT EXISTS idx_audit_log_event ON audit_log(event);
CREATE INDEX IF NOT EXISTS idx_audit_log_time ON audit_log(timestamp);

-- Audit checkpoints
CREATE TABLE IF NOT EXISTS audit_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Nonces (replay protection)
CREATE TABLE IF NOT EXISTS nonces (
    key TEXT PRIMARY KEY,        -- "{domain}:{nonce}"
    domain TEXT NOT NULL,
    nonce TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nonces_domain ON nonces(domain);
CREATE INDEX IF NOT EXISTS idx_nonces_recorded ON nonces(recorded_at);
