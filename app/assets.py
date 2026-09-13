"""Owner-reviewed activation of private imported persona definitions.

Templates are not PO evidence. Only current published knowledge can become a
persona, and retries never replace a PO-edited profile or conversation.
"""
import json
import uuid
from .contracts import strings
from .research import owner
from .store import AppError


def register_templates(service, user, people, receipt):
    owner(user)
    p = user['project_id']
    epoch = service.store.epoch(p)
    existing = {r['id']: r for r in service.store.list(p, 'persona_template')}
    inserts, ids = [], []
    for key, person in people.items():
        rid = str(uuid.uuid5(uuid.NAMESPACE_URL, p + ':' + receipt['content_digest'] + ':' + key))
        ids.append(rid)
        if rid in existing:
            continue
        evidence_ids = [receipt['insight_ids'][i] for i in person['insights']]
        evidence_ids += [receipt['voc_ids'][i] for i in person['voc_refs']]
        definition = {k: person[k] for k in ('name', 'segment', 'goals', 'constraints', 'assumptions') if k in person}
        definition['evidence_ids'] = evidence_ids
        inserts.append(('persona_template', {
            'pack_id': receipt['pack_id'], 'pack_digest': receipt['content_digest'], 'template_key': key,
            'encrypted_definition': service.store.encrypt(json.dumps(definition, ensure_ascii=False)),
            'persona_id': receipt['persona_ids'].get(key),
            'interview_conversation_id': receipt['interview_conversation_id']}, rid))
    if inserts:
        service.store.write(p, inserts=inserts, expected_epoch=epoch)
    return ids


class Assets:
    def template_previews(self, user):
        owner(user)
        p = user['project_id']
        allowed = {r['id'] for r in self.knowledge(p)}
        rows = []
        for r in self.store.list(p, 'persona_template'):
            definition = json.loads(self.store.decrypt(r['encrypted_definition']))
            missing = [rid for rid in definition['evidence_ids'] if rid not in allowed]
            active = False
            if r.get('persona_id'):
                person = self.store.get(p, 'persona', r['persona_id'])
                active = self.accessible(p, person)
            rows.append({'id': r['id'], 'version': r['version'], 'pack_id': r['pack_id'],
                'key': r['template_key'], 'definition': definition, 'missing_evidence_ids': missing,
                'persona_id': r.get('persona_id'), 'status': 'active' if active else
                    'requires_review' if r.get('persona_id') else 'blocked' if missing else 'ready'})
        return rows

    def activate_templates(self, user, body):
        owner(user)
        p = user['project_id']
        epoch = self.store.epoch(p)
        ids = strings(body.get('template_ids'), 8, 80)
        if not ids or len(ids) != len(body['template_ids']):
            raise AppError('중복 없는 페르소나 초안 1~8개를 선택하세요.')
        versions = body.get('versions', {})
        if not isinstance(versions, dict):
            raise AppError('초안 버전 목록을 확인하세요.')
        knowledge = {r['id']: r for r in self.knowledge(p)}
        inserts, updates, people = [], [], []
        for rid in ids:
            r = self.store.get(p, 'persona_template', rid)
            if r.get('persona_id'):
                # Return the current version, including any PO edits. A withdrawn
                # persona must be consciously reviewed through the persona editor.
                people.append(self.get_persona(p, r['persona_id']))
                continue
            if type(versions.get(rid)) is not int or versions[rid] != r['version']:
                raise AppError('초안이 변경됐습니다. 목록을 새로고침하세요.', 409)
            definition = json.loads(self.store.decrypt(r['encrypted_definition']))
            if any(eid not in knowledge for eid in definition['evidence_ids']):
                raise AppError('필요한 인사이트를 먼저 검토·공개하고 VoC 철회 여부를 확인하세요.', 409)
            evidence = [knowledge[eid] for eid in definition['evidence_ids']]
            person = self.save_persona(user, {**definition, 'observations': [
                {'evidence_id': e['id'], 'quote': e['text'][:500]} for e in evidence]},
                input_evidence=evidence, persist=False)
            inserts.append(('persona', person, person['id']))
            updates.append(('persona_template', rid, {'persona_id': person['id']}, r['version']))
            people.append(person)
        if inserts or updates:
            self.store.write(p, inserts=inserts, updates=updates, expected_epoch=epoch)
            self.audit(user, 'persona_templates_activated', ','.join(ids))
        return {'personas': [self.get_persona(p, r['id']) for r in people],
                'created': len(inserts), 'model_called': False,
                'note': '자료 기반 가상 프로필입니다. 실제 고객 검증이 아닙니다.'}
