"""Executor abstraction (§7 spec).

MVP: LocalHermesExecutor — dispatch via `hermes -p <profile> chat -q "<prompt>"`.
Next level: RemoteVpsExecutor tanpa merombak dispatcher (cukup subclass baru).
"""
import json
import os
import subprocess
import time
from abc import ABC, abstractmethod

from . import task_template


class Executor(ABC):
    """Kontrak executor. `run` adalah handle buram antar dispatch/poll/kill."""

    @abstractmethod
    def build_command(self, task, profile):
        """Bangun perintah dispatch (tanpa menjalankannya) — untuk dry-run/audit."""

    @abstractmethod
    def dispatch(self, task, profile):
        """Kirim task ke profile. Return dict run: {run_id, task_id, profile, started_at, ...}."""

    @abstractmethod
    def poll(self, run):
        """Baca <task_id>.status.json. Return {state, tail_log, artifacts, blocker}."""

    @abstractmethod
    def kill(self, run):
        """Hentikan eksekusi yang berjalan."""


def read_status_file(status_dir, task_id):
    path = os.path.join(status_dir, f"{task_id}.status.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "state": data.get("state", "running"),
            "tail_log": data.get("tail_log", ""),
            "artifacts": data.get("artifacts", []),
            "blocker": data.get("blocker"),
        }
    except (ValueError, OSError):
        return {"state": "running", "tail_log": "(status file rusak)",
                "artifacts": [], "blocker": None}


class LocalHermesExecutor(Executor):
    """Jalankan `hermes -p <profile> chat -q` di host yang sama dengan container.

    container: nama container hermes bila hermes CLI hanya ada di dalam container
               (dipanggil via `docker exec`); None bila hermes ada di PATH host.
    """

    def __init__(self, status_dir="/root/.hermes/status", container=None,
                 hermes_bin="hermes", timeout=3600):
        self.status_dir = status_dir
        self.container = container
        self.hermes_bin = hermes_bin
        self.timeout = timeout

    def build_command(self, task, profile):
        prompt = task_template.render(task, status_dir=self.status_dir)
        inner = [self.hermes_bin, "-p", profile["name"], "chat", "-q", prompt]
        if self.container:
            return ["docker", "exec", self.container] + inner
        return inner

    def dispatch(self, task, profile):
        cmd = self.build_command(task, profile)
        os.makedirs(self.status_dir, exist_ok=True)
        # hapus status lama biar tidak terbaca sebagai hasil run baru
        stale = os.path.join(self.status_dir, f"{task['id']}.status.json")
        if os.path.exists(stale):
            os.remove(stale)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {
            "run_id": f"{task['id']}-{int(time.time())}",
            "task_id": task["id"],
            "profile": profile["name"],
            "pid": proc.pid,
            "started_at": time.time(),
            "cmd": cmd,
        }

    def poll(self, run):
        return read_status_file(self.status_dir, run["task_id"])

    def kill(self, run):
        pid = run.get("pid")
        if not pid:
            return
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass


class MockExecutor(Executor):
    """Executor tiruan untuk verifikasi tanpa memanggil model asli.

    behavior: dict task_id -> "ok" | "fail" | "block" | "hang"
      ok    -> langsung tulis status done
      fail  -> langsung tulis status failed
      block -> langsung tulis status blocked + blocker
      hang  -> tidak menulis apa-apa (poll mengembalikan None)
    """

    def __init__(self, status_dir, behavior=None):
        self.status_dir = status_dir
        self.behavior = behavior or {}
        self.dispatched = []

    def build_command(self, task, profile):
        return ["mock", "-p", profile["name"], task["id"]]

    def dispatch(self, task, profile):
        os.makedirs(self.status_dir, exist_ok=True)
        self.dispatched.append((task["id"], profile["name"]))
        mode = self.behavior.get(task["id"], "ok")
        if mode != "hang":
            state = {"ok": "done", "fail": "failed", "block": "blocked"}[mode]
            payload = {
                "task_id": task["id"], "state": state,
                "tail_log": f"mock {state}",
                "artifacts": [task.get("output_path")] if task.get("output_path") else [],
                "blocker": "mock blocker" if mode == "block" else None,
            }
            with open(os.path.join(self.status_dir, f"{task['id']}.status.json"),
                      "w", encoding="utf-8") as f:
                json.dump(payload, f)
        return {"run_id": f"mock-{task['id']}", "task_id": task["id"],
                "profile": profile["name"], "started_at": time.time()}

    def poll(self, run):
        return read_status_file(self.status_dir, run["task_id"])

    def kill(self, run):
        pass
