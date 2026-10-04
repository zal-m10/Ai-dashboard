/* AI Dashboard v2 — vanilla JS, tanpa emoji */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------- API ---------- */
async function api(method, path, body) {
  const opt = { method, credentials: "same-origin",
                headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  if (r.status === 401) { showLogin(); throw new Error("unauthorized"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
  return data;
}
const GET = (p) => api("GET", p);
const POST = (p, b) => api("POST", p, b);
const PUT = (p, b) => api("PUT", p, b);
const DEL = (p) => api("DELETE", p);

/* ---------- Login ---------- */
function showLogin() {
  $("login").classList.remove("hidden");
  $("app").classList.add("hidden");
}
async function doLogin() {
  const pass = $("login-pass").value.trim();
  $("login-err").textContent = "";
  try {
    const r = await fetch("/api/login", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pass }), credentials: "same-origin" });
    if (!r.ok) throw new Error("kata sandi salah");
    $("login").classList.add("hidden");
    $("app").classList.remove("hidden");
    $("login-pass").value = "";
    await boot();
  } catch (e) { $("login-err").textContent = e.message; }
}
$("login-btn").onclick = doLogin;
$("login-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
$("logout-btn").onclick = async () => { await POST("/api/logout"); showLogin(); };
$("logout-btn-m").onclick = async () => { await POST("/api/logout"); showLogin(); };
function closeMenu() { $("sidebar").classList.remove("open"); $("backdrop").classList.remove("show"); }
$("menu-btn").onclick = () => { $("sidebar").classList.add("open"); $("backdrop").classList.add("show"); };
$("backdrop").onclick = closeMenu;
$("chat-back").onclick = () => document.body.classList.remove("chat-open");

/* ---------- Navigasi ---------- */
let profiles = [];
document.querySelectorAll(".nav-btn[data-view]").forEach((b) => {
  b.onclick = () => {
    document.body.classList.remove("chat-open");
    if (window.innerWidth <= 860) closeMenu();
    document.querySelectorAll(".nav-btn[data-view]").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    $("view-" + b.dataset.view).classList.remove("hidden");
    ({ chat: loadThreads, misi: loadMissions, profile: loadProfilesView,
       notif: loadNotif, biaya: loadUsage })[b.dataset.view]();
  };
});

/* ---------- Modal ---------- */
let modalOk = null;
function openModal(title, bodyHTML, onOk, okLabel) {
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = bodyHTML;
  $("modal-ok").textContent = okLabel || "Simpan";
  modalOk = onOk;
  $("modal").classList.remove("hidden");
}
$("modal-cancel").onclick = () => $("modal").classList.add("hidden");
$("modal-ok").onclick = async () => {
  if (modalOk) await modalOk();
  $("modal").classList.add("hidden");
};
const field = (id) => document.querySelector("#modal-body #" + id).value;

/* ================================================================
   CHAT
================================================================ */
let curThread = null, pollTimer = null, lastMsgJSON = "";

async function loadProfiles() {
  const d = await GET("/api/profiles");
  profiles = d.profiles;
  const lead = profiles.find((p) => p.is_lead_agent);
  const sel = $("target-select");
  sel.innerHTML = "";
  const mk = (v, t) => { const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o); };
  mk(lead ? lead.name : "default", "Lead Agent" + (lead ? " (" + lead.name + ")" : ""));
  profiles.filter((p) => !p.is_lead_agent).forEach((p) => mk(p.name, p.name + " — " + (p.role || "specialist")));
  // kotak multi
  const box = $("multi-box");
  box.innerHTML = "";
  profiles.forEach((p) => {
    const l = document.createElement("label");
    const c = document.createElement("input");
    c.type = "checkbox"; c.value = p.name;
    c.onchange = syncMulti;
    l.appendChild(c); l.appendChild(document.createTextNode(" " + p.name));
    box.appendChild(l);
  });
}
function syncMulti() {
  const checked = [...$("multi-box").querySelectorAll("input:checked")].map((c) => c.value);
  if (checked.length) $("target-select").value = "";
}
$("multi-btn").onclick = () => {
  const box = $("multi-box");
  box.classList.toggle("hidden");
  if (box.classList.contains("hidden")) [...box.querySelectorAll("input")].forEach((c) => c.checked = false);
};
$("target-select").onchange = () => {
  [...$("multi-box").querySelectorAll("input")].forEach((c) => c.checked = false);
  $("multi-box").classList.add("hidden");
};
function currentTarget() {
  const checked = [...$("multi-box").querySelectorAll("input:checked")].map((c) => c.value);
  if (checked.length) return "multi:" + checked.join(",");
  return $("target-select").value || "default";
}

async function loadThreads() {
  await loadProfiles();
  const d = await GET("/api/chat/threads");
  const box = $("threads");
  box.innerHTML = "";
  d.threads.forEach((t) => {
    const el = document.createElement("div");
    el.className = "thread-item" + (curThread === t.id ? " active" : "");
    el.innerHTML = `<div class="t-title"></div><div class="t-sub"></div>`;
    el.querySelector(".t-title").textContent = t.title || t.target;
    el.querySelector(".t-sub").textContent = (t.last_msg || "belum ada pesan").slice(0, 60);
    el.onclick = () => openThread(t.id);
    box.appendChild(el);
  });
  if (curThread) openThread(curThread, true);
}

async function openThread(tid, soft) {
  curThread = tid;
  if (!soft) document.body.classList.add("chat-open");
  document.querySelectorAll(".thread-item").forEach((el) => el.classList.remove("active"));
  await renderMessages();
  if (!soft) loadThreads();
  clearInterval(pollTimer);
  pollTimer = setInterval(renderMessages, 3000);
}

async function renderMessages() {
  if (!curThread) { $("messages").innerHTML = ""; return; }
  const d = await GET("/api/chat/threads/" + curThread + "/messages").catch(() => null);
  if (!d) return;
  const j = JSON.stringify(d.messages);
  if (j === lastMsgJSON) return;
  lastMsgJSON = j;
  const box = $("messages");
  box.innerHTML = "";
  d.messages.forEach((m) => {
    const el = document.createElement("div");
    const isUser = m.role === "user";
    el.className = "msg " + (isUser ? "user" : "agent") +
      (m.status === "sending" ? " sending" : "") + (m.status === "error" ? " error" : "");
    if (!isUser) {
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = m.role;
      el.appendChild(who);
    }
    el.appendChild(document.createTextNode(m.status === "sending" ? "mengetik..." : m.body));
    box.appendChild(el);
  });
  box.scrollTop = box.scrollHeight;
}

$("new-thread-btn").onclick = () => {
  curThread = null; lastMsgJSON = "";
  document.querySelectorAll(".thread-item").forEach((el) => el.classList.remove("active"));
  $("messages").innerHTML = "";
  clearInterval(pollTimer);
  document.body.classList.add("chat-open");
};

async function sendChat() {
  const text = $("chat-input").value.trim();
  if (!text) return;
  $("chat-input").value = "";
  const target = currentTarget();
  try {
    const r = await POST("/api/chat/send", curThread
      ? { thread_id: curThread, text }
      : { target, text });
    curThread = r.thread_id;
    lastMsgJSON = "";
    await renderMessages();
    clearInterval(pollTimer);
    pollTimer = setInterval(renderMessages, 3000);
    loadThreads();
  } catch (e) { alert("Gagal kirim: " + e.message); }
}
$("send-btn").onclick = sendChat;
$("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

$("to-mission-btn").onclick = async () => {
  if (!curThread) { alert("Buka thread dulu."); return; }
  const d = await GET("/api/chat/threads/" + curThread + "/messages");
  const first = d.messages.find((m) => m.role === "user");
  const title = (first ? first.body : "Misi dari chat").slice(0, 80);
  const brief = d.messages.map((m) => m.role + ": " + m.body).join("\n").slice(0, 4000);
  openModal("Jadikan misi", `
    <label>Judul misi<input type="text" id="f-title" value=""></label>
    <label class="check"><input type="checkbox" id="f-skip"> Lewati approval gate (langsung jalan)</label>
  `, async () => {
    const r = await POST("/api/missions", {
      title: field("f-title") || title,
      brief,
      skip_gate: document.querySelector("#modal-body #f-skip").checked,
    });
    document.querySelector('.nav-btn[data-view="misi"]').click();
    openMission(r.id);
  }, "Buat misi");
  document.querySelector("#modal-body #f-title").value = title;
};

/* ================================================================
   MISI
================================================================ */
const ST_BADGE = { draft: "", decomposed: "", awaiting_approval: "warn", in_progress: "ok",
                   review: "warn", done: "ok", paused: "", blocked: "warn", cancelled: "" };
async function loadMissions() {
  $("mission-detail").classList.add("hidden");
  const d = await GET("/api/missions");
  const box = $("mission-list");
  box.innerHTML = "";
  d.missions.forEach((m) => {
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `<h3></h3>
      <span class="badge ${ST_BADGE[m.status] || ""}"></span>
      <span class="badge">progres ${m.progress || 0}%</span>
      <div class="row"><button class="btn small">Buka</button></div>`;
    el.querySelector("h3").textContent = m.title;
    el.querySelector(".badge").textContent = m.status;
    el.querySelector("button").onclick = () => openMission(m.id);
    box.appendChild(el);
  });
  if (!d.missions.length) box.innerHTML = '<p class="muted">Belum ada misi.</p>';
}
async function openMission(mid) {
  const d = await GET("/api/missions/" + mid);
  const m = d.mission;
  $("mission-list").innerHTML = "";
  $("mission-detail").classList.remove("hidden");
  $("md-title").textContent = m.title;
  $("md-meta").textContent = `Status: ${m.status} | Progres: ${d.progress}% | Estimasi: ${m.cost_est_low ?? "-"}..${m.cost_est_high ?? "-"}`;
  const acts = $("md-actions");
  acts.innerHTML = "";
  const btn = (label, action, body) => {
    const b = document.createElement("button");
    b.className = "btn small"; b.textContent = label;
    b.onclick = async () => { await POST(`/api/missions/${mid}/action`, { action, ...(body || {}) }); openMission(mid); };
    acts.appendChild(b);
  };
  if (m.status === "draft") btn("Pecah jadi task", "to_decomposed");
  if (m.status === "decomposed") btn("Minta approval", "request_approval", { cost_low: 0.1, cost_high: 0.5 });
  if (m.status === "awaiting_approval") { btn("Approve", "approve", { envelope_low: 0.1, envelope_high: 0.5 }); btn("Revisi", "request_revision", { note: "perlu revisi" }); }
  const disp = document.createElement("button");
  disp.className = "btn small"; disp.textContent = "Dispatch sekarang";
  disp.onclick = async () => { await POST(`/api/missions/${mid}/dispatch`); openMission(mid); };
  if (m.status === "in_progress") acts.appendChild(disp);
  if (m.status === "review") btn("Tandai selesai", "mark_done");
  const box = $("md-tasks");
  box.innerHTML = "";
  (d.tasks || []).forEach((t) => {
    const el = document.createElement("div");
    el.className = "task";
    el.innerHTML = `<b></b> <span class="badge ${ST_BADGE[t.status] || ""}"></span><div class="muted small"></div>`;
    el.querySelector("b").textContent = t.title;
    el.querySelector(".badge").textContent = t.status;
    el.querySelector(".muted").textContent = (t.assignee || "") + (t.result_summary ? " — " + t.result_summary.slice(0, 120) : "");
    box.appendChild(el);
  });
  if (!(d.tasks || []).length) box.innerHTML = '<p class="muted">Belum ada task.</p>';
}
$("mission-back").onclick = loadMissions;
$("new-mission-btn").onclick = () => {
  openModal("Misi baru", `
    <label>Judul<input type="text" id="f-title"></label>
    <label>Brief (opsional)<textarea id="f-brief"></textarea></label>
    <label class="check"><input type="checkbox" id="f-skip"> Lewati approval gate</label>
  `, async () => {
    const r = await POST("/api/missions", { title: field("f-title"),
      brief: field("f-brief"), skip_gate: document.querySelector("#modal-body #f-skip").checked });
    loadMissions(); openMission(r.id);
  }, "Buat");
};

/* ================================================================
   PROFILE
================================================================ */
async function loadProfilesView() {
  await loadProfiles();
  const box = $("profile-list");
  box.innerHTML = "";
  profiles.forEach((p) => {
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `<h3></h3>
      <span class="badge ${p.is_lead_agent ? "lead" : ""}"></span>
      <span class="badge"></span><br>
      <span class="muted small"></span>
      <div class="row"></div>`;
    el.querySelector("h3").textContent = p.name;
    const badges = el.querySelectorAll(".badge");
    badges[0].textContent = p.is_lead_agent ? "Lead Agent" : p.role;
    badges[1].textContent = p.model || "model belum diset";
    el.querySelector(".muted").textContent = p.has_soul ? "SOUL.md ada" : "SOUL.md kosong";
    const row = el.querySelector(".row");
    const mk = (label, fn, danger) => {
      const b = document.createElement("button");
      b.className = "btn small" + (danger ? " danger" : "");
      b.textContent = label; b.onclick = fn; row.appendChild(b);
    };
    mk("Edit SOUL", () => editSoul(p.name));
    mk("Ganti model", () => changeModel(p.name));
    if (!p.is_lead_agent) {
      mk("Jadikan Lead Agent", async () => {
        if (!confirm(`Jadikan '${p.name}' sebagai Lead Agent?`)) return;
        await POST(`/api/profiles/${p.name}/lead-agent`);
        loadProfilesView();
      });
      mk("Hapus", async () => {
        const sure = prompt(`Ketik nama profile untuk hapus: ${p.name}`);
        if (sure !== p.name) return;
        await DEL("/api/profiles/" + p.name);
        loadProfilesView();
      }, true);
    }
    box.appendChild(el);
  });
}
$("new-profile-btn").onclick = () => {
  openModal("Tambah profile", `
    <label>Nama (huruf kecil, angka, strip)<input type="text" id="f-name" placeholder="marketing"></label>
    <label>Deskripsi<input type="text" id="f-desc"></label>
    <label>Role<input type="text" id="f-role" value="specialist"></label>
    <label>Model<input type="text" id="f-model" placeholder="Razix-Free"></label>
    <label>SOUL.md<textarea id="f-soul" placeholder="# ..."></textarea></label>
  `, async () => {
    await POST("/api/profiles", { name: field("f-name"), description: field("f-desc"),
      role: field("f-role"), model: field("f-model"), soul: field("f-soul") });
    loadProfilesView();
  }, "Buat profile");
};
async function editSoul(name) {
  const d = await GET(`/api/profiles/${name}/soul`);
  openModal("SOUL.md — " + name, `<label><textarea id="f-soul" style="min-height:260px"></textarea></label>`,
    async () => { await PUT(`/api/profiles/${name}/soul`, { soul: field("f-soul") }); },
    "Simpan SOUL");
  document.querySelector("#modal-body #f-soul").value = d.soul;
}
async function changeModel(name) {
  openModal("Ganti model — " + name, `<label>Model<input type="text" id="f-model"></label>`,
    async () => { await PUT(`/api/profiles/${name}/model`, { model: field("f-model") }); loadProfilesView(); });
}

/* ================================================================
   NOTIFIKASI
================================================================ */
async function loadNotif() {
  const d = await GET("/api/notifications");
  $("notif-gateway").value = d.gateway || "telegram";
  $("notif-enabled").checked = !!d.enabled;
  $("notif-gate").checked = !!d.on_gate;
  $("notif-done").checked = !!d.on_done;
  $("notif-error").checked = !!d.on_error;
  $("notif-msg").textContent = "";
}
$("notif-save").onclick = async () => {
  await PUT("/api/notifications", { gateway: $("notif-gateway").value,
    enabled: $("notif-enabled").checked, on_gate: $("notif-gate").checked,
    on_done: $("notif-done").checked, on_error: $("notif-error").checked });
  $("notif-msg").textContent = "Tersimpan.";
};
$("notif-test").onclick = async () => {
  const d = await POST("/api/notifications/test");
  $("notif-msg").textContent = d.note || "OK";
};

/* ================================================================
   BIAYA
================================================================ */
function fmtNum(n) { return (n || 0).toLocaleString("id-ID"); }
async function loadUsage() {
  const days = $("usage-days").value;
  $("usage-summary").innerHTML = '<p class="muted">Memuat...</p>';
  $("usage-table").innerHTML = "";
  const d = await GET("/api/usage?days=" + days);
  $("usage-summary").innerHTML = `
    <div class="stat"><div class="v">${fmtNum(d.total_tokens)}</div><div class="l">Total token</div></div>
    <div class="stat"><div class="v">${fmtNum(d.input_tokens)}</div><div class="l">Input</div></div>
    <div class="stat"><div class="v">${fmtNum(d.output_tokens)}</div><div class="l">Output</div></div>
    <div class="stat"><div class="v">${fmtNum(d.sessions)}</div><div class="l">Sesi</div></div>`;
  let html = `<table class="data"><tr><th>Profile</th><th>Total token</th><th>Input</th><th>Output</th><th>Sesi</th></tr>`;
  d.profiles.forEach((p) => {
    html += `<tr><td>${p.profile}</td><td>${fmtNum(p.total_tokens)}</td><td>${fmtNum(p.input_tokens)}</td><td>${fmtNum(p.output_tokens)}</td><td>${fmtNum(p.sessions)}</td></tr>`;
  });
  $("usage-table").innerHTML = html + "</table>";
}
$("usage-days").onchange = loadUsage;

/* ---------- boot ---------- */
async function boot() {
  await loadThreads();
}
showLogin();
