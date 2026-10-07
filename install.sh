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

if [[ -e "$ENV_FILE" ]]; then
  printf 'Файл %s уже существует. Перезаписать? [y/N]: ' "$ENV_FILE"
  read -r answer
  [[ "$answer" == y || "$answer" == Y ]] || { printf 'Настройка .env отменена.\n' >&2; exit 1; }
fi
.venv/bin/python - <<'PY'
from vless_control.env_setup import prompt_env, write_env
write_env(prompt_env(), ".env")
PY
chmod 600 "$ENV_FILE"

# Load dotenv as data (never source/eval user-provided values as shell code).
while IFS='=' read -r key value; do
  case "$key" in
    TELEGRAM_BOT_TOKEN|TELEGRAM_ADMIN_IDS|DATABASE_PATH|XRAY_CONFIG_PATH|XRAY_BINARY|XRAY_SERVICE)
      printf -v "$key" '%s' "$value" ;;
  esac
done < "$ENV_FILE"
[[ "$XRAY_BINARY" == /* && "$XRAY_CONFIG_PATH" == /* ]] || { printf 'Пути Xray должны быть абсолютными.\n' >&2; exit 1; }
[[ "$XRAY_SERVICE" =~ ^[a-zA-Z0-9_.@-]+$ ]] || { printf 'Недопустимое имя systemd-службы.\n' >&2; exit 1; }

XRAY_UNIT="/etc/systemd/system/${XRAY_SERVICE}.service"
existing=0
for path in "$XRAY_BINARY" "$XRAY_CONFIG_PATH" "$XRAY_UNIT" /etc/systemd/system/vless-control.service; do
  [[ -e "$path" ]] && existing=1
done
if (( existing )); then
  [[ $REPLACE -eq 1 ]] || { printf 'Обнаружена существующая установка; без --replace-existing замена запрещена.\n' >&2; exit 1; }
  printf 'Обнаружены существующие файлы. Создать backup и заменить? [y/N]: '
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
printf 'Рабочий Xray config.json не создаётся этим скриптом.\n'
printf 'После подготовки конфига выполните проверку и явный запуск:\n  %s run -test -config %s\n  systemctl enable --now %s\n  systemctl enable --now vless-control\n' "$XRAY_BINARY" "$XRAY_CONFIG_PATH" "$XRAY_SERVICE"
