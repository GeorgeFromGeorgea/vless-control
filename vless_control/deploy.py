"""Reconcile registry clients into an existing Xray JSON config safely.

Apply is opt-in, requires a full existing config, validates with Xray, keeps a
verified timestamped backup, and rolls back if validation or service reload fails.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def reconcile_clients(config: dict, assignments: dict[str, list[dict]], *, target_port: int | None = None) -> dict:
    """Replace managed VLESS clients per known inbound, preserve unrelated config.

    assignments maps inbound tag -> list of client dicts. Every VLESS inbound
    touched by this manager must be passed. Unlisted inbound and config keys stay.
    """
    result = copy.deepcopy(config)
    inbounds = result.get("inbounds")
    if not isinstance(inbounds, list):
        raise ValueError("config must contain an inbounds list")
    found: set[str] = set()
    tags: set[str] = set()
    # Select the requested inbound by full connection semantics and only manage it.
    if target_port is not None:
        matches = [i for i in inbounds if i.get("protocol") == "vless" and i.get("port") == target_port
                   and i.get("streamSettings", {}).get("security", "none") == "none"
                   and i.get("streamSettings", {}).get("network", "tcp") == "tcp"]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one VLESS none/tcp inbound on port {target_port}")
        target = matches[0]
        if not target.get("tag"):
            target["tag"] = f"vless-control-{target_port}"
        if target["tag"] not in assignments:
            raise ValueError(f"no explicit assignment for selected inbound tag {target['tag']!r}")
    for index, inbound in enumerate(inbounds):
        # Unmanaged operator inbounds may legitimately have no tag. Assign a
        # private in-memory identity only when they're targeted (never rewrite them).
        if not inbound.get("tag") and target_port is not None and inbound is target:
            pass
        elif not inbound.get("tag"):
            continue
        if not inbound.get("protocol"):
            raise ValueError("each inbound must have a protocol")
        if inbound["tag"] in tags:
            raise ValueError(f"duplicate inbound tag: {inbound['tag']}")
        tags.add(inbound["tag"])
        if target_port is not None and inbound is target:
            found.add(inbound["tag"])
            settings = inbound.setdefault("settings", {})
            if not isinstance(settings.get("clients"), list):
                raise ValueError("target VLESS inbound must have a clients list")
            sanitized = [c for c in settings["clients"] if not str(c.get("email", "")).startswith("vless-control-")]
            seen_ids = {c.get("id") for c in sanitized if c.get("id")}
            for client in assignments[target["tag"]]:
                if not client.get("id") or client["id"] in seen_ids:
                    raise ValueError("missing or duplicate UUID")
                seen_ids.add(client["id"])
                sanitized.append({k: client[k] for k in ("id", "email", "flow", "level") if k in client})
            settings["clients"] = sanitized
            continue
        if inbound.get("protocol") == "vless" and not isinstance(inbound.get("settings", {}).get("clients"), list):
            raise ValueError(f"VLESS inbound {inbound.get('tag', '<untagged>')!r} must have a clients list")
        if inbound.get("tag") not in assignments:
            continue
        found.add(inbound["tag"])
        if inbound.get("protocol") != "vless":
            raise ValueError(f"inbound {inbound['tag']!r} is not VLESS")
        settings = inbound["settings"]
        clients = assignments[inbound["tag"]]
        seen_ids: set[str] = set()
        sanitized = [
            client for client in settings.get("clients", [])
            if not str(client.get("email", "")).startswith("vless-control-")
        ]
        seen_ids.update(client.get("id") for client in sanitized if client.get("id"))
        for c in clients:
            cid = c.get("id")
            if not cid or cid in seen_ids:
                raise ValueError(f"missing or duplicate UUID in inbound {inbound['tag']!r}")
            seen_ids.add(cid)
            sanitized.append({k: c[k] for k in ("id", "email", "flow", "level") if k in c})
        settings["clients"] = sanitized
    missing = set(assignments) - found
    if missing:
        raise ValueError(f"unknown inbound tags: {', '.join(sorted(missing))}")
    return result


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
        dirfd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        temp.unlink(missing_ok=True)


class XrayConfigDeployer:
    """Safe file deployer; service restart only when explicitly injected/called."""
    def __init__(self, config_path: str | Path, xray_binary: str = "/usr/local/bin/xray"):
        self.path = Path(config_path)
        self.xray_binary = xray_binary

    def validate(self, candidate: Path) -> None:
        result = subprocess.run(
            [self.xray_binary, "run", "-test", "-config", str(candidate)],
            check=False, capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[:1500]
            raise RuntimeError(f"Xray config validation failed: {detail}")

    def stage_and_validate(self, config: dict) -> Path:
        raw = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".xray-candidate-", suffix=".json", dir=self.path.parent)
        candidate = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(candidate, 0o600)
            self.validate(candidate)
            return candidate
        except Exception:
            candidate.unlink(missing_ok=True)
            raise

    def deploy(self, config: dict, restart: Callable[[], None] | None = None) -> Path:
        """Install candidate; if restart callback fails, restore prior bytes.

        restart=None is file-only mode and does not touch systemd. Caller must
        ensure a service is not concurrently editing this file.
        """
        if not self.path.is_file():
            raise FileNotFoundError(f"Existing config required; refusing to create from scratch: {self.path}")
        candidate = self.stage_and_validate(config)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.path.with_name(f"{self.path.name}.bak.{stamp}")
        old = self.path.read_bytes()
        mode = self.path.stat().st_mode & 0o777
        try:
            with backup.open("xb") as stream:
                stream.write(old)
                stream.flush()
                os.fsync(stream.fileno())
            if backup.read_bytes() != old:
                raise IOError("backup verification failed")
            _atomic_write(self.path, candidate.read_bytes(), mode)
            self.validate(self.path)
            if restart is not None:
                restart()
            return backup
        except Exception:
            _atomic_write(self.path, old, mode)
            if restart is not None:
                try:
                    restart()  # restore old running config after rollback
                except Exception:
                    pass
            raise
        finally:
            candidate.unlink(missing_ok=True)
