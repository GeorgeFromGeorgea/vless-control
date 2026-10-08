"""Serialized registry/Xray transitions. No service calls occur at import time."""
from __future__ import annotations
import fcntl
import json

import subprocess
from pathlib import Path
from contextlib import contextmanager
from .deploy import XrayConfigDeployer, reconcile_clients

@contextmanager
def process_lock(path: str | Path):
    lock = Path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

class RuntimeService:
    def __init__(self, registry, *, host: str, port: int = 443,
                 config_path: str = "/usr/local/etc/xray/config.json",
                 xray_binary: str = "/usr/local/bin/xray", service: str = "xray",
                 lock_path: str = "/run/lock/vless-control.lock", restart=None,
                 target_tag: str | None = None):
        if not host.strip() or not 1 <= int(port) <= 65535:
            raise ValueError("PUBLIC_SERVER_HOST and a valid managed port are required")
        import os
        configured_port = os.getenv("XRAY_MANAGED_VLESS_PORT", "").strip()
        if configured_port and (not configured_port.isdigit() or int(configured_port) != int(port)):
            raise ValueError("managed port does not match XRAY_MANAGED_VLESS_PORT")
        self.registry, self.host, self.port = registry, host.strip(), int(port)
        self.config_path, self.xray_binary, self.service = config_path, xray_binary, service
        self.lock_path, self.restart, self.target_tag = lock_path, restart, target_tag

    def _restart(self):
        if self.restart:
            return self.restart()
        subprocess.run(["systemctl", "restart", self.service], check=True, timeout=60)
        subprocess.run(["systemctl", "is-active", "--quiet", self.service], check=True, timeout=15)

    def _apply(self, override: tuple[str, str] | None = None):
        path = Path(self.config_path)
        config = json.loads(path.read_text(encoding="utf-8"))
        matches = [i for i in config.get("inbounds", []) if i.get("protocol") == "vless" and i.get("port") == self.port
                   and i.get("streamSettings", {}).get("security", "none") == "none"
                   and i.get("streamSettings", {}).get("network", "tcp") == "tcp"]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one plain none/tcp VLESS inbound on port {self.port}")
        tag = matches[0].get("tag")
        if not tag or (self.target_tag and tag != self.target_tag):
            raise ValueError("selected inbound has no matching explicit managed tag")
        if not self.target_tag:
            raise ValueError("XRAY_MANAGED_PROFILE_TAG is required")
        clients = self.registry.xray_assignments()
        if override:
            uuid, status = override
            for values in clients.values():
                values[:] = [c for c in values if c["id"] != uuid]
            if status == "active":
                with self.registry._connect() as db:
                    rows = db.execute("SELECT p.name FROM user_profiles up JOIN profiles p ON p.id=up.profile_id WHERE up.user_id=(SELECT id FROM users WHERE client_uuid=?) AND p.active=1", (uuid,)).fetchall()
                for row in rows:
                    clients.setdefault(row["name"], []).append({"id":uuid,"email":f"vless-control-{uuid}"})
        if tag not in clients:
            clients[tag] = []
        assignments = {tag: clients[tag]}
        candidate = reconcile_clients(config, assignments, target_port=self.port)
        # For a selected port, prune only explicitly manager-marked clients even
        # if they are stale/orphaned in the runtime registry.
        XrayConfigDeployer(path, self.xray_binary).deploy(candidate, restart=self._restart)

    def create(self, label: str, days: int | None = None):
        with process_lock(self.lock_path):
            item = self.registry.add_user(label, days=days)
            try:
                profile_id = self._profile()
                self.registry.assign_profiles(item["id"], [profile_id])
                self._apply()
            except Exception:
                self.registry.rollback_user(item["id"])
                raise
            return item

    def _profile(self):
        with self.registry._connect() as db:
            row = db.execute("SELECT * FROM profiles WHERE active=1 AND name=?", (self.target_tag,)).fetchone()
        if row:
            expected = {"name": self.target_tag, "host": self.host, "port": self.port,
                        "security": "none", "transport": "tcp", "sni": "",
                        "public_key": "", "short_id": "", "path": ""}
            if any(row[key] != value for key, value in expected.items()):
                raise ValueError("managed profile does not exactly match configured host, port, or tag")
            return int(row["id"])
        return self.registry.add_profile(self.target_tag, self.host, self.port, "none", "tcp")

    def transition(self, user_id: int, status: str):
        with process_lock(self.lock_path):
            user = self.registry.get_user(user_id)
            if not user:
                return False
            # Expiry is enforced during assignment generation; transition and deploy
            # are serialized, and DB status commits only after deploy success.
            return self.registry.transition(user_id, status, deploy=lambda: self._apply((user["client_uuid"], status)))

    def delete(self, user_id: int):
        with process_lock(self.lock_path):
            user = self.registry.get_user(user_id)
            if not user: return False
            return self.registry.delete(user_id, deploy=lambda: self._apply((user["client_uuid"], "revoked")))

    def cleanup_expired(self):
        with process_lock(self.lock_path):
            expired = self.registry.due_users()
            if expired:
                self._apply()
                self.registry.mark_expired(expired)
            return expired


def runtime_from_env(registry):
    """Fail-closed production wiring; invocation alone performs no deploy."""
    import os
    host = os.getenv("PUBLIC_SERVER_HOST", "").strip()
    raw_port = os.getenv("XRAY_MANAGED_VLESS_PORT", "443").strip()
    if not raw_port.isdigit():
        raise ValueError("XRAY_MANAGED_VLESS_PORT must be numeric")
    return RuntimeService(registry, host=host, port=int(raw_port),
        config_path=os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json"),
        xray_binary=os.getenv("XRAY_BINARY", "/usr/local/bin/xray"),
        service=os.getenv("XRAY_SERVICE", "xray"),
        lock_path=os.getenv("VLESS_CONTROL_LOCK_PATH", "/run/lock/vless-control.lock"),
        target_tag=os.getenv("XRAY_MANAGED_PROFILE_TAG", "").strip() or None)