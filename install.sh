#!/bin/bash
# ============================================================================
# AI Dashboard — installer satu perintah (v3)
#
# Arsitektur (dikunci):
#   1. Hermes diinstall via installer RESMI sampai tuntas:
#        curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
#      Model AI & gateway Telegram diatur lewat flow resmi Hermes sendiri
#      (`hermes setup`). Installer ini TIDAK memotong prosesnya, TIDAK
#      membuat profile manual, TIDAK mengatur model, TIDAK meminta token bot.
#   2. Profile bawaan "default" dijadikan Lead Agent: SOUL.md bawaannya
#      di-backup dulu, lalu diisi SOUL Lead Agent.
#   3. Installer ini HANYA mengurus dashboard: membaca semua profile Hermes
#      (termasuk "default" yang tinggal di HERMES_HOME itu sendiri) dan
#      mengkoneksikannya dengan dashboard secara real-time.
#
# Cara pakai:
#   git clone https://github.com/zal-m10/Ai-dashboard && cd Ai-dashboard && bash install.sh
#   curl -fsSL https://raw.githubusercontent.com/zal-m10/Ai-dashboard/main/install.sh | bash
#
# Env (opsional):
#   MC_DOMAIN   domain dashboard (ditanya bila tidak diisi)
#   MC_EMAIL    email untuk Let's Encrypt (ditanya bila tidak diisi)
# ============================================================================
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
MC_DIR="/root/.mission-control"

NON_INTERACTIVE=0
SKIP_HERMES=0
SKIP_LEADAGENT=0
SKIP_DASHBOARD=0
SKIP_NGINX=0
DASHBOARD_PASSWORD_BARU=""

usage() {
  cat <<'EOF'
AI Dashboard installer (v3)

Opsi:
  --non-interactive   jangan tanya apa-apa; wajib sediakan env:
                      MC_DOMAIN, MC_EMAIL
  --skip-hermes       lewati instalasi Hermes
  --skip-leadagent    lewati setup Lead Agent (backup+isi SOUL.md default)
  --skip-dashboard    lewati deploy dashboard
  --skip-nginx        lewati setup nginx/HTTPS
  -h, --help          tampilkan bantuan ini
EOF
}

for arg in "$@"; do
  case "$arg" in
    --non-interactive) NON_INTERACTIVE=1 ;;
    --skip-hermes) SKIP_HERMES=1 ;;
    --skip-leadagent) SKIP_LEADAGENT=1 ;;
    --skip-dashboard) SKIP_DASHBOARD=1 ;;
    --skip-nginx) SKIP_NGINX=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opsi tidak dikenal: $arg"; usage; exit 1 ;;
  esac
done

# --- helper output ---
hijau() { printf '\033[0;32m%s\033[0m\n' "$*"; }
kuning() { printf '\033[1;33m%s\033[0m\n' "$*"; }
merah() { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
info()  { printf '\n== %s ==\n' "$*"; }
die()   { merah "GAGAL: $*"; exit 1; }

# Minta input: pakai env bila ada, tanya bila interaktif, gagal bila non-interaktif.
# $1=nama env, $2=prompt, $3=default (opsional), $4=rahasia(1/0)
minta() {
  local env_name="$1" prompt="$2" def="${3:-}" rahasia="${4:-0}" val=""
  val="${!env_name:-}"
  if [ -n "$val" ]; then printf '%s' "$val"; return 0; fi
  if [ "$NON_INTERACTIVE" = "1" ]; then die "$env_name wajib diisi (mode non-interaktif)"; fi
  if [ ! -t 0 ]; then die "$env_name wajib diisi via env (tidak ada terminal)"; fi
  if [ "$rahasia" = "1" ]; then
    read -rsp "$prompt: " val; echo
  elif [ -n "$def" ]; then
    read -rp "$prompt [$def]: " val; val="${val:-$def}"
  else
    read -rp "$prompt: " val
  fi
  [ -n "$val" ] || die "$env_name tidak boleh kosong"
  printf '%s' "$val"
}

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export HERMES_HOME

# ============================================================================
# Tahap 0 — prasyarat
# ============================================================================
tahap0_prasyarat() {
  info "Tahap 0/4 — cek prasyarat"
  [ "$(id -u)" = "0" ] || die "jalankan sebagai root (pakai sudo)"
  # shellcheck disable=SC1091
  . /etc/os-release 2>/dev/null || die "/etc/os-release tidak ditemukan"
  case "${ID:-}" in
    ubuntu|debian) hijau "OS: $PRETTY_NAME" ;;
    *) die "OS tidak didukung: ${PRETTY_NAME:-?} (butuh Ubuntu/Debian)" ;;
  esac
  local ram_kb
  ram_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo)"
  if [ "$ram_kb" -lt 900000 ]; then
    kuning "peringatan: RAM ${ram_kb}kB (< ~900MB), instalasi mungkin lambat"
  else
    hijau "RAM: $((ram_kb / 1024)) MB"
  fi
  for cmd in python3 curl; do
    command -v "$cmd" >/dev/null || {
      kuning "$cmd belum ada, install via apt..."
      apt-get update -qq && apt-get install -y -qq "$cmd" || die "gagal install $cmd"
    }
  done
  mkdir -p "$HERMES_HOME" "$MC_DIR"
  chmod 700 "$HERMES_HOME" "$MC_DIR"
  hijau "prasyarat OK (HERMES_HOME=$HERMES_HOME)"
}

# ============================================================================
# Tahap 1 — Hermes via installer resmi (sampai tuntas, tanpa dipotong)
# ============================================================================
tahap1_hermes() {
  info "Tahap 1/4 — install Hermes (installer resmi)"
  if [ -x "$HOME/.local/bin/hermes" ] && "$HOME/.local/bin/hermes" --version >/dev/null 2>&1; then
    hijau "Hermes sudah terinstall, lewati"
    hermes --version 2>/dev/null | head -1
  else
    # Bersihkan sisa proses Hermes dari install yang terputus (mis. DC di
    # tengah jalan): proses 'gateway run'/'setup' yang menggantung mengunci
    # update dan membuat installer resmi gagal ("an update is still running").
    # Aman: binary hermes belum jalan, jadi tidak ada instalasi sehat yang
    # terganggu di sini.
    pkill -9 -f "hermes-agen[t]" 2>/dev/null || true
    sleep 1
    kuning "jalankan installer resmi Hermes (bisa beberapa menit)..."
    # Dijalankan LANGSUNG di foreground, output apa adanya ke terminal.
    # TUI interaktif resmi Hermes (menu pilihan setup dkk) butuh terminal
    # asli — JANGAN dialihkan ke background/log, itu merusak tampilannya.
    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash \
      || die "installer resmi Hermes gagal"
    "$HOME/.local/bin/hermes" --version >/dev/null 2>&1 \
      || die "binary hermes tidak jalan setelah install"
    hijau "Hermes terinstall: $(hermes --version 2>/dev/null | head -1)"
  fi

  # wrapper agar cukup ketik: hermes (dari shell mana pun).
  # Dibuat SELALU (bukan cuma saat install) — dashboard memanggil `hermes`
  # via subprocess dan /root/.local/bin tidak ada di PATH systemd.
  cat > /usr/local/bin/hermes <<'EOF'
#!/bin/bash
export HERMES_HOME=/root/.hermes
export PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
exec /root/.local/bin/hermes "$@"
EOF
  chmod +x /usr/local/bin/hermes

  # pastikan profile bawaan "default" terdaftar
  if hermes profile list 2>/dev/null | grep -q "default"; then
    hijau "profile bawaan 'default' terdeteksi"
  else
    die "profile 'default' tidak ditemukan setelah install Hermes"
  fi

  # info gateway (domain-nya Hermes, bukan installer ini)
  if hermes gateway list 2>/dev/null | grep -qi "running"; then
    hijau "gateway Hermes sudah running"
  else
    kuning "gateway belum running — atur via flow resmi Hermes: hermes setup"
    kuning "(dashboard tetap bisa dipakai; notifikasi Telegram aktif setelah gateway jalan)"
  fi
}

# ============================================================================
# Tahap 2 — Lead Agent = profile bawaan "default"
# ============================================================================
tahap2_leadagent() {
  info "Tahap 2/4 — Lead Agent (profile 'default')"
  local soul="$HERMES_HOME/SOUL.md"

  # 1. backup SOUL.md bawaan — WAJIB sebelum diisi ulang
  if [ -f "$soul" ]; then
    local bakdir="$HERMES_HOME/backups"
    mkdir -p "$bakdir"
    local bak="$bakdir/SOUL.md.bawaan-$(date +%Y%m%d-%H%M%S)"
    # jangan backup dua kali bila isinya sudah SOUL Lead Agent kita
    if ! grep -q "Lead Agent — AI Dashboard" "$soul" 2>/dev/null; then
      cp -a "$soul" "$bak"
      hijau "SOUL.md bawaan di-backup ke $bak"
    else
      hijau "SOUL.md sudah berisi SOUL Lead Agent, lewati penulisan"
      printf 'default\n' > "$MC_DIR/lead-agent"
      return 0
    fi
  fi

  # 2. isi SOUL Lead Agent ke profile default
  cat > "$soul" <<'EOF'
# Lead Agent — AI Dashboard

Kamu adalah Lead Agent. Kamu berbicara langsung dengan pemilik (user) dalam
Bahasa Indonesia yang santai dan jelas.

Tugasmu:
- Memahami keinginan bisnis user, bertanya bila kurang jelas.
- Membuat ringkasan misi (brief) yang siap dieksekusi.
- Melaporkan progres dengan bahasa bisnis, bukan bahasa teknis.

Kamu tidak menulis kode sendiri — kamu mendelegasikan ke Lead Engineer
dan profile spesialis.

SOUL.md bawaan Hermes di-backup di direktori backups/ sebelum file ini ditulis.
EOF
  hijau "SOUL.md Lead Agent ditulis ke profile 'default'"

  # 3. penanda untuk dashboard: profile "default" adalah Lead Agent
  printf 'default\n' > "$MC_DIR/lead-agent"
  chmod 600 "$MC_DIR/lead-agent"

  # 4. verifikasi ringan (tidak fatal — model diatur via `hermes setup`)
  kuning "verifikasi: coba sapa profile default..."
  local jawab=""
  jawab="$(timeout 180 hermes -p default chat -q "Balas tepat: DASHBOARD-OK" 2>/dev/null | tr -d '\r\n\t ' || true)"
  if [ "$jawab" = "DASHBOARD-OK" ]; then
    hijau "profile default menjawab: OK"
  else
    kuning "profile default belum menjawab — kemungkinan model/API key belum dikonfigurasi."
    kuning "jalankan: hermes setup  (flow resmi Hermes untuk model & gateway)"
  fi
}

# ============================================================================
# Tahap 3 — dashboard
# ============================================================================
tahap3_dashboard() {
  info "Tahap 3/4 — dashboard"
  local src="${DASHBOARD_SRC:-}"
  if [ -z "$src" ]; then
    local url="${DASHBOARD_TARBALL:-https://github.com/zal-m10/Ai-dashboard/archive/refs/heads/main.tar.gz}"
    kuning "download dashboard dari $url ..."
    local tgz=/tmp/aidashboard-web.tgz
    curl -fsSL --max-time 180 -o "$tgz" "$url" || die "gagal download dashboard"
    rm -rf /tmp/aidashboard-web; mkdir -p /tmp/aidashboard-web
    tar xzf "$tgz" -C /tmp/aidashboard-web || die "gagal ekstrak dashboard"
    src="$(find /tmp/aidashboard-web -maxdepth 3 -name mc -type d -path "*ai-dashboard*" | head -1)"
    src="$(dirname "$src")"
    [ -d "$src/mc" ] || die "arsip tidak berisi ai-dashboard/mc"
    rm -f "$tgz"
  fi
  [ -d "$src/mc" ] || die "sumber dashboard tidak valid: $src (butuh DASHBOARD_SRC atau DASHBOARD_TARBALL)"
  mkdir -p "$HERMES_HOME/mission-control" "$HERMES_HOME/status"
  cp -r "$src/mc" "$HERMES_HOME/mission-control/"
  hijau "kode dashboard tersalin ke $HERMES_HOME/mission-control/"

  local passfile="$HERMES_HOME/mission-control/.dashboard-pass"
  if [ ! -f "$passfile" ]; then
    # NB: '|| true' wajib — head -c menutup pipe setelah 24 byte lalu tr mati
    # kena SIGPIPE; tanpa ini, set -e + pipefail membunuh script diam-diam
    # tepat setelah "kode dashboard tersalin" (kasus 5 Okt 2026).
    < /dev/urandom tr -dc 'A-Za-z0-9' | head -c 24 > "$passfile" || true
    chmod 600 "$passfile"
    DASHBOARD_PASSWORD_BARU="$(cat "$passfile")"
    kuning "password dashboard dibuat (ditampilkan sekali di ringkasan akhir)"
  else
    hijau "password dashboard sudah ada, lewati"
  fi

  cat > /etc/systemd/system/mc-dashboard.service <<'EOF'
[Unit]
Description=AI Dashboard
After=network.target
[Service]
Type=simple
User=root
Environment=HERMES_HOME=/root/.hermes
Environment=MC_DB=/root/.hermes/mission-control.db
Environment=MC_PASSWORD_FILE=/root/.hermes/mission-control/.dashboard-pass
Environment=MC_STATUS_DIR=/root/.hermes/status
Environment=MC_BIND=127.0.0.1
Environment=MC_PORT=8090
ExecStart=/usr/bin/python3 /root/.hermes/mission-control/mc/web/server.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now mc-dashboard
  sleep 3
  systemctl is-active --quiet mc-dashboard || die "mc-dashboard gagal start"
  curl -sf --max-time 10 http://127.0.0.1:8090/ -o /dev/null \
    || die "dashboard tidak merespon di 127.0.0.1:8090"
  hijau "dashboard jalan di 127.0.0.1:8090"
}

# ============================================================================
# Tahap 4 — nginx + domain + sertifikat HTTPS
# ============================================================================
tahap4_nginx() {
  info "Tahap 4/4 — nginx + HTTPS"
  local domain email
  domain="$(minta MC_DOMAIN "Domain dashboard (contoh: ai-dashboard.contoh.id)")"
  kuning "pastikan DNS A record $domain sudah mengarah ke IP server ini"

  command -v nginx >/dev/null || { apt-get update -qq && apt-get install -y -qq nginx; }
  command -v certbot >/dev/null || { apt-get install -y -qq certbot python3-certbot-nginx; }

  local vhost=/etc/nginx/sites-available/ai-dashboard
  if grep -q "server_name $domain" "$vhost" 2>/dev/null && grep -q "listen 443 ssl" "$vhost" 2>/dev/null; then
    hijau "vhost HTTPS untuk $domain sudah ada, lewati"
  else
    cat > "$vhost" <<EOF
server {
    listen 80;
    server_name $domain;
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    ln -sf "$vhost" /etc/nginx/sites-enabled/ai-dashboard
    nginx -t || die "konfigurasi nginx tidak valid"
    systemctl reload nginx 2>/dev/null || systemctl restart nginx

    if [ -f "/etc/letsencrypt/live/$domain/fullchain.pem" ]; then
      hijau "sertifikat untuk $domain sudah ada, lewati"
    else
      email="$(minta MC_EMAIL "Email untuk Let's Encrypt")"
      certbot --nginx -d "$domain" --non-interactive --agree-tos -m "$email" \
        --redirect || die "certbot gagal — pastikan DNS sudah pointing dan port 80 terbuka"
      hijau "sertifikat HTTPS terpasang"
    fi
  fi
  sleep 2
  local https_code
  https_code="$(curl -sk --noproxy "*" --max-time 15 -o /dev/null -w "%{http_code}" \
    https://127.0.0.1/ -H "Host: $domain" 2>/dev/null || true)"
  if [ "$https_code" = "200" ]; then
    hijau "HTTPS lokal OK — publik: https://$domain (pastikan DNS A record mengarah ke IP server)"
  else
    kuning "HTTPS lokal belum merespon (kode: $https_code) — cek nginx bila perlu"
  fi
  printf '%s' "$domain" > "$MC_DIR/domain"
}

# ============================================================================
# main
# ============================================================================
main() {
  echo "AI Dashboard installer v3 (Hermes resmi + dashboard)"
  echo "HERMES_HOME=$HERMES_HOME"
  [ "$SKIP_HERMES"    = "0" ] && tahap0_prasyarat && tahap1_hermes
  [ "$SKIP_LEADAGENT" = "0" ] && tahap2_leadagent
  [ "$SKIP_DASHBOARD" = "0" ] && tahap3_dashboard
  [ "$SKIP_NGINX"     = "0" ] && tahap4_nginx

  echo
  hijau "INSTALASI SELESAI"
  echo "  hermes    : $(hermes --version 2>/dev/null | head -1)"
  echo "  profile   : $(hermes profile list 2>/dev/null | grep -c . || true) profile (termasuk 'default')"
  echo "  lead agent: $(cat "$MC_DIR/lead-agent" 2>/dev/null || echo '?')"
  echo "  gateway   : $(hermes gateway list 2>/dev/null | grep -m1 -i "running" || echo 'belum running — jalankan: hermes setup')"
  if [ -f "$MC_DIR/domain" ]; then
    echo "  dashboard : https://$(cat "$MC_DIR/domain")"
  fi
  if [ -n "$DASHBOARD_PASSWORD_BARU" ]; then
    echo "  password  : $DASHBOARD_PASSWORD_BARU  (CATAT — hanya ditampilkan sekali)"
  fi
  echo "  chat CLI  : hermes -p default chat"
  echo
  kuning "Langkah berikutnya (flow resmi Hermes):"
  echo "  1. hermes setup   (atur model AI & gateway Telegram bila belum)"
  echo "  2. Buat profile spesialis (Lead Engineer, Frontend/Backend Dev) dari dashboard: Profile"
}

main "$@"
