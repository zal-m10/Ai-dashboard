# AI Dashboard

Dashboard misi untuk mengelola profile-profile [Hermes](https://github.com/imkofty/Hermuse):
Lead Agent → Lead Engineer → profile spesialis (frontend, backend, dst.), dengan
satu approval gate, chat multi-target, dan notifikasi via Telegram.

* Bahasa UI: Indonesia · tanpa emoji
* Gateway: Telegram (multiplexer Hermes)
* Tanpa Docker — semua jalan native sebagai systemd service

## Instalasi satu perintah

Jalankan di VPS kosong (Ubuntu 22.04/24.04, akses root):

```bash
sudo apt-get update -y && sudo apt-get install -y git && \
git clone https://github.com/zal-m10/Ai-dashboard && \
cd Ai-dashboard && bash install.sh
```

Atau langsung via curl:

```bash
curl -fsSL https://raw.githubusercontent.com/zal-m10/Ai-dashboard/main/install.sh | bash
```

Installer bersifat **idempotent** (aman dijalankan ulang) dan akan memandu
pengisian:

| Yang ditanya | Keterangan |
|---|---|
| Token bot Telegram | Untuk gateway Hermes |
| Base URL + API key provider AI | Contoh: 9Router (`https://9router-muse.free-account.my.id/v1`), atau endpoint OpenAI-compatible lain |
| Domain dashboard | Contoh: `ai-dashboard.contoh.id` (arahkan DNS ke IP VPS dulu) |
| Email | Untuk sertifikat HTTPS Let's Encrypt |

Yang dikerjakan installer (6 tahap):

1. Cek prasyarat (OS, RAM, tool dasar)
2. Install Hermes native + setup Lead Agent
3. Install & jalankan Telegram gateway (systemd)
4. Setup provider/model AI
5. Buat profile awal (lead-agent, lead-engineer, frontend-dev, backend-dev) + deploy dashboard (systemd, `127.0.0.1:8090`)
6. Setup nginx + HTTPS (sertifikat Let's Encrypt otomatis)

Di akhir instalasi, password dashboard ditampilkan **sekali** — simpan baik-baik.

### Opsi installer

```bash
bash install.sh --non-interactive   # butuh env: TELEGRAM_BOT_TOKEN, NINEROUTER_API_KEY
bash install.sh --skip-nginx        # lewati nginx/HTTPS (mis. di balik tunnel)
bash install.sh --help              # semua opsi --skip-*
```

## Struktur repo

```
install.sh            installer satu perintah (idempotent)
ai-dashboard/
  mc/                 kode dashboard (backend Python + frontend vanilla JS)
    web/server.py     HTTP server (login, API chat/misi/profile/notif/biaya)
    web/static/       UI: index.html, style.css, app.js
    db.py             SQLite (chat, misi, profile meta, notifikasi)
    dispatch.py       orkestrasi misi + approval gate
    profiles.py       CRUD profile Hermes asli (bukan mock)
  tests/              skrip verifikasi
```

## Fitur

- **Chat** — pilih tujuan: Lead Agent (default), satu profile langsung, atau
  multi-profile; riwayat tersimpan di SQLite; tombol "Jadikan misi"
- **Misi** — board misi + task, satu approval gate sebelum eksekusi
- **Profile** — tambah/hapus profile Hermes asli, tunjuk Lead Agent, edit
  `SOUL.md`, ganti model
- **Notifikasi** — on/off event via gateway Telegram (approval, selesai, error)
- **Biaya** — agregat pemakaian token per profile (`hermes insights`)

## Catatan

- Profile tersimpan di `~/.hermes` (persisten), dashboard di
  `/root/.hermes/mission-control`, database di `/root/.hermes/mission-control.db`
- Jangan jalankan dua poller Telegram pada token bot yang sama
