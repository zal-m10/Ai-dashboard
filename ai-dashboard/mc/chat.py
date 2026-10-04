"""Chat persistent: thread + pesan di SQLite, pengiriman via worker thread."""
import json
import secrets
import subprocess
import threading
import time

# target -> daftar profile. "default" (lead agent) / "marketing" / "multi:a,b"
def parse_target(target):
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target wajib diisi")
    target = target.strip()
    if target.startswith("multi:"):
        names = [n.strip() for n in target[len("multi:"):].split(",") if n.strip()]
        if not names:
            raise ValueError("multi: butuh minimal satu profile")
        return names
    return [target]


def target_label(target):
    names = parse_target(target)
    if len(names) == 1:
        return names[0]
    return "multi (" + ", ".join(names) + ")"


def create_thread(conn, target, title=None):
    parse_target(target)  # validasi
    tid = "th-" + secrets.token_hex(4)
    if not title:
        title = target_label(target)
    conn.execute(
        "INSERT INTO chat_threads (id, target, title) VALUES (?, ?, ?)",
        (tid, target, title[:120]))
    conn.commit()
    return tid


def list_threads(conn, limit=50):
    rows = conn.execute(
        "SELECT t.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id=t.id) AS n_msg,"
        " (SELECT body FROM chat_messages m WHERE m.thread_id=t.id ORDER BY id DESC LIMIT 1) AS last_msg"
        " FROM chat_threads t ORDER BY t.updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_thread(conn, tid):
    r = conn.execute("SELECT * FROM chat_threads WHERE id=?", (tid,)).fetchone()
    return dict(r) if r else None


def rename_thread(conn, tid, title):
    conn.execute("UPDATE chat_threads SET title=?, updated_at=datetime('now') WHERE id=?",
                 (title[:120], tid))
    conn.commit()


def delete_thread(conn, tid):
    conn.execute("DELETE FROM chat_messages WHERE thread_id=?", (tid,))
    conn.execute("DELETE FROM chat_threads WHERE id=?", (tid,))
    conn.commit()


def add_message(conn, thread_id, role, body, status="done"):
    cur = conn.execute(
        "INSERT INTO chat_messages (thread_id, role, body, status) VALUES (?, ?, ?, ?)",
        (thread_id, role, body, status))
    conn.execute("UPDATE chat_threads SET updated_at=datetime('now') WHERE id=?", (thread_id,))
    conn.commit()
    return cur.lastrowid


def list_messages(conn, thread_id, limit=300):
    rows = conn.execute(
        "SELECT id, role, body, status, created_at FROM chat_messages"
        " WHERE thread_id=? ORDER BY id ASC LIMIT ?", (thread_id, limit)).fetchall()
    return [dict(r) for r in rows]


def update_message(conn, msg_id, body, status):
    conn.execute("UPDATE chat_messages SET body=?, status=? WHERE id=?", (body, status, msg_id))
    conn.commit()


def run_one(profile, query, timeout=300):
    """Return (reply, tokens_dict). Raise RuntimeError bila gagal."""
    proc = subprocess.run(
        ["hermes", "-p", profile, "chat", "-q", query, "--format", "stream-json"],
        capture_output=True, text=True, timeout=timeout)
    parts, tokens = [], {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "text" and ev.get("text"):
            parts.append(ev["text"])
        elif ev.get("type") == "result":
            tokens = ev.get("tokens") or {}
            if ev.get("text"):
                parts = [ev["text"]]
    reply = "\n".join(parts).strip()
    if not reply:
        raise RuntimeError(f"profile '{profile}' tidak menjawab (rc={proc.returncode})")
    return reply, tokens


def _worker(db_path, thread_id, targets, text):
    """Jalankan di thread: kirim ke tiap target, update pesan placeholder."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    placeholders = list(conn.execute(
        "SELECT id, role FROM chat_messages WHERE thread_id=? AND status='sending'"
        " ORDER BY id", (thread_id,)).fetchall())
    for row, profile in zip(placeholders, targets):
        try:
            reply, _tokens = run_one(profile, text)
            update_message(conn, row["id"], reply, "done")
        except Exception as e:  # noqa: BLE001
            update_message(conn, row["id"], f"gagal: {e}", "error")
    conn.execute("UPDATE chat_threads SET updated_at=datetime('now') WHERE id=?", (thread_id,))
    conn.commit()
    conn.close()


def send_async(db_path, conn, thread_id, text):
    """Simpan pesan user + placeholder per target, jalankan worker. Return target list."""
    th = get_thread(conn, thread_id)
    if not th:
        raise ValueError("thread tidak ditemukan")
    targets = parse_target(th["target"])
    text = (text or "").strip()
    if not text:
        raise ValueError("pesan kosong")
    if len(text) > 20000:
        raise ValueError("pesan terlalu panjang")
    add_message(conn, thread_id, "user", text, "done")
    for profile in targets:
        add_message(conn, thread_id, profile, "", "sending")
    t = threading.Thread(target=_worker, args=(db_path, thread_id, targets, text), daemon=True)
    t.start()
    return targets
