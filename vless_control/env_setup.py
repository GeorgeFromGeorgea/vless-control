"""Interactive, secret-safe project environment configuration."""
from __future__ import annotations

import getpass
import os
import re
from pathlib import Path


ADMIN_ID_RE = re.compile(r"^[0-9]+(?:,[0-9]+)*$")


def validate_admin_ids(value: str) -> str:
    value = value.strip()
    if not value or not ADMIN_ID_RE.fullmatch(value):
        raise ValueError("нужны числовые Telegram ID через запятую")
    return value


def prompt_env(input_fn=input, secret_fn=getpass.getpass, output_fn=print) -> dict[str, str]:
    """Ask for settings; token is never returned to output_fn."""
    token = secret_fn("Токен Telegram-бота (ввод скрыт): ").strip()
    if not token or any(ch.isspace() for ch in token):
        raise ValueError("токен не должен быть пустым или содержать пробелы")
    admin_ids = validate_admin_ids(input_fn("Числовой Telegram ID администратора: "))
    values = {
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_ADMIN_IDS": admin_ids,
        "DATABASE_PATH": input_fn("Путь к базе [data/vless-control.sqlite3]: ").strip() or "data/vless-control.sqlite3",
        "XRAY_CONFIG_PATH": input_fn("Путь к Xray config [/usr/local/etc/xray/config.json]: ").strip() or "/usr/local/etc/xray/config.json",
        "XRAY_BINARY": input_fn("Путь к бинарнику Xray [/usr/local/bin/xray]: ").strip() or "/usr/local/bin/xray",
        "XRAY_SERVICE": input_fn("Имя systemd-службы [xray]: ").strip() or "xray",
    }
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("значения не должны содержать переводы строк")
    output_fn("Настройки получены. Токен скрыт; будет записан только в локальный .env.")
    return values


def write_env(values: dict[str, str], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "# Generated locally; do not commit or publish.\n" + "".join(
        f"{key}={value}\n" for key, value in values.items()
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.chmod(target, 0o600)
    return target
