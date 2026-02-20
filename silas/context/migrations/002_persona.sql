-- Phase 5a: Personality state + event persistence

CREATE TABLE IF NOT EXISTS persona_state (
    scope_id TEXT PRIMARY KEY,
    baseline_axes TEXT NOT NULL,     -- JSON (AxisProfile)
    mood TEXT NOT NULL,              -- JSON (MoodState)
    active_preset TEXT NOT NULL,
    voice TEXT NOT NULL,             -- JSON (VoiceConfig)
    last_context TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persona_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    scope_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    trusted INTEGER NOT NULL,
    delta_axes TEXT NOT NULL,        -- JSON
    delta_mood TEXT NOT NULL,        -- JSON
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_persona_events_scope_created
    ON persona_events(scope_id, created_at);
