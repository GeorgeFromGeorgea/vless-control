"""Generate deterministic Xray JSON from explicitly configured inbounds."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_config(profiles: list[dict[str, Any]], clients: list[dict[str, Any]]) -> dict[str, Any]:
    """Build only; deliberately does not write files or restart services."""
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
    for inbound in config.get("inbounds", []):
        tag, port, listen = inbound.get("tag"), inbound.get("port"), inbound.get("listen")
        if not tag or tag in tags:
            raise ValueError("inbound tags must be non-empty and unique")
        tags.add(tag)
        key = (listen, int(port))
        if key in ports:
            raise ValueError(f"duplicate listen/port: {listen}:{port}")
        ports.add(key)
        if not 1 <= int(port) <= 65535:
            raise ValueError("invalid inbound port")
        if inbound.get("protocol") != "vless" or not inbound.get("settings", {}).get("clients"):
            raise ValueError("inbound must be VLESS and include clients")
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
