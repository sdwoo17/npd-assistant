import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
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
            CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,attempts INTEGER NOT NULL,window_start REAL NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

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
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], csrf, time.time() + 8 * 3600))
        return token, csrf

    def authenticate(self, token):
        with self.db() as db:
            row = db.execute("SELECT u.id,u.email,u.role,u.project_id,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise AppError("로그인이 필요합니다.", 401)
        return dict(row)

    def logout(self, token):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def put(self, project, kind, body, record_id=None):
        obj = dict(body, id=record_id or str(uuid.uuid4()), project_id=project, kind=kind, created_at=timestamp())
        with self.db() as db:
            db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (obj["id"], project, kind, json.dumps(obj, ensure_ascii=False, allow_nan=False), obj["created_at"]))
        return obj

    def get(self, project, kind, rid):
        if not isinstance(rid, str):
            raise AppError("유효한 식별자가 필요합니다.")
        with self.db() as db:
            row = db.execute("SELECT body FROM records WHERE id=? AND project_id=? AND kind=?", (rid, project, kind)).fetchone()
        if not row:
            raise AppError("자료를 찾을 수 없거나 접근 권한이 없습니다.", 404)
        return json.loads(row["body"])

    def list(self, project, kind):
        with self.db() as db:
            rows = db.execute("SELECT body FROM records WHERE project_id=? AND kind=? ORDER BY created_at", (project, kind)).fetchall()
        return [json.loads(row["body"]) for row in rows]

    def update(self, project, kind, rid, changes):
        obj = self.get(project, kind, rid)
        obj.update(changes)
        obj["updated_at"] = timestamp()
        with self.db() as db:
            db.execute("UPDATE records SET body=? WHERE id=? AND project_id=? AND kind=?", (json.dumps(obj, ensure_ascii=False, allow_nan=False), rid, project, kind))
        return obj

    def voc(self, project, source_key, body):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM voc_keys WHERE project_id=? AND source_key=?", (project, source_key)).fetchone():
                return None
            obj = dict(body, id=str(uuid.uuid4()), project_id=project, kind="voc", created_at=timestamp())
            db.execute("INSERT INTO voc_keys VALUES(?,?,?)", (project, source_key, obj["id"]))
            db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (obj["id"], project, "voc", json.dumps(obj, ensure_ascii=False), obj["created_at"]))
        return obj
