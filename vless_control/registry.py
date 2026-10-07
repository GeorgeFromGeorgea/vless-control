"""Small SQLite registry. Stores UUID identities, never rendered share links."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Iterable


class Registry:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    label TEXT NOT NULL UNIQUE,
                    client_uuid TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535),
                    security TEXT NOT NULL CHECK(security IN ('reality','tls','none')),
                    transport TEXT NOT NULL CHECK(transport IN ('tcp','ws')),
                    sni TEXT NOT NULL DEFAULT '',
                    public_key TEXT NOT NULL DEFAULT '',
                    short_id TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    profile_id INTEGER NOT NULL REFERENCES profiles(id),
                    PRIMARY KEY(user_id, profile_id)
                );
            """)

    def add_user(self, label: str) -> dict:
        label = label.strip()
        if not label or len(label) > 80:
            raise ValueError("label must contain 1..80 characters")
        client_uuid = str(uuid.uuid4())
        with self._connect() as db:
            cur = db.execute("INSERT INTO users(label,client_uuid) VALUES (?,?)", (label, client_uuid))
            return {"id": cur.lastrowid, "label": label, "uuid": client_uuid}

    def add_profile(self, name: str, host: str, port: int, security: str,
                    transport: str = "tcp", sni: str = "", public_key: str = "",
                    short_id: str = "", path: str = "") -> int:
        name, host = name.strip(), host.strip()
        if not name or not host or len(name) > 80:
            raise ValueError("profile name and host are required")
        if not 1 <= int(port) <= 65535:
            raise ValueError("invalid port")
        if security not in {"reality", "tls", "none"} or transport not in {"tcp", "ws"}:
            raise ValueError("unsupported security/transport")
        if security == "reality" and (transport != "tcp" or not sni or not public_key or not short_id):
            raise ValueError("REALITY/TCP requires SNI, public key, and short ID")
        with self._connect() as db:
            cur = db.execute("""INSERT INTO profiles(name,host,port,security,transport,sni,public_key,short_id,path)
                VALUES (?,?,?,?,?,?,?,?,?)""", (name, host, int(port), security, transport, sni, public_key, short_id, path))
            return int(cur.lastrowid)

    def assign_profiles(self, user_id: int, profile_ids: Iterable[int]) -> None:
        ids = list(dict.fromkeys(int(x) for x in profile_ids))
        with self._connect() as db:
            user = db.execute("SELECT id FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
            if not user:
                raise ValueError("active user not found")
            for pid in ids:
                profile = db.execute("SELECT id FROM profiles WHERE id=? AND active=1", (pid,)).fetchone()
                if not profile:
                    raise ValueError(f"active profile {pid} not found")
                db.execute("INSERT OR IGNORE INTO user_profiles(user_id,profile_id) VALUES (?,?)", (user_id, pid))

    def list_connections(self, user_id: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("""SELECT u.label,u.client_uuid,p.* FROM users u
                JOIN user_profiles up ON up.user_id=u.id JOIN profiles p ON p.id=up.profile_id
                WHERE u.id=? AND u.active=1 AND p.active=1 ORDER BY p.name""", (user_id,)).fetchall()
            return [dict(row) for row in rows]

    def xray_assignments(self) -> dict[str, list[dict]]:
        """Return managed profile names as inbound tags, including empty clients."""
        with self._connect() as db:
            profiles = db.execute("SELECT name FROM profiles WHERE active=1 ORDER BY name").fetchall()
            rows = db.execute("""SELECT p.name AS inbound_tag, u.client_uuid
                FROM users u JOIN user_profiles up ON up.user_id=u.id
                JOIN profiles p ON p.id=up.profile_id
                WHERE u.active=1 AND p.active=1 ORDER BY p.name,u.id""").fetchall()
        result = {row["name"]: [] for row in profiles}
        for row in rows:
            result[row["inbound_tag"]].append({"id": row["client_uuid"], "email": f"vless-control-{row['client_uuid']}"})
        return result

    def deactivate_user(self, user_id: int) -> bool:
        with self._connect() as db:
            cur = db.execute("UPDATE users SET active=0 WHERE id=? AND active=1", (user_id,))
            return cur.rowcount == 1


def vless_uri(connection: dict) -> str:
    """Render a credential URI from a validated record; caller must protect it."""
    from urllib.parse import urlencode, quote

    security = connection["security"]
    transport = connection["transport"]
    if security not in {"reality", "tls", "none"} or transport not in {"tcp", "ws"}:
        raise ValueError("unsupported security/transport")
    if security == "reality" and transport != "tcp":
        raise ValueError("REALITY requires TCP")
    q = {"encryption": "none", "type": transport, "security": security}
    if security == "none" and transport == "tcp":
        q.pop("security")
    if security == "reality":
        q.update({"sni": connection["sni"], "pbk": connection["public_key"], "sid": connection["short_id"], "flow": "xtls-rprx-vision"})
    elif security == "tls":
        q["sni"] = connection["sni"]
    if transport == "ws":
        q.update({"host": connection["host"], "path": connection["path"]})
    label = quote(f"{connection['label']} - {connection['name']}", safe="")
    return f"vless://{connection['client_uuid']}@{connection['host']}:{connection['port']}?{urlencode(q)}#{label}"
