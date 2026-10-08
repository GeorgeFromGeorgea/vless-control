"""Generate deterministic Xray JSON from explicitly configured inbounds."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


def build_reality_bootstrap_config(
    name: str, port: int, sni: str, private_key: str, short_id: str,
    listen: str = "0.0.0.0",
) -> dict[str, Any]:
    """Build a single VLESS/REALITY TCP inbound; does not write or apply it."""
    if not name or not name.strip():
        raise ValueError("name must not be empty")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("invalid inbound port")
    if not sni or not sni.strip():
        raise ValueError("SNI must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", sni.strip()) or ".." in sni:
        raise ValueError("SNI must be a hostname without scheme/path")
    if not private_key or not private_key.strip():
        raise ValueError("private key must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", private_key.strip()):
        raise ValueError("invalid REALITY private key format")
    if not short_id or len(short_id) > 16 or len(short_id) % 2 or any(c not in "0123456789abcdefABCDEF" for c in short_id):
        raise ValueError("invalid short ID (must be 2-16 hexadecimal characters, even length)")
    if not isinstance(listen, str) or not listen:
        raise ValueError("listen address must not be empty")
    return build_config([{
        "name": name.strip(), "port": port, "listen": listen,
        "security": "reality", "transport": "tcp", "sni": sni.strip(),
        "private_key": private_key.strip(), "short_id": short_id.lower(),
    }], [])


def build_config(profiles: list[dict[str, Any]], clients: list[dict[str, Any]]) -> dict[str, Any]:
    """Build only; deliberately does not write files or restart services."""
    if not profiles:
        raise ValueError("at least one profile is required")
    inbounds = []
    for p in profiles:
        if not p.get("active", 1):
            continue
        security = p["security"]
        transport = p["transport"]
        if security not in {"reality", "tls", "none"} or transport not in {"tcp", "ws"}:
            raise ValueError("unsupported security/transport")
        settings: dict[str, Any] = {"clients": clients, "decryption": "none"}
        stream: dict[str, Any] = {"network": transport, "security": security}
        if security == "reality":
            if transport != "tcp":
                raise ValueError("REALITY currently requires TCP/raw transport")
            settings["clients"] = [{**c, "flow": "xtls-rprx-vision"} for c in clients]
            stream["realitySettings"] = {
                "show": False,
                "dest": f"{p['sni']}:443",
                "xver": 0,
                "serverNames": [p["sni"]],
                "privateKey": p["private_key"],
                "shortIds": [p["short_id"]],
            }
        elif security == "tls":
            stream["tlsSettings"] = {"certificates": [{"certificateFile": p["certificate_file"], "keyFile": p["key_file"]}]}
        if transport == "ws":
            stream["wsSettings"] = {"path": p["path"]}
        inbound = {
            "tag": p["name"], "listen": p.get("listen", "0.0.0.0"), "port": int(p["port"]),
            "protocol": "vless", "settings": settings, "streamSettings": stream,
        }
        inbounds.append(inbound)
    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }


def validate_config(config: dict[str, Any]) -> None:
    ports: set[tuple[str, int]] = set()
    tags: set[str] = set()
    inbounds = config.get("inbounds", [])
    if not isinstance(inbounds, list):
        raise ValueError("inbounds must be a list")
    if not inbounds:
        raise ValueError("at least one inbound is required")
    for inbound in inbounds:
        if not isinstance(inbound, dict):
            raise ValueError("each inbound must be an object")
        tag, port, listen = inbound.get("tag"), inbound.get("port"), inbound.get("listen")
        if not tag or tag in tags:
            raise ValueError("inbound tags must be non-empty and unique")
        tags.add(tag)
        try:
            port_number = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid inbound port") from exc
        key = (listen, port_number)
        if key in ports:
            raise ValueError(f"duplicate listen/port: {listen}:{port}")
        ports.add(key)
        if not 1 <= port_number <= 65535:
            raise ValueError("invalid inbound port")
        if inbound.get("protocol") != "vless" or not isinstance(inbound.get("settings", {}).get("clients"), list):
            raise ValueError("inbound must be VLESS and include a clients list")
        stream = inbound.get("streamSettings", {})
        if stream.get("security") == "reality":
            rs = stream.get("realitySettings", {})
            if not rs.get("privateKey") or not rs.get("serverNames") or not rs.get("shortIds"):
                raise ValueError("incomplete REALITY settings")


def write_config_atomically(config: dict[str, Any], path: str | Path) -> None:
    """Write to a temporary file then rename; caller must handle backup and service checks."""
    validate_config(config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".new")
    temp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
