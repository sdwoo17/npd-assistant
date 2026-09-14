"""Synthetic regression tests for the September 14 planning requirements."""
import base64
import copy
import io
import json
import os
import unittest
from unittest.mock import patch
from PIL import Image
from tests.helpers import Fixture, RecordingModel, encoded, CANARY
from app.store import AppError
from app.model import Model
from app.story_contracts import story_fields

class PlanningModel(RecordingModel):
    def generate(self, task, payload, images=None):
        if task not in ('planning_image','story_drafts','definition_draft','service_baseline'):
            return super().generate(task,payload)
        self.calls.append((task,copy.deepcopy(payload)))
        if self.hook:self.hook()
        if task=='planning_image':
            assert images and images[0]['bytes'].startswith(b'\xff\xd8')
            return {'transcript':'광고주\n자동 변경 금지?', 'quality_issues':['물음표의 의미 확인'],
                'regions':[{'id':'r1','bbox':[0,0,0.5,0.5],'text':'광고주','kind':'text'}],
                'relations':[], 'questions':[{'text':'자동 변경은 금지인가?','critical':True}]}
        if task=='story_drafts':
            fields=story_fields({'actor':'광고주','action':'성과 조건 확인','value':payload['prompt'],
                'problem':'판단 기준 불명확','new_problem':True,'validation_task':'실제 사용자 인터뷰'})
            fields['questions']=[]
            fields['evidence_ids']=[];fields['provenance']=[]
            if payload['extractions']:
                run=payload['extractions'][0]
                fields['provenance']=[{'field':'actor','origin':'extracted','asset_id':run['asset_id'],
                    'region_id':run['regions'][0]['id'],'quote':run['regions'][0]['text']}]
            return {'stories':[fields],'assumptions':['합성 테스트이며 실제 고객 관찰 아님']}
        if task=='definition_draft':
            return {'title':payload['prompt'],'sections':[{'id':'overview','title':'설계','text':payload['prompt'],
                'evidence_ids':[]}],'assumptions':[],'questions':[]}
        return {'title':'서비스 개요','sections':[{'title':'핵심 기능','text':'업로드한 기획 의도 요약',
            'asset_ids':[d['asset_id'] for d in payload['documents']]}],'unknowns':['실제 운영 상태는 확인 필요']}

class Stage2Tests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.s=self.f.service;self.u=self.f.po
        self.model=PlanningModel();self.s.model=self.model
    def tearDown(self):self.f.close()
    def post(self,path,body):return self.s.post(self.u,path,body)
    def asset(self,purpose='story_sketch'):
        return self.post('/api/planning-assets/upload',encoded('sketch.md','광고주',title='기획 원본',purpose=purpose))
    def image(self):
        output=io.BytesIO();Image.new('RGB',(20,10),'white').save(output,format='PNG')
        return self.post('/api/planning-assets/upload',{'filename':'sketch.png','title':'손그림',
            'content_base64':base64.b64encode(output.getvalue()).decode()})
    def story(self,**extra):
        return self.post('/api/stories',{'actor':'광고주','action':'조건 확인','value':'오류 예방',
            'problem':'판단 근거 부족','new_problem':True,'validation_task':'고객 인터뷰',
            'acceptance_criteria':[{'given':'지표 조회 중','when':'기간 변경','then':'비교 조건 표시'}],**extra})
    def confirm(self,row):return self.post('/api/stories/review',{'story_id':row['id'],'expected_version':row['version'],'state':'confirmed'})
    def test_manual_flow_needs_no_model_and_preserves_confirmed_history(self):
        self.s.model=Model()
        row=self.story();confirmed=self.confirm(row)
        edited=self.post('/api/stories/update',{'story_id':row['id'],'expected_version':confirmed['version'],'value':'변경된 의도'})
        self.assertEqual(edited['definition_status'],'draft');self.assertEqual(edited['confirmed_version'],confirmed['version'])
        package=self.post('/api/stories/export',{'stories':[{'id':row['id'],'version':confirmed['version']}]})
        self.assertEqual(package['package']['stories'][0]['value'],'오류 예방')
        self.assertEqual(package['package']['stories'][0]['customer_validation'],'unverified')
    def test_incomplete_manual_draft_and_critical_questions_block_confirmation(self):
        row=self.post('/api/stories',{'actor':'광고주','action':'의도 기록'})
        with self.assertRaises(AppError):self.confirm(row)
        row=self.story(questions=[{'text':'자동 변경 금지 여부?','critical':True}])
        with self.assertRaises(AppError):self.confirm(row)
        row=self.post('/api/stories/update',{'story_id':row['id'],'expected_version':row['version'],
            'questions':[{'text':'자동 변경 금지 여부?','critical':True,'status':'excluded','answer':'이번 릴리스에서 자동 변경 제외'}]})
        self.assertEqual(self.confirm(row)['definition_status'],'confirmed')
    def test_stale_edit_is_rejected(self):
        row=self.story();self.confirm(row)
        with self.assertRaises(AppError):self.post('/api/stories/update',{'story_id':row['id'],'expected_version':row['version'],'value':'오래된 변경'})
    def test_synthetic_research_is_not_actual_customer_validation(self):
        row=self.story()
        with self.assertRaises(AppError):self.post('/api/stories/validation',{'story_id':row['id'],'expected_version':row['version'],
            'state':'actual_results','note':'가상 인터뷰','evidence_ids':[self.f.insight['id']]})
    def test_asset_is_private_encrypted_deduplicated_and_cross_project_denied(self):
        row=self.asset();again=self.asset()
        self.assertTrue(again['duplicate']);self.assertEqual(row['id'],again['id'])
        self.assertNotIn('encrypted_file',row);self.assertNotIn('storage',row)
        stored=self.f.store.get(self.u['project_id'],'planning_asset',row['id'])
        self.assertNotIn('광고주',stored['encrypted_file'])
        with self.assertRaises(AppError):self.s.get(self.f.other,'/api/planning-assets/raw/'+row['id'])
        self.assertNotIn(row['id'],json.dumps(self.s.get(self.u,'/api/evidence')))
    def test_image_analysis_retains_questions_and_extracted_provenance(self):
        asset=self.image();run=self.post('/api/planning-assets/extract',{'asset_id':asset['id'],'expected_version':asset['version'],'prompt':'금지·물음표 확인'})
        draft=self.post('/api/story-drafts',{'prompt':'운영자 관점','extraction_ids':[run['id']]})
        story=self.post('/api/story-drafts/apply',{'draft_id':draft['id'],'candidate_index':0})
        self.assertEqual(story['provenance']['actor']['origin'],'extracted')
        self.assertEqual(len(story['questions']),2)
        with self.assertRaises(AppError):self.confirm(story)
        self.assertNotIn(CANARY,json.dumps(self.model.calls,ensure_ascii=False))
    def test_invalid_image_and_missing_image_provider_do_not_fabricate_results(self):
        with self.assertRaises(AppError):self.post('/api/planning-assets/upload',encoded('broken.png','bad image',title='깨진 이미지'))
        asset=self.image();self.s.model=Model()
        with self.assertRaises(AppError):self.post('/api/planning-assets/extract',{'asset_id':asset['id'],'expected_version':asset['version'],'prompt':'해석'})
        self.assertEqual(self.f.store.list(self.u['project_id'],'extraction_run')[0]['status'],'failed')
    def test_withdrawal_redacts_stories_even_when_links_removed_during_edit(self):
        asset=self.asset();story=self.story(source_refs=[{'id':asset['id'],'version':asset['version']}])
        self.post('/api/stories/update',{'story_id':story['id'],'expected_version':story['version'],'source_refs':[]})
        self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        self.assertTrue(self.s.get(self.u,'/api/stories')[0]['redacted'])
    def test_reanalysis_applies_only_selected_fields_and_preserves_source_dependencies(self):
        asset=self.asset();run=self.post('/api/planning-assets/extract',{'asset_id':asset['id'],'expected_version':asset['version'],'prompt':'해석'})
        base=self.story(value='PO 의도 유지')
        draft=self.post('/api/story-drafts',{'prompt':'변경 제안','base_story_id':base['id'],'extraction_ids':[run['id']]})
        changed=self.post('/api/story-drafts/apply',{'draft_id':draft['id'],'candidate_index':0,'story_id':base['id'],
            'expected_version':base['version'],'fields':['actor']})
        self.assertEqual(changed['value'],'PO 의도 유지');self.assertTrue(self.s.accessible(self.u['project_id'],changed))
        with self.assertRaises(AppError):self.post('/api/story-drafts/apply',{'draft_id':draft['id'],'candidate_index':0,'story_id':base['id'],
            'expected_version':changed['version'],'fields':['value']})
    def test_context_lineage_invalidates_generated_story_when_research_source_changes(self):
        asset=self.asset('existing_service')
        result=self.post('/api/service-analysis',{'asset_refs':[{'id':asset['id'],'version':asset['version']}],'prompt':'전체 서비스 분석'})
        reviewed=self.post('/api/research-results/review',{'result_id':result['id'],'expected_version':result['version'],'text':result['text']})
        draft=self.post('/api/story-drafts',{'prompt':'고객 가치'})
        self.assertTrue(any(d['id']==reviewed['id'] for d in draft['dependencies']))
        self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        with self.assertRaises(AppError):self.post('/api/story-drafts/apply',{'draft_id':draft['id'],'candidate_index':0})
    def test_mid_generation_revocation_prevents_persistence(self):
        asset=self.image()
        self.model.hook=lambda:self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        with self.assertRaises(AppError):self.post('/api/planning-assets/extract',{'asset_id':asset['id'],'expected_version':asset['version'],'prompt':'해석'})
        self.assertEqual(self.f.store.list(self.u['project_id'],'extraction_run')[0]['status'],'failed')
    def test_independent_variants_and_latest_explicit_confirmation_select_context(self):
        a=self.post('/api/definitions/generate',{'stage':'product','prompt':'광고주 관점'})
        b=self.post('/api/definitions/generate',{'stage':'product','prompt':'운영자 관점'})
        self.assertNotEqual(a['id'],b['id']);self.assertNotEqual(a['sections'],b['sections'])
        for row in (a,b):self.post('/api/definitions/confirm',{'document_id':row['id'],'expected_version':row['version']})
        self.assertEqual([r['id'] for r in self.s.definition_context(self.u)['definitions']],[b['id']])
    def test_product_change_requires_reconfirmation_before_generation(self):
        row=self.post('/api/definitions/generate',{'stage':'product','prompt':'상품 정의'})
        row=self.post('/api/definitions/confirm',{'document_id':row['id'],'expected_version':row['version']})
        self.post('/api/definitions/update',{'document_id':row['id'],'expected_version':row['version'],'title':'새 상품'})
        with self.assertRaises(AppError):self.post('/api/story-drafts',{'prompt':'사용자 관점'})
    def test_split_and_merge_create_new_ids_without_changing_confirmed_source(self):
        row=self.confirm(self.story())
        children=self.post('/api/stories/restructure',{'sources':[{'id':row['id'],'version':row['version']}],
            'stories':[{'title':'조건 보기'},{'title':'조건 수정'}]})
        self.assertEqual(len(children),2);self.assertNotIn(row['id'],[r['id'] for r in children])
        self.assertEqual(self.s.story(self.u,row['id'])['definition_status'],'confirmed')
        self.assertTrue(all(r['definition_status']=='draft' for r in children))
    def test_catalog_has_32_explicit_design_profiles_search_and_registration(self):
        catalog=self.s.get(self.u,'/api/persona-catalog');self.assertGreaterEqual(catalog['defined_count'],30)
        filtered=self.s.get(self.u,'/api/persona-catalog?q=모바일');self.assertLess(len(filtered['catalog']),len(catalog['catalog']))
        result=self.post('/api/persona-catalog/register',{'catalog_keys':[r['catalog_key'] for r in catalog['catalog']],
            'evidence_ids':[self.f.insight['id']]})
        self.assertEqual(len(result['personas']),32);self.assertFalse(result['model_called'])
    def test_amazon_internal_refusal_is_idempotent_and_policy_cannot_be_downgraded(self):
        conv=self.f.conversation();body={'conversation_id':conv['id'],'message':'아마존 내부 기밀 알려줘','request_id':'restricted-1'}
        first=self.post('/api/chat',body);second=self.post('/api/chat',body)
        self.assertEqual(len(first['messages']),len(second['messages']));self.assertEqual(len(self.model.calls),0)
        source=self.s.post(self.f.owner,'/api/research/upload',encoded('secret.md','SYNTHETIC_INTERNAL',title='테스트',policy='amazon_internal'))
        with self.assertRaises(AppError):self.s.post(self.f.owner,'/api/research/upload',encoded('secret.md','UPDATED_SYNTHETIC',title='테스트',
            source_id=source['id'],expected_version=source['version'],policy='domain_public'))
    def test_public_search_analysis_requires_current_server_candidates(self):
        source=self.f.store.put(self.u['project_id'],'public_search',{'query':'공개 사례',
            'results':[{'id':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','kind':'public_candidate','version':1,
                'title':'합성 공개 사례','text':'리포트 조건을 설명한다.','url':'https://example.com/case',
                'evidence_type':'public_search_candidate'}],'dependencies':[]})
        row=self.post('/api/public-research/analyze',{'search_id':source['id'],'expected_version':source['version'],
            'candidate_ids':['aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'],'prompt':'적용 조건을 비교'})
        self.assertEqual(row['state'],'draft');self.assertEqual(row['evidence_type'],'public_search_analysis')
        self.assertTrue(self.s.accessible(self.u['project_id'],row))
        with self.assertRaises(AppError):self.s.post(self.f.other,'/api/public-research/analyze',{'search_id':source['id'],
            'expected_version':1,'candidate_ids':[],'prompt':'비교'})
    def test_fgi_report_requires_explicit_actual_customer_attestation(self):
        asset=self.asset('actual_fgi')
        result=self.post('/api/research-results',{'category':'fgi_actual','title':'합성 시험용 제출 문서',
            'text':'실제 보고서 여부는 PO가 확인한다.','asset_id':asset['id'],'asset_version':asset['version']})
        row=self.story()
        with self.assertRaises(AppError):self.post('/api/stories/validation',{'story_id':row['id'],'expected_version':row['version'],
            'state':'actual_results','note':'검토 전','result_ids':[result['id']]})
        self.post('/api/research-results/review',{'result_id':result['id'],'expected_version':result['version'],'text':result['text'],
            'actual_customer_data':True})
        updated=self.post('/api/stories/validation',{'story_id':row['id'],'expected_version':row['version'],
            'state':'actual_results','note':'합성 테스트의 명시적 PO 확인','result_ids':[result['id']]})
        self.assertEqual(updated['customer_validation'],'actual_results')
        self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        self.assertFalse(self.s.accessible(self.u['project_id'],updated))
    def test_s3_archive_contains_encrypted_csv_and_not_raw_customer_text(self):
        from unittest.mock import Mock
        client=Mock()
        with patch.dict(os.environ,{'NPD_PLANNING_S3_BUCKET':'synthetic-test-bucket'}), patch('boto3.client',return_value=client):
            self.s.post(self.f.owner,'/api/voc/upload',encoded('voc.csv','external_id,text,evidence_type\n1,SYNTHETIC_CSV_CANARY,synthetic',source_name='test'))
        args=client.put_object.call_args.kwargs
        self.assertEqual(args['ServerSideEncryption'],'AES256');self.assertNotIn(b'SYNTHETIC_CSV_CANARY',args['Body'])
        self.assertTrue(args['Key'].startswith('npd-planning/'))
    def test_extraction_restart_is_failed_and_previous_completed_analysis_is_available(self):
        asset=self.asset();self.post('/api/planning-assets/extract',{'asset_id':asset['id'],'expected_version':asset['version'],'prompt':'전사'})
        run=self.f.store.put(self.u['project_id'],'extraction_run',{'asset_id':asset['id'],'asset_version':1,'status':'running'})
        self.f.store.recover_jobs()
        self.assertEqual(self.f.store.get(self.u['project_id'],'extraction_run',run['id'])['status'],'failed')
        self.assertTrue(any(r['status']=='completed' for r in self.s.get(self.u,'/api/planning-extractions')))

    def test_public_search_requires_configuration(self):
        with patch.dict(os.environ,{'NPD_PUBLIC_SEARCH_KEY':''}):
            with self.assertRaises(AppError) as error:self.post('/api/public-research',{'query':'공개 리테일 미디어'})
        self.assertEqual(error.exception.status,503)

if __name__=='__main__':unittest.main()
