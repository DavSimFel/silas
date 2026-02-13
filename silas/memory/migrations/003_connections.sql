-- Phase 6b: Connection registry persistence

CREATE TABLE IF NOT EXISTS connections (
    connection_id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT "active",
    permissions_granted TEXT NOT NULL DEFAULT "[]",
    token_expires_at TEXT,
    last_refresh TEXT,
    last_health_check TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_connections_skill_name ON connections(skill_name);
CREATE INDEX IF NOT EXISTS idx_connections_status ON connections(status);
