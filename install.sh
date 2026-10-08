#!/usr/bin/env bash
# Единая установка VLESS Control и Xray. Ничего не запускает автоматически.
set -Eeuo pipefail

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$PROJECT_DIR/.env"
REPLACE=0
for arg in "$@"; do
  case "$arg" in
    --replace-existing) REPLACE=1 ;;
    --help|-h) printf 'Использование: sudo ./install.sh [--replace-existing]\n'; exit 0 ;;
    *) printf 'Неизвестный параметр: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { printf 'Запустите от root: sudo ./install.sh\n' >&2; exit 1; }
command -v apt-get >/dev/null || { printf 'Поддерживаются Ubuntu/Debian с apt-get.\n' >&2; exit 1; }
source /etc/os-release
case "${ID:-}" in ubuntu|debian) ;; *) printf 'ОС не поддерживается: %s\n' "${PRETTY_NAME:-unknown}" >&2; exit 1;; esac

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip curl ca-certificates unzip

cd "$PROJECT_DIR"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

if [[ ! -e "$ENV_FILE" ]]; then
  read -rsp 'Telegram bot token: ' TELEGRAM_BOT_TOKEN; printf '\n'
  read -rp 'Telegram admin IDs (comma-separated): ' TELEGRAM_ADMIN_IDS
  read -rp 'Database path [/var/lib/vless-control/bot.db]: ' DATABASE_PATH
  read -rp 'Xray binary [/usr/local/bin/xray]: ' XRAY_BINARY
  read -rp 'Xray config [/usr/local/etc/xray/config.json]: ' XRAY_CONFIG_PATH
  read -rp 'Xray service [xray]: ' XRAY_SERVICE
  DATABASE_PATH=${DATABASE_PATH:-/var/lib/vless-control/bot.db}
  XRAY_BINARY=${XRAY_BINARY:-/usr/local/bin/xray}
  XRAY_CONFIG_PATH=${XRAY_CONFIG_PATH:-/usr/local/etc/xray/config.json}
  XRAY_SERVICE=${XRAY_SERVICE:-xray}
  umask 077
  printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_ADMIN_IDS=%s\nDATABASE_PATH=%s\nXRAY_CONFIG_PATH=%s\nXRAY_BINARY=%s\nXRAY_SERVICE=%s\n' "$TELEGRAM_BOT_TOKEN" "$TELEGRAM_ADMIN_IDS" "$DATABASE_PATH" "$XRAY_CONFIG_PATH" "$XRAY_BINARY" "$XRAY_SERVICE" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
# Load dotenv as data (never source/eval user-provided values as shell code).
while IFS='=' read -r key value; do
  case "$key" in
    TELEGRAM_BOT_TOKEN|TELEGRAM_ADMIN_IDS|DATABASE_PATH|XRAY_CONFIG_PATH|XRAY_BINARY|XRAY_SERVICE)
      printf -v "$key" '%s' "$value" ;;
  esac
done < "$ENV_FILE"
[[ "$XRAY_BINARY" == /* && "$XRAY_CONFIG_PATH" == /* ]] || { printf 'Пути Xray должны быть абсолютными.\n' >&2; exit 1; }
[[ "$XRAY_SERVICE" =~ ^[a-zA-Z0-9_.@-]+$ ]] || { printf 'Недопустимое имя systemd-службы.\n' >&2; exit 1; }
DEFAULT_REALITY_SNI="www.cloudflare.com"
read -rp "REALITY SNI hostname [$DEFAULT_REALITY_SNI] (Enter = default): " REALITY_SNI
REALITY_SNI=${REALITY_SNI:-$DEFAULT_REALITY_SNI}
read -rp 'VLESS inbound port [443]: ' REALITY_PORT
REALITY_PORT=${REALITY_PORT:-443}
[[ "$REALITY_SNI" =~ ^[A-Za-z0-9.-]{1,253}$ && "$REALITY_SNI" == *.* && "$REALITY_SNI" != .* && "$REALITY_SNI" != *. && "$REALITY_SNI" != *..* ]] || { printf 'Недопустимый SNI. Введите только доменное имя, например www.cloudflare.com, без https:// и пути.\n' >&2; exit 1; }
[[ "$REALITY_PORT" =~ ^[0-9]+$ ]] && (( REALITY_PORT > 0 && REALITY_PORT < 65536 )) || { printf 'Недопустимый порт.\n' >&2; exit 1; }
command -v ss >/dev/null && ! ss -H -lnt "sport = :$REALITY_PORT" | grep -q . || { printf 'Порт занят или ss недоступен: %s\n' "$REALITY_PORT" >&2; exit 1; }

XRAY_UNIT="/etc/systemd/system/${XRAY_SERVICE}.service"
existing=0
for path in "$XRAY_BINARY" "$XRAY_CONFIG_PATH" "$XRAY_UNIT" /etc/systemd/system/vless-control.service; do
  [[ -e "$path" ]] && existing=1
done
if (( existing )); then
  if [[ -e "$XRAY_CONFIG_PATH" ]] || systemctl is-active --quiet "$XRAY_SERVICE" 2>/dev/null; then
    printf 'Обнаружен существующий Xray-конфиг или активная служба. Установка REALITY заменяет весь конфиг и запрещена без отдельной безопасной миграции; --replace-existing не переопределяет этот запрет.\n' >&2
    exit 1
  fi
  [[ $REPLACE -eq 1 ]] || { printf 'Обнаружена существующая установка; без --replace-existing замена запрещена.\n' >&2; exit 1; }
  printf 'Обнаружены неактивные файлы. Создать backup и заменить? [y/N]: '
  read -r answer
  [[ "$answer" == y || "$answer" == Y ]] || { printf 'Отменено.\n'; exit 1; }
fi

backup_dir=/root/vless-control-backups/$(date +%Y%m%d_%H%M%S)
mkdir -p "$backup_dir"
for path in "$XRAY_BINARY" "$XRAY_CONFIG_PATH" "$XRAY_UNIT" /etc/systemd/system/vless-control.service; do
  if [[ -e "$path" ]]; then
    dest="$backup_dir$(dirname -- "$path")"
    mkdir -p "$dest"
    cp -a "$path" "$dest/"
    cmp -s "$path" "$dest/$(basename -- "$path")" || { printf 'Backup verification failed: %s\n' "$path" >&2; exit 1; }
  fi
done

arch=$(uname -m)
case "$arch" in
  x86_64|amd64) asset='Xray-linux-64.zip' ;;
  aarch64|arm64) asset='Xray-linux-arm64-v8a.zip' ;;
  armv7l|armv7*) asset='Xray-linux-arm32-v7a.zip' ;;
  *) printf 'Неподдерживаемая архитектура: %s\n' "$arch" >&2; exit 1;;
esac
read -r tag url checksum < <(.venv/bin/python - "$asset" <<'PY'
import json, sys, urllib.request
asset=sys.argv[1]
with urllib.request.urlopen('https://api.github.com/repos/XTLS/Xray-core/releases/latest', timeout=30) as r:
    release=json.load(r)
files={a['name']:a['browser_download_url'] for a in release['assets']}
if asset not in files or asset+'.dgst' not in files: raise SystemExit('официальный архив или checksum не найден')
with urllib.request.urlopen(files[asset]+'.dgst', timeout=30) as r: text=r.read().decode()
sha=next((line.split('=',1)[1].strip() for line in text.splitlines() if line.startswith('SHA2-256=')), None)
if not sha: raise SystemExit('SHA2-256 отсутствует в официальном checksum')
print(release['tag_name'], files[asset], sha)
PY
)
printf 'Скачивается официальный Xray %s (%s)...\n' "$tag" "$asset"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl --fail --location --retry 3 --output "$tmp/xray.zip" "$url"
printf '%s  %s\n' "$checksum" "$tmp/xray.zip" | sha256sum --check --status
.venv/bin/python - "$tmp/xray.zip" "$tmp/extract" <<'PY'
import sys, zipfile
from pathlib import Path
archive, dest = map(Path, sys.argv[1:]); dest.mkdir()
with zipfile.ZipFile(archive) as z:
    for info in z.infolist():
        p=Path(info.filename)
        if p.is_absolute() or '..' in p.parts or info.is_dir() or not info.filename:
            continue
        if p.name == 'xray':
            target=dest/p.name
            with z.open(info) as src, target.open('wb') as out: out.write(src.read())
            break
    else: raise SystemExit('файл xray не найден в архиве')
PY
install -d -m 755 "$(dirname -- "$XRAY_BINARY")" "$(dirname -- "$XRAY_CONFIG_PATH")"
install -m 755 "$tmp/extract/xray" "$XRAY_BINARY"
"$XRAY_BINARY" version >/dev/null
reality_keys=$("$XRAY_BINARY" x25519)
PRIVATE_KEY=$(printf '%s\n' "$reality_keys" | awk -F': ' '/Private key:/ {print $2}')
PUBLIC_KEY=$(printf '%s\n' "$reality_keys" | awk -F': ' '/Password/ {print $2}')
[[ "$PRIVATE_KEY" =~ ^[A-Za-z0-9_-]{32,128}$ && "$PUBLIC_KEY" =~ ^[A-Za-z0-9_-]{32,128}$ ]] || { printf 'Xray x25519 key generation failed.\n' >&2; exit 1; }
SHORT_ID=$(openssl rand -hex 8)
CONFIG_TMP=$(mktemp "$(dirname -- "$XRAY_CONFIG_PATH")/.xray-config.XXXXXX")
build_reality_bootstrap_config() {
  "$PROJECT_DIR/.venv/bin/python" - "$REALITY_PORT" "$REALITY_SNI" "$PRIVATE_KEY" "$SHORT_ID" > "$CONFIG_TMP" <<'PY'
import json,sys
port,sni,key,sid=sys.argv[1:]
config={"log":{"loglevel":"warning"},"inbounds":[{"tag":"vless-reality-in","listen":"0.0.0.0","port":int(port),"protocol":"vless","settings":{"clients":[],"decryption":"none"},"streamSettings":{"network":"tcp","security":"reality","realitySettings":{"show":False,"dest":f"{sni}:443","xver":0,"serverNames":[sni],"privateKey":key,"shortIds":[sid]}}}],"outbounds":[{"protocol":"freedom","tag":"direct"}]}
json.dump(config,sys.stdout,indent=2); print()
PY
}
build_reality_bootstrap_config
"$XRAY_BINARY" run -test -config "$CONFIG_TMP"
install -d -m 755 "$(dirname -- "$XRAY_CONFIG_PATH")"
install -m 600 "$CONFIG_TMP" "$XRAY_CONFIG_PATH.new"
mv -f -- "$XRAY_CONFIG_PATH.new" "$XRAY_CONFIG_PATH"
rm -f "$CONFIG_TMP"

cat > "$XRAY_UNIT" <<EOF
[Unit]
Description=Xray
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=$XRAY_BINARY run -config $XRAY_CONFIG_PATH
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/vless-control.service <<EOF
[Unit]
Description=VLESS Control Telegram bot
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PROJECT_DIR/.venv/bin/vless-control-bot
Restart=on-failure
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$XRAY_UNIT" /etc/systemd/system/vless-control.service
systemctl daemon-reload
printf '\nГотово. Xray и бот установлены, но НЕ запущены и НЕ включены.\nBackup: %s\n' "$backup_dir"
printf 'Valid VLESS/REALITY inbound configured on %s:%s. No VLESS client is created; add clients through the bot after startup.\n' "$REALITY_SNI" "$REALITY_PORT"
printf 'To enable/start both services now, explicitly type YES: '
read -r consent
if [[ "$consent" == YES ]]; then
  systemctl enable --now "$XRAY_SERVICE"
  systemctl enable --now vless-control
else
  printf 'Services left stopped and disabled.\n'
fi
