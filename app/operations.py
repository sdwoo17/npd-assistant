"""Local operator functions; deliberately not exposed through HTTP routes."""
import base64
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from .store import AppError, Store

MAGIC = b'NPD-BACKUP-1\n'
MAX_BACKUP = 100 * 1024 * 1024


def existing_store(directory):
    path = Path(directory)
    if not all((path / f).is_file() for f in ('npd.sqlite3', 'private.key')):
        raise AppError('기존 DB와 암호화 키가 있는 데이터 폴더를 지정하세요.')
    return Store(path)


def private_password(password):
    if (not isinstance(password, str) or not 16 <= len(password) <= 1000
            or password in ('Owner-demo-2026!', 'Planner-demo-2026!')):
        raise AppError('공개 예제와 다른 16~1,000자 비밀번호가 필요합니다.')


def provision_account(store, email, password, role, project):
    private_password(password)
    with store.db() as db:
        if not db.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone():
            raise AppError('등록된 프로젝트 ID를 지정하세요.', 404)
    uid = store.create_user(email, password, role, project)
    store.put(project, 'audit', {'actor_id': 'local_operator', 'action': 'account_created', 'record_id': uid})
    return uid


def change_account(store, email, *, password=None, disabled=None):
    if (password is None) == (disabled is None) or (disabled is not None and type(disabled) is not bool):
        raise AppError('비밀번호 변경 또는 계정 상태 변경 하나를 지정하세요.')
    if password is not None:
        private_password(password)
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000).hex()
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT id FROM users WHERE email=?', (email.strip().lower(),)).fetchone()
        if not row:
            raise AppError('등록된 계정을 찾을 수 없습니다.', 404)
        uid = row['id']
        projects = db.execute('SELECT project_id,role FROM memberships WHERE user_id=?', (uid,)).fetchall()
        if disabled:
            for project in projects:
                if project['role'] == 'owner' and not db.execute(
                    "SELECT 1 FROM memberships m WHERE m.project_id=? AND m.role='owner' AND m.user_id!=? "
                    'AND NOT EXISTS (SELECT 1 FROM account_state a WHERE a.user_id=m.user_id AND a.disabled=1)',
                    (project['project_id'], uid)).fetchone():
                    raise AppError('프로젝트의 마지막 활성 소유자는 비활성화할 수 없습니다.', 409)
        if password is not None:
            db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?', (salt, digest, uid))
        else:
            db.execute('INSERT OR REPLACE INTO account_state VALUES(?,?)', (uid, int(disabled)))
        db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
        # Stop pending evidence-based generations using the project's CAS guard.
        for project in projects:
            db.execute('INSERT INTO project_state VALUES(?,1) ON CONFLICT(project_id) DO UPDATE SET epoch=epoch+1', (project['project_id'],))
    for project in projects:
        store.put(project['project_id'], 'audit', {'actor_id': 'local_operator', 'action':
            'password_rotated' if password is not None else 'account_disabled' if disabled else 'account_enabled', 'record_id': uid})
    return {'user_id': uid, 'sessions_revoked': True}


def backup_cipher(passphrase, salt):
    private_password(passphrase)
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def backup(store, destination, passphrase):
    salt = os.urandom(16)
    cipher = backup_cipher(passphrase, salt)
    target = Path(destination).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='npd-backup-') as tmp:
        snapshot = Path(tmp) / 'snapshot.sqlite3'
        # SQLite's backup API includes a transactionally consistent view even
        # when the server is running; copying a live .sqlite file is not safe.
        with store.db() as source, sqlite3.connect(snapshot) as dest:
            source.backup(dest)
        if snapshot.stat().st_size > 50 * 1024 * 1024:
            raise AppError('파일럿 백업 한도는 DB 50MiB입니다. 관리형 백업으로 전환하세요.')
        files = {'npd.sqlite3': base64.b64encode(snapshot.read_bytes()).decode(),
                 'private.key': base64.b64encode((store.directory / 'private.key').read_bytes()).decode()}
        receipt = store.directory / 'demo-import.json'
        if receipt.is_file():
            if receipt.stat().st_size > 2 * 1024 * 1024:
                raise AppError('적재 영수증 크기를 확인하세요.')
            files['demo-import.json'] = base64.b64encode(receipt.read_bytes()).decode()
        envelope = cipher.encrypt(json.dumps({'schema': 'npd.backup.v1', 'files': files}).encode())
        if len(envelope) + len(MAGIC) + 16 > MAX_BACKUP:
            raise AppError('백업 파일 크기 한도를 초과했습니다.')
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise AppError('기존 백업 파일을 덮어쓰지 않습니다.', 409)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(MAGIC + salt + envelope)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return {'status': 'encrypted_backup_created', 'bytes': target.stat().st_size}


def restore(archive, destination, passphrase):
    target = Path(destination).absolute()
    if target.exists():
        raise AppError('복구에는 새 데이터 폴더를 지정하세요. 기존 환경은 보존합니다.', 409)
    try:
        with Path(archive).open('rb') as stream:
            data = stream.read(MAX_BACKUP + 1)
        if not data.startswith(MAGIC) or len(data) > MAX_BACKUP:
            raise ValueError()
        offset = len(MAGIC)
        content = backup_cipher(passphrase, data[offset:offset+16]).decrypt(data[offset+16:])
        payload = json.loads(content)
        files = payload['files']
        if (payload['schema'] != 'npd.backup.v1' or not {'npd.sqlite3', 'private.key'}.issubset(files)
                or not set(files).issubset({'npd.sqlite3', 'private.key', 'demo-import.json'})):
            raise ValueError()
        files = {name: base64.b64decode(value, validate=True) for name, value in files.items()}
        Fernet(files['private.key'])
    except (InvalidToken, ValueError, KeyError, TypeError, OSError):
        raise AppError('백업의 암호·무결성·형식을 확인하세요. 복구하지 않았습니다.') from None
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise AppError('기존 환경은 덮어쓰지 않습니다.', 409)
    staging = Path(tempfile.mkdtemp(prefix='npd-restore-', dir=target.parent))
    installed = False
    try:
        for name, value in files.items():
            path = staging / name
            with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as f:
                f.write(value)
        recovered = Store(staging)
        with recovered.db() as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise AppError('DB 무결성 검사에 실패했습니다.')
            # Verify all encrypted records, including prior versions, before
            # installation; mismatched encryption keys must never produce PASS.
            for table in ('records', 'record_history'):
                for row in db.execute('SELECT body FROM ' + table):
                    for key, value in json.loads(row['body']).items():
                        if key.startswith('encrypted_'):
                            recovered.decrypt(value)
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM login_attempts')
        recovered.recover_jobs()
        os.rename(staging, target)
        installed = True
        return {'status': 'restored', 'sessions_revoked': True, 'integrity_checked': True}
    except Exception:
        raise AppError('복구 검증에 실패했습니다. 기존 환경은 변경하지 않았습니다.') from None
    finally:
        if not installed:
            shutil.rmtree(staging)
            try:
                target.rmdir()
            except OSError:
                pass
