"""Validation helpers for the guided Telegram profile wizard."""

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
