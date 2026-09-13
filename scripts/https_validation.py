"""Opt-in gate against an existing HTTPS pilot, using an isolated synthetic project."""
import argparse
import getpass
import json
import secrets
import sys
import time
import urllib.error
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.store import AppError
from scripts.live_validation import Client, exercise_http


def https_origin(value):
    parsed = urlparse(value)
    try:
        valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                 and not parsed.password and parsed.path in ('', '/') and not parsed.params
                 and not parsed.query and not parsed.fragment and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise AppError('도메인만 포함한 HTTPS URL을 입력하세요. 예: https://npd.example.com')
    return 'https://' + parsed.netloc


def check_session(client):
    cookies = [c for c in client.cookies if c.name == 'npd_session']
    if (len(cookies) != 1 or not cookies[0].secure or not cookies[0].has_nonstandard_attr('HttpOnly')
            or cookies[0].get_nonstandard_attr('SameSite') != 'Strict'):
        raise AppError('Secure·HttpOnly 세션 쿠키 검증에 실패했습니다.', 500)


def run_remote(origin, credentials, client_factory=Client, require_secure=True):
    """Return only measurements. Keep the isolated project for operator inspection."""
    steps, clients, homes = [], {}, {}
    project = None
    started = time.perf_counter()
    result = {'status': 'FAIL'}
    try:
        if credentials['owner'][0].strip().lower() == credentials['po'][0].strip().lower():
            raise AppError('서로 다른 소유자와 PO 계정이 필요합니다.')
        anonymous = client_factory(origin)
        if anonymous.call('/healthz').get('status') != 'ok':
            raise AppError('Health check failed.')
        try:
            anonymous.call('/api/bootstrap')
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
        else:
            raise AppError('Unauthenticated project access was allowed.')
        for role in ('owner', 'po'):
            client = clients[role] = client_factory(origin)
            client.login(*credentials[role])
            if require_secure:
                check_session(client)
            boot = client.call('/api/bootstrap')
            homes[role] = boot['user']['project_id']
            if boot['model_provider'] != 'bedrock':
                raise AppError('배포 서버의 모델 제공자가 Bedrock이 아닙니다.')
            if role == 'owner' and boot['user']['role'] != 'owner':
                raise AppError('소유자 역할의 계정이 필요합니다.', 403)
        owner, po = clients['owner'], clients['po']
        project = owner.call('/api/projects', {'title': 'SYNTHETIC HTTPS validation ' + secrets.token_hex(6)})['id']
        owner.csrf = owner.call('/api/projects/switch', {'project_id': project})['csrf']
        owner.call('/api/projects/members', {'email': credentials['po'][0], 'role': 'po'})
        po.csrf = po.call('/api/projects/switch', {'project_id': project})['csrf']
        if po.call('/api/bootstrap')['user']['role'] != 'po':
            raise AppError('별도 PO 권한 검증에 실패했습니다.')
        marker = 'OWNER_PRIVATE_REMOTE_GATE_' + secrets.token_hex(12)
        exercise_http(owner, po, marker, steps)
        result = {'status': 'PASS', 'provider': 'bedrock', 'personas': 2, 'interview_rounds': 2,
                  'prd_version': 2, 'http_raw_protection_checked': True, 'revocation_checked': True,
                  'model_input_guarded': False, 'secure_cookie_checked': require_secure,
                  'note': '실제 응답의 의미 품질은 별도 PO 검토가 필요합니다. 서버 내부 모델 입력 검사는 로컬 live_validation 게이트에서 수행합니다.'}
    except Exception as exc:
        result = {'status': 'FAIL', 'http_status': getattr(exc, 'code', None) if isinstance(exc, urllib.error.HTTPError) else None,
                  'reason': 'HTTPS/model/domain gate failed; credentials, source text and generated content omitted.'}
    finally:
        cleanup_ok = True
        for role, client in clients.items():
            try:
                if role in homes:
                    client.csrf = client.call('/api/projects/switch', {'project_id': homes[role]})['csrf']
                client.call('/api/logout', {})
            except Exception:
                cleanup_ok = False
        if not cleanup_ok:
            result.update(status='FAIL', session_cleanup_failed=True)
    return {**result, 'project_id': project, 'project_retained_for_review': project is not None,
            'steps': steps, 'seconds': round(time.perf_counter() - started, 2)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True)
    p.add_argument('--run', action='store_true', help='Create a synthetic validation project and invoke the deployed paid Bedrock model')
    p.add_argument('--passes', type=int, choices=range(1, 4), default=1)
    p.add_argument('--output', help='Write only gate metrics, never model output or credentials')
    args = p.parse_args()
    try:
        origin = https_origin(args.url)
    except AppError as exc:
        p.error(str(exc))
    if not args.run:
        print(json.dumps({'status': 'NOT_RUN', 'reason': 'Explicit --run is required.'}))
        return 2
    credentials = {role: (input(role + ' email: '), getpass.getpass(role + ' password: ')) for role in ('owner', 'po')}
    runs = []
    for index in range(args.passes):
        result = run_remote(origin, credentials)
        result['pass'] = index + 1
        runs.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if args.output:
            Path(args.output).write_text(json.dumps({'runs': runs}, ensure_ascii=False, indent=2) + '\n')
        if result['status'] != 'PASS':
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
