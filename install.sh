#!/bin/bash
# ============================================================================
# AI Dashboard — installer satu perintah
# Fase A+B: prasyarat, Hermes, gateway Telegram, model AI, profile awal, dashboard, nginx+HTTPS
# Cara pakai:
#   curl -fsSL https://raw.githubusercontent.com/zal-m10/Ai-dashboard/main/install.sh | bash
#   curl -fsSL ... | bash -s -- --non-interactive   (butuh env di bawah)
#
# Env (opsional, untuk mode non-interaktif):
#   TELEGRAM_BOT_TOKEN   token bot Telegram untuk gateway
#   NINEROUTER_API_KEY   API key 9Router / provider OpenAI-compatible
#   NINEROUTER_BASE_URL  default: https://9router-muse.free-account.my.id/v1
# ============================================================================
set -euo pipefail

HERMES_VERSION="2026.9.24"
HERMES_TARBALL="https://github.com/NousResearch/hermes-agent/archive/refs/tags/v${HERMES_VERSION}.tar.gz"
HERMES_HOME="${HERMES_HOME:-/root/.hermes}"
SRC_DIR="/opt/hermes-src"
MC_DIR="/root/.mission-control"
NINEROUTER_BASE_URL="${NINEROUTER_BASE_URL:-https://9router-muse.free-account.my.id/v1}"

NON_INTERACTIVE=0
SKIP_HERMES=0
SKIP_GATEWAY=0
SKIP_MODEL=0
SKIP_PROFILES=0
SKIP_DASHBOARD=0
SKIP_NGINX=0
DASHBOARD_PASSWORD_BARU=""

usage() {
  cat <<'EOF'
AI Dashboard installer (Fase A)

Opsi:
  --non-interactive   jangan tanya apa-apa; wajib sediakan env:
                      TELEGRAM_BOT_TOKEN, NINEROUTER_API_KEY
                      (opsional: NINEROUTER_BASE_URL)
  --skip-hermes       lewati instalasi Hermes
  --skip-gateway      lewati setup gateway Telegram
  --skip-model        lewati setup model AI
  --skip-profiles     lewati pembuatan profile awal
  --skip-dashboard    lewati deploy dashboard
  --skip-nginx        lewati setup nginx/HTTPS
  -h, --help          tampilkan bantuan ini
EOF
}

for arg in "$@"; do
  case "$arg" in
    --non-interactive) NON_INTERACTIVE=1 ;;
    --skip-hermes) SKIP_HERMES=1 ;;
    --skip-gateway) SKIP_GATEWAY=1 ;;
    --skip-model) SKIP_MODEL=1 ;;
    --skip-profiles) SKIP_PROFILES=1 ;;
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
# API key untuk semua pemanggilan hermes di installer ini
if [ -f "$MC_DIR/9router-apikey" ]; then
  export HERMES_CUSTOM_9ROUTER_MUSE_FREE_ACCOUNT_MY_ID_API_KEY="$(cat "$MC_DIR/9router-apikey")"
fi

# ============================================================================
# Tahap 0 — prasyarat
# ============================================================================
tahap0_prasyarat() {
  info "Tahap 0/6 — cek prasyarat"
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
  hijau "prasyarat OK"
}

# ============================================================================
# Tahap 1 — Hermes native
# ============================================================================
tahap1_hermes() {
  info "Tahap 1/6 — install Hermes"
  local ver=""
  ver="$("$HOME/.local/bin/hermes" --version 2>/dev/null || true)"
  if [ -x "$HOME/.local/bin/hermes" ] && printf '%s' "$ver" | grep -q "0.21.5"; then
    hijau "Hermes 0.21.5 sudah terinstall, lewati"
    return 0
  fi
  kuning "download Hermes v${HERMES_VERSION}..."
  local tgz=/tmp/hermes-agent.tgz
  curl -fsSL --max-time 300 -o "$tgz" "$HERMES_TARBALL" || die "gagal download $HERMES_TARBALL"
  rm -rf "$SRC_DIR"
  mkdir -p "$SRC_DIR"
  tar xzf "$tgz" -C /tmp
  mv /tmp/hermes-agent-"${HERMES_VERSION}"/* "$SRC_DIR"/
  rm -f "$tgz"
  echo "$HERMES_VERSION" > "$SRC_DIR/.aidashboard-version"
  if ! command -v uv >/dev/null; then
    kuning "install uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "gagal install uv"
  fi
  export PATH="$HOME/.local/bin:$PATH"
  kuning "jalankan setup-hermes.sh (bisa beberapa menit)..."
  # setup-hermes.sh upstream punya 2 prompt interaktif ("install ripgrep? [Y/n]",
  # "jalankan setup wizard? [Y/n]") + set -e, sehingga </dev/null membuatnya
  # mati di tengah jalan (read kena EOF -> exit 1). Jawab "n" untuk keduanya:
  # ripgrep opsional (grep fallback cukup), wizard di-skip karena setup
  # dilakukan sendiri oleh installer di tahap berikutnya.
  (cd "$SRC_DIR" && printf 'nn\n' | bash setup-hermes.sh) || die "setup-hermes.sh gagal"
  "$HOME/.local/bin/hermes" --version || die "binary hermes tidak jalan"
  # wrapper agar cukup ketik: hermes
  cat > /usr/local/bin/hermes <<'EOF'
#!/bin/bash
export HERMES_HOME=/root/.hermes
export PATH="/root/.local/bin:/usr/local/bin:$PATH"
if [ -z "${HERMES_CUSTOM_9ROUTER_MUSE_FREE_ACCOUNT_MY_ID_API_KEY:-}" ]; then
  export HERMES_CUSTOM_9ROUTER_MUSE_FREE_ACCOUNT_MY_ID_API_KEY=$(cat /root/.mission-control/9router-apikey 2>/dev/null)
fi
exec /root/.local/bin/hermes "$@"
EOF
  chmod +x /usr/local/bin/hermes
  hijau "Hermes terinstall: $(hermes --version 2>/dev/null | head -1)"
}

# ============================================================================
# Tahap 2 — profile lead-agent + gateway Telegram
# ============================================================================
tahap2_gateway() {
  info "Tahap 2/6 — profile lead-agent + gateway Telegram"
  if [ -d "$HERMES_HOME/profiles/lead-agent" ]; then
    hijau "profile lead-agent sudah ada, lewati"
  else
    kuning "buat profile lead-agent..."
    hermes profile create lead-agent --description "Lead Agent AI Dashboard" \
      || die "gagal buat profile lead-agent"
    cat > "$HERMES_HOME/profiles/lead-agent/SOUL.md" <<'EOF'
# Lead Agent — AI Dashboard

Kamu adalah Lead Agent. Kamu berbicara langsung dengan pemilik (user) dalam
Bahasa Indonesia yang santai dan jelas.

Tugasmu:
- Memahami keinginan bisnis user, bertanya bila kurang jelas.
- Membuat ringkasan misi (brief) yang siap dieksekusi.
- Melaporkan progres dengan bahasa bisnis, bukan bahasa teknis.

Kamu tidak menulis kode sendiri — kamu mendelegasikan ke Lead Engineer
dan profile spesialis.
EOF
    hijau "profile lead-agent dibuat"
  fi

  local token=""
  local envfile="$HERMES_HOME/.env"
  touch "$envfile"; chmod 600 "$envfile"
  if grep -q "^TELEGRAM_BOT_TOKEN=" "$envfile"; then
    hijau "TELEGRAM_BOT_TOKEN sudah ada di $envfile, lewati"
  else
    token="$(minta TELEGRAM_BOT_TOKEN "Token bot Telegram" "" 1)"
    printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token" >> "$envfile"
    unset token
    hijau "token tersimpan di $envfile (mode 600)"
  fi

  local gwlist=""
  gwlist="$(hermes -p lead-agent gateway list 2>/dev/null || true)"
  if printf '%s' "$gwlist" | grep -q "lead-agent.*running"; then
    hijau "gateway lead-agent sudah running, lewati"
  else
    kuning "install + start gateway (systemd)..."
    hermes -p lead-agent gateway install --system --start-now \
      || kuning "gateway install gagal — bisa diulang manual: hermes -p lead-agent gateway install --system --start-now"
  fi
}

# ============================================================================
# Tahap 3 — model AI (9Router / OpenAI-compatible)
# ============================================================================
tahap3_model() {
  info "Tahap 3/6 — setup model AI"
  local base_url="$NINEROUTER_BASE_URL" api_key=""
  if [ -f "$MC_DIR/9router-apikey" ]; then
    api_key="$(cat "$MC_DIR/9router-apikey")"
    hijau "API key sudah tersimpan, validasi ulang..."
  else
    base_url="$(minta NINEROUTER_BASE_URL "Base URL provider" "$NINEROUTER_BASE_URL" 0)"
    api_key="$(minta NINEROUTER_API_KEY "API key provider" "" 1)"
  fi

  kuning "validasi API key ke $base_url/models ..."
  local http
  http="$(curl -s --max-time 20 -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $api_key" "$base_url/models")" \
    || die "tidak bisa mencapai $base_url"
  [ "$http" = "200" ] || die "API key ditolak (HTTP $http), periksa kembali"

  printf '%s' "$api_key" > "$MC_DIR/9router-apikey"
  chmod 600 "$MC_DIR/9router-apikey"
  cat > "$MC_DIR/hermes-env" <<EOF
HERMES_CUSTOM_9ROUTER_MUSE_FREE_ACCOUNT_MY_ID_API_KEY=$api_key
EOF
  chmod 600 "$MC_DIR/hermes-env"
  unset api_key
  hijau "API key valid dan tersimpan (mode 600)"

  # config provider per profile lead-agent (format yang terbukti jalan)
  local cfg="$HERMES_HOME/profiles/lead-agent/config.yaml"
  if grep -q "9router-muse" "$cfg" 2>/dev/null; then
    hijau "config model lead-agent sudah ada, lewati"
  else
    cat > "$cfg" <<EOF
model:
  default: Razix-PowerFull
  provider: custom
  base_url: $base_url
api_key: <redacted>
custom_providers:
  - name: RAZIX
    base_url: $base_url
    key_env: HERMES_CUSTOM_9ROUTER_MUSE_FREE_ACCOUNT_MY_ID_API_KEY
    model: Razix-PowerFull
_config_version: 46
EOF
    hijau "config model lead-agent ditulis"
  fi
}

# ============================================================================
# main
# ============================================================================
main() {
  echo "AI Dashboard installer — Fase A+B (Hermes + gateway + model + profile + dashboard + HTTPS)"
  echo "HERMES_HOME=$HERMES_HOME"
  [ "$SKIP_HERMES"    = "0" ] && tahap0_prasyarat && tahap1_hermes
  [ "$SKIP_GATEWAY"   = "0" ] && tahap2_gateway
  [ "$SKIP_MODEL"     = "0" ] && tahap3_model

  # refresh API key bila baru ditulis tahap 3
  if [ -f "$MC_DIR/9router-apikey" ]; then
    export HERMES_CUSTOM_9ROUTER_MUSE_FREE_ACCOUNT_MY_ID_API_KEY="$(cat "$MC_DIR/9router-apikey")"
  fi

  info "Verifikasi inti"
  local i jawab ok=0
  for i in 1 2 3; do
    jawab="$(hermes -p lead-agent chat -q "Balas tepat: FASE-A-OK" --format stream-json 2>/dev/null \
      | python3 -c "
import json,sys
for line in sys.stdin:
    line=line.strip()
    if line.startswith('{'):
        ev=json.loads(line)
        if ev.get('type')=='result': print(ev.get('text',''))
" 2>/dev/null || true)"
    if [ "$jawab" = "FASE-A-OK" ]; then ok=1; break; fi
    kuning "verifikasi chat gagal (percobaan $i/3), coba lagi..."
    sleep 5
  done
  [ "$ok" = "1" ] || die "verifikasi gagal — lead-agent tidak menjawab dengan benar setelah 3x"
  hijau "lead-agent menjawab via model AI: OK"

  [ "$SKIP_PROFILES"  = "0" ] && tahap4_profiles
  [ "$SKIP_DASHBOARD" = "0" ] && tahap5_dashboard
  [ "$SKIP_NGINX"     = "0" ] && tahap6_nginx

  echo
  hijau "INSTALASI SELESAI"
  echo "  hermes    : $(hermes --version 2>/dev/null | head -1)"
  echo "  profile   : $(hermes profile list 2>/dev/null | grep -c . || true) profile"
  echo "  gateway   : $(hermes gateway list 2>/dev/null | grep -m1 "default" || true)"
  if [ -f "$MC_DIR/domain" ]; then
    echo "  dashboard : https://$(cat "$MC_DIR/domain")"
  fi
  if [ -n "$DASHBOARD_PASSWORD_BARU" ]; then
    echo "  password  : $DASHBOARD_PASSWORD_BARU  (CATAT — hanya ditampilkan sekali)"
  fi
  echo "  chat CLI  : hermes -p lead-agent chat"
}


# ============================================================================
# Tahap 4 — profile awal (Fase B)
# ============================================================================

soul_lead_engineer() { cat <<'EOF'
# Lead Engineer — AI Dashboard

Kamu adalah Lead Engineer. Kamu menerima brief dari Lead Agent dalam Bahasa Indonesia.

Tugasmu:
- Memecah brief menjadi task-task teknis yang kecil dan jelas (self-contained).
- Menentukan dependensi antar task dan kontrak antar bagian (misal: API, format file).
- Mendelegasikan ke profile spesialis (frontend-dev, backend-dev, marketing, dll).
- Mereview hasil kerja spesialis dan mengintegrasikannya.

Kamu tidak ngobrol basa-basi dengan user — fokus ke rekayasa.
EOF
}

soul_frontend_dev() { cat <<'EOF'
# Frontend Developer — AI Dashboard

Kamu adalah Frontend Developer. Kamu mengerjakan task frontend dari Lead Engineer.

Keahlianmu: HTML, CSS, JavaScript, layout responsif, dan UI yang rapi.
Setiap task yang kamu terima mencantumkan output_path — tulis file ke sana,
lalu tulis file status JSON sesuai template yang diberikan di instruksi task.

Bekerja sesuai instruksi, jangan melebar ke luar task.
EOF
}

soul_backend_dev() { cat <<'EOF'
# Backend Developer — AI Dashboard

Kamu adalah Backend Developer. Kamu mengerjakan task backend dari Lead Engineer.

Keahlianmu: Python, API, database SQLite, dan logika server.
Setiap task yang kamu terima mencantumkan output_path — tulis file ke sana,
lalu tulis file status JSON sesuai template yang diberikan di instruksi task.

Bekerja sesuai instruksi, jangan melebar ke luar task.
EOF
}

buat_profile() {
  local name="$1" desc="$2" model="$3" soul_fn="$4" base_url="$5" api_key="$6"
  if [ -d "$HERMES_HOME/profiles/$name" ]; then
    hijau "profile $name sudah ada, lewati"
    return 0
  fi
  kuning "buat profile $name..."
  hermes profile create "$name" --description "$desc" || die "gagal buat profile $name"
  "$soul_fn" > "$HERMES_HOME/profiles/$name/SOUL.md"
  cat > "$HERMES_HOME/profiles/$name/config.yaml" <<EOF
model:
  default: $model
  provider: custom
  base_url: $base_url
api_key: <redacted>
custom_providers:
  - name: RAZIX
    base_url: $base_url
    key_env: HERMES_CUSTOM_9ROUTER_MUSE_FREE_ACCOUNT_MY_ID_API_KEY
    model: $model
_config_version: 46
EOF
  hijau "profile $name dibuat (model $model)"
}

tahap4_profiles() {
  info "Tahap 4/6 — profile awal"
  local base_url="$NINEROUTER_BASE_URL" api_key=""
  if [ -f "$MC_DIR/9router-apikey" ]; then
    api_key="$(cat "$MC_DIR/9router-apikey")"
  else
    base_url="$(minta NINEROUTER_BASE_URL "Base URL provider" "$NINEROUTER_BASE_URL" 0)"
    api_key="$(minta NINEROUTER_API_KEY "API key provider" "" 1)"
    printf '%s' "$api_key" > "$MC_DIR/9router-apikey"; chmod 600 "$MC_DIR/9router-apikey"
    printf 'HERMES_CUSTOM_9ROUTER_MUSE_FREE_ACCOUNT_MY_ID_API_KEY=%s\n' "$api_key" > "$MC_DIR/hermes-env"
    chmod 600 "$MC_DIR/hermes-env"
  fi
  buat_profile "lead-engineer" "Lead Engineer AI Dashboard" "Razix-PowerFull" soul_lead_engineer "$base_url" "$api_key"
  buat_profile "frontend-dev"  "Frontend Developer AI Dashboard" "Razix-Free"  soul_frontend_dev  "$base_url" "$api_key"
  buat_profile "backend-dev"   "Backend Developer AI Dashboard"  "Razix-Free"  soul_backend_dev   "$base_url" "$api_key"
  unset api_key
  # penanda lead agent (dibaca dashboard v2 / Fase C)
  printf 'lead-agent\n' > "$MC_DIR/lead-agent"
  hijau "profile awal selesai"
}

# ============================================================================
# Tahap 5 — dashboard (kode v2: ai-dashboard/mc)
# ============================================================================
tahap5_dashboard() {
  info "Tahap 5/6 — dashboard"
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
    < /dev/urandom tr -dc 'A-Za-z0-9' | head -c 24 > "$passfile"
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
EnvironmentFile=/root/.mission-control/hermes-env
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
# Tahap 6 — nginx + domain + sertifikat HTTPS
# ============================================================================
tahap6_nginx() {
  info "Tahap 6/6 — nginx + HTTPS"
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

main "$@"
