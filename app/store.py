import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from cryptography.fernet import Fernet


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def timestamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        keyfile = self.directory / "private.key"
        if not keyfile.exists():
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        self.cipher = Fernet(keyfile.read_bytes())
        self.path = self.directory / "npd.sqlite3"
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,project_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,csrf TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS records_scope ON records(project_id,kind);
            CREATE TABLE IF NOT EXISTS voc_keys(project_id TEXT NOT NULL,source_key TEXT NOT NULL,record_id TEXT NOT NULL,PRIMARY KEY(project_id,source_key));
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memberships(user_id TEXT NOT NULL,project_id TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(user_id,project_id));
            CREATE TABLE IF NOT EXISTS record_history(id TEXT NOT NULL,project_id TEXT NOT NULL,kind TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(project_id,id,version));
            CREATE TABLE IF NOT EXISTS project_state(project_id TEXT PRIMARY KEY,epoch INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS persona_alias ON records(project_id,json_extract(body,'$.alias')) WHERE kind='persona';
            CREATE UNIQUE INDEX IF NOT EXISTS turn_request ON records(project_id,json_extract(body,'$.conversation_id'),json_extract(body,'$.request_id')) WHERE kind='turn';
            CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,attempts INTEGER NOT NULL,window_start REAL NOT NULL);
            """)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(sessions)")}
            if "project_id" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")
            db.execute("INSERT OR IGNORE INTO projects SELECT DISTINCT project_id,project_id FROM users")
            db.execute("INSERT OR IGNORE INTO memberships SELECT id,project_id,role FROM users")
            db.execute("UPDATE sessions SET project_id=(SELECT project_id FROM users WHERE users.id=sessions.user_id) WHERE project_id IS NULL")
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def encrypt(self, text):
        return self.cipher.encrypt(text.encode()).decode()

    def decrypt(self, text):
        return self.cipher.decrypt(text.encode()).decode()

    def create_user(self, email, password, role, project_id):
        if role not in ("owner", "po") or len(password) < 12:
            raise AppError("역할 또는 비밀번호를 확인하세요. 비밀번호는 12자 이상이어야 합니다.")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260000).hex()
        uid = str(uuid.uuid4())
        with self.db() as db:
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (uid, email.strip().lower(), salt, digest, role, project_id))
            db.execute("INSERT OR IGNORE INTO projects VALUES(?,?)", (project_id, project_id))
            db.execute("INSERT INTO memberships VALUES(?,?,?)", (uid, project_id, role))
        return uid

    def login(self, email, password, client_key="local"):
        email = email.strip().lower()
        key = hashlib.sha256((client_key + ":" + email).encode()).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM login_attempts WHERE key=?", (key,)).fetchone()
            if previous and time.time() - previous["window_start"] < 300 and previous["attempts"] >= 10:
                raise AppError("로그인 시도가 많습니다. 잠시 후 다시 시도하세요.", 429)
            count = previous["attempts"] + 1 if previous and time.time() - previous["window_start"] < 300 else 1
            start = previous["window_start"] if count > 1 else time.time()
            db.execute("INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)", (key, count, start))
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        salt = row["salt"] if row else "00" * 16
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260000).hex()
        if not row or not hmac.compare_digest(digest, row["password_hash"]):
            raise AppError("이메일 또는 비밀번호를 확인하세요.", 401)
        token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(24)
        with self.db() as db:
            db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO sessions(token_hash,user_id,csrf,expires,project_id) VALUES(?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], csrf, time.time() + 8 * 3600, row["project_id"]))
        return token, csrf

    def has_public_demo_passwords(self):
        """Startup check against documented sample passwords, independent of email."""
        with self.db() as db:
            rows = db.execute("SELECT salt,password_hash FROM users").fetchall()
        for row in rows:
            for password in ("Owner-demo-2026!", "Planner-demo-2026!"):
                digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row["salt"]), 260000).hex()
                if hmac.compare_digest(digest, row["password_hash"]):
                    return True
        return False

    def authenticate(self, token):
        with self.db() as db:
            row = db.execute("SELECT u.id,u.email,m.role,s.project_id,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id JOIN memberships m ON m.user_id=u.id AND m.project_id=s.project_id WHERE token_hash=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise AppError("로그인이 필요합니다.", 401)
        return dict(row)

    def logout(self, token):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def epoch(self, project):
        with self.db() as db:
            row = db.execute("SELECT epoch FROM project_state WHERE project_id=?", (project,)).fetchone()
        return row["epoch"] if row else 0

    def assert_epoch(self, project, expected):
        if self.epoch(project) != expected:
            raise AppError("근거가 변경됐습니다. 최신 자료로 다시 시도하세요.", 409)

    def write(self, project, inserts=(), updates=(), expected_epoch=None):
        """One transaction for a complete turn/PRD update and its version guards.

        inserts: (kind, body, optional id); updates: (kind, id, changes, expected version).
        No partial turn or stale optimistic update survives a failing transaction.
        """
        saved = []
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT epoch FROM project_state WHERE project_id=?", (project,)).fetchone()
            epoch = current["epoch"] if current else 0
            if expected_epoch is not None and epoch != expected_epoch:
                raise AppError("근거가 변경됐습니다. 최신 자료로 다시 시도하세요.", 409)
            changed = False
            for kind, body, rid in inserts:
                obj = dict(body, id=rid or str(uuid.uuid4()), project_id=project, kind=kind, created_at=timestamp(), version=1)
                try:
                    db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (obj["id"], project, kind, json.dumps(obj, ensure_ascii=False, allow_nan=False), obj["created_at"]))
                except sqlite3.IntegrityError:
                    raise AppError("동일한 이름 또는 요청이 이미 저장됐습니다.", 409)
                saved.append(obj)
                changed |= kind in ("source", "insight", "voc", "feature", "persona")
            for kind, rid, changes, expected in updates:
                row = db.execute("SELECT body FROM records WHERE id=? AND project_id=? AND kind=?", (rid, project, kind)).fetchone()
                if not row:
                    raise AppError("자료를 찾을 수 없거나 접근 권한이 없습니다.", 404)
                obj = json.loads(row["body"])
                version = obj.get("version", 1)
                if expected is not None and version != expected:
                    raise AppError("다른 변경이 먼저 저장됐습니다. 새로고침 후 다시 시도하세요.", 409)
                db.execute("INSERT OR IGNORE INTO record_history VALUES(?,?,?,?,?)", (rid, project, kind, version, row["body"]))
                protected = {"id", "kind", "project_id", "created_at", "version"}
                obj.update({k: v for k, v in changes.items() if k not in protected})
                obj.update(version=version + 1, updated_at=timestamp())
                try:
                    db.execute("UPDATE records SET body=? WHERE id=? AND project_id=? AND kind=?", (json.dumps(obj, ensure_ascii=False, allow_nan=False), rid, project, kind))
                except sqlite3.IntegrityError:
                    raise AppError("동일한 이름이 이미 사용 중입니다.", 409)
                saved.append(obj)
                changed |= kind in ("source", "insight", "voc", "feature", "persona")
            if changed:
                db.execute("INSERT INTO project_state VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET epoch=excluded.epoch", (project, epoch + 1))
        return saved

    def put(self, project, kind, body, record_id=None):
        return self.write(project, inserts=[(kind, body, record_id)])[0]

    def get(self, project, kind, rid):
        if not isinstance(rid, str):
            raise AppError("유효한 식별자가 필요합니다.")
        with self.db() as db:
            row = db.execute("SELECT body FROM records WHERE id=? AND project_id=? AND kind=?", (rid, project, kind)).fetchone()
        if not row:
            raise AppError("자료를 찾을 수 없거나 접근 권한이 없습니다.", 404)
        obj = json.loads(row["body"])
        obj.setdefault("version", 1)
        return obj

    def list(self, project, kind):
        with self.db() as db:
            rows = db.execute("SELECT body FROM records WHERE project_id=? AND kind=? ORDER BY created_at,id", (project, kind)).fetchall()
        records = [json.loads(row["body"]) for row in rows]
        for record in records:
            record.setdefault("version", 1)
        return records

    def update(self, project, kind, rid, changes, expected_version=None):
        return self.write(project, updates=[(kind, rid, changes, expected_version)])[0]

    def history(self, project, kind, rid):
        current = self.get(project, kind, rid)
        with self.db() as db:
            rows = db.execute("SELECT body FROM record_history WHERE project_id=? AND kind=? AND id=? ORDER BY version", (project, kind, rid)).fetchall()
        return [json.loads(r["body"]) for r in rows] + [current]

    def voc(self, project, source_key, body):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM voc_keys WHERE project_id=? AND source_key=?", (project, source_key)).fetchone():
                return None
            obj = dict(body, id=str(uuid.uuid4()), project_id=project, kind="voc", created_at=timestamp(), version=1)
            db.execute("INSERT INTO voc_keys VALUES(?,?,?)", (project, source_key, obj["id"]))
            db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (obj["id"], project, "voc", json.dumps(obj, ensure_ascii=False, allow_nan=False), obj["created_at"]))
            db.execute("INSERT INTO project_state VALUES(?,1) ON CONFLICT(project_id) DO UPDATE SET epoch=epoch+1", (project,))
        return obj

    def projects(self, user_id):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT p.id,p.title,m.role FROM projects p JOIN memberships m ON p.id=m.project_id WHERE m.user_id=? ORDER BY p.title,p.id", (user_id,))]

    def recover_jobs(self):
        """Call only on single-server startup, after the previous process has stopped."""
        with self.db() as db:
            rows = db.execute("SELECT project_id,body FROM records WHERE kind='job'").fetchall()
        recovered = 0
        for row in rows:
            job = json.loads(row['body'])
            if job.get('status') == 'running':
                self.update(row['project_id'], 'job', job['id'], {
                    'status': 'failed', 'error': '서버 재시작으로 처리가 중단됐습니다. 작업을 재시도하세요.'}, job.get('version', 1))
                recovered += 1
        return recovered

    def create_project(self, user_id, title):
        rid = str(uuid.uuid4())
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                raise AppError("등록된 사용자 계정이 필요합니다.", 403)
            db.execute("INSERT INTO projects VALUES(?,?)", (rid, title))
            db.execute("INSERT INTO memberships VALUES(?,?,?)", (user_id, rid, "owner"))
        return {"id": rid, "title": title, "role": "owner"}

    def switch_project(self, user, project):
        new_csrf = secrets.token_urlsafe(24)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM memberships WHERE user_id=? AND project_id=?", (user["id"], project)).fetchone():
                raise AppError("프로젝트 접근 권한이 없습니다.", 403)
            cursor = db.execute("UPDATE sessions SET project_id=?,csrf=? WHERE user_id=? AND csrf=? AND expires>?", (project, new_csrf, user["id"], user["csrf"], time.time()))
            if cursor.rowcount != 1:
                raise AppError("세션이 변경됐습니다. 다시 로그인하세요.", 401)
        return {"project_id": project, "csrf": new_csrf}

    def add_member(self, project, email, role):
        if role not in ("owner", "po"):
            raise AppError("역할을 확인하세요.")
        with self.db() as db:
            row = db.execute("SELECT id FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
            if not row:
                raise AppError("먼저 서버에서 사용자 계정을 등록하세요.", 404)
            if db.execute("SELECT 1 FROM memberships WHERE user_id=? AND project_id=?", (row["id"], project)).fetchone():
                raise AppError("이미 참여 중인 계정입니다.", 409)
            db.execute("INSERT INTO memberships VALUES(?,?,?)", (row["id"], project, role))
        return {"project_id": project, "role": role, "added": True}
