"""Requirements regressions using synthetic evidence and an explicit model double."""
import copy
import json
import unittest
from unittest.mock import patch
from app.ingest import classify_all, parse_csv
from app.store import AppError
from tests.helpers import Fixture, encoded


class RequirementsAuditTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s, self.store = self.f.service, self.f.store
        self.po, self.owner = self.f.po, self.f.owner
        self.p = self.po['project_id']

    def tearDown(self):
        self.f.close()

    def discussion(self):
        conv = self.f.conversation()
        self.s.post(self.po, '/api/chat', {'conversation_id': conv['id'], 'message': '소재 리포트 분석'})
        return conv

    def debrief(self, conv):
        return self.s.post(self.po, '/api/debriefs', {'conversation_id': conv['id']})

    def additional_evidence(self):
        row = self.s.post(self.owner, '/api/insights', {
            'source_id': self.f.source['id'], 'title': '후속 근거',
            'text': '승인 전 비교 조건 검토가 필요하다.', 'feature': 'reporting'})
        return self.s.post(self.owner, '/api/insights/release', {'insight_id': row['id'], 'published': True})

    def review(self, row, extra):
        body = copy.deepcopy(row)
        body.update(debrief_id=row['id'], expected_version=row['version'])
        body['common_needs'][0].update(text='추가 근거로 편집한 요구', evidence_ids=[extra['id']])
        return self.s.post(self.po, '/api/debriefs/update', body)

    def proposal(self, conv):
        return self.s.post(self.po, '/api/proposals', {'conversation_id': conv['id']})

    def accept(self, proposal):
        return self.s.post(self.po, '/api/proposals/decision', {'proposal_id': proposal['id'], 'expected_version': proposal['version'], 'state': 'accepted'})

    def test_empty_project_returns_no_evidence_without_model_call(self):
        user = self.f.other
        conv = self.s.post(user, '/api/conversations', {'title': 'Empty project'})
        result = self.s.post(user, '/api/chat', {'conversation_id': conv['id'], 'message': 'Please analyze advertiser needs'})
        self.assertEqual(result['messages'][-1]['status'], 'no_evidence')
        self.assertEqual(self.f.model.calls, [])

    def test_reviewed_evidence_is_exported_and_revocation_hides_debrief(self):
        conv = self.discussion()
        row = self.debrief(conv)
        extra = self.additional_evidence()
        reviewed = self.review(row, extra)
        package = self.s.get(self.po, '/api/export/' + conv['id'])
        self.assertIn(extra['id'], {e['id'] for e in package['evidence']})
        self.assertIn(extra['id'], {d['id'] for d in reviewed['dependencies']})
        self.s.post(self.owner, '/api/insights/release', {'insight_id': extra['id'], 'published': False})
        self.assertEqual(self.s.get(self.po, '/api/debriefs'), [])
        self.assertNotIn('추가 근거로 편집한 요구', json.dumps(self.s.get(self.po, '/api/export/' + conv['id']), ensure_ascii=False))

    def test_legacy_review_without_added_evidence_lineage_is_hidden(self):
        conv = self.discussion()
        row = self.debrief(conv)
        extra = self.additional_evidence()
        groups = copy.deepcopy(row['common_needs'])
        groups[0].update(text='LEGACY_UNTRACKED_CLAIM', evidence_ids=[extra['id']])
        self.store.update(self.p, 'debrief', row['id'], {'common_needs': groups, 'review_status': 'po_reviewed'}, row['version'])
        self.assertEqual(self.s.get(self.po, '/api/debriefs'), [])
        self.assertNotIn('LEGACY_UNTRACKED_CLAIM', json.dumps(self.s.get(self.po, '/api/export/' + conv['id'])))

    def test_review_rejects_revocation_during_save(self):
        conv = self.discussion()
        row = self.debrief(conv)
        extra = self.additional_evidence()
        original = self.store.write
        def race(project, *args, **kwargs):
            if any(u[0] == 'debrief' for u in kwargs.get('updates', [])):
                original(project, updates=[('insight', extra['id'], {'published': False}, extra['version'])])
            return original(project, *args, **kwargs)
        with patch.object(self.store, 'write', side_effect=race):
            with self.assertRaises(AppError) as caught:
                self.review(row, extra)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(self.p, 'debrief', row['id'])['version'], row['version'])

    def test_proposal_uses_reviewed_debrief_and_tracks_its_evidence(self):
        conv = self.discussion()
        row = self.debrief(conv)
        extra = self.additional_evidence()
        reviewed = self.review(row, extra)
        proposal = self.proposal(conv)
        payload = self.f.model.calls[-1][1]
        self.assertEqual(payload['debriefs'][0]['common_needs'], reviewed['common_needs'])
        self.assertIn(extra['id'], {e['id'] for e in payload['evidence']})
        self.assertEqual(proposal['source_debrief_versions'], [{'id': row['id'], 'version': reviewed['version']}])
        self.s.post(self.owner, '/api/insights/release', {'insight_id': extra['id'], 'published': False})
        with self.assertRaises(AppError):
            self.accept(proposal)

    def test_changed_debrief_prevents_stale_proposal_acceptance(self):
        conv = self.discussion()
        row = self.debrief(conv)
        proposal = self.proposal(conv)
        self.review(row, self.f.insight)
        with self.assertRaises(AppError) as caught:
            self.accept(proposal)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(self.p, 'prd', conv['prd_id'])['version'], 1)
        self.assertEqual(self.store.get(self.p, 'proposal', proposal['id'])['state'], 'draft')

    def test_new_po_decision_prevents_old_proposal_acceptance(self):
        conv = self.discussion()
        proposal = self.proposal(conv)
        self.s.post(self.po, '/api/conversations/decisions', {'conversation_id': conv['id'], 'text': '자동 실행 제외'})
        with self.assertRaises(AppError) as caught:
            self.accept(proposal)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(self.p, 'prd', conv['prd_id'])['version'], 1)

    def test_decision_change_during_generation_does_not_save_proposal(self):
        conv = self.discussion()
        self.f.model.hook = lambda: self.s.post(self.po, '/api/conversations/decisions', {'conversation_id': conv['id'], 'text': '새 결정'})
        with self.assertRaises(AppError) as caught:
            self.proposal(conv)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.list(self.p, 'proposal'), [])

    def test_decision_change_during_acceptance_rolls_back_prd(self):
        conv = self.discussion()
        proposal = self.proposal(conv)
        current = self.store.get(self.p, 'conversation', conv['id'])
        original = self.store.write
        def race(project, *args, **kwargs):
            if any(u[0] == 'prd' for u in kwargs.get('updates', [])):
                original(project, updates=[('conversation', conv['id'], {'objective': '다른 기획 범위'}, current['version'])])
            return original(project, *args, **kwargs)
        with patch.object(self.store, 'write', side_effect=race):
            with self.assertRaises(AppError) as caught:
                self.accept(proposal)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(self.p, 'prd', conv['prd_id'])['version'], 1)

    def test_po_voc_edit_masks_pii_and_allows_explicit_clear(self):
        result = self.s.post(self.owner, '/api/voc/upload', encoded('synthetic.csv', 'external_id,text,evidence_type\na,소재 분석,synthetic\n', source_name='synthetic'))
        rid = result['results'][0]['voc_id']
        edited = self.s.post(self.po, '/api/voc/feature', {'voc_id': rid, 'feature_ids': ['reporting'],
            'problem': '연락 test@example.test', 'need': '010-1234-5678 연락'})
        content = json.dumps(self.s.get(self.po, '/api/evidence'))
        self.assertNotIn('test@example.test', content)
        self.assertNotIn('010-1234-5678', content)
        cleared = self.s.post(self.po, '/api/voc/feature', {'voc_id': rid, 'feature_ids': ['reporting'],
            'expected_version': edited['version'], 'problem': '', 'need': ''})
        self.assertEqual((cleared['problem'], cleared['need']), ('', ''))

    def test_parent_service_edit_validates_existing_children_atomically(self):
        parent = {'key': 'parent', 'name': '부모', 'service_id': 'a', 'terms': []}
        child = {'key': 'child', 'name': '자식', 'service_id': 'a', 'parent_feature_id': 'parent', 'terms': []}
        self.s.post(self.owner, '/api/features/import', {'features': [parent, child]})
        before = self.s.features(self.p)
        with self.assertRaises(AppError):
            self.s.post(self.owner, '/api/features/import', {'features': [{**parent, 'service_id': 'b'}]})
        self.assertEqual(self.s.features(self.p), before)

    def test_initial_rules_do_not_mix_services(self):
        tree = {'alpha': {'terms': ['alpha'], 'service_id': 'a'}, 'beta': {'terms': ['beta'], 'service_id': 'b'}}
        linked = classify_all('alpha beta', tree)
        self.assertEqual(len({tree[k]['service_id'] for k in linked}), 1)
        rows, errors = parse_csv('external_id,text\na,alpha beta\n', tree)
        self.assertEqual(errors, [])
        self.assertTrue(all(tree[k]['service_id'] == rows[0]['service_id'] for k in rows[0]['feature_ids']))

    def test_markdown_preserves_prd_section_and_applied_version(self):
        conv = self.discussion()
        proposal = self.accept(self.proposal(conv))
        result = self.s.get(self.po, '/api/export/' + conv['id'] + '?format=markdown')['text']
        self.assertIn('PRD ID: ' + conv['prd_id'], result)
        self.assertIn('반영 PRD: ' + conv['prd_id'] + ' v' + str(proposal['applied_prd_version']), result)
        self.assertIn('문단 ID: ' + proposal['changes'][0]['section_id'], result)
        self.assertIn('제안 ID: ' + proposal['id'], result)


if __name__ == '__main__':
    unittest.main()
