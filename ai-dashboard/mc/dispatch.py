"""Dispatcher: kirim task yang dependensinya sudah done, pantau status file,
catat hasil ke DB. Deterministik — dashboard yang pegang lifecycle."""
import json

from . import db as dbmod

# task status: pending → dispatched → running → done | failed | blocked | cancelled
TERMINAL = {"done", "failed", "blocked", "cancelled"}


def _deps_of(task):
    try:
        deps = json.loads(task.get("depends_on") or "[]")
        return deps if isinstance(deps, list) else []
    except (ValueError, TypeError):
        return []


def ready_tasks(conn, mission_id):
    """Task pending yang semua prasyaratnya sudah done."""
    tasks = dbmod.list_tasks(conn, mission_id)
    done = {t["id"] for t in tasks if t["status"] == "done"}
    return [t for t in tasks
            if t["status"] == "pending" and all(d in done for d in _deps_of(t))]


class Dispatcher:
    def __init__(self, conn, executor, actor="dashboard"):
        self.conn = conn
        self.executor = executor
        self.actor = actor
        self.runs = {}  # task_id -> run dict

    def dispatch_ready(self, mission_id):
        """Dispatch semua task yang siap. Return daftar task_id yang dikirim."""
        sent = []
        for task in ready_tasks(self.conn, mission_id):
            profile = dbmod.get_profile(self.conn, task["assignee"])
            if not profile:
                dbmod.set_task_status(self.conn, task["id"], "blocked",
                                      f"profile {task['assignee']} tidak terdaftar")
                dbmod.log_event(self.conn, self.actor, "dispatch_failed",
                                mission_id=mission_id, task_id=task["id"],
                                detail=f"profile {task['assignee']} tidak terdaftar")
                continue
            dbmod.bump_attempt(self.conn, task["id"])
            run = self.executor.dispatch(task, profile)
            self.runs[task["id"]] = run
            dbmod.set_task_status(self.conn, task["id"], "dispatched")
            dbmod.set_profile_status(self.conn, profile["name"], "working",
                                     current_task=task["id"])
            dbmod.log_event(self.conn, self.actor, "task_dispatched",
                            mission_id=mission_id, task_id=task["id"],
                            detail=f"ke {profile['name']} (attempt {task['attempt'] + 1})")
            sent.append(task["id"])
        return sent

    def poll_all(self, mission_id):
        """Baca status file semua run aktif; update DB. Return dict task_id -> state."""
        results = {}
        # recovery: task dispatched/running yang tidak ada di runs
        # (mis. server restart sehingga runs di memori hilang)
        for t in dbmod.list_tasks(self.conn, mission_id):
            if t["status"] in ("dispatched", "running") and t["id"] not in self.runs:
                self.runs[t["id"]] = {"run_id": f"recovered-{t['id']}",
                                      "task_id": t["id"],
                                      "profile": t["assignee"],
                                      "started_at": 0}
                dbmod.log_event(self.conn, self.actor, "run_recovered",
                                mission_id=mission_id, task_id=t["id"])
        for task_id, run in list(self.runs.items()):
            status = self.executor.poll(run)
            if status is None:
                continue  # belum ada status file → masih jalan
            state = status.get("state", "running")
            task = dbmod.get_task(self.conn, task_id)
            if not task:
                continue
            if task["status"] == "dispatched" and state in ("running", "done",
                                                           "failed", "blocked"):
                dbmod.set_task_status(self.conn, task_id, "running")
            if state in TERMINAL:
                dbmod.set_task_status(
                    self.conn, task_id, state,
                    result_summary=status.get("tail_log"))
                profile = dbmod.get_profile(self.conn, task["assignee"])
                if profile:
                    pstate = "blocked" if state == "blocked" else "idle"
                    dbmod.set_profile_status(self.conn, profile["name"], pstate)
                dbmod.log_event(
                    self.conn, self.actor, f"task_{state}",
                    mission_id=mission_id, task_id=task_id,
                    detail=status.get("tail_log") or status.get("blocker"))
                del self.runs[task_id]
            results[task_id] = state
        return results

    def kill_task(self, task_id, reason="manual kill"):
        task = dbmod.get_task(self.conn, task_id)
        if not task:
            return False
        run = self.runs.pop(task_id, None)
        if run:
            self.executor.kill(run)
        dbmod.set_task_status(self.conn, task_id, "cancelled",
                              result_summary=f"di-kill: {reason}")
        profile = dbmod.get_profile(self.conn, task["assignee"])
        if profile:
            dbmod.set_profile_status(self.conn, profile["name"], "idle")
        dbmod.log_event(self.conn, self.actor, "task_killed",
                        mission_id=task["mission_id"], task_id=task_id,
                        detail=reason)
        return True

    def mission_progress(self, mission_id):
        tasks = dbmod.list_tasks(self.conn, mission_id)
        if not tasks:
            return 0.0
        done = sum(1 for t in tasks if t["status"] == "done")
        return round(done / len(tasks) * 100, 1)
