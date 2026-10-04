#!/usr/bin/env python3
"""AI Dashboard server v2 — stdlib only (no pip deps).

Env:
  MC_DB            SQLite path (default /opt/data/mission-control.db)
  MC_PASSWORD_FILE file berisi password dashboard (default /root/.mission-control/dashboard-pass)
  MC_EXECUTOR      local | mock (default local)
  MC_STATUS_DIR    dir <task_id>.status.json (default /opt/data/status)
  MC_BIND / MC_PORT (default 127.0.0.1:8090 — hanya localhost; akses luar via tunnel)
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from mc import db as dbmod
from mc import state
from mc import dispatch as dispatchmod
from mc import chat as chatmod
from mc import profiles as profmod
from mc import usage as usagemod
from mc.executor import LocalHermesExecutor, MockExecutor

DB_PATH = os.environ.get("MC_DB", "/opt/data/mission-control.db")
PASSWORD_FILE = os.environ.get("MC_PASSWORD_FILE", "/root/.mission-control/dashboard-pass")
EXECUTOR_KIND = os.environ.get("MC_EXECUTOR", "local")
STATUS_DIR = os.environ.get("MC_STATUS_DIR", "/opt/data/status")
BIND = os.environ.get("MC_BIND", "127.0.0.1")
PORT = int(os.environ.get("MC_PORT", "8090"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SESSION_TTL = 86400

sessions = {}  # token -> expires_at
sessions_lock = threading.Lock()
dispatch_lock = threading.Lock()


def load_password():
    with open(PASSWORD_FILE, encoding="utf-8") as f:
        return f.read().strip()


def new_session():
    token = secrets.token_hex(32)
    with sessions_lock:
        sessions[token] = time.time() + SESSION_TTL
    return token


def check_session(cookie_header):
    if not cookie_header:
        return False
    token = None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("mc_session="):
            token = part[len("mc_session="):]
    if not token:
        return False
    with sessions_lock:
        exp = sessions.get(token)
        if not exp or exp < time.time():
            sessions.pop(token, None)
            return False
        sessions[token] = time.time() + SESSION_TTL  # sliding
    return True


def drop_session(cookie_header):
    if not cookie_header:
        return
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("mc_session="):
            with sessions_lock:
                sessions.pop(part[len("mc_session="):], None)


# ---------- executor & dispatcher (global, guarded by dispatch_lock) ----------

def build_executor():
    if EXECUTOR_KIND == "mock":
        return MockExecutor(STATUS_DIR)
    return LocalHermesExecutor(status_dir=STATUS_DIR, container=None)


_executor = build_executor()
_disp_conn = None


def get_dispatcher():
    global _disp_conn
    if _disp_conn is None:
        dbmod.init_db(DB_PATH)
        _disp_conn = dbmod.connect(DB_PATH)
    return dispatchmod.Dispatcher(_disp_conn, _executor)


SCHEMA_V2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schema_v2.sql")

def init_db_all(path=None):
    dbmod.init_db(path or DB_PATH)
    conn = dbmod.connect(path or DB_PATH)
    with open(SCHEMA_V2, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

def fresh_conn():
    init_db_all()
    return dbmod.connect(DB_PATH)


# ---------- helpers ----------

def jparse(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def task_out(t):
    return {
        "id": t["id"], "title": t["title"], "description": t["description"],
        "assignee": t["assignee"], "mission_context": t["mission_context"],
        "interface_contract": jparse(t["interface_contract"]),
        "acceptance_criteria": jparse(t["acceptance_criteria"]),
        "output_path": t["output_path"],
        "depends_on": jparse(t["depends_on"]) or [],
        "cost_est_low": t["cost_est_low"], "cost_est_high": t["cost_est_high"],
        "status": t["status"], "attempt": t["attempt"],
        "result_summary": t["result_summary"], "updated_at": t["updated_at"],
    }


def mission_progress(conn, mission_id):
    tasks = dbmod.list_tasks(conn, mission_id)
    if not tasks:
        return 0.0
    return round(sum(1 for t in tasks if t["status"] == "done") / len(tasks) * 100, 1)


def run_hermes_chat(profile, query, timeout=300):
    """Jalankan `hermes -p <profile> chat -q` via stream-json. Return (reply, tokens)."""
    proc = subprocess.run(
        ["hermes", "-p", profile, "chat", "-q", query, "--format", "stream-json"],
        capture_output=True, text=True, timeout=timeout,
    )
    reply_parts, tokens = [], {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "text" and ev.get("text"):
            reply_parts.append(ev["text"])
        elif ev.get("type") == "result":
            tokens = ev.get("tokens") or {}
            if ev.get("text"):
                reply_parts = [ev["text"]]
    reply = "\n".join(reply_parts).strip()
    if not reply:
        raise RuntimeError(f"hermes tidak mengembalikan jawaban (rc={proc.returncode})")
    return reply, tokens


# ---------- HTTP handler ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "MCMVP/1.0"

    def log_message(self, fmt, *args):
        pass  # diam di log; audit ada di DB

    # -- plumbing --
    def _send(self, code, obj, cookies=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for c in cookies or []:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        if path in ("/index.html", "/login.html"):
            rel = path.lstrip("/")
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]  # /static/app.js -> app.js
        else:
            self._send(404, {"error": "not found"})
            return
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self._send(404, {"error": "not found"})
            return
        ctype = {"html": "text/html; charset=utf-8", "css": "text/css",
                 "js": "application/javascript"}.get(full.rsplit(".", 1)[-1], "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _require_auth(self):
        if check_session(self.headers.get("Cookie")):
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    # -- routing --
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/login" or not path.startswith("/api/"):
            if path.startswith("/api/"):
                self._send(404, {"error": "not found"})
            else:
                self._static(path if path != "/" else "/index.html")
            return
        if not self._require_auth():
            return
        try:
            if path == "/api/missions":
                self.api_list_missions()
            elif path == "/api/profiles":
                self.api_list_profiles()
            elif path.startswith("/api/profiles/") and path.endswith("/soul"):
                self.api_get_soul(path.split("/")[3])
            elif path == "/api/chat/threads":
                self.api_list_threads()
            elif path.startswith("/api/chat/threads/") and path.endswith("/messages"):
                self.api_list_messages(path.split("/")[4])
            elif path == "/api/notifications":
                self.api_get_notif()
            elif path == "/api/usage":
                self.api_usage(parsed)
            elif path.startswith("/api/missions/") and path.endswith("/cost"):
                self.api_cost(path.split("/")[3])
            elif path.startswith("/api/missions/"):
                self.api_mission_detail(path.split("/")[3])
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/login":
            body = self._body()
            try:
                ok = secrets.compare_digest(body.get("password", ""), load_password())
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                token = new_session()
                self._send(200, {"ok": True},
                           cookies=[f"mc_session={token}; HttpOnly; Path=/; SameSite=Lax; Max-Age={SESSION_TTL}"])
            else:
                self._send(401, {"error": "wrong password"})
            return
        if path == "/api/logout":
            drop_session(self.headers.get("Cookie"))
            self._send(200, {"ok": True},
                       cookies=["mc_session=; HttpOnly; Path=/; Max-Age=0"])
            return
        if not self._require_auth():
            return
        try:
            body = self._body()
            if path == "/api/missions":
                self.api_create_mission(body)
            elif path.startswith("/api/missions/") and path.endswith("/action"):
                self.api_mission_action(path.split("/")[3], body)
            elif path.startswith("/api/missions/") and path.endswith("/tasks"):
                self.api_create_task(path.split("/")[3], body)
            elif path.startswith("/api/missions/") and path.endswith("/dispatch"):
                self.api_dispatch(path.split("/")[3])
            elif path.startswith("/api/missions/") and path.endswith("/poll"):
                self.api_poll(path.split("/")[3])
            elif path.startswith("/api/tasks/") and path.endswith("/kill"):
                self.api_kill_task(path.split("/")[3], body)
            elif path.startswith("/api/tasks/") and path.endswith("/reassign"):
                self.api_reassign_task(path.split("/")[3], body)
            elif path == "/api/profiles":
                self.api_create_profile(body)
            elif path.startswith("/api/profiles/") and path.endswith("/lead-agent"):
                self.api_set_lead_agent(path.split("/")[3])
            elif path == "/api/chat/threads":
                self.api_create_thread(body)
            elif path == "/api/chat/send":
                self.api_chat_send(body)
            elif path == "/api/notifications/test":
                self.api_notif_test()
            else:
                self._send(404, {"error": "not found"})
        except state.TransitionError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self._require_auth():
            return
        try:
            body = self._body()
            if path.startswith("/api/profiles/") and path.endswith("/soul"):
                self.api_set_soul(path.split("/")[3], body)
            elif path.startswith("/api/profiles/") and path.endswith("/model"):
                self.api_set_model(path.split("/")[3], body)
            elif path == "/api/notifications":
                self.api_set_notif(body)
            elif path.startswith("/api/chat/threads/"):
                self.api_rename_thread(path.split("/")[4], body)
            else:
                self._send(404, {"error": "not found"})
        except (ValueError, RuntimeError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self._require_auth():
            return
        try:
            if path.startswith("/api/profiles/"):
                self.api_delete_profile(path.split("/")[3])
            elif path.startswith("/api/chat/threads/"):
                self.api_delete_thread(path.split("/")[4])
            else:
                self._send(404, {"error": "not found"})
        except (ValueError, RuntimeError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    # -- API impl --
    def api_list_missions(self):
        conn = fresh_conn()
        rows = conn.execute("SELECT * FROM missions ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            m = dict(r)
            out.append({"id": m["id"], "title": m["title"], "status": m["status"],
                        "revision_round": m["revision_round"],
                        "progress": mission_progress(conn, m["id"]),
                        "cost_est_low": m["cost_est_low"], "cost_est_high": m["cost_est_high"],
                        "updated_at": m["updated_at"]})
        conn.close()
        self._send(200, {"missions": out})

    def api_create_mission(self, body):
        title = (body.get("title") or "").strip()
        if not title:
            self._send(400, {"error": "title wajib diisi"})
            return
        mid = body.get("id") or f"m-{secrets.token_hex(4)}"
        conn = fresh_conn()
        try:
            dbmod.create_mission(conn, mid, title, body.get("brief"),
                                 skip_gate=bool(body.get("skip_gate")))
        except Exception as e:  # noqa: BLE001
            conn.close()
            self._send(400, {"error": f"id sudah dipakai? {e}"})
            return
        conn.close()
        self._send(201, {"id": mid})

    def api_mission_detail(self, mid):
        conn = fresh_conn()
        m = dbmod.get_mission(conn, mid)
        if not m:
            conn.close()
            self._send(404, {"error": "not found"})
            return
        tasks = [task_out(t) for t in dbmod.list_tasks(conn, mid)]
        approvals = [dict(r) for r in conn.execute(
            "SELECT round, decision, note, decided_by, envelope_low, envelope_high, decided_at"
            " FROM approvals WHERE mission_id=? ORDER BY id", (mid,)).fetchall()]
        events = [dict(r) for r in conn.execute(
            "SELECT actor, action, detail, created_at FROM events"
            " WHERE mission_id=? ORDER BY id DESC LIMIT 50", (mid,)).fetchall()]
        progress = mission_progress(conn, mid)
        conn.close()
        self._send(200, {"mission": m, "tasks": tasks, "approvals": approvals,
                         "events": events, "progress": progress})

    def api_mission_action(self, mid, body):
        action = body.get("action")
        conn = fresh_conn()
        try:
            if action == "to_decomposed":
                st = state.transition(conn, mid, "decomposed")
            elif action == "request_approval":
                st = state.request_approval(conn, mid, body.get("cost_low"), body.get("cost_high"))
            elif action == "approve":
                st = state.approve(conn, mid, body.get("envelope_low"), body.get("envelope_high"))
            elif action == "request_revision":
                st = state.request_revision(conn, mid, body.get("note", ""))
            elif action == "pause":
                st = state.transition(conn, mid, "paused")
            elif action == "resume":
                st = state.transition(conn, mid, "in_progress")
            elif action == "unblock":
                st = state.transition(conn, mid, "in_progress")
            elif action == "to_review":
                st = state.transition(conn, mid, "review")
            elif action == "mark_done":
                st = state.transition(conn, mid, "done")
            elif action == "cancel":
                st = state.cancel(conn, mid, reason=body.get("reason"))
            else:
                conn.close()
                self._send(400, {"error": f"action tidak dikenal: {action}"})
                return
        finally:
            conn.close()
        self._send(200, {"status": st})

    def api_create_task(self, mid, body):
        title = (body.get("title") or "").strip()
        if not title or not body.get("description") or not body.get("assignee"):
            self._send(400, {"error": "title, description, assignee wajib diisi"})
            return
        tid = body.get("id") or f"t-{secrets.token_hex(4)}"
        conn = fresh_conn()
        m = dbmod.get_mission(conn, mid)
        if not m:
            conn.close()
            self._send(404, {"error": "mission not found"})
            return
        try:
            dbmod.create_task(
                conn, tid, mid, title, body["description"], body["assignee"],
                mission_context=body.get("mission_context"),
                interface_contract=body.get("interface_contract"),
                acceptance_criteria=body.get("acceptance_criteria"),
                output_path=body.get("output_path"),
                depends_on=body.get("depends_on") or [],
                cost_est_low=body.get("cost_est_low"),
                cost_est_high=body.get("cost_est_high"))
            dbmod.log_event(conn, "dashboard", "task_created", mission_id=mid,
                            task_id=tid, detail=f"{title} → {body['assignee']}")
        except Exception as e:  # noqa: BLE001
            conn.close()
            self._send(400, {"error": str(e)})
            return
        conn.close()
        self._send(201, {"id": tid})

    def api_dispatch(self, mid):
        with dispatch_lock:
            disp = get_dispatcher()
            sent = disp.dispatch_ready(mid)
        self._send(200, {"dispatched": sent})

    def api_poll(self, mid):
        with dispatch_lock:
            disp = get_dispatcher()
            states = disp.poll_all(mid)
            progress = disp.mission_progress(mid)
        self._send(200, {"states": states, "progress": progress})

    def api_kill_task(self, tid, body):
        with dispatch_lock:
            disp = get_dispatcher()
            ok = disp.kill_task(tid, body.get("reason") or "manual kill")
        self._send(200, {"ok": ok})

    def api_reassign_task(self, tid, body):
        assignee = body.get("assignee")
        if not assignee:
            self._send(400, {"error": "assignee wajib diisi"})
            return
        conn = fresh_conn()
        t = dbmod.get_task(conn, tid)
        if not t:
            conn.close()
            self._send(404, {"error": "not found"})
            return
        if t["status"] != "pending":
            conn.close()
            self._send(400, {"error": "hanya task pending yang bisa di-reassign"})
            return
        if not dbmod.get_profile(conn, assignee):
            conn.close()
            self._send(400, {"error": f"profile {assignee} tidak terdaftar"})
            return
        conn.execute("UPDATE tasks SET assignee=?, updated_at=? WHERE id=?",
                     (assignee, dbmod.now_iso(), tid))
        dbmod.log_event(conn, "dashboard", "task_reassigned",
                        mission_id=t["mission_id"], task_id=tid,
                        detail=f"{t['assignee']} → {assignee}")
        conn.commit()
        conn.close()
        self._send(200, {"ok": True})

    # ---------- v2: profiles ----------
    def _profiles_with_meta(self):
        conn = fresh_conn()
        meta = {r["profile"]: dict(r) for r in conn.execute("SELECT * FROM profile_meta").fetchall()}
        conn.close()
        out = []
        for p in profmod.list_profiles():
            m = meta.get(p["name"], {})
            out.append({**p,
                        "role": m.get("role", "specialist"),
                        "is_lead_agent": bool(m.get("is_lead_agent", 0))})
        return out

    def api_list_profiles(self):
        try:
            self._send(200, {"profiles": self._profiles_with_meta()})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def api_create_profile(self, body):
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip()
        role = (body.get("role") or "specialist").strip()
        model = (body.get("model") or "").strip()
        soul = body.get("soul") or ""
        try:
            profmod.create_profile(name, description)
            if soul:
                profmod.set_soul(name, soul)
            if model:
                profmod.set_model(name, model)
            conn = fresh_conn()
            conn.execute("INSERT OR REPLACE INTO profile_meta (profile, role, is_lead_agent)"
                         " VALUES (?, ?, 0)", (name, role))
            conn.commit()
            conn.close()
        except (ValueError, RuntimeError) as e:
            self._send(400, {"error": str(e)})
            return
        self._send(201, {"name": name})

    def api_delete_profile(self, name):
        conn = fresh_conn()
        r = conn.execute("SELECT is_lead_agent FROM profile_meta WHERE profile=?", (name,)).fetchone()
        if r and r["is_lead_agent"]:
            conn.close()
            self._send(400, {"error": "tidak bisa hapus Lead Agent — tunjuk pengganti dulu"})
            return
        conn.execute("DELETE FROM profile_meta WHERE profile=?", (name,))
        conn.commit()
        conn.close()
        try:
            profmod.delete_profile(name)
        except (ValueError, RuntimeError) as e:
            self._send(400, {"error": str(e)})
            return
        self._send(200, {"ok": True})

    def api_set_lead_agent(self, name):
        if not profmod.profile_exists(name):
            self._send(404, {"error": "profile tidak ada"})
            return
        conn = fresh_conn()
        conn.execute("UPDATE profile_meta SET is_lead_agent=0")
        conn.execute("INSERT INTO profile_meta (profile, role, is_lead_agent)"
                     " VALUES (?, 'lead-agent', 1)"
                     " ON CONFLICT(profile) DO UPDATE SET is_lead_agent=1, role='lead-agent'",
                     (name,))
        conn.commit()
        conn.close()
        # tulis penanda sederhana
        try:
            mc_dir = "/root/.mission-control"
            os.makedirs(mc_dir, exist_ok=True)
            with open(os.path.join(mc_dir, "lead-agent"), "w", encoding="utf-8") as f:
                f.write(name + "\n")
        except OSError:
            pass
        self._send(200, {"ok": True, "lead_agent": name})

    def api_get_soul(self, name):
        if not profmod.profile_exists(name):
            self._send(404, {"error": "profile tidak ada"})
            return
        self._send(200, {"name": name, "soul": profmod.get_soul(name)})

    def api_set_soul(self, name, body):
        profmod.set_soul(name, body.get("soul", ""))
        self._send(200, {"ok": True})

    def api_set_model(self, name, body):
        profmod.set_model(name, body.get("model", ""))
        self._send(200, {"ok": True})

    # ---------- v2: chat ----------
    def api_list_threads(self):
        conn = fresh_conn()
        out = chatmod.list_threads(conn)
        conn.close()
        self._send(200, {"threads": out})

    def api_create_thread(self, body):
        target = (body.get("target") or "default").strip()
        title = (body.get("title") or "").strip() or None
        conn = fresh_conn()
        try:
            tid = chatmod.create_thread(conn, target, title)
        except ValueError as e:
            conn.close()
            self._send(400, {"error": str(e)})
            return
        conn.close()
        self._send(201, {"id": tid, "target": target})

    def api_list_messages(self, tid):
        conn = fresh_conn()
        if not chatmod.get_thread(conn, tid):
            conn.close()
            self._send(404, {"error": "thread tidak ada"})
            return
        out = chatmod.list_messages(conn, tid)
        conn.close()
        self._send(200, {"messages": out})

    def api_rename_thread(self, tid, body):
        conn = fresh_conn()
        chatmod.rename_thread(conn, tid, (body.get("title") or "").strip())
        conn.close()
        self._send(200, {"ok": True})

    def api_delete_thread(self, tid):
        conn = fresh_conn()
        chatmod.delete_thread(conn, tid)
        conn.close()
        self._send(200, {"ok": True})

    def api_chat_send(self, body):
        tid = (body.get("thread_id") or "").strip()
        text = body.get("text") or ""
        target = (body.get("target") or "").strip()
        conn = fresh_conn()
        try:
            if not tid:
                if not target:
                    # default ke lead agent aktif
                    cur = conn.execute("SELECT profile FROM profile_meta"
                                       " WHERE is_lead_agent=1 LIMIT 1").fetchone()
                    target = cur["profile"] if cur else "default"
                tid = chatmod.create_thread(conn, target)
            targets = chatmod.send_async(DB_PATH, conn, tid, text)
        except ValueError as e:
            conn.close()
            self._send(400, {"error": str(e)})
            return
        conn.close()
        self._send(202, {"thread_id": tid, "targets": targets, "status": "sending"})

    # ---------- v2: notifications ----------
    def api_get_notif(self):
        conn = fresh_conn()
        r = conn.execute("SELECT * FROM notif_settings WHERE id=1").fetchone()
        conn.close()
        self._send(200, dict(r) if r else {})

    def api_set_notif(self, body):
        conn = fresh_conn()
        conn.execute("UPDATE notif_settings SET gateway=?, enabled=?, on_gate=?,"
                     " on_done=?, on_error=? WHERE id=1",
                     (body.get("gateway", "telegram"), int(bool(body.get("enabled", 1))),
                      int(bool(body.get("on_gate", 1))), int(bool(body.get("on_done", 1))),
                      int(bool(body.get("on_error", 1)))))
        conn.commit()
        conn.close()
        self._send(200, {"ok": True})

    def api_notif_test(self):
        # Fase C: hanya validasi gateway terdaftar; pengiriman via gateway di Fase E
        conn = fresh_conn()
        r = conn.execute("SELECT gateway, enabled FROM notif_settings WHERE id=1").fetchone()
        conn.close()
        self._send(200, {"ok": True, "note": "tes kirim via gateway: Fase E",
                         "gateway": r["gateway"] if r else None,
                         "enabled": bool(r["enabled"]) if r else False})

    # ---------- v2: usage ----------
    def api_usage(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            days = max(1, min(90, int(qs.get("days", ["7"])[0])))
        except ValueError:
            days = 7
        names = [p["name"] for p in profmod.list_profiles()]
        self._send(200, usagemod.usage_all(names, days=days))

    def api_cost(self, mid):
        conn = fresh_conn()
        m = dbmod.get_mission(conn, mid)
        if not m:
            conn.close()
            self._send(404, {"error": "not found"})
            return
        est = {"low": m["cost_est_low"], "high": m["cost_est_high"]}
        actual = conn.execute(
            "SELECT COALESCE(SUM(amount_low),0) low, COALESCE(SUM(amount_high),0) high"
            " FROM cost_ledger WHERE mission_id=? AND kind='actual'", (mid,)).fetchone()
        by_profile = [dict(r) for r in conn.execute(
            "SELECT profile, COALESCE(SUM(amount_low),0) actual_low,"
            " COALESCE(SUM(amount_high),0) actual_high, COUNT(*) task_count"
            " FROM cost_ledger WHERE mission_id=? AND kind='actual' GROUP BY profile",
            (mid,)).fetchall()]
        conn.close()
        self._send(200, {"estimate": est,
                         "actual": {"low": actual["low"], "high": actual["high"]},
                         "by_profile": by_profile})


def auto_loop(interval=30):
    """Background: dispatch task siap + poll status untuk semua mission in_progress."""
    while True:
        try:
            conn = fresh_conn()
            mids = [r["id"] for r in conn.execute(
                "SELECT id FROM missions WHERE status='in_progress'").fetchall()]
            conn.close()
            for mid in mids:
                with dispatch_lock:
                    disp = get_dispatcher()
                    sent = disp.dispatch_ready(mid)
                    states = disp.poll_all(mid)
                    progress = disp.mission_progress(mid)
                if sent or states:
                    print(f"[auto] {mid}: dispatched={sent} states={states}", flush=True)
                # semua task selesai -> otomatis masuk review
                if progress >= 100:
                    try:
                        conn2 = fresh_conn()
                        if dbmod.get_mission(conn2, mid)["status"] == "in_progress":
                            state.transition(conn2, mid, "review", actor="dashboard",
                                             detail="semua task selesai")
                            dbmod.log_event(conn2, "dashboard", "auto_to_review",
                                            mission_id=mid)
                            print(f"[auto] {mid}: -> review", flush=True)
                        conn2.close()
                    except state.TransitionError:
                        pass
        except Exception as e:  # noqa: BLE001
            print(f"[auto] error: {e}", flush=True)
        time.sleep(interval)


def main():
    init_db_all()
    conn = dbmod.connect(DB_PATH)
    # seed profile_meta: baca penanda lead agent, default = profile "default"
    lead = "default"
    try:
        with open("/root/.mission-control/lead-agent", encoding="utf-8") as f:
            lead = (f.read().strip() or lead)
    except OSError:
        pass
    for p in profmod.list_profiles():
        conn.execute("INSERT OR IGNORE INTO profile_meta (profile, role) VALUES (?, 'specialist')",
                     (p["name"],))
    if profmod.profile_exists(lead):
        conn.execute("UPDATE profile_meta SET is_lead_agent=0")
        conn.execute("UPDATE profile_meta SET is_lead_agent=1, role='lead-agent' WHERE profile=?",
                     (lead,))
    conn.commit()
    conn.close()
    threading.Thread(target=auto_loop, daemon=True).start()
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"AI Dashboard v2 di http://{BIND}:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
