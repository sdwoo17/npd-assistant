"""Remaining audit cases use synthetic fixtures and a recording model only."""
import copy
import json
import unittest
from unittest.mock import patch
from app.contracts import validate_answer, date_value
from app.store import AppError
from tests.helpers import Fixture, encoded


class RemainingGuards(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s, self.store, self.po, self.owner = self.f.service, self.f.store, self.f.po, self.f.owner
        self.p = self.po['project_id']

    def tearDown(self):
        self.f.close()

    def discussion(self):
        conv = self.f.conversation()
        self.s.post(self.po, '/api/chat', {'conversation_id': conv['id'], 'message': '소재 리포트 분석'})
        return conv

    def proposal(self, conv):
        return self.s.post(self.po, '/api/proposals', {'conversation_id': conv['id']})

    def accept(self, row):
        return self.s.post(self.po, '/api/proposals/decision', {
            'proposal_id': row['id'], 'expected_version': row['version'], 'state': 'accepted'})

    def debrief(self, conv):
        return self.s.post(self.po, '/api/debriefs', {'conversation_id': conv['id']})

    def review(self, row, summary=None):
        body = copy.deepcopy(row)
        body.update(debrief_id=row['id'], expected_version=row['version'])
        body['common_needs'][0]['text'] = '자동화는 채택하지 않는다.'
        if summary is not None:
            body['summary'] = summary
        return self.s.post(self.po, '/api/debriefs/update', body)

    def test_reused_prd_export_includes_evidence_and_accepted_proposal(self):
        conv = self.discussion()
        accepted = self.accept(self.proposal(conv))
        followup = self.s.post(self.po, '/api/conversations', {'title': '후속 검토', 'prd_id': conv['prd_id']})
        package = self.s.get(self.po, '/api/export/' + followup['id'])
        self.assertIn(self.f.insight['id'], {e['id'] for e in package['evidence']})
        self.assertIn(accepted['id'], {r['id'] for r in package['proposals']})
        self.assertIn(accepted['id'], self.s.markdown_package(package))
        self.s.insight_release(self.owner, {'insight_id': self.f.insight['id'], 'published': False})
        package = self.s.get(self.po, '/api/export/' + followup['id'])
        self.assertTrue(package['prd']['redacted'])
        self.assertEqual(package['evidence'], [])
        self.assertEqual(package['proposals'], [])

    def test_used_feature_move_is_rejected_without_partial_taxonomy_change(self):
        upload = self.s.voc_upload(self.owner, encoded('synthetic.csv',
            'external_id,text,feature,evidence_type\na,소재 리포트 분석,reporting,synthetic\n', source_name='synthetic'))
        before = self.s.features(self.p)
        epoch = self.store.epoch(self.p)
        with self.assertRaises(AppError) as caught:
            self.s.import_features(self.owner, {'features': [
                {'key': 'unused', 'name': '새 기능', 'service_id': 'new'},
                {'key': 'reporting', 'name': '리포트', 'service_id': 'new'}]})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.s.features(self.p), before)
        self.assertEqual(self.store.epoch(self.p), epoch)
        row = self.store.get(self.p, 'voc', upload['results'][0]['voc_id'])
        self.assertEqual(row['service_id'], before['reporting']['service_id'])

    def test_unused_feature_can_move_services(self):
        self.s.import_features(self.owner, {'features': [{'key': 'unused', 'name': '새 기능', 'service_id': 'first'}]})
        self.s.import_features(self.owner, {'features': [{'key': 'unused', 'name': '새 기능', 'service_id': 'second'}]})
        self.assertEqual(self.s.features(self.p)['unused']['service_id'], 'second')

    def test_review_replaces_old_summary_in_model_and_export(self):
        conv = self.discussion()
        original = self.f.model.generate
        def generated(task, payload):
            result = original(task, payload)
            if task == 'debrief':
                result['text'] = '자동화를 채택한다.'
            return result
        with patch.object(self.f.model, 'generate', side_effect=generated):
            row = self.debrief(conv)
        reviewed = self.review(row)
        self.assertNotIn('자동화를 채택한다.', reviewed['text'])
        self.assertIn('자동화는 채택하지 않는다.', reviewed['text'])
        self.proposal(conv)
        self.assertNotIn('자동화를 채택한다.', json.dumps(self.f.model.calls[-1][1], ensure_ascii=False))
        self.assertNotIn('자동화를 채택한다.', self.s.markdown_package(self.s.export_package(self.po, conv['id'])))
        self.assertIn('자동화를 채택한다.', self.store.history(self.p, 'debrief', row['id'])[0]['text'])

    def test_explicit_review_summary_is_used_consistently(self):
        conv = self.discussion()
        reviewed = self.review(self.debrief(conv), '결론은 실제 고객 인터뷰로 추가 확인한다.')
        self.assertEqual(reviewed['text'], reviewed['summary'])
        self.assertEqual(reviewed['text'], '결론은 실제 고객 인터뷰로 추가 확인한다.')

    def test_new_reviewed_debrief_invalidates_existing_proposal(self):
        conv = self.discussion()
        proposal = self.proposal(conv)
        draft = self.debrief(conv)
        self.assertFalse(self.s.proposal_stale(self.po, proposal))
        self.review(draft)
        with self.assertRaises(AppError) as caught:
            self.accept(proposal)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get(self.p, 'prd', conv['prd_id'])['version'], 1)
        self.assertTrue(self.s.get(self.po, '/api/proposals')[0]['planning_stale'])

    def test_only_selected_review_is_used_and_drafts_do_not_replace_it(self):
        conv = self.discussion()
        first = self.review(self.debrief(conv))
        self.debrief(conv)
        proposal = self.proposal(conv)
        self.assertEqual(proposal['source_debrief_versions'], [{'id': first['id'], 'version': first['version']}])
        self.assertEqual([d['id'] for d in self.f.model.calls[-1][1]['debriefs']], [first['id']])
        self.assertEqual(self.accept(proposal)['state'], 'accepted')

    def test_review_during_proposal_generation_prevents_partial_save(self):
        conv = self.discussion()
        row = self.debrief(conv)
        self.f.model.hook = lambda: self.review(row)
        with self.assertRaises(AppError) as caught:
            self.proposal(conv)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.list(self.p, 'proposal'), [])

    def test_selecting_another_review_invalidates_old_proposal(self):
        conv = self.discussion()
        first = self.review(self.debrief(conv))
        self.review(self.s.make_debrief(self.po, {'conversation_id': conv['id'], 'regenerate': True, 'reason': '검토본 후보 비교'}))
        proposal = self.proposal(conv)
        current = self.s.conversation(self.po, conv['id'])
        self.s.select_debrief(self.po, {'conversation_id': conv['id'], 'expected_version': current['version'],
            'debrief_id': first['id'], 'debrief_version': first['version']})
        with self.assertRaises(AppError):
            self.accept(proposal)

    def test_external_shaped_unknown_citations_are_rejected_atomically(self):
        conv = self.discussion()
        original = self.f.model.generate
        def generated(task, payload):
            result = original(task, payload)
            if task == 'chat':
                result['text'] += ' [INS-ADS-999999]'
            return result
        before = self.store.list(self.p, 'message')
        with patch.object(self.f.model, 'generate', side_effect=generated):
            with self.assertRaises(AppError) as caught:
                self.s.post(self.po, '/api/chat', {'conversation_id': conv['id'], 'message': '소재 리포트 분석'})
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(self.store.list(self.p, 'message'), before)
        evidence = self.s.knowledge(self.p)
        for citation in ('INS-ADS-missing', 'PROB-unknown', 'VOC-unknown', 'ins-ads-unknown'):
            with self.subTest(citation=citation), self.assertRaises(AppError):
                validate_answer({'text': '[' + citation + ']', 'evidence_ids': [self.f.insight['id']]}, evidence)

    def test_date_contract_rejects_hour_twenty_four_on_every_python(self):
        with self.assertRaises(AppError):
            date_value('2026-09-12T24:00:00Z')
        self.assertEqual(date_value('2026-09-12T23:59:59Z'), '2026-09-12T23:59:59Z')
