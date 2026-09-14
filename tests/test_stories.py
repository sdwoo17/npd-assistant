import base64
import copy
import io
import json
import unittest
from PIL import Image
from app.model import BedrockModel
from app.store import AppError
from app.story_contracts import extraction
from tests.helpers import Fixture, CANARY
from tests.story_fixture import StoryModel, story_result


class StoriesTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s, self.po = self.f.service, self.f.po
        self.s.model = StoryModel()

    def tearDown(self):
        self.f.close()

    def post(self, route, body):
        return self.s.post(self.po, route, body)

    def asset(self, image=False):
        data = {'title': 'PO 기획 원본', 'input_type': 'text', 'text': '표본 부족이면 광고를 자동 중단하지 않는다.'}
        if image:
            out = io.BytesIO()
            Image.new('RGB', (200, 120), 'white').save(out, format='PNG')
            data = {'title': '이미지 테스트', 'input_type': 'image', 'filename': 'plan.png', 'content_base64': base64.b64encode(out.getvalue()).decode()}
        return self.post('/api/planning-assets', data)

    def draft(self, image=False):
        a = self.asset(image)
        r = self.post('/api/stories/extract', {'asset_id': a['id'], 'request_id': 'first'})
        return self.post('/api/stories/apply-candidate', {'extraction_id': r['id'], 'candidate_index': 0}), a, r

    def ready(self):
        story, a, r = self.draft()
        q = [{**q, 'state': 'answered', 'response': '실제 데이터로 기준을 검증한다'} for q in story['questions']]
        story = self.post('/api/stories', {'story_id': story['id'], 'expected_version': story['version'], 'questions': q,
            'evidence_ids': [self.f.insight['id']], 'validation_plan': '광고주 리허설'})
        return story, a, r

    def confirm(self, story):
        return self.post('/api/stories/confirm', {'story_id': story['id'], 'expected_version': story['version'], 'reviewed_source': True, 'reviewed_criteria': True})

    def test_po_upload_scope_encryption_and_no_raw_research(self):
        a = self.asset(True)
        self.assertFalse(any(k.startswith('encrypted') for k in a))
        stored = self.f.store.get('project-a', 'planning_asset', a['id'])
        self.assertIn('encrypted_original', stored)
        self.assertNotIn('image_base64', json.dumps(self.s.get(self.po, '/api/stage2')))
        self.assertEqual(self.s.planning_asset_content(self.po, a['id'])['mime'], 'image/jpeg')
        with self.assertRaises(AppError):
            self.s.planning_asset_content(self.f.other, a['id'])
        with self.assertRaises(AppError):
            self.s.research_upload(self.po, {})
        self.assertNotIn(CANARY, json.dumps(self.s.get(self.po, '/api/stage2')))

    def test_duplicate_upload_and_request_are_idempotent(self):
        a = self.asset()
        self.assertEqual(a['id'], self.asset()['id'])
        r = self.post('/api/stories/extract', {'asset_id': a['id'], 'request_id': 'once'})
        self.assertEqual(r['id'], self.post('/api/stories/extract', {'asset_id': a['id'], 'request_id': 'once'})['id'])
        self.assertEqual(len(self.s.model.calls), 1)
        with self.assertRaises(AppError):
            self.post('/api/stories/extract', {'asset_id': a['id'], 'request_id': 'once', 'notes': '다른 입력'})
        with self.assertRaises(AppError):
            self.post('/api/stories/extract', {'asset_id': a['id'], 'request_id': 'invalid', 'notes': []})
        self.assertEqual(len(self.f.store.list('project-a', 'story_extraction')), 1)

    def test_image_content_and_draft_provenance(self):
        story, a, r = self.draft(True)
        self.assertIsInstance(self.s.model.last_image['source']['bytes'], bytes)
        self.assertEqual(story['origins']['exceptions']['region_ids'], ['r1'])
        self.assertEqual(story['definition_status'], 'draft')
        self.assertEqual(story['validation_status'], 'unverified')
        self.assertNotIn(CANARY, json.dumps(self.s.model.calls))

    def test_confirmation_blocks_questions_and_does_not_validate_customer(self):
        story, _, _ = self.draft()
        with self.assertRaises(AppError): self.confirm(story)
        q = [{**q, 'state': 'excluded', 'response': '이번 MVP는 상태 표시만 검증한다'} for q in story['questions']]
        story = self.post('/api/stories', {'story_id': story['id'], 'expected_version': 1, 'questions': q, 'validation_plan': 'PO 관찰 테스트'})
        confirmed = self.confirm(story)
        self.assertEqual(confirmed['definition_status'], 'confirmed')
        self.assertEqual(confirmed['validation_status'], 'planned')
        self.assertEqual(confirmed['approval']['confirmed_version'], confirmed['version'])
        with self.assertRaises(AppError): self.confirm(story)

    def test_edit_and_reanalysis_preserve_manual_fields_and_approval_requires_review(self):
        story, a, _ = self.ready()
        confirmed = self.confirm(story)
        edited = self.post('/api/stories', {'story_id': story['id'], 'expected_version': confirmed['version'], 'goal': 'PO가 직접 정한 목표'})
        self.assertIsNone(edited['approval'])
        run = self.post('/api/stories/extract', {'asset_id': a['id'], 'request_id': 'again'})
        self.assertEqual(self.f.store.get('project-a', 'user_story', story['id'])['goal'], 'PO가 직접 정한 목표')
        applied = self.post('/api/stories/apply-candidate', {'story_id': story['id'], 'expected_version': edited['version'],
            'extraction_id': run['id'], 'candidate_index': 0, 'fields': ['title']})
        self.assertEqual(applied['goal'], 'PO가 직접 정한 목표')
        self.assertEqual(applied['acceptance_criteria'], edited['acceptance_criteria'])
        self.assertGreater(len(self.s.get(self.po, '/api/stories/versions/'+story['id'])), 2)

    def test_context_change_invalidates_confirmed_story(self):
        c = self.post('/api/stage2/context', {'customer': '광고 운영자'})
        story, _, _ = self.ready()
        self.confirm(story)
        self.post('/api/stage2/context', {'expected_version': c['version'], 'customer': '대행사 운영자'})
        self.assertTrue(self.s.get(self.po, '/api/stage2')['stories'][0]['redacted'])
        with self.assertRaises(AppError): self.s.stage_export(self.po, {})

    def test_withdrawal_hides_story_history_and_blocks_exports(self):
        story, a, _ = self.ready()
        confirmed = self.confirm(story)
        self.post('/api/planning-assets/withdraw', {'asset_id': a['id'], 'expected_version': 1})
        self.assertTrue(all(r['redacted'] for r in self.s.get(self.po, '/api/stories/versions/'+story['id'])))
        with self.assertRaises(AppError): self.confirm(confirmed)
        with self.assertRaises(AppError): self.s.stage_export(self.po, {})
        with self.assertRaises(AppError): self.asset()

    def test_research_withdrawal_hides_linked_stories(self):
        story, _, _ = self.ready()
        self.confirm(story)
        self.s.post(self.f.owner, '/api/insights/release', {'insight_id': self.f.insight['id'], 'published': False})
        self.assertTrue(self.s.get(self.po, '/api/stage2')['stories'][0]['redacted'])

    def test_model_race_has_no_partial_story_or_completed_run(self):
        a = self.asset()
        self.s.model.hook = lambda: self.post('/api/planning-assets/withdraw', {'asset_id': a['id'], 'expected_version': 1})
        with self.assertRaises(AppError): self.post('/api/stories/extract', {'asset_id': a['id'], 'request_id': 'race'})
        self.assertEqual(self.f.store.list('project-a', 'story_extraction')[0]['status'], 'failed')
        self.assertEqual(self.f.store.list('project-a', 'user_story'), [])

    def test_malformed_image_and_regions_fail_closed(self):
        with self.assertRaises(AppError): self.post('/api/planning-assets', {'title': 'bad', 'filename': 'bad.png', 'content_base64': 'AAAA'})
        result = story_result(); result['regions'][0]['x'] = 1.1
        with self.assertRaises(AppError): extraction(result)
        result = story_result(); result['candidates'][0]['origins'][0] = {'field': 'title', 'origin': 'from_source', 'region_ids': ['invented']}
        with self.assertRaises(AppError): extraction(result)

    def test_unconfigured_visual_has_no_canned_fallback(self):
        a = self.asset(True)
        self.s.model = BedrockModel(model='')
        with self.assertRaises(AppError): self.post('/api/stories/extract', {'asset_id': a['id'], 'request_id': 'no-model'})
        self.assertEqual(self.f.store.list('project-a', 'user_story'), [])

    def test_split_and_merge_preserve_lineage_questions_and_versions(self):
        story, _, _ = self.ready()
        children = self.post('/api/stories/reorganize', {'story_ids': [story['id']], 'versions': {story['id']: story['version']},
            'stories': [{'title': '비교 상태'}, {'title': '보류 사유'}]})
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0]['questions'], story['questions'])
        self.assertEqual(children[0]['ancestry'][0]['id'], story['id'])
        merged = self.post('/api/stories/reorganize', {'story_ids': [r['id'] for r in children], 'versions': {r['id']: r['version'] for r in children}, 'stories': [{'title': '통합'}]})
        self.assertEqual(len(merged[0]['ancestry']), 2)
        with self.assertRaises(AppError): self.confirm(story)

    def test_requirements_export_prd_pin_exact_confirmed_versions(self):
        story, _, _ = self.ready()
        story = self.confirm(story)
        req = self.post('/api/story-requirements', {'story_id': story['id'], 'story_version': story['version'], 'title': '판단 보류 표시', 'condition': '표본 부족', 'behavior': '보류 상태 표시'})
        package = self.s.stage_export(self.po, {})
        prd = self.post('/api/stage2/prd', {'title': '스토리 PRD', 'story_versions': {story['id']: story['version']}, 'requirement_versions': {req['id']: req['version']}, 'context_version': None})
        self.assertEqual(package['schema_version'], 'npd.story-package.v1')
        self.assertNotIn('encrypted_', json.dumps(package))
        self.assertNotIn(CANARY, self.s.stage_export(self.po, {'format': 'markdown'})['text'])
        self.assertTrue(self.s.accessible('project-a', prd))
        self.post('/api/stories', {'story_id': story['id'], 'expected_version': story['version'], 'title': '변경'})
        self.assertFalse(self.s.accessible('project-a', req))
        self.assertFalse(self.s.accessible('project-a', prd))

    def test_withdrawn_evidence_cannot_be_removed_to_reveal_derived_text(self):
        story, _, run = self.ready()
        self.s.post(self.f.owner, '/api/insights/release', {'insight_id': self.f.insight['id'], 'published': False})
        with self.assertRaises(AppError):
            self.post('/api/stories', {'story_id': story['id'], 'expected_version': story['version'], 'evidence_ids': []})
        repeat = self.post('/api/stories/apply-candidate', {'extraction_id': run['id'], 'candidate_index': 0})
        self.assertTrue(repeat['redacted'])

    def test_product_context_only_change_can_be_explicitly_reviewed(self):
        context = self.post('/api/stage2/context', {'customer': '광고주'})
        story, _, _ = self.ready()
        confirmed = self.confirm(story)
        self.post('/api/stage2/context', {'expected_version': context['version'], 'customer': '대행사'})
        reviewed = self.post('/api/stories', {'story_id': story['id'], 'expected_version': confirmed['version']})
        self.assertEqual(reviewed['definition_status'], 'draft')
        self.assertIsNone(reviewed['approval'])
        self.assertEqual(reviewed['context_ref']['version'], 2)

    def test_selected_acceptance_criteria_only_apply_and_malformed_questions(self):
        story, _, run = self.ready()
        original_goal = story['goal']
        updated = self.post('/api/stories/apply-candidate', {'story_id': story['id'], 'expected_version': story['version'],
            'extraction_id': run['id'], 'candidate_index': 0, 'fields': ['acceptance_criteria']})
        self.assertEqual(updated['goal'], original_goal)
        self.assertEqual(updated['origins']['acceptance_criteria']['origin'], 'ai_proposed')
        with self.assertRaises(AppError):
            self.post('/api/stories', {'title': 'invalid', 'questions': [123]})
        with self.assertRaises(AppError):
            self.post('/api/stories', {'title': 'invalid', 'questions': [{'id': {}, 'text': 'bad'}]})

    def test_baseline_prd_is_supplied_and_its_version_is_guarded(self):
        prd = self.post('/api/prds', {'title': '기존 상품', 'sections': [{'id': 'existing', 'title': '현행', 'text': '기존에는 수동 비교만 지원한다'}]})
        self.post('/api/stage2/context', {'customer': '광고주', 'prd_id': prd['id']})
        story, _, run = self.draft()
        self.assertEqual(self.s.model.calls[-1][1]['baseline_prd']['version'], 1)
        self.assertEqual(story['baseline_refs'][0]['id'], prd['id'])
        self.post('/api/prds/update', {'prd_id': prd['id'], 'expected_version': 1, 'title': '변경', 'sections': prd['sections']})
        self.assertFalse(self.s.accessible('project-a', run))
        self.assertFalse(self.s.accessible('project-a', story))
        with self.assertRaises(AppError): self.post('/api/stories', {'story_id': story['id'], 'expected_version': story['version']})

    def test_concurrent_confirmation_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        story, _, _ = self.ready()
        def attempt(_):
            try: return self.confirm(story)['definition_status']
            except AppError as exc: return exc.status
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(results.count('confirmed'), 1)
        self.assertEqual(results.count(409), 3)

    def test_restart_recovers_running_extraction_without_creating_story(self):
        self.f.store.put('project-a', 'story_extraction', {'request_id': 'interrupted', 'status': 'running', 'dependencies': []})
        self.assertEqual(self.f.store.recover_jobs(), 1)
        self.assertEqual(self.f.store.list('project-a', 'story_extraction')[0]['status'], 'failed')
        self.assertEqual(self.f.store.list('project-a', 'user_story'), [])

    def test_bedrock_converse_contains_image_block(self):
        class Client:
            def converse(self, **kwargs):
                self.request = kwargs
                return {'stopReason': 'end_turn', 'output': {'message': {'content': [{'text': json.dumps(story_result())}]}}}
        c = Client()
        model = BedrockModel(model='test-vision', client=c)
        image = {'format': 'jpeg', 'source': {'bytes': b'synthetic-test-image'}}
        model.generate_visual('story_extract', {'input_kind': 'image'}, image)
        self.assertEqual(c.request['messages'][0]['content'][1], {'image': image})


if __name__ == '__main__': unittest.main()
