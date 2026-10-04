# Mission Control Dashboard — API contract (Fase 3)

Base: sama dengan origin server. Semua response JSON.
Auth: cookie `mc_session` (HttpOnly, SameSite=Lax). Semua endpoint `/api/*`
kecuali `POST /api/login` butuh cookie valid → 401 `{error:"unauthorized"}` bila tidak ada.

## Auth
- `POST /api/login` body `{password}` → `200 {ok:true}` + Set-Cookie; salah → `401 {error:"wrong password"}`
- `POST /api/logout` → `200 {ok:true}` (cookie dihapus)

## Missions
- `GET /api/missions` → `{missions:[{id,title,status,revision_round,progress,cost_est_low,cost_est_high,updated_at}]}`  
  `progress` = persen task done (0..100).
- `POST /api/missions` body `{id? (default acak), title, brief?, skip_gate?}` → `201 {id}`
- `GET /api/missions/{id}` → `{mission:{...}, tasks:[{id,title,description,assignee,status,attempt,cost_est_low,cost_est_high,depends_on[],result_summary,updated_at}], approvals:[{round,decision,note,decided_by,envelope_low,envelope_high,decided_at}], events:[{actor,action,detail,created_at}] (50 terakhir, terbaru dulu), progress}`

## Mission actions (gate + kontrol)
- `POST /api/missions/{id}/action` body `{action, ...params}` → `200 {status}` (status baru) atau `400 {error}` bila transisi ilegal.
  - `{action:"to_decomposed"}` — draft → decomposed
  - `{action:"request_approval", cost_low, cost_high}` — decomposed → awaiting_approval (atau in_progress bila skip_gate)
  - `{action:"approve", envelope_low, envelope_high}` — awaiting_approval → in_progress
  - `{action:"request_revision", note}` — awaiting_approval → decomposed (maks 3x)
  - `{action:"pause"}` / `{action:"resume"}` / `{action:"unblock"}` — in_progress ⇄ paused/blocked
  - `{action:"to_review"}` — in_progress → review
  - `{action:"mark_done"}` — review → done
  - `{action:"cancel", reason?}` — any → cancelled

## Tasks
- `POST /api/missions/{id}/tasks` body `{id?, title, description, assignee, mission_context?, interface_contract? (obj), acceptance_criteria? (array), output_path?, depends_on? (array), cost_est_low?, cost_est_high?}` → `201 {id}`
- `POST /api/missions/{id}/dispatch` → `200 {dispatched:[task_ids]}` (hanya task pending yg dependensinya done)
- `POST /api/missions/{id}/poll` → `200 {states:{task_id:state}, progress}`
- `POST /api/tasks/{task_id}/kill` body `{reason?}` → `200 {ok:true}`
- `POST /api/tasks/{task_id}/reassign` body `{assignee}` → `200 {ok:true}` (hanya bila task masih pending)

## Org / profiles
- `GET /api/profiles` → `{profiles:[{name,role,model,status,current_task}]}`

## Chat Lead Agent
- `POST /api/chat` body `{message}` → `200 {reply}` (bisa lambat ~30-60 dtk; tampilkan spinner)

## Cost
- `GET /api/missions/{id}/cost` → `{estimate:{low,high}, actual:{low,high}, by_profile:[{profile,actual_low,actual_high,task_count}]}`

## Error umum
- `400 {error:"..."}` validasi / transisi ilegal; `404 {error:"not found"}`; `500 {error:"..."}`.

## Frontend: 7 layar
1. **Mission board** — daftar mission + status + progress + tombol "Mission baru".
2. **Org view** — pohon izal → lead-agent → lead-engineer → specialists; tiap node status live (dari /api/profiles).
3. **Task drill-down** — detail mission: daftar task (status, assignee, dependensi), log events, tombol dispatch/poll.
4. **Approval screen** — saat status awaiting_approval: tampilkan plan (tasks + assignee + estimasi), tombol Approve / Revisi (prompt catatan) / Batal.
5. **Chat Lead Agent** — riwayat chat + input; POST /api/chat.
6. **Kontrol** — di halaman mission: pause/resume/dispatch/poll/kill task/reassign task/cancel mission. Aksi destruktif (kill/cancel) wajib `confirm()`.
7. **Cost panel** — di halaman mission: estimasi vs aktual + per profile.
Navigasi: sidebar (Board, Org, Chat) + klik mission → tab (Tasks, Approval, Kontrol, Cost).
Semua fetch pakai `credentials:"same-origin"`. Bila 401 → redirect ke /login.html.
Login page sederhana: POST /api/login, sukses → redirect /.
