"""Backfill deferred profiles for an existing private pack, without reimporting data."""
import argparse
import getpass
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.assets import register_templates
from app.demo_assets import DemoPack, keyed, research_sources
from app.model import create_model
from app.service import Service
from app.store import AppError, Store


def register(service, user, directory, receipt):
    # Hydrate exactly the same manifest inputs as the original import before
    # checking its digest. Changed files cannot silently repoint old evidence IDs.
    pack = DemoPack(directory)
    m = pack.manifest
    for key in ('features', 'insights', 'personas', 'prd'):
        pack.json_file(m[key])
    if m.get('annotations'):
        pack.json_file(m['annotations'])
    for item in m['voc']:
        pack.read(item['file'])
    research_sources(pack, m, keyed(pack.json_file(m['insights']), 'key', 200))
    if receipt['pack_id'] != m['pack_id'] or receipt['content_digest'] != pack.digest():
        raise AppError('원래 적재한 자료 팩과 내용이 다릅니다. 기존 자산은 변경하지 않았습니다.', 409)
    # Resolve every reference inside this authenticated project before writes.
    for kind, key in [('source', 'source_ids'), ('insight', 'insight_ids'), ('voc', 'voc_ids')]:
        for rid in receipt[key].values():
            service.store.get(user['project_id'], kind, rid)
    service.store.get(user['project_id'], 'conversation', receipt['interview_conversation_id'])
    return register_templates(service, user, keyed(pack.json_file(m['personas']), 'key', 8), receipt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets', required=True)
    p.add_argument('--data-dir', required=True)
    args = p.parse_args()
    target = Path(args.data_dir)
    if not (target / 'npd.sqlite3').is_file():
        p.error('An existing data directory is required.')
    store = Store(target)
    token = None
    try:
        token, _ = store.login(input('Owner email: '), getpass.getpass('Owner password: '))
        user = store.authenticate(token)
        receipt = json.loads((target / 'demo-import.json').read_text())
        ids = register(Service(store, create_model()), user, args.assets, receipt)
        print(json.dumps({'registered_templates': len(ids), 'insights_released': False, 'model_called': False}))
        return 0
    except (AppError, KeyError, TypeError, ValueError, OSError):
        print('프로필 등록 실패. 소유자 계정, 기존 적재 영수증, 동일 자료 팩을 확인하세요.', file=sys.stderr)
        return 1
    finally:
        if token:
            store.logout(token)


if __name__ == '__main__':
    raise SystemExit(main())
