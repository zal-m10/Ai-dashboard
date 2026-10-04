-- AI Dashboard v2 — tabel tambahan (digabung ke mission-control.db yang sama)

CREATE TABLE IF NOT EXISTS chat_threads (
  id TEXT PRIMARY KEY,
  target TEXT NOT NULL,
  title TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'done',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id, id);

CREATE TABLE IF NOT EXISTS profile_meta (
  profile TEXT PRIMARY KEY,
  role TEXT NOT NULL DEFAULT 'specialist',
  is_lead_agent INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notif_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  gateway TEXT NOT NULL DEFAULT 'telegram',
  enabled INTEGER NOT NULL DEFAULT 1,
  on_gate INTEGER NOT NULL DEFAULT 1,
  on_done INTEGER NOT NULL DEFAULT 1,
  on_error INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO notif_settings (id) VALUES (1);
