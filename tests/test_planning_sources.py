"""Synthetic crop, PO revision, input-order and source recovery acceptance checks."""
import base64
import copy
import io
import unittest
from unittest.mock import patch
from PIL import Image
from app.store import AppError
from app.planning_regions import original_rect
from tests.helpers import Fixture, encoded
from tests.test_stage2 import PlanningModel


class PlanningSourceTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(); self.s = self.f.service; self.u = self.f.po
        self.model = PlanningModel(); self.s.model = self.model
    def tearDown(self):
        self.f.close()
    def post(self, path, body):
        return self.s.post(self.u, path, body)
    def asset(self, title='기획 원본', content='광고주'):
        return self.post('/api/planning-assets', encoded('sketch.md', content, title=title))
    def revise(self, asset, content='수정된 광고주 조건'):
        return self.post('/api/planning-assets', encoded('revised.md', content, title=asset['title'],
            asset_id=asset['id'], expected_version=asset['version']))
    def image(self):
        picture = Image.new('RGB', (80, 40), 'white')
        for x in range(20, 60):
            for y in range(10, 30): picture.putpixel((x, y), (220, 30, 20))
        output = io.BytesIO(); picture.save(output, format='PNG')
        return self.post('/api/planning-assets', {'filename': 'synthetic.png', 'title': '합성 색상 영역',
            'content_base64': base64.b64encode(output.getvalue()).decode()})
    def extract(self, asset, **options):
        return self.post('/api/planning-assets/extract', {'asset_id': asset['id'], 'expected_version': asset['version'], 'prompt': '역할 확인', **options})
    def story(self, asset, **options):
        return self.post('/api/stories', {'actor':'광고주', 'action':'조건 확인', 'value':'PO가 정한 가치',
            'problem':'판단 기준 부족', 'new_problem':True, 'validation_task':'실제 광고주 검증',
            'acceptance_criteria':[{'given':'지표 조회','when':'조건 변경','then':'선택 조건 표시'}],
            'source_refs':[{'id':asset['id'],'version':asset['version']}], **options})
    def preview(self, story):
        return self.post('/api/stories/recovery-preview', {'story_id':story['id'], 'expected_version':story['version']})
    def recover(self, preview, **options):
        return self.post('/api/stories/recover', {'story_id':preview['story']['id'], 'expected_version':preview['story']['version'],
            'review_id':preview['review_id'], 'note':'원본 변경을 비교함', 'acknowledged':True, **options})
    def test_crop_rotation_sends_only_selected_pixels_and_maps_regions_to_original(self):
        asset = self.image(); original_generate = self.model.generate; received = []
        def capture(task, payload, images=None):
            if images:
                with Image.open(io.BytesIO(images[0]['bytes'])) as cropped:
                    received.append((cropped.size, cropped.getpixel((cropped.width//2, cropped.height//2))))
            return original_generate(task, payload, images=images)
        with patch.object(self.model, 'generate', side_effect=capture):
            run = self.extract(asset, crop=[.25,.25,.5,.5], rotation=90)
        self.assertEqual(received[0][0], (20,40))
        self.assertGreater(received[0][1][0], 180); self.assertLess(received[0][1][1], 70)
        self.assertEqual(run['regions'][0]['bbox'], [.25,.5,.25,.25])
        self.assertEqual(run['view']['rotation'], 90)
        stored = self.f.store.get(self.u['project_id'], 'planning_asset', asset['id'])
        self.assertEqual(stored['version'], 1); self.assertEqual(stored['hash'], asset['hash'])
    def test_all_quarter_turns_map_normalized_region_corners(self):
        expected = {0:[.25,.30,.20,.15],90:[.30,.45,.15,.20],180:[.45,.45,.20,.15],270:[.45,.25,.15,.20]}
        for rotation, wanted in expected.items():
            actual = original_rect([.1,.2,.4,.3], {'crop':[.2,.2,.5,.5], 'rotation':rotation})
            for a,b in zip(actual,wanted): self.assertAlmostEqual(a,b)
    def test_invalid_crop_rotation_and_document_crop_do_not_start_jobs(self):
        asset = self.image()
        for options in ({'crop':[-.1,0,1,1]}, {'crop':[0,0,0,.5]}, {'rotation':True}, {'rotation':45}, {'crop':[0,0,float('nan'),1]}):
            with self.assertRaises(AppError): self.extract(asset, **options)
        with self.assertRaises(AppError): self.extract(self.asset(), crop=[0,0,1,1])
        self.assertEqual(self.f.store.list(self.u['project_id'],'extraction_run'), [])
    def test_region_edit_creates_immutable_revision_retains_ambiguities_and_originals(self):
        asset=self.image(); run=self.extract(asset)
        regions=copy.deepcopy(run['regions']); regions[0].update(text='PO가 읽은 광고주', bbox=[.2,.3,.3,.4])
        edited=self.post('/api/planning-assets/regions', {'extraction_id':run['id'],'expected_version':run['version'],
            'transcript':'PO가 읽은 광고주','regions':regions,'relations':[],'note':'영역 경계와 역할 전사 수정'})
        self.assertNotEqual(edited['id'],run['id'])
        self.assertEqual(self.s.extraction(self.u,run['id'])['regions'][0]['text'],'광고주')
        self.assertEqual(edited['regions'][0]['original_text'],'광고주')
        self.assertEqual(edited['regions'][0]['original_bbox'],[0,0,.5,.5])
        self.assertEqual(edited['questions'],run['questions']); self.assertEqual(edited['quality_issues'],run['quality_issues'])
        draft=self.post('/api/story-drafts',{'prompt':'수정 전사로 작성','extraction_ids':[edited['id']]})
        ref=draft['stories'][0]['provenance']['actor']
        self.assertEqual(ref['extraction_id'],edited['id']);self.assertEqual(ref['bbox'],[.2,.3,.3,.4])
        self.assertEqual(ref['quote'],'PO가 읽은 광고주')
    def test_region_edit_rejects_dangling_relationships_old_versions_and_cross_project(self):
        asset=self.image();run=self.extract(asset)
        body={'extraction_id':run['id'],'expected_version':run['version'],'transcript':run['transcript'],
            'regions':run['regions'],'relations':[],'note':'수정'}
        with self.assertRaises(AppError):self.s.post(self.f.other,'/api/planning-assets/regions',body)
        with self.assertRaises(AppError):self.post('/api/planning-assets/regions',{**body,'expected_version':99})
        with self.assertRaises(AppError):self.post('/api/planning-assets/regions',{**body,'relations':[{
            'from_region':'r1','to_region':'missing','meaning':'분기','uncertain':True}]})
        self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        with self.assertRaises(AppError):self.post('/api/planning-assets/regions',body)
    def test_explicit_order_reaches_model_and_ambiguous_duplicate_assets_are_rejected(self):
        a=self.extract(self.asset('첫 이미지','광고주')); b=self.extract(self.asset('둘째 이미지','운영자'))
        draft=self.post('/api/story-drafts',{'prompt':'둘째 먼저','extraction_ids':[b['id'],a['id']]})
        self.assertEqual(draft['extraction_ids'],[b['id'],a['id']])
        self.assertEqual([r['id'] for r in self.model.calls[-1][1]['extractions']],draft['extraction_ids'])
        self.post('/api/story-drafts',{'prompt':'원본 없이 직접 제안'})
        self.assertEqual(self.model.calls[-1][1]['extractions'],[])
        same=self.extract(self.s.asset(self.u,a['asset_id']))
        for ids in ([a['id'],a['id']], [a['id'],same['id']]):
            with self.assertRaises(AppError):self.post('/api/story-drafts',{'prompt':'중복','extraction_ids':ids})
    def test_source_recovery_preserves_po_edits_history_and_requires_new_confirmation(self):
        asset=self.asset();row=self.story(asset)
        row=self.post('/api/stories/review',{'story_id':row['id'],'expected_version':row['version'],'state':'confirmed'})
        current=self.revise(asset);preview=self.preview(row)
        self.assertEqual(preview['changes'][0]['previous']['version'],1)
        self.assertEqual(preview['changes'][0]['current']['version'],2)
        changed=self.recover(preview)
        self.assertEqual(changed['id'],row['id']);self.assertEqual(changed['version'],row['version']+1)
        self.assertEqual(changed['value'],'PO가 정한 가치');self.assertEqual(changed['confirmed_version'],row['version'])
        self.assertEqual(changed['source_refs'],[{'id':asset['id'],'version':current['version']}])
        self.assertEqual(changed['definition_status'],'draft');self.assertTrue(self.s.accessible(self.u['project_id'],changed))
        history=self.f.store.history(self.u['project_id'],'user_story',row['id'])
        self.assertTrue(any(r['version']==row['version'] and r['definition_status']=='confirmed' for r in history))
        with self.assertRaises(AppError):self.post('/api/stories/review',{'story_id':row['id'],'expected_version':changed['version'],'state':'confirmed'})
        changed['questions'][-1].update(status='answered',answer='역할과 조건을 확인하고 수용 기준 유지')
        edited=self.post('/api/stories/update',{'story_id':row['id'],'expected_version':changed['version'],'questions':changed['questions']})
        self.assertEqual(self.post('/api/stories/review',{'story_id':row['id'],'expected_version':edited['version'],'state':'confirmed'})['definition_status'],'confirmed')
    def test_recovery_keeps_historical_provenance_without_pretending_new_image_coordinates(self):
        asset=self.image();run=self.extract(asset)
        draft=self.post('/api/story-drafts',{'prompt':'설계','extraction_ids':[run['id']]})
        row=self.post('/api/story-drafts/apply',{'draft_id':draft['id'],'candidate_index':0})
        self.revise(asset)
        changed=self.recover(self.preview(row));ref=changed['provenance']['actor']
        self.assertTrue(ref['historical_source']);self.assertEqual(ref['asset_version'],1)
        self.assertEqual(ref['bbox'],[0,0,.5,.5]);self.assertEqual(ref['original_value'],row['provenance']['actor']['original_value'])
    def test_withdrawal_denies_recovery_and_historical_raw_reads(self):
        asset=self.asset();row=self.story(asset);current=self.revise(asset);preview=self.preview(row)
        old=self.s.get(self.u,'/api/planning-assets/raw/'+asset['id']+'?version=1')
        self.assertEqual(old['text'],'광고주')
        with self.assertRaises(AppError):self.s.get(self.f.other,'/api/planning-assets/raw/'+asset['id']+'?version=1')
        self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':current['version']})
        for action in (lambda:self.preview(row), lambda:self.recover(preview), lambda:self.s.get(self.u,'/api/planning-assets/raw/'+asset['id']+'?version=1')):
            with self.assertRaises(AppError):action()
        self.assertTrue(self.s.story_list(self.u)[0]['redacted'])
    def test_revoked_shared_evidence_cannot_be_laundered_by_source_recovery(self):
        asset=self.asset();row=self.story(asset,evidence_ids=[self.f.insight['id']]);self.revise(asset)
        preview=self.preview(row)
        self.s.post(self.f.owner,'/api/insights/release',{'insight_id':self.f.insight['id'],'published':False})
        for action in (lambda:self.preview(row),lambda:self.recover(preview)):
            with self.assertRaises(AppError):action()
        self.assertTrue(self.s.story_list(self.u)[0]['redacted'])
    def test_review_acknowledgment_snapshot_cas_and_project_scope_are_enforced(self):
        asset=self.asset();row=self.story(asset);current=self.revise(asset);preview=self.preview(row)
        with self.assertRaises(AppError):self.recover(preview,acknowledged=False)
        with self.assertRaises(AppError):self.recover(preview,note='')
        with self.assertRaises(AppError):self.s.post(self.f.other,'/api/stories/recovery-preview',{'story_id':row['id'],'expected_version':row['version']})
        self.revise(current,'원본 재변경')
        with self.assertRaises(AppError):self.recover(preview)
        latest=self.preview(row);changed=self.recover(latest)
        with self.assertRaises(AppError):self.recover(latest)
        self.assertEqual(self.s.story(self.u,row['id'])['version'],changed['version'])
    def test_withdrawal_during_recovery_does_not_persist_partial_edit(self):
        asset=self.asset();row=self.story(asset);current=self.revise(asset);preview=self.preview(row)
        original=self.s.source_recovery_state
        def revoke_after_read(*args):
            result=original(*args)
            self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':current['version']})
            return result
        with patch.object(self.s,'source_recovery_state',side_effect=revoke_after_read):
            with self.assertRaises(AppError):self.recover(preview)
        self.assertEqual(self.f.store.get(self.u['project_id'],'user_story',row['id'])['version'],row['version'])
    def test_required_source_review_question_cannot_be_removed_or_downgraded(self):
        asset=self.asset();row=self.story(asset);self.revise(asset);row=self.recover(self.preview(row))
        downgraded=copy.deepcopy(row['questions']);downgraded[-1]['critical']=False
        for questions in ([],downgraded):
            with self.assertRaises(AppError):self.post('/api/stories/update',{'story_id':row['id'],'expected_version':row['version'],'questions':questions})
        self.assertEqual(self.s.story(self.u,row['id'])['version'],row['version'])
        draft=self.post('/api/story-drafts',{'prompt':'확인 질문 유지','base_story_id':row['id']})
        applied=self.post('/api/story-drafts/apply',{'draft_id':draft['id'],'candidate_index':0,'story_id':row['id'],'expected_version':row['version'],'fields':['questions']})
        self.assertEqual(applied['questions'][-1]['id'],row['questions'][-1]['id'])
        with self.assertRaises(AppError):self.post('/api/stories/review',{'story_id':row['id'],'expected_version':applied['version'],'state':'confirmed'})

    def test_recovery_resets_actual_validation_to_planned(self):
        asset=self.asset();row=self.story(asset)
        # Synthetic state setup isolates recovery from the separately tested actual-FGI attestation API.
        row=self.f.store.update(self.u['project_id'],'user_story',row['id'],{'customer_validation':'actual_results'},row['version'])
        self.revise(asset)
        self.assertEqual(self.recover(self.preview(row))['customer_validation'],'planned')


if __name__=='__main__':unittest.main()
