"""SQLite source of truth for project-owned VLESS identities."""
from __future__ import annotations
import sqlite3, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Callable
from .profile_wizard import validate_profile_fields

ACTIVE = "active"
PAUSED = "paused"
REVOKED = "revoked"

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

class Registry:
    def __init__(self, path: str | Path, clock: Callable[[], datetime] = utc_now):
        self.path = str(path); self.clock = clock
        if self.path != ":memory:": Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()
    def _connect(self):
        db=sqlite3.connect(self.path, timeout=30, isolation_level=None); db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA busy_timeout=30000"); return db
    def _init(self):
        with self._connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, label TEXT NOT NULL UNIQUE, client_uuid TEXT NOT NULL UNIQUE,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), status TEXT NOT NULL DEFAULT 'active'
   CHECK(status IN ('active','paused','revoked')), expires_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
 CREATE TABLE IF NOT EXISTS profiles (id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,host TEXT NOT NULL,port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535),security TEXT NOT NULL CHECK(security IN ('reality','tls','none')),transport TEXT NOT NULL CHECK(transport IN ('tcp','ws')),sni TEXT NOT NULL DEFAULT '',public_key TEXT NOT NULL DEFAULT '',short_id TEXT NOT NULL DEFAULT '',path TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)));
 CREATE TABLE IF NOT EXISTS user_profiles (user_id INTEGER NOT NULL REFERENCES users(id),profile_id INTEGER NOT NULL REFERENCES profiles(id),PRIMARY KEY(user_id,profile_id));""")
            cols={r[1] for r in db.execute("PRAGMA table_info(users)")}
            if "expires_at" not in cols: db.execute("ALTER TABLE users ADD COLUMN expires_at TEXT")
            if "status" not in cols: db.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            db.execute("UPDATE users SET status=CASE WHEN active=1 THEN 'active' ELSE 'revoked' END WHERE status IS NULL OR status NOT IN ('active','paused','revoked')")
    def add_user(self,label:str, expires_at:datetime|None=None, days:int|None=None)->dict:
        label=label.strip()
        if not label or len(label)>80: raise ValueError("label must contain 1..80 characters")
        if days is not None:
            if days not in (1,7,30): raise ValueError("days must be 1, 7, or 30")
            expires_at=self.clock()+timedelta(days=days)
        if expires_at is not None and expires_at <= self.clock():
            raise ValueError("expires_at must be in the future")
        exp=iso(expires_at) if expires_at else None; ident=str(uuid.uuid4())
        with self._connect() as db:
            try:
                cur=db.execute("INSERT INTO users(label,client_uuid,expires_at,status,active) VALUES(?,?,?,'active',1)",(label,ident,exp))
            except sqlite3.IntegrityError as exc:
                if db.execute("SELECT 1 FROM users WHERE label=?", (label,)).fetchone():
                    raise ValueError("label already exists; choose a different label") from exc
                raise
            return {"id":cur.lastrowid,"label":label,"uuid":ident,"expires_at":exp,"status":"active"}
    def list_users(self, include_expired=True):
        with self._connect() as db:
            q="SELECT id,label,client_uuid AS uuid,status,expires_at,created_at FROM users"
            if not include_expired: q += " WHERE status='active'"
            return [dict(r) for r in db.execute(q+" ORDER BY id DESC")]
    def get_user(self,user_id):
        with self._connect() as db:
            r=db.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone(); return dict(r) if r else None
    def due_users(self)->list[int]:
        now=iso(self.clock())
        with self._connect() as db:
            return [r[0] for r in db.execute("SELECT id FROM users WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?",(now,))]
    def mark_expired(self, user_ids):
        with self._connect() as db:
            rows = db.execute("SELECT id FROM users WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?", (iso(self.clock()),)).fetchall()
            db.executemany("UPDATE users SET status='revoked',active=0 WHERE id=? AND status='active'", [(int(i),) for i in user_ids if int(i) in {r[0] for r in rows}])
    def rollback_user(self, user_id):
        with self._connect() as db:
            db.execute("DELETE FROM user_profiles WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
    def due_users(self)->list[int]:
        now=iso(self.clock())
        with self._connect() as db:
            return [r[0] for r in db.execute("SELECT id FROM users WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?",(now,))]
    def transition(self,user_id,status:str, deploy:Callable[[],None]|None=None)->bool:
        if status not in (ACTIVE,PAUSED,REVOKED): raise ValueError("invalid status")
        with self._connect() as db:
            row=db.execute("SELECT status,expires_at FROM users WHERE id=?",(user_id,)).fetchone()
        if not row: return False
        if status == ACTIVE and row["expires_at"] is not None and row["expires_at"] <= iso(self.clock()):
            raise ValueError("expired user cannot be activated")
        if deploy: deploy() # DB is unchanged until safe deploy succeeds
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT status,expires_at FROM users WHERE id=?",(user_id,)).fetchone()
            if not row: db.rollback(); return False
            if status == ACTIVE and row["expires_at"] is not None and row["expires_at"] <= iso(self.clock()):
                db.rollback()
                raise ValueError("expired user cannot be activated")
            db.execute("UPDATE users SET status=?,active=? WHERE id=?",(status,1 if status==ACTIVE else 0,user_id)); db.commit(); return True
    def pause(self,i,deploy=None): return self.transition(i,PAUSED,deploy)
    def resume(self,i,deploy=None): return self.transition(i,ACTIVE,deploy)
    def revoke(self,i,deploy=None): return self.transition(i,REVOKED,deploy)
    def delete(self,i,deploy=None):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT id FROM users WHERE id=?",(i,)).fetchone()
            if not row: db.rollback(); return False
            if deploy: deploy()
            db.execute("DELETE FROM user_profiles WHERE user_id=?",(i,)); db.execute("DELETE FROM users WHERE id=?",(i,)); db.commit(); return True
    def add_profile(self,name,host,port,security,transport="tcp",sni="",public_key="",short_id="",path=""):
        name,host=name.strip(),host.strip()
        if not name or not host or len(name)>80: raise ValueError("profile name and host are required")
        if not 1<=int(port)<=65535 or security not in {"reality","tls","none"} or transport not in {"tcp","ws"}: raise ValueError("unsupported profile")
        validate_profile_fields(host,security,transport,sni,public_key,short_id)
        if security=="reality" and (transport!="tcp" or not sni or not public_key or not short_id): raise ValueError("REALITY/TCP requires SNI, public key, and short ID")
        with self._connect() as db:
            return int(db.execute("INSERT INTO profiles(name,host,port,security,transport,sni,public_key,short_id,path) VALUES(?,?,?,?,?,?,?,?,?)",(name,host,int(port),security,transport,sni,public_key,short_id,path)).lastrowid)
    def assign_profiles(self,user_id,profile_ids:Iterable[int]):
        ids=list(dict.fromkeys(int(x) for x in profile_ids))
        with self._connect() as db:
            if not db.execute("SELECT id FROM users WHERE id=? AND status='active'",(user_id,)).fetchone(): raise ValueError("active user not found")
            for pid in ids:
                if not db.execute("SELECT id FROM profiles WHERE id=? AND active=1",(pid,)).fetchone(): raise ValueError("active profile not found")
                db.execute("INSERT OR IGNORE INTO user_profiles VALUES(?,?)",(user_id,pid))
    def list_connections(self,user_id):
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT u.label,u.client_uuid,p.* FROM users u JOIN user_profiles up ON up.user_id=u.id JOIN profiles p ON p.id=up.profile_id WHERE u.id=? AND u.status='active' AND p.active=1 ORDER BY p.name",(user_id,))]
    def xray_assignments(self):
        with self._connect() as db:
            now=iso(self.clock())
            ps=db.execute("SELECT name FROM profiles WHERE active=1 ORDER BY name").fetchall(); rows=db.execute("SELECT p.name AS inbound_tag,u.client_uuid FROM users u JOIN user_profiles up ON up.user_id=u.id JOIN profiles p ON p.id=up.profile_id WHERE u.status='active' AND (u.expires_at IS NULL OR u.expires_at>?) AND p.active=1 ORDER BY p.name,u.id",(now,)).fetchall()
        out={r['name']:[] for r in ps}
        for r in rows: out[r['inbound_tag']].append({'id':r['client_uuid'],'email':f"vless-control-{r['client_uuid']}",'level':0})
        return out
    def deactivate_user(self,user_id):
        user=self.get_user(user_id)
        return bool(user and user['status'] != REVOKED and self.revoke(user_id))

def vless_uri(c):
    from urllib.parse import urlencode,quote
    if c["security"] not in {"reality","tls","none"} or c["transport"] not in {"tcp","ws"}: raise ValueError("unsupported security/transport")
    q={"encryption":"none","type":c["transport"],"security":c["security"]}
    if c["security"]=="none" and c["transport"]=="tcp": q.pop("security")
    if c["security"]=="reality": q.update({"sni":c["sni"],"pbk":c["public_key"],"sid":c["short_id"],"flow":"xtls-rprx-vision"})
    elif c["security"]=="tls": q["sni"]=c["sni"]
    if c["transport"]=="ws": q.update({"host":c["host"],"path":c["path"]})
    label = quote(str(c['label']) + ' - ' + str(c['name']), safe='')
    return f"vless://{c['client_uuid']}@{c['host']}:{c['port']}?{urlencode(q)}#{label}"
