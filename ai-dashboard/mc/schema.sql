-- Mission Control MVP schema (SQLite)
-- DB path di server: ~/.hermes/mission-control.db

CREATE TABLE IF NOT EXISTS missions (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    brief           TEXT,                       -- mission brief dari Lead Agent (bahasa bisnis)
    status          TEXT NOT NULL DEFAULT 'draft',
    revision_round  INTEGER NOT NULL DEFAULT 0, -- putaran revisi di gate, maks 3
    cost_est_low    REAL,                       -- estimasi biaya (range)
    cost_est_high   REAL,
    skip_gate       INTEGER NOT NULL DEFAULT 0, -- 1 = "langsung jalan", lewati approval
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    mission_id      TEXT NOT NULL REFERENCES missions(id),
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,              -- instruksi teknis konkret
    assignee        TEXT NOT NULL,              -- nama profile
    mission_context TEXT,                       -- ringkasan 3-5 baris
    interface_contract TEXT,                    -- JSON: {inputs, outputs}
    acceptance_criteria TEXT,                   -- JSON: [daftar cek]
    output_path     TEXT,                       -- lokasi artefak hasil
    depends_on      TEXT NOT NULL DEFAULT '[]', -- JSON: [id task prasyarat]
    cost_est_low    REAL,
    cost_est_high   REAL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempt         INTEGER NOT NULL DEFAULT 0,
    result_summary  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_mission ON tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);

CREATE TABLE IF NOT EXISTS profiles (
    name         TEXT PRIMARY KEY,              -- mis. lead-agent, frontend-dev
    role         TEXT NOT NULL,                 -- chief-of-staff / tech-lead / specialist
    soul_path    TEXT,                          -- path SOUL.md profile
    model        TEXT,                          -- model yang di-pin
    status       TEXT NOT NULL DEFAULT 'idle',  -- idle / working / blocked
    current_task TEXT,                         -- id task yang sedang dikerjakan
    has_telegram INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approvals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id    TEXT NOT NULL REFERENCES missions(id),
    round         INTEGER NOT NULL,             -- putaran ke berapa (1..3)
    decision      TEXT NOT NULL,                -- approved / revised / cancelled
    note          TEXT,                         -- catatan revisi dari izal
    decided_by    TEXT NOT NULL DEFAULT 'izal',
    envelope_low  REAL,                         -- amplop biaya yang di-approve
    envelope_high REAL,
    decided_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT,
    task_id    TEXT,
    actor      TEXT NOT NULL,                   -- dashboard / izal / lead-agent / ...
    action     TEXT NOT NULL,                   -- dispatch / approve / revisi / kill / ...
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_mission ON events(mission_id);

CREATE TABLE IF NOT EXISTS cost_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL REFERENCES missions(id),
    task_id    TEXT,
    profile    TEXT,
    kind       TEXT NOT NULL,                   -- estimate / actual
    amount_low REAL,
    amount_high REAL,
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cost_mission ON cost_ledger(mission_id);
