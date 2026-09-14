"""Explicit recovery after a planning original revision; revoked evidence stays hidden."""
import copy
from .contracts import text, revision
from .store import AppError


class StoryRecovery:
    def source_recovery_state(self, user, rid):
        p = user['project_id']
        row = self.store.get(p, 'user_story', rid)
        # Only project-private planning originals may be rebased. Other evidence,
        # including evidence reached through a document/persona, must remain current.
        other = {**row, 'dependencies': [d for d in row.get('dependencies', []) if d['kind'] != 'planning_asset']}
        if not self.accessible(p, other):
            raise AppError('공유 근거 또는 연결 문서를 사용할 수 없습니다. 해당 자료를 먼저 검토하세요.', 409)
        changes, sources = [], {}
        for dep in row.get('dependencies', []):
            if dep['kind'] != 'planning_asset':
                continue
            current = self.asset(user, dep['id'])  # A withdrawn original must never be recovered.
            historical = next((r for r in self.store.history(p, 'planning_asset', dep['id']) if r['version'] == dep['version']), None)
            if not historical or historical.get('withdrawn') or historical['purpose'] != current['purpose']:
                raise AppError('기획 원본의 공유 범위가 변경됐습니다.', 409)
            sources[current['id']] = {'kind': 'planning_asset', 'id': current['id'], 'version': current['version']}
            if historical['version'] != current['version']:
                change = {'previous': self.asset_metadata(historical), 'current': self.asset_metadata(current)}
                if change not in changes:
                    changes.append(change)
        if not changes:
            raise AppError('버전 재검토가 필요한 기획 원본이 없습니다.', 409)
        dependencies = other['dependencies'] + list(sources.values())
        return row, changes, dependencies

    def preview_story_recovery(self, user, body):
        p = user['project_id']; epoch = self.store.epoch(p)
        row, changes, deps = self.source_recovery_state(user, text(body, 'story_id', 80))
        if row['version'] != revision(body):
            raise AppError('스토리가 변경됐습니다. 다시 비교하세요.', 409)
        self.planning_actor(user)
        review = self.store.write(p, inserts=[('story_source_review', {'story_id': row['id'], 'story_version': row['version'],
            'dependencies': deps, 'changes': [{'id': c['current']['id'], 'from_version': c['previous']['version'],
                'to_version': c['current']['version']} for c in changes], 'created_by': user['id']}, None)], expected_epoch=epoch)[0]
        return {'review_id': review['id'], 'story': row, 'changes': changes,
            'disclosure': '이전 PO 편집을 유지한 새 초안을 만듭니다. 변경 원본의 조건·역할·수용 기준은 다시 검토해야 합니다.'}

    def recover_story_sources(self, user, body):
        p = user['project_id']; epoch = self.store.epoch(p)
        review = self.store.get(p, 'story_source_review', text(body, 'review_id', 80))
        rid = text(body, 'story_id', 80)
        row, changes, deps = self.source_recovery_state(user, rid)
        if review['created_by'] != user['id'] or review['story_id'] != rid or review['story_version'] != revision(body) or row['version'] != revision(body) or review['dependencies'] != deps:
            raise AppError('비교한 스토리·원본 버전이 변경됐습니다. 다시 비교하세요.', 409)
        if body.get('acknowledged') is not True:
            raise AppError('이전 원본과 현재 원본의 변경 내용을 확인하세요.')
        note = text(body, 'note', 3000)
        source_versions = {d['id']: d['version'] for d in deps if d['kind'] == 'planning_asset'}
        provenance = copy.deepcopy(row.get('provenance', {}))
        for ref in provenance.values():
            if ref.get('asset_id') in source_versions and ref.get('asset_version') != source_versions[ref['asset_id']]:
                ref['historical_source'] = True  # Preserve original coordinates/quotes; never relabel as current.
        questions = copy.deepcopy(row['questions'])
        if len(questions) >= 40:
            raise AppError('확인 질문이 40개입니다. 정리한 새 스토리로 검토하세요.', 409)
        questions.append({'id': 'source-review-'+review['id'], 'text': '변경된 기획 원본의 역할·조건·수용 기준과 이전 출처를 다시 확인했나요?',
            'critical': True, 'status': 'unanswered', 'answer': ''})
        updates = {'dependencies': deps, 'source_refs': [{'id': r['id'], 'version': source_versions[r['id']]} for r in row['source_refs']],
            'provenance': provenance, 'questions': questions, 'definition_status': 'draft',
            'customer_validation': 'planned' if row.get('customer_validation') == 'actual_results' else row.get('customer_validation', 'unverified'),
            'source_review': {'id': review['id'], 'changes': review['changes'], 'note': note, 'reviewed_by': user['id']},
            'updated_by': user['id']}
        self.planning_actor(user)
        return self.store.write(p, updates=[('user_story', rid, updates, row['version'])], expected_epoch=epoch)[0]
