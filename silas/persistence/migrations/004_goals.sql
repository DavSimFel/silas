-- Phase 7a: Goals + autonomy persistence
--
-- NOTE (post Goals→Topics merge, PR #322): The "goal" tables below now back
-- the Topic model.  goal_id == topic_id, goal_runs tracks Topic execution
-- history, and standing_approvals stores per-Topic approval grants.
-- The table names are preserved to avoid breaking existing deployments.

CREATE TABLE IF NOT EXISTS goals (
    goal_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    schedule TEXT NOT NULL,           -- JSON
    work_template TEXT NOT NULL,      -- JSON
    skills TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    standing_approval INTEGER NOT NULL DEFAULT 0,
    spawn_policy_hash TEXT,
    verification TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goal_runs (
    run_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    status TEXT NOT NULL,
    work_item_id TEXT,
    started_at TEXT,
    completed_at TEXT,
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE TABLE IF NOT EXISTS standing_approvals (
    approval_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    max_uses INTEGER,
    uses_remaining INTEGER
);

CREATE INDEX IF NOT EXISTS idx_goal_runs_goal_started
    ON goal_runs(goal_id, started_at);

CREATE INDEX IF NOT EXISTS idx_standing_approvals_goal_policy
    ON standing_approvals(goal_id, policy_hash);
