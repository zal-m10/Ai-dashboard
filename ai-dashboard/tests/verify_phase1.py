"""Verifikasi Fase 1: mission dummy → task dummy → dispatch (mock) → status DB benar.
Jalankan: python3 tests/verify_phase1.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mc import db as dbmod
from mc import state
from mc import dispatch as dispatchmod
from mc import task_template
from mc.executor import MockExecutor, LocalHermesExecutor

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond, extra=""):
    results.append((PASS if cond else FAIL, name, extra))
    if not cond:
        print(f"  [{FAIL}] {name} {extra}")


def main():
    tmp = tempfile.mkdtemp(prefix="mc-phase1-")
    db_path = os.path.join(tmp, "mission-control.db")
    status_dir = os.path.join(tmp, "status")
    dbmod.init_db(db_path)
    conn = dbmod.connect(db_path)

    # --- 1. schema terbuat ---
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    check("schema: 6 tabel ada",
          {"missions", "tasks", "profiles", "approvals", "events", "cost_ledger"} <= tables,
          str(sorted(tables)))

    # --- 2. profile dummy ---
    dbmod.upsert_profile(conn, "frontend-dev", "specialist", model="murah-cepat")
    dbmod.upsert_profile(conn, "backend-dev", "specialist", model="murah-cepat")
    check("profile terdaftar", dbmod.get_profile(conn, "frontend-dev")["status"] == "idle")

    # --- 3. mission lifecycle ---
    dbmod.create_mission(conn, "m-dummy", "Toko online dummy", brief="brief bisnis")
    m = dbmod.get_mission(conn, "m-dummy")
    check("mission draft", m["status"] == "draft")

    state.transition(conn, "m-dummy", "decomposed")
    check("draft → decomposed", dbmod.get_mission(conn, "m-dummy")["status"] == "decomposed")

    try:
        state.transition(conn, "m-dummy", "in_progress")
        check("transisi ilegal ditolak", False)
    except state.TransitionError:
        check("transisi ilegal ditolak", True)

    # --- 4. tasks dengan dependensi: t1 → t2, t1 → t3 ---
    dbmod.create_task(conn, "t1", "m-dummy", "Setup API", "buat endpoint /products",
                      "backend-dev", mission_context="toko online",
                      interface_contract={"inputs": ["req"], "outputs": ["GET /products -> JSON"]},
                      acceptance_criteria=["GET /products return 200", "format JSON valid"],
                      output_path="/tmp/api.py", cost_est_low=1, cost_est_high=3)
    dbmod.create_task(conn, "t2", "m-dummy", "Halaman produk", "buat UI list produk",
                      "frontend-dev", depends_on=["t1"], cost_est_low=1, cost_est_high=2)
    dbmod.create_task(conn, "t3", "m-dummy", "Tes API", "curl test",
                      "backend-dev", depends_on=["t1"], cost_est_low=0.5, cost_est_high=1)
    check("3 task dibuat", len(dbmod.list_tasks(conn, "m-dummy")) == 3)

    # --- 5. gate ---
    st = state.request_approval(conn, "m-dummy", 2.5, 6)
    check("decomposed → awaiting_approval", st == "awaiting_approval")
    st = state.approve(conn, "m-dummy", 2.5, 6)
    check("approve → in_progress", st == "in_progress")
    appr = conn.execute("SELECT * FROM approvals WHERE mission_id='m-dummy'").fetchone()
    check("approval tercatat + amplop", appr["decision"] == "approved" and appr["envelope_high"] == 6)

    # --- 6. dispatch dengan mock: t2/t3 belum boleh jalan sebelum t1 done ---
    ex = MockExecutor(status_dir, behavior={"t1": "ok", "t2": "ok", "t3": "block"})
    disp = dispatchmod.Dispatcher(conn, ex)
    sent = disp.dispatch_ready("m-dummy")
    check("hanya t1 yang di-dispatch duluan", sent == ["t1"], str(sent))
    check("t1 status dispatched", dbmod.get_task(conn, "t1")["status"] == "dispatched")
    check("backend-dev working", dbmod.get_profile(conn, "backend-dev")["status"] == "working")

    polled = disp.poll_all("m-dummy")
    check("t1 done via status file", polled.get("t1") == "done")
    check("t1 tercatat done di DB", dbmod.get_task(conn, "t1")["status"] == "done")
    check("backend-dev kembali idle", dbmod.get_profile(conn, "backend-dev")["status"] == "idle")

    sent = disp.dispatch_ready("m-dummy")
    check("t2+t3 di-dispatch setelah t1 done", sorted(sent) == ["t2", "t3"], str(sent))
    polled = disp.poll_all("m-dummy")
    check("t2 done, t3 blocked", polled.get("t2") == "done" and polled.get("t3") == "blocked",
          str(polled))
    check("t3 blocker tercatat", "mock blocker" in (dbmod.get_task(conn, "t3")["result_summary"] or "") or
          dbmod.get_task(conn, "t3")["status"] == "blocked")
    check("progress 66.7%", disp.mission_progress("m-dummy") == 66.7,
          str(disp.mission_progress("m-dummy")))

    # --- 7. kill task ---
    dbmod.create_task(conn, "t4", "m-dummy", "Task ngaco", "x", "frontend-dev")
    ex2 = MockExecutor(status_dir, behavior={"t4": "hang"})
    disp2 = dispatchmod.Dispatcher(conn, ex2)
    disp2.dispatch_ready("m-dummy")
    check("t4 dispatched (hang)", dbmod.get_task(conn, "t4")["status"] == "dispatched")
    check("kill berhasil", disp2.kill_task("t4", "tes kill"))
    check("t4 cancelled", dbmod.get_task(conn, "t4")["status"] == "cancelled")

    # --- 8. revisi maksimal 3x ---
    dbmod.create_mission(conn, "m-rev", "Revisi test")
    state.transition(conn, "m-rev", "decomposed")
    state.request_approval(conn, "m-rev", 1, 2)
    for i in range(3):
        state.request_revision(conn, "m-rev", f"revisi {i+1}")
        state.transition(conn, "m-rev", "awaiting_approval")
    try:
        state.request_revision(conn, "m-rev", "revisi ke-4")
        check("revisi ke-4 ditolak", False)
    except state.TransitionError:
        check("revisi ke-4 ditolak", True)
    check("revision_round = 3", dbmod.get_mission(conn, "m-rev")["revision_round"] == 3)

    # --- 9. skip gate ("langsung jalan") ---
    dbmod.create_mission(conn, "m-skip", "Skip test", skip_gate=True)
    state.transition(conn, "m-skip", "decomposed")
    st = state.request_approval(conn, "m-skip", 1, 2)
    check("skip gate → langsung in_progress", st == "in_progress")

    # --- 10. task template render ---
    t1 = dbmod.get_task(conn, "t1")
    prompt = task_template.render(t1, status_dir=status_dir)
    check("template memuat semua field §5",
          all(k in prompt for k in ["t1", "GET /products", "Acceptance criteria",
                                    "status.json", "t1.status.json"]))

    # --- 11. LocalHermesExecutor: bangun command tanpa eksekusi ---
    lex = LocalHermesExecutor(status_dir=status_dir, container="hermes-mc")
    cmd = lex.build_command(t1, {"name": "backend-dev"})
    check("local executor command benar",
          cmd[:3] == ["docker", "exec", "hermes-mc"] and cmd[3:6] == ["hermes", "-p", "backend-dev"],
          " ".join(cmd[:6]))

    # --- 12. audit log & cost ledger ---
    n_events = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    check("audit log terisi", n_events > 10, f"{n_events} events")
    dbmod.record_cost(conn, "m-dummy", "estimate", 2.5, 6, note="gate")
    dbmod.record_cost(conn, "m-dummy", "actual", 1.2, 1.2, task_id="t1",
                      profile="backend-dev", note="selesai")
    n_cost = conn.execute("SELECT COUNT(*) c FROM cost_ledger").fetchone()["c"]
    check("cost ledger terisi", n_cost == 2)

    # --- 13. pause/resume/blocked ---
    state.transition(conn, "m-dummy", "paused")
    state.transition(conn, "m-dummy", "in_progress")
    state.transition(conn, "m-dummy", "blocked")
    state.transition(conn, "m-dummy", "in_progress")
    state.transition(conn, "m-dummy", "review")
    state.transition(conn, "m-dummy", "done")
    check("full lifecycle ke done", dbmod.get_mission(conn, "m-dummy")["status"] == "done")

    fails = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(fails)}/{len(results)} checks lolos")
    conn.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
