"""State machine mission — sesuai §4 spec.

draft → decomposed → awaiting_approval → in_progress → review → done
                ↘______________________↗  (revisi, maks 3 putaran)
in_progress → paused ⇄ in_progress
in_progress → blocked → in_progress   (replan dalam amplop)
any → cancelled
"""
from . import db as dbmod

MAX_REVISIONS = 3

TRANSITIONS = {
    "draft": {"decomposed", "cancelled"},
    "decomposed": {"awaiting_approval", "cancelled"},
    "awaiting_approval": {"in_progress", "decomposed", "cancelled"},
    "in_progress": {"review", "paused", "blocked", "cancelled"},
    "paused": {"in_progress", "cancelled"},
    "blocked": {"in_progress", "cancelled"},
    "review": {"done", "in_progress", "cancelled"},
    "done": set(),
    "cancelled": set(),
}


class TransitionError(Exception):
    pass


def can_transition(frm, to):
    return to in TRANSITIONS.get(frm, set())


def transition(conn, mission_id, to, actor="dashboard", detail=None):
    """Pindahkan status mission; raise TransitionError bila tidak valid."""
    mission = dbmod.get_mission(conn, mission_id)
    if not mission:
        raise TransitionError(f"mission {mission_id} tidak ditemukan")
    frm = mission["status"]
    if not can_transition(frm, to):
        raise TransitionError(f"transisi {frm} → {to} tidak diizinkan")
    if frm == "awaiting_approval" and to == "decomposed":
        # revisi: hitung putaran, tolak bila sudah 3x
        if mission["revision_round"] >= MAX_REVISIONS:
            raise TransitionError(
                f"sudah {MAX_REVISIONS}x revisi — mission harus di-cancel atau dibuat ulang"
            )
        dbmod.bump_revision(conn, mission_id)
    dbmod.set_mission_status(conn, mission_id, to)
    dbmod.log_event(conn, actor, f"mission_{frm}_to_{to}",
                    mission_id=mission_id, detail=detail)
    return to


# ---- approval gate helpers ----

def request_approval(conn, mission_id, cost_low, cost_high, actor="lead-engineer"):
    """Lead Engineer selesai decomposing → masuk gate."""
    mission = dbmod.get_mission(conn, mission_id)
    if mission["status"] != "decomposed":
        raise TransitionError("request_approval hanya dari status decomposed")
    dbmod.set_cost_estimate(conn, mission_id, cost_low, cost_high)
    if mission["skip_gate"]:
        # "langsung jalan": lewati gate — tetap lewat awaiting_approval sesaat
        # agar audit trail lengkap, lalu auto-approve tanpa menunggu izal.
        dbmod.log_event(conn, actor, "gate_skipped", mission_id=mission_id,
                        detail=f"estimasi {cost_low}..{cost_high}")
        transition(conn, mission_id, "awaiting_approval", actor=actor,
                   detail="gate di-skip (langsung jalan)")
        transition(conn, mission_id, "in_progress", actor=actor,
                   detail="auto-approve: gate di-skip")
        return "in_progress"
    transition(conn, mission_id, "awaiting_approval", actor=actor,
               detail=f"estimasi {cost_low}..{cost_high}")
    return "awaiting_approval"


def approve(conn, mission_id, envelope_low, envelope_high, decided_by="izal"):
    mission = dbmod.get_mission(conn, mission_id)
    if mission["status"] != "awaiting_approval":
        raise TransitionError("approve hanya dari status awaiting_approval")
    round_no = mission["revision_round"] + 1
    dbmod.record_approval(conn, mission_id, round_no, "approved",
                          decided_by=decided_by,
                          envelope_low=envelope_low, envelope_high=envelope_high)
    dbmod.set_cost_estimate(conn, mission_id, envelope_low, envelope_high)
    dbmod.log_event(conn, decided_by, "gate_approved", mission_id=mission_id,
                    detail=f"amplop {envelope_low}..{envelope_high}")
    transition(conn, mission_id, "in_progress", actor=decided_by)
    return "in_progress"


def request_revision(conn, mission_id, note, decided_by="izal"):
    mission = dbmod.get_mission(conn, mission_id)
    if mission["status"] != "awaiting_approval":
        raise TransitionError("request_revision hanya dari status awaiting_approval")
    round_no = mission["revision_round"] + 1
    dbmod.record_approval(conn, mission_id, round_no, "revised",
                          note=note, decided_by=decided_by)
    dbmod.log_event(conn, decided_by, "gate_revision_requested",
                    mission_id=mission_id, detail=note)
    transition(conn, mission_id, "decomposed", actor=decided_by, detail=note)
    return "decomposed"


def cancel(conn, mission_id, actor="izal", reason=None):
    mission = dbmod.get_mission(conn, mission_id)
    if mission["status"] in ("done", "cancelled"):
        raise TransitionError(f"mission sudah {mission['status']}")
    dbmod.set_mission_status(conn, mission_id, "cancelled")
    dbmod.log_event(conn, actor, "mission_cancelled",
                    mission_id=mission_id, detail=reason)
    return "cancelled"
