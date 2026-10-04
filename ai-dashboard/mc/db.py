"""Akses SQLite untuk Mission Control. DB default di ~/.hermes/mission-control.db."""
import json
import os
import sqlite3

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

DEFAULT_DB = os.path.expanduser("~/.hermes/mission-control.db")


def connect(path=None):
    path = path or DEFAULT_DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path=None):
    conn = connect(path)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def now_iso():
    return __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def log_event(conn, actor, action, mission_id=None, task_id=None, detail=None):
    conn.execute(
        "INSERT INTO events (mission_id, task_id, actor, action, detail)"
        " VALUES (?, ?, ?, ?, ?)",
        (mission_id, task_id, actor, action, detail),
    )
    conn.commit()


def _json_or_none(value):
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


# ---- missions ----
def create_mission(conn, mission_id, title, brief=None, skip_gate=False):
    conn.execute(
        "INSERT INTO missions (id, title, brief, skip_gate) VALUES (?, ?, ?, ?)",
        (mission_id, title, brief, 1 if skip_gate else 0),
    )
    log_event(conn, "dashboard", "mission_created", mission_id=mission_id,
              detail=f"title={title} skip_gate={bool(skip_gate)}")
    conn.commit()


def get_mission(conn, mission_id):
    row = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
    return dict(row) if row else None


def set_mission_status(conn, mission_id, status):
    conn.execute(
        "UPDATE missions SET status = ?, updated_at = ? WHERE id = ?",
        (status, now_iso(), mission_id),
    )
    conn.commit()


def bump_revision(conn, mission_id):
    conn.execute(
        "UPDATE missions SET revision_round = revision_round + 1, updated_at = ? WHERE id = ?",
        (now_iso(), mission_id),
    )
    conn.commit()


def set_cost_estimate(conn, mission_id, low, high):
    conn.execute(
        "UPDATE missions SET cost_est_low = ?, cost_est_high = ?, updated_at = ? WHERE id = ?",
        (low, high, now_iso(), mission_id),
    )
    conn.commit()


# ---- tasks ----
def create_task(conn, task_id, mission_id, title, description, assignee,
                mission_context=None, interface_contract=None,
                acceptance_criteria=None, output_path=None, depends_on=None,
                cost_est_low=None, cost_est_high=None):
    conn.execute(
        """INSERT INTO tasks (id, mission_id, title, description, assignee,
                              mission_context, interface_contract, acceptance_criteria,
                              output_path, depends_on, cost_est_low, cost_est_high)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, mission_id, title, description, assignee, mission_context,
         _json_or_none(interface_contract), _json_or_none(acceptance_criteria),
         output_path, json.dumps(depends_on or []),
         cost_est_low, cost_est_high),
    )
    conn.commit()


def get_task(conn, task_id):
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(conn, mission_id):
    rows = conn.execute(
        "SELECT * FROM tasks WHERE mission_id = ? ORDER BY created_at", (mission_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def set_task_status(conn, task_id, status, result_summary=None):
    if result_summary is not None:
        conn.execute(
            "UPDATE tasks SET status = ?, result_summary = ?, updated_at = ? WHERE id = ?",
            (status, result_summary, now_iso(), task_id),
        )
    else:
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), task_id),
        )
    conn.commit()


def bump_attempt(conn, task_id):
    conn.execute("UPDATE tasks SET attempt = attempt + 1 WHERE id = ?", (task_id,))
    conn.commit()


# ---- profiles ----
def upsert_profile(conn, name, role, soul_path=None, model=None, has_telegram=False):
    conn.execute(
        """INSERT INTO profiles (name, role, soul_path, model, has_telegram)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET role=excluded.role,
               soul_path=excluded.soul_path, model=excluded.model,
               has_telegram=excluded.has_telegram""",
        (name, role, soul_path, model, 1 if has_telegram else 0),
    )
    conn.commit()


def set_profile_status(conn, name, status, current_task=None):
    conn.execute(
        "UPDATE profiles SET status = ?, current_task = ? WHERE name = ?",
        (status, current_task, name),
    )
    conn.commit()


def get_profile(conn, name):
    row = conn.execute("SELECT * FROM profiles WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


# ---- approvals & cost ----
def record_approval(conn, mission_id, round_no, decision, note=None,
                    decided_by="izal", envelope_low=None, envelope_high=None):
    conn.execute(
        """INSERT INTO approvals (mission_id, round, decision, note, decided_by,
                                  envelope_low, envelope_high)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (mission_id, round_no, decision, note, decided_by, envelope_low, envelope_high),
    )
    conn.commit()


def record_cost(conn, mission_id, kind, amount_low, amount_high,
                task_id=None, profile=None, note=None):
    conn.execute(
        """INSERT INTO cost_ledger (mission_id, task_id, profile, kind,
                                    amount_low, amount_high, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (mission_id, task_id, profile, kind, amount_low, amount_high, note),
    )
    conn.commit()
