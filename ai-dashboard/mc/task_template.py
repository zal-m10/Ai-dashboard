"""Render Task Template (§5 spec) menjadi prompt self-contained untuk profile."""
import json


def _fmt_contract(raw):
    if not raw:
        return "- (tidak ada contract khusus)"
    try:
        c = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return str(raw)
    lines = []
    for inp in c.get("inputs", []) or []:
        lines.append(f"- INPUT: {inp}")
    for out in c.get("outputs", []) or []:
        lines.append(f"- OUTPUT: {out}")
    return "\n".join(lines) if lines else "- (contract kosong)"


def _fmt_list(raw):
    if not raw:
        return "- (tidak ada)"
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return str(raw)
    if not items:
        return "- (tidak ada)"
    return "\n".join(f"- {it}" for it in items)


def render(task, status_dir="/root/.hermes/status"):
    """task: dict dari db.get_task(). Menghasilkan prompt teks lengkap."""
    deps = task.get("depends_on") or "[]"
    try:
        deps_list = json.loads(deps) if isinstance(deps, str) else deps
    except (ValueError, TypeError):
        deps_list = []
    deps_str = ", ".join(deps_list) if deps_list else "tidak ada"

    return f"""# TASK {task['id']}
Judul: {task['title']}
Assignee: {task['assignee']} | Mission: {task['mission_id']}

## Konteks mission
{task.get('mission_context') or '(tidak ada)'}

## Instruksi
{task['description']}

## Interface contract
{_fmt_contract(task.get('interface_contract'))}

## Acceptance criteria (wajib terpenuhi semua)
{_fmt_list(task.get('acceptance_criteria'))}

## Output
Tulis hasil/artefak ke: {task.get('output_path') or '(tidak ditentukan)'}
Estimasi biaya task ini: {task.get('cost_est_low')}..{task.get('cost_est_high')}

## Dependensi
Task prasyarat: {deps_str}

## Pelaporan status (WAJIB)
Selama dan setelah mengerjakan, tulis file status JSON ke:
{status_dir}/{task['id']}.status.json
dengan format:
{{"task_id": "{task['id']}", "state": "running|done|failed|blocked",
  "tail_log": "ringkasan 5 baris terakhir", "artifacts": ["path1", "path2"],
  "blocker": "isi bila blocked, selain itu null"}}

Kerjakan sekarang. Jangan bertanya klarifikasi kecuali benar-benar blocker —
bila blocker, tulis state "blocked" di file status beserta alasannya.
"""
