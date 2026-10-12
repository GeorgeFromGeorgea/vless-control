"""Validation helpers for the guided Telegram profile wizard."""

import base64
import ipaddress
import re


def valid_host(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        if len(value) > 253 or not value:
            return False
        labels = value.rstrip(".").split(".")
        return all(len(label) <= 63 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) for label in labels)


def valid_reality_key(value: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))) == 32 and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None
    except Exception:
        return False


def validate_profile_fields(host: str, security: str, transport: str, sni: str = "", public_key: str = "", short_id: str = "") -> None:
    if not valid_host(host):
        raise ValueError("Введите корректный IP-адрес или hostname")
    if security == "reality":
        if transport != "tcp" or not valid_host(sni):
            raise ValueError("REALITY требует корректный SNI и TCP")
        if not valid_reality_key(public_key):
            raise ValueError("Некорректный публичный ключ REALITY")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", short_id):
            raise ValueError("short ID должен содержать 2–16 шестнадцатеричных символов")


BASE_FIELDS = ("name", "host", "port", "security", "transport")
PROMPTS = {
    "name": "Введите уникальное имя профиля (оно должно совпадать с inbound tag Xray):",
    "host": "Введите адрес сервера для клиента (IP или домен):",
    "port": "Введите порт от 1 до 65535:",
    "security": "Выберите защиту: reality, tls или none:",
    "transport": "Выберите транспорт: tcp или ws (REALITY поддерживает только tcp):",
    "sni": "Введите SNI (имя сервера):",
    "public_key": "Введите публичный ключ REALITY (не приватный):",
    "short_id": "Введите short ID REALITY:",
    "path": "Введите WebSocket path, начиная с /:",
}

PREFERRED_PORT_HINT = "Рекомендации: 443 — стандартный HTTPS/TLS; 8080/8088 — HTTP/WS; 8443 — альтернативный HTTPS/TLS; 10000–20000 — свободный запасной диапазон. Порт 80 обычно занят веб-сервером. Выбор не гарантирует доступность извне: проверьте firewall."


def port_occupants(port: int) -> list[str]:
    """Return local listeners using a port; failure is reported as unknown."""
    try:
        result = __import__("subprocess").run(["ss", "-H", "-ltnup"], capture_output=True, text=True, timeout=3, check=True)
    except Exception as exc:
        raise RuntimeError("не удалось проверить занятость порта") from exc
    needle = f":{port}"
    return [line.strip()[:240] for line in result.stdout.splitlines() if needle in line]


def validate_port_available(port: int) -> None:
    if port in {22, 53, 25, 110, 143, 587, 993, 995}:
        raise ValueError("этот системный/почтовый порт не рекомендуется использовать")
    occupants = port_occupants(port)
    if occupants:
        raise ValueError(f"порт уже занят ({occupants[0]})")

def next_field(values: dict) -> str | None:
    for field in BASE_FIELDS:
        if field not in values:
            return field
    security, transport = values["security"], values["transport"]
    extras = ("sni", "public_key", "short_id") if security == "reality" else (("sni",) if security == "tls" else ())
    if transport == "ws":
        extras += ("path",)
    return next((field for field in extras if field not in values), None)


def accept_value(values: dict, field: str, text: str) -> dict:
    value = text.strip()
    if not value:
        raise ValueError("Значение не должно быть пустым")
    if value in {"👤 Пользователи", "➕ Новый ключ", "🧩 Назначить профили"}:
        raise ValueError("Это кнопка меню, а не значение. Введите значение или нажмите «Отмена»")
    if field == "host" and not valid_host(value):
        raise ValueError("Введите корректный IP-адрес или hostname")
    if field == "sni" and not valid_host(value):
        raise ValueError("Введите корректный SNI (hostname или IP)")
    if field == "public_key" and not valid_reality_key(value):
        raise ValueError("Некорректный публичный ключ REALITY")
    if field == "short_id" and not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", value):
        raise ValueError("short ID должен содержать 2–16 шестнадцатеричных символов")
    if field == "port":
        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError("Порт должен быть числом") from exc
        if not 1 <= value <= 65535:
            raise ValueError("Порт должен быть от 1 до 65535")
    elif field == "security" and value not in {"reality", "tls", "none"}:
        raise ValueError("Выберите reality, tls или none")
    elif field == "transport" and value not in {"tcp", "ws"}:
        raise ValueError("Выберите tcp или ws")
    elif field == "transport" and values.get("security") == "reality" and value != "tcp":
        raise ValueError("Для REALITY в этом проекте доступен только tcp")
    elif field == "path" and not value.startswith("/"):
        raise ValueError("WebSocket path должен начинаться с /")
    elif field == "name" and len(value) > 80:
        raise ValueError("Имя профиля должно быть не длиннее 80 символов")
    elif field == "host" and len(value) > 253:
        raise ValueError("Адрес слишком длинный")
    return {**values, field: value}
