"""Pilot account lifecycle and encrypted backup/restore. Run on the server host."""
import argparse
import getpass
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.operations import existing_store, provision_account, change_account, backup, restore
from app.store import AppError


def password(prompt):
    value = getpass.getpass(prompt)
    if value != getpass.getpass('Repeat to confirm: '):
        raise AppError('입력값이 일치하지 않습니다.')
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', required=True)
    commands = p.add_subparsers(dest='action', required=True)
    commands.add_parser('list-accounts')
    create = commands.add_parser('create-account')
    create.add_argument('--email', required=True)
    create.add_argument('--project-id', required=True)
    create.add_argument('--role', choices=('owner', 'po'), default='po')
    for action in ('reset-password', 'disable-account', 'enable-account'):
        cmd = commands.add_parser(action)
        cmd.add_argument('--email', required=True)
    commands.add_parser('backup').add_argument('--output', required=True)
    commands.add_parser('restore').add_argument('--archive', required=True)
    args = p.parse_args()
    try:
        if args.action == 'restore':
            result = restore(args.archive, args.data_dir, getpass.getpass('Backup passphrase: '))
        else:
            store = existing_store(args.data_dir)
            if args.action == 'list-accounts':
                with store.db() as db:
                    result = [dict(r) for r in db.execute('SELECT u.id,u.email,m.project_id,m.role,COALESCE(a.disabled,0) disabled FROM users u JOIN memberships m ON m.user_id=u.id LEFT JOIN account_state a ON a.user_id=u.id ORDER BY u.email,m.project_id')]
            elif args.action == 'create-account':
                result = {'user_id': provision_account(store, args.email, password('Private password (16+ characters): '), args.role, args.project_id)}
            elif args.action == 'reset-password':
                result = change_account(store, args.email, password=password('New private password: '))
            elif args.action in ('disable-account', 'enable-account'):
                result = change_account(store, args.email, disabled=args.action == 'disable-account')
            else:
                result = backup(store, args.output, password('Backup passphrase (16+ characters): '))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (AppError, OSError) as exc:
        print(str(exc) if isinstance(exc, AppError) else '파일 접근에 실패했습니다.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
