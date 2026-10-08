#!/usr/bin/env bash
# Fresh-server-only installer. Never adopts or replaces an existing Xray installation.
set -Eeuo pipefail
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$PROJECT_DIR/.env"
XRAY_BINARY=/usr/local/bin/xray
XRAY_CONFIG_PATH=/usr/local/etc/xray/config.json
XRAY_SERVICE=xray
XRAY_MANAGED_PROFILE_TAG=vless-control-managed
VLESS_CONTROL_UNIT=/etc/systemd/system/vless-control.service
XRAY_UNIT=/etc/systemd/system/xray.service

fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Запустите от root: sudo ./install.sh'
command -v apt-get >/dev/null || fail 'Поддерживаются Ubuntu/Debian с apt-get.'
source /etc/os-release
case "${ID:-}" in ubuntu|debian) ;; *) fail "ОС не поддерживается: ${PRETTY_NAME:-unknown}";; esac

# Detect system package installs, alternate layouts, configs, and units before any writes.
for path in "$XRAY_BINARY" "$XRAY_CONFIG_PATH" "$XRAY_UNIT" "$VLESS_CONTROL_UNIT" "$ENV_FILE" /usr/bin/xray /usr/sbin/xray /etc/xray /etc/xray/config.json /usr/local/etc/xray /etc/systemd/system/xray.service.d; do
  [[ ! -e "$path" ]] || fail "Обнаружен существующий Xray/конфиг/unit ($path). Установка разрешена только на чистом сервере; ничего не изменено."
done
if command -v dpkg-query >/dev/null && dpkg-query -W -f='${Status}' xray 2>/dev/null | grep -q 'install ok installed'; then
  fail 'Обнаружен установленный пакет Xray; ничего не изменено.'
fi
if systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' | grep -Eq '^(xray|vless-control)\.service$'; then
  fail 'Обнаружена существующая systemd-служба; ничего не изменено.'
fi

read -rsp 'Telegram bot token: ' TELEGRAM_BOT_TOKEN; printf '\n'
read -rp 'Telegram admin IDs (comma-separated numeric IDs): ' TELEGRAM_ADMIN_IDS
cat <<'EOF'
Публичный адрес VPS — IP или домен, по которому устройства будут подключаться к серверу.
Его можно посмотреть в панели VPS в поле IPv4 address.

Введите только IP или домен — без https:// и без порта.
Пример IP: 203.0.113.10
Пример домена: vpn.example.com
Примеры являются демонстрационными, не вводите их буквально.
EOF
read -rp 'Public server host/IP for client links: ' PUBLIC_SERVER_HOST
read -rp 'Managed VLESS TCP port [443]: ' XRAY_MANAGED_VLESS_PORT
XRAY_MANAGED_VLESS_PORT=${XRAY_MANAGED_VLESS_PORT:-443}
[[ -n "$TELEGRAM_BOT_TOKEN" && "$TELEGRAM_ADMIN_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]] || fail 'Нужны токен и числовые ID администраторов.'
[[ "$PUBLIC_SERVER_HOST" =~ ^[A-Za-z0-9.-]+$ && "$PUBLIC_SERVER_HOST" != .* && "$PUBLIC_SERVER_HOST" != *. && "$PUBLIC_SERVER_HOST" != *..* ]] || fail 'Недопустимый публичный host/IP.'
[[ "$XRAY_MANAGED_VLESS_PORT" =~ ^[0-9]+$ ]] && (( XRAY_MANAGED_VLESS_PORT > 0 && XRAY_MANAGED_VLESS_PORT < 65536 )) || fail 'Недопустимый порт.'
command -v ss >/dev/null || fail 'Не найден ss; нельзя безопасно проверить порт.'
! ss -H -lnt "sport = :$XRAY_MANAGED_VLESS_PORT" | grep -q . || fail "TCP-порт занят: $XRAY_MANAGED_VLESS_PORT; ничего не изменено."

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip curl ca-certificates unzip
cd "$PROJECT_DIR"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

arch=$(uname -m)
case "$arch" in x86_64|amd64) asset='Xray-linux-64.zip' ;; aarch64|arm64) asset='Xray-linux-arm64-v8a.zip' ;; armv7l|armv7*) asset='Xray-linux-arm32-v7a.zip' ;; *) fail "Неподдерживаемая архитектура: $arch";; esac
read -r tag url checksum < <(.venv/bin/python - "$asset" <<'PY'
import json,sys,urllib.request
asset=sys.argv[1]
with urllib.request.urlopen('https://api.github.com/repos/XTLS/Xray-core/releases/latest',timeout=30) as r: release=json.load(r)
files={a['name']:a['browser_download_url'] for a in release['assets']}
if asset not in files or asset+'.dgst' not in files: raise SystemExit('официальный архив или checksum не найден')
with urllib.request.urlopen(files[asset]+'.dgst',timeout=30) as r: text=r.read().decode()
sha=next((line.split('=',1)[1].strip() for line in text.splitlines() if line.startswith('SHA2-256=')),None)
if not sha: raise SystemExit('SHA2-256 отсутствует')
print(release['tag_name'],files[asset],sha)
PY
)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl --fail --location --retry 3 --output "$tmp/xray.zip" "$url"
printf '%s  %s\n' "$checksum" "$tmp/xray.zip" | sha256sum --check --status
.venv/bin/python - "$tmp/xray.zip" "$tmp/extract" <<'PY'
import sys,zipfile
from pathlib import Path
archive,dest=map(Path,sys.argv[1:]); dest.mkdir()
with zipfile.ZipFile(archive) as z:
 for info in z.infolist():
  p=Path(info.filename)
  if p.is_absolute() or '..' in p.parts or info.is_dir(): continue
  if p.name=='xray':
   with z.open(info) as src,(dest/'xray').open('wb') as out: out.write(src.read())
   break
 else: raise SystemExit('файл xray не найден')
PY
"$tmp/extract/xray" version >/dev/null
install -d -m 755 /usr/local/bin /usr/local/etc/xray
# Atomic no-clobber creation: link fails if another process populated the target.
install -m 755 "$tmp/extract/xray" "$tmp/xray"
ln "$tmp/xray" "$XRAY_BINARY" || fail 'Xray binary target appeared; refusing overwrite.'
CONFIG_TMP=$(mktemp /usr/local/etc/xray/.config.XXXXXX)
.venv/bin/python - "$XRAY_MANAGED_VLESS_PORT" "$XRAY_MANAGED_PROFILE_TAG" > "$CONFIG_TMP" <<'PY'
import json,sys
port,tag=sys.argv[1:]
config={"log":{"loglevel":"warning"},"inbounds":[{"tag":tag,"listen":"0.0.0.0","port":int(port),"protocol":"vless","settings":{"clients":[],"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"}}],"outbounds":[{"protocol":"freedom","tag":"direct"}]}
json.dump(config,sys.stdout,indent=2); print()
PY
"$XRAY_BINARY" run -test -config "$CONFIG_TMP"
ln "$CONFIG_TMP" "$XRAY_CONFIG_PATH" || fail 'Xray config target appeared; refusing overwrite.'
rm "$CONFIG_TMP"
# No-overwrite unit creation using temporary files and hard links.
cat > "$tmp/xray.service" <<EOF
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
cat > "$tmp/vless-control.service" <<EOF
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
install -d -m 755 /etc/systemd/system
ln "$tmp/xray.service" "$XRAY_UNIT" || fail 'Xray unit target appeared; refusing overwrite.'
ln "$tmp/vless-control.service" "$VLESS_CONTROL_UNIT" || fail 'Bot unit target appeared; refusing overwrite.'
umask 077
ENV_TMP=$(mktemp "$PROJECT_DIR/.env.XXXXXX")
printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_ADMIN_IDS=%s\nPUBLIC_SERVER_HOST=%s\nDATABASE_PATH=/var/lib/vless-control/bot.db\nXRAY_CONFIG_PATH=%s\nXRAY_BINARY=%s\nXRAY_SERVICE=%s\nXRAY_MANAGED_VLESS_PORT=%s\nXRAY_MANAGED_PROFILE_TAG=%s\n' "$TELEGRAM_BOT_TOKEN" "$TELEGRAM_ADMIN_IDS" "$PUBLIC_SERVER_HOST" "$XRAY_CONFIG_PATH" "$XRAY_BINARY" "$XRAY_SERVICE" "$XRAY_MANAGED_VLESS_PORT" "$XRAY_MANAGED_PROFILE_TAG" > "$ENV_TMP"
ln "$ENV_TMP" "$ENV_FILE" || fail '.env already exists; refusing overwrite.'
rm "$ENV_TMP"
chmod 600 "$ENV_FILE"
systemctl daemon-reload
printf 'Установка завершена; службы оставлены остановленными и выключенными.\n'
printf 'Конфигурация: VLESS/TCP, security none, tag=%s, port=%s. Убедитесь, что порт открыт в firewall.\n' "$XRAY_MANAGED_PROFILE_TAG" "$XRAY_MANAGED_VLESS_PORT"
printf 'Для явного запуска обеих служб введите YES: '
read -r consent
if [[ "$consent" == YES ]]; then systemctl enable --now xray && systemctl enable --now vless-control; else printf 'Службы оставлены остановленными и выключенными.\n'; fi
