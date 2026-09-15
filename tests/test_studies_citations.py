"""Synthetic FGI lifecycle, isolation, concurrency and pasted citation checks."""
import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from app.studies import GUIDE_SECTIONS
from app.store import AppError
from tests.helpers import Fixture, CANARY


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s, self.po, self.owner = self.f.service, self.f.po, self.f.owner
        self.p = self.po['project_id']

    def tearDown(self):
        self.f.close()

    def post(self, path, **body):
        return self.s.post(self.po, '/api/' + path, body)

    def prepare(self, people=None):
        people = people or [self.f.persona()]
        row = self.post('studies', title='합성 소재 연구')
        row = self.post('studies/update', study_id=row['id'], expected_version=row['version'], stage='recruitment',
                        objective='소재 리포트 분석', research_questions=['판단 근거는 무엇인가요?'])
        return self.post('studies/update', study_id=row['id'], expected_version=row['version'], stage='guide',
                         recruitment_criteria='소재 리포트를 사용하는 광고주 · 가상 모집', persona_ids=[r['id'] for r in people])

    def guide(self, row):
        row = self.post('studies/guide', study_id=row['id'], expected_version=row['version'])
        return self.post('studies/guide', study_id=row['id'], expected_version=row['version'],
                         sections=row['guide']['sections'], assumptions=row['guide']['assumptions'])

    def start(self, row):
        return self.post('studies/start', study_id=row['id'], expected_version=row['version'])

    def discuss(self, row):
        return self.post('chat', conversation_id=row['conversation_id'], message='소재 리포트의 판단 근거는 무엇인가요?')

    def review(self, row):
        draft = self.post('debriefs', conversation_id=row['conversation_id'])
        return self.post('debriefs/update', **(draft | {'debrief_id': draft['id'], 'expected_version': draft['version'],
                         'summary': '실제 고객에게 판단 근거를 추가 확인한다.'}))

    def complete(self, row, review):
        current = self.s.study(self.po, row['id'])
        return self.post('studies/complete', study_id=row['id'], expected_version=current['version'],
                         debrief_id=review['id'], debrief_version=review['version'])

    def test_full_study_review_proposal_and_export_lineage(self):
        row = self.start(self.guide(self.prepare()))
        self.assertEqual(row['stage'], 'session')
        self.discuss(row)
        payload = self.f.model.calls[-1][1]
        self.assertEqual(payload['study']['id'], row['id'])
        self.assertEqual(len(payload['study']['guide']['sections']), 8)
        review = self.review(row)
        completed = self.complete(row, review)
        self.assertEqual(completed['status'], 'completed')
        proposal = self.post('proposals', conversation_id=row['conversation_id'])
        self.assertEqual(proposal['source_debrief_versions'], [{'id': review['id'], 'version': review['version']}])
        self.post('proposals/decision', proposal_id=proposal['id'], expected_version=proposal['version'], state='accepted')
        package = self.s.export_package(self.po, row['conversation_id'])
        md = self.s.markdown_package(package)
        self.assertEqual(package['study']['completed_debrief']['id'], review['id'])
        for title in GUIDE_SECTIONS:
            self.assertIn(title, md)
        self.assertIn(row['objective'], md)
        self.assertIn(self.f.insight['id'], md)
        self.assertNotIn(CANARY, json.dumps(package))
        self.assertNotIn(CANARY, json.dumps(self.f.model.calls))
        with self.assertRaises(AppError):
            self.discuss(row)
        with self.assertRaises(AppError):
            self.review(row)
        conv = self.s.conversation(self.po, row['conversation_id'])
        with self.assertRaises(AppError):
            self.post('debriefs/select', conversation_id=conv['id'], expected_version=conv['version'],
                      debrief_id=review['id'], debrief_version=review['version'])

    def test_stages_review_and_versions_prevent_orphans(self):
        with self.assertRaises(AppError):
            self.post('studies', title='불법 단계', stage='session')
        row = self.prepare()
        draft = self.post('studies/guide', study_id=row['id'], expected_version=row['version'])
        with self.assertRaises(AppError):
            self.start(draft)
        self.assertEqual(self.f.store.list(self.p, 'conversation'), [])
        self.assertEqual(self.f.store.list(self.p, 'prd'), [])
        with self.assertRaises(AppError):
            self.post('studies/guide', study_id=row['id'], expected_version=row['version'], sections=draft['guide']['sections'])
        with self.assertRaises(AppError):
            self.post('studies/guide', study_id=draft['id'], expected_version=draft['version'], sections=[None] * 8)
        row = self.guide(draft)
        running = self.start(row)
        with self.assertRaises(AppError):
            self.start(row)
        self.assertEqual(self.start(running)['id'], running['id'])
        self.assertEqual(len(self.f.store.list(self.p, 'conversation')), 1)
        with self.assertRaises(AppError):
            self.post('studies/update', study_id=running['id'], expected_version=running['version'], objective='변경')
        with self.assertRaises(AppError):
            self.post('conversations/state', conversation_id=running['conversation_id'], mode='research')

    def test_design_change_requires_guide_review_again(self):
        row = self.guide(self.prepare())
        changed = self.post('studies/update', study_id=row['id'], expected_version=row['version'], objective='다른 리포트 연구')
        self.assertIsNone(changed['guide'])
        with self.assertRaises(AppError):
            self.start(changed)

    def test_pinned_roster_survives_profile_edit_and_directed_tags(self):
        a, b = self.f.persona('광고주A'), self.f.persona('광고주B')
        row = self.guide(self.prepare([a, b]))
        self.post('personas/update', **(a | {'persona_id': a['id'], 'expected_version': a['version'], 'goals': '변경한 가상 목표'}))
        row = self.start(row)
        conv = self.post('chat', conversation_id=row['conversation_id'], message='@광고주a 소재 리포트 분석')
        self.assertEqual(conv['participant_persona_versions'], row['participants'])
        self.assertEqual(conv['messages'][-1]['persona_version'], a['version'])
        conv = self.discuss(row)
        self.assertEqual([m['persona_id'] for m in conv['messages'][-2:]], [a['id'], b['id']])
        outsider = self.f.persona('다른광고주')
        before = len(conv['messages'])
        with self.assertRaises(AppError):
            self.post('chat', conversation_id=conv['id'], message='@' + outsider['alias'] + ' 소재 리포트 분석')
        self.assertEqual(len(self.s.conversation(self.po, conv['id'])['messages']), before)

    def test_retiring_profiles_preserves_old_sessions_but_blocks_new_recruitment(self):
        person = self.f.persona()
        row = self.guide(self.prepare([person]))
        old = self.f.conversation([person['id']])
        archived = self.post('personas/archive', persona_id=person['id'], expected_version=person['version'], archived=True)
        with self.assertRaises(AppError):
            self.start(row)
        with self.assertRaises(AppError):
            self.f.conversation([person['id']])
        self.post('chat', conversation_id=old['id'], message='소재 리포트 분석')
        self.assertEqual(self.s.get(self.po, '/api/bootstrap')['personas'], [])
        self.post('personas/archive', persona_id=person['id'], expected_version=archived['version'], archived=False)
        self.start(row)

    def test_revoked_profiles_can_leave_pool_without_exposing_content(self):
        person = self.f.persona('RevokedProfileSecretName')
        self.s.insight_release(self.owner, {'insight_id': self.f.insight['id'], 'published': False})
        boot = self.s.get(self.po, '/api/bootstrap')
        self.assertEqual(boot['persona_pool_count'], 1)
        self.assertEqual(boot['personas'], [])
        unavailable = boot['unavailable_personas'][0]
        self.assertEqual(unavailable['id'], person['id'])
        self.assertNotIn('RevokedProfileSecretName', json.dumps(unavailable))
        saved = self.post('personas/archive', persona_id=unavailable['id'], expected_version=unavailable['version'], archived=True)
        self.assertTrue(saved['redacted'])
        self.assertEqual(self.s.get(self.po, '/api/bootstrap')['persona_pool_count'], 0)
        with self.assertRaises(AppError):
            self.post('personas/archive', persona_id=saved['id'], expected_version=saved['version'], archived=False)

    def test_completion_rejects_unseen_debrief_version_and_new_messages(self):
        row = self.start(self.guide(self.prepare()))
        self.discuss(row)
        review = self.review(row)
        with self.assertRaises(AppError):
            self.complete(row, review | {'version': review['version'] - 1})
        self.discuss(row)
        with self.assertRaises(AppError):
            self.complete(row, review)
        self.assertEqual(self.complete(row, self.review(row))['status'], 'completed')

    def test_revocation_redacts_whole_study_and_blocks_reuse(self):
        row = self.start(self.guide(self.prepare()))
        self.discuss(row)
        self.s.insight_release(self.owner, {'insight_id': self.f.insight['id'], 'published': False})
        self.assertTrue(self.s.study_list(self.po)[0]['redacted'])
        with self.assertRaises(AppError):
            self.discuss(row)
        package = self.s.export_package(self.po, row['conversation_id'])
        self.assertTrue(package['study']['redacted'])
        self.assertNotIn(row['objective'], json.dumps(package['study'], ensure_ascii=False))
        self.assertEqual(package['evidence'], [])
        self.s.markdown_package(package)

    def test_guide_generation_race_saves_no_stale_draft(self):
        row = self.prepare()
        self.f.model.hook = lambda: self.s.insight_release(self.owner, {'insight_id': self.f.insight['id'], 'published': False})
        with self.assertRaises(AppError):
            self.post('studies/guide', study_id=row['id'], expected_version=row['version'])
        self.assertIsNone(self.f.store.get(self.p, 'study', row['id'])['guide'])

    def test_study_is_project_scoped_and_participant_cap_does_not_delete_legacy(self):
        people = [self.f.persona('광고주' + chr(65 + i)) for i in range(8)]
        row = self.prepare(people[:6])
        with self.assertRaises(AppError):
            self.post('studies/update', study_id=row['id'], expected_version=row['version'], persona_ids=[r['id'] for r in people])
        self.assertEqual(len(self.f.conversation([r['id'] for r in people])['persona_ids']), 8)
        self.assertEqual(self.s.study_list(self.f.other), [])
        with self.assertRaises(AppError) as caught:
            self.s.study(self.f.other, row['id'])
        self.assertEqual(caught.exception.status, 404)

    def test_pool_cap_is_transactional_under_concurrent_creation_and_restore(self):
        people = [self.f.persona('광고주' + str(i)) for i in range(99)]
        def create(i):
            try:
                return self.f.persona('동시' + str(i))
            except AppError as e:
                return e.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertIn(409, results)
        archived = self.post('personas/archive', persona_id=people[0]['id'], expected_version=people[0]['version'], archived=True)
        self.f.persona('추가광고주')
        with self.assertRaises(AppError):
            self.post('personas/archive', persona_id=archived['id'], expected_version=archived['version'], archived=False)
        self.assertTrue(self.f.store.get(self.p, 'persona', archived['id'])['archived'])


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s, self.po = self.f.service, self.f.po
        self.e = self.s.knowledge(self.po['project_id'])[0]

    def tearDown(self):
        self.f.close()

    def check(self, **body):
        return self.s.post(self.po, '/api/citations/verify', body)

    def test_unique_counts_versions_and_no_storage_or_model_call(self):
        rid, version = self.e['id'], self.e['version']
        before = self.f.store.epoch(self.po['project_id'])
        result = self.check(text=f'[{rid}] [{rid}] v{version} [{rid}] v999 [INS-ADS-404]')
        self.assertEqual((result['total_citations'], result['unique_citations'], result['verified_unique']), (4, 2, 1))
        self.assertEqual(result['counts'], {'verified': 2, 'version_mismatch': 1, 'unknown_or_unavailable': 1})
        self.assertEqual(result['version_unchecked'], 1)
        self.assertFalse(result['stored'])
        self.assertFalse(result['model_called'])
        self.assertEqual(self.f.model.calls, [])
        self.assertEqual(self.f.store.epoch(self.po['project_id']), before)
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(self.check(text=f'[{rid}]', expected_versions={rid: version})['version_unchecked'], 0)

    def test_private_foreign_and_unknown_are_indistinguishable(self):
        private = self.s.post(self.f.owner, '/api/insights', {'source_id': self.f.source['id'], 'title': 'private', 'text': '비공개', 'feature': 'reporting'})
        foreign = self.f.store.put(self.f.other['project_id'], 'insight', {'text': 'foreign', 'published': True})
        result = self.check(text=' '.join('[' + rid + ']' for rid in [private['id'], foreign['id'], 'INS-NOT-FOUND']))
        for row in result['references']:
            self.assertEqual(row['status'], 'unknown_or_unavailable')
            self.assertEqual(set(row), {'id', 'occurrences', 'requested_version', 'status'})
        self.s.insight_release(self.f.owner, {'insight_id': self.e['id'], 'published': False})
        row = self.check(text='[' + self.e['id'] + ']')['references'][0]
        self.assertEqual(row['status'], 'retracted')
        self.assertNotIn('title', row)

    def test_empty_unrelated_text_and_invalid_version_input(self):
        self.assertEqual(self.check(text='[a normal Markdown label]')['total_citations'], 0)
        for versions in ([], {'x': True}, {'x': 0}, {'x': '2'}):
            with self.subTest(versions=versions), self.assertRaises(AppError):
                self.check(text='', expected_versions=versions)

    def test_imported_prd_keeps_known_lineage_and_marks_unresolved_references(self):
        rid = self.e['id']
        prd = self.s.post(self.po, '/api/prds', {'title': '합성 외부 PRD', 'sections': [
            {'id': 'problem', 'title': '문제', 'text': f'가설 [{rid}] [INS-EXTERNAL-UNKNOWN]'}]})
        self.assertEqual(prd['unresolved_citation_ids'], ['INS-EXTERNAL-UNKNOWN'])
        conv = self.s.post(self.po, '/api/conversations', {'title': '이어 검토', 'prd_id': prd['id']})
        self.assertIn(rid, {r['id'] for r in self.s.export_package(self.po, conv['id'])['evidence']})
        self.s.insight_release(self.f.owner, {'insight_id': rid, 'published': False})
        self.assertTrue(self.s.export_package(self.po, conv['id'])['prd']['redacted'])
