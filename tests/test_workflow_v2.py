"""Behavioral regressions for the audited gaps and versioned workflow contracts."""
import base64
import copy
import io
import json
import os
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from app.contracts import date_value
from app.ingest import chunks, classify_all, decode_file, parse_csv, retrieve
from app.model import BedrockModel, SCHEMAS, validate, create_model
from app.service import Service
from app.store import AppError, Store
from tests.helpers import Fixture, RecordingModel, encoded, CANARY


class TransformModel(RecordingModel):
    def __init__(self, transform):
        super().__init__()
        self.transform = transform

    def generate(self, task, payload):
        result = super().generate(task, payload)
        return self.transform(task, payload, result)


class WorkflowV2Tests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.s, self.store, self.owner, self.po = self.f.service, self.f.store, self.f.owner, self.f.po
        self.p = self.po['project_id']

    def tearDown(self):
        self.f.close()

    def insight(self, content='소재 리포트의 다른 근거', **fields):
        r = self.s.post(self.owner, '/api/insights', {'source_id': self.f.source['id'], 'title':'공개 인사이트', 'text':content, 'feature':'reporting', **fields})
        return self.s.post(self.owner, '/api/insights/release', {'insight_id':r['id'],'published':True})

    def voc(self, count=3):
        value = 'external_id,text,segment,occurred_at,evidence_type\n' + ''.join(
            f'case-{i},소재 리포트의 비교 조건이 필요합니다,소규모 광고주,{"2026-09-01" if i < 2 else "2025-09-01"},synthetic\n' for i in range(count))
        return self.s.post(self.owner, '/api/voc/upload', encoded('fixture.csv', value, source_name='synthetic-center'))

    def chat(self, conv, message='소재 리포트를 개선해 주세요', **extra):
        return self.s.post(self.po, '/api/chat', {'conversation_id':conv['id'],'message':message, **extra})

    def test_po_messages_and_pinned_decisions_reach_proposal_model(self):
        conv = self.f.conversation()
        directive = '자동 소재 교체는 제외하고 분석만 제공'
        self.chat(conv, directive)
        self.s.post(self.po, '/api/conversations/decisions', {'conversation_id':conv['id'],'text':directive})
        result = self.s.post(self.po, '/api/proposals', {'conversation_id':conv['id']})
        payload = self.f.model.calls[-1][1]
        self.assertIn(directive, [m['text'] for m in payload['conversation'] if m['speaker']=='PO'])
        self.assertEqual(payload['decisions'][0]['text'], directive)
        self.assertEqual(result['decision_ids'], [payload['decisions'][0]['id']])
        self.assertEqual(result['source_message_ids'], [m['id'] for m in payload['conversation']])

    def test_proposal_rejects_missing_po_decision_reference(self):
        conv = self.f.conversation(); self.chat(conv)
        self.s.post(self.po, '/api/conversations/decisions', {'conversation_id':conv['id'],'text':'자동 교체 제외'})
        self.s.model = TransformModel(lambda task,payload,result: {**result,'decision_ids':[]} if task=='proposal' else result)
        with self.assertRaises(AppError): self.s.post(self.po, '/api/proposals', {'conversation_id':conv['id']})
        self.assertEqual(self.store.list(self.p,'proposal'), [])

    def test_indirect_revocation_masks_chat_proposal_prd_export_and_new_history(self):
        a = self.insight('소재 리포트 SYNTHETIC_REVOKED_CLAIM')
        b = self.f.insight
        def transform(task,payload,result):
            if task in ('chat','proposal'):
                result.update(text='소재 리포트 SYNTHETIC_REVOKED_CLAIM', evidence_ids=[b['id']], observations=[])
            return result
        self.s.model = TransformModel(transform)
        conv = self.f.conversation(); self.chat(conv)
        self.s.post(self.po, '/api/conversations/decisions', {'conversation_id':conv['id'],'text':'SYNTHETIC_REVOKED_CLAIM 활용'})
        proposal = self.s.post(self.po, '/api/proposals', {'conversation_id':conv['id']})
        self.s.post(self.po, '/api/proposals/decision', {'proposal_id':proposal['id'],'expected_version':proposal['version'],'state':'accepted'})
        self.s.post(self.owner, '/api/insights/release', {'insight_id':a['id'],'published':False})
        for route in ['/api/bootstrap','/api/conversations/'+conv['id'],'/api/proposals','/api/prds','/api/export/'+conv['id'], '/api/export/'+conv['id']+'?format=markdown']:
            with self.subTest(route=route): self.assertNotIn('SYNTHETIC_REVOKED_CLAIM',json.dumps(self.s.get(self.po,route)))
        self.s.model = RecordingModel(); self.chat(conv,'소재 리포트 후속 질문')
        self.assertNotIn('SYNTHETIC_REVOKED_CLAIM',json.dumps(self.s.model.calls[-1][1]))

    def test_indirect_revocation_during_generation_prevents_commit(self):
        a = self.insight('소재 리포트 A 근거')
        self.f.model.hook = lambda: self.s.post(self.owner, '/api/insights/release', {'insight_id':a['id'],'published':False})
        conv = self.f.conversation()
        with self.assertRaises(AppError) as caught: self.chat(conv)
        self.assertEqual(caught.exception.status,409)
        self.assertEqual(self.store.list(self.p,'message'),[])

    def test_changed_insight_requires_republication_and_old_answers_stay_invalid(self):
        conv = self.f.conversation(); self.chat(conv)
        old = self.store.get(self.p,'insight',self.f.insight['id'])
        new = self.s.post(self.owner,'/api/insights/update',{**old,'insight_id':old['id'],'expected_version':old['version'],'text':'변경된 소재 리포트 가설'})
        self.assertFalse(new['published'])
        self.assertEqual(new['version'],old['version']+1)
        self.s.post(self.owner,'/api/insights/release',{'insight_id':old['id'],'published':True})
        self.assertTrue(self.s.conversation(self.po,conv['id'])['messages'][-1]['redacted'])
        with self.assertRaises(AppError): self.s.post(self.owner,'/api/insights/update',{**old,'insight_id':old['id'],'expected_version':old['version']})

    def test_source_versions_invalidate_stale_insights(self):
        source = self.store.get(self.p,'source',self.f.source['id'])
        new = self.s.post(self.owner,'/api/research/upload',encoded('next.md','다음 소재 리포트 자료',title='자료 갱신',source_id=source['id'],expected_version=source['version']))
        self.assertEqual(new['content_version'],2)
        self.assertEqual(self.s.knowledge(self.p),[])
        with self.assertRaises(AppError): self.s.post(self.owner,'/api/insights/release',{'insight_id':self.f.insight['id'],'published':True})
        self.assertEqual(len(self.s.get(self.owner,'/api/research/versions/'+source['id'])),2)

    def test_batch_release_is_atomic_for_foreign_identifier(self):
        self.s.post(self.owner,'/api/insights/release',{'insight_id':self.f.insight['id'],'published':False})
        with self.assertRaises(AppError): self.s.post(self.owner,'/api/insights/release',{'insight_ids':[self.f.insight['id'],'foreign-id'],'published':True})
        self.assertFalse(self.store.get(self.p,'insight',self.f.insight['id'])['published'])

    def test_long_document_chunks_cover_every_character_and_retry_is_tracked(self):
        content = ('소재 리포트 검토 자료\n' * 5000) + 'END_OF_PRIVATE_DOCUMENT'
        src = self.s.post(self.owner,'/api/research/upload',encoded('long.md',content,title='장문 연구'))
        attempts = [0]
        def fail_second(task,payload,result):
            attempts[0]+=1
            if attempts[0]==2: raise AppError('injected provider failure',502)
            return result
        self.s.model = TransformModel(fail_second)
        initial = len(self.store.list(self.p,'insight'))
        with self.assertRaises(AppError): self.s.post(self.owner,'/api/research/extract',{'source_id':src['id']})
        self.assertEqual(len(self.store.list(self.p,'insight')),initial)
        job = [j for j in self.s.get(self.owner,'/api/jobs') if j['action']=='research_extract'][-1]
        self.assertEqual(job['status'],'failed')
        self.s.model = RecordingModel()
        drafts = self.s.post(self.owner,'/api/jobs/retry',{'job_id':job['id']})
        sent = ''.join(payload['document'] for task,payload in self.s.model.calls if task=='insights')
        self.assertEqual(sent,content)
        self.assertTrue(all(not d['published'] for d in drafts))
        j = self.store.get(self.p,'job',job['id']);self.assertEqual((j['attempts'],j['status']),(2,'completed'))
        self.assertNotIn('encrypted_payload',json.dumps(self.s.get(self.owner,'/api/jobs')))

    def test_failed_document_upload_records_safe_job(self):
        with self.assertRaises(AppError): self.s.post(self.owner,'/api/research/upload',encoded('bad.exe','TOP_SECRET_FILE',title='bad'))
        jobs = self.s.get(self.owner,'/api/jobs')
        self.assertEqual(jobs[-1]['status'],'failed')
        self.assertNotIn('TOP_SECRET_FILE',json.dumps(jobs))
        with self.assertRaises(AppError) as e: self.s.get(self.po,'/api/jobs')
        self.assertEqual(e.exception.status,403)

    def test_csv_missing_optional_cells_errors_dates_and_original_ids(self):
        value='external_id,text,segment,occurred_at,evidence_type\nshort-only\nvalid,소재 리포트\ninvalid,소재,유형,2026-02-30,synthetic\n'
        result=self.s.post(self.owner,'/api/voc/upload',encoded('edge.csv',value,source_name='center'))
        self.assertEqual((result['imported'],len(result['errors'])),(1,2))
        row=self.s.get(self.po,'/api/voc')['records'][0]
        self.assertEqual(row['external_id'],'valid');self.assertEqual(row['source_name'],'center')
        self.assertEqual(row['segment'],'미지정')

    def test_duplicate_composite_keys_cannot_collide_by_delimiter_or_truncation(self):
        for source,external in [('a:b','c'),('a','b:c')]:
            r=self.s.post(self.owner,'/api/voc/upload',encoded('a.csv','external_id,text\n'+external+',소재 리포트\n',source_name=source));self.assertEqual(r['imported'],1)
        oversized='x'*201
        r=self.s.post(self.owner,'/api/voc/upload',encoded('a.csv','external_id,text\n'+oversized+',소재 리포트\n',source_name='long'))
        self.assertEqual((r['imported'],len(r['errors'])),(0,1))

    def test_pii_in_external_id_and_segment_is_not_disclosed(self):
        r=self.s.post(self.owner,'/api/voc/upload',encoded('a.csv','external_id,text,segment\nperson@example.com,소재 리포트 010-1234-5678,manager@example.com\n',source_name='source@example.com'))
        public=json.dumps([r,self.s.knowledge(self.p),self.s.get(self.po,'/api/voc')])
        for secret in ['person@example.com','manager@example.com','source@example.com','010-1234-5678']:
            self.assertNotIn(secret,public)

    def test_date_filters_stats_and_chat_inputs_have_reproducible_denominator(self):
        self.voc()
        result=self.s.post(self.po,'/api/voc/analysis',{'filters':{'date_from':'2026-09-01','date_to':'2026-09-30'}})
        self.assertEqual(result['total'],2)
        conv=self.f.conversation();self.chat(conv,'2026년 9월 소재 리포트 불만을 분석해 주세요')
        payload=self.f.model.calls[-1][1]
        self.assertEqual(payload['statistics']['denominator'],2)
        voc=[e for e in payload['evidence'] if e['kind']=='voc']
        self.assertEqual(len(voc),2)
        self.assertTrue(all(e['occurred_at'].startswith('2026-09') for e in voc))
        self.assertTrue(all(e['external_id'] and e['source_name'] for e in voc))

    def test_multi_label_prevents_report_word_swallowing_creative_issue(self):
        result=classify_all('소재 단위 성과를 보려면 리포트를 소재별로 나눠야 합니다.')
        self.assertEqual(result[0],'creative_test');self.assertIn('reporting',result)

    def test_feature_tree_validates_cycles_unknown_parents_and_service_boundaries(self):
        cases=[[
            {'key':'child','name':'child','service_id':'portal','parent_feature_id':'unknown','terms':[]}],
            [{'key':'a','name':'a','service_id':'p','parent_feature_id':'b','terms':[]},{'key':'b','name':'b','service_id':'p','parent_feature_id':'a','terms':[]}],
            [{'key':'a','name':'a','service_id':'p','terms':[]},{'key':'b','name':'b','service_id':'q','parent_feature_id':'a','terms':[]}]]
        for rows in cases:
            with self.subTest(rows=rows),self.assertRaises(AppError):self.s.post(self.owner,'/api/features/import',{'features':rows})
        self.assertEqual(self.store.list(self.p,'feature'),[])
        self.s.post(self.owner,'/api/features/import',{'features':[{'key':'approval_detail','name':'승인 이력','service_id':'advertiser_portal','parent_feature_id':'workflow','terms':['검토 담당자']}]})
        self.assertIn('approval_detail',self.s.features(self.p))
        with self.assertRaises(AppError):self.s.post(self.po,'/api/features/import',{'features':[]})

    def test_ai_classification_is_batched_and_does_not_overwrite_po_review(self):
        self.voc(52)
        rows=self.s.get(self.po,'/api/voc')['records']
        self.s.post(self.po,'/api/voc/feature',{'voc_id':rows[0]['id'],'feature':'budget'})
        result=self.s.post(self.po,'/api/voc/classify',{})
        self.assertEqual(result['updated'],51)
        calls=[p for t,p in self.f.model.calls if t=='classify'];self.assertEqual([len(p['voc']) for p in calls],[25,25,1])
        self.assertEqual(self.store.get(self.p,'voc',rows[0]['id'])['feature'],'budget')

    def test_invalid_ai_classification_cannot_partially_commit(self):
        self.voc()
        before=self.store.list(self.p,'voc')
        def transform(task,payload,result):
            if task=='classify':result['classifications'][-1]['feature_ids']=['foreign-feature']
            return result
        self.s.model=TransformModel(transform)
        with self.assertRaises(AppError):self.s.post(self.po,'/api/voc/classify',{})
        self.assertEqual(self.store.list(self.p,'voc'),before)

    def test_multilingual_search_includes_titles_and_project_scope(self):
        r=self.insight('Creative experiments require statistical significance and adequate sample size.',title='이미지 실험의 통계적 유의성',feature='creative_test')
        for query in ['이미지 실험 통계적 유의성','creative experiments significance','Werbemittel statistische Signifikanz','création publicitaire expérience','素材 実験 有意']:
            with self.subTest(query=query):self.assertIn(r['id'],[e['id'] for e in self.s.post(self.po,'/api/search',{'query':query})['evidence']])
        self.assertEqual(self.s.post(self.f.other,'/api/search',{'query':'creative'})['evidence'],[])

    def test_unsupported_numbers_and_fabricated_quote_are_rejected(self):
        for mutation in ['number','quote','inline']:
            with self.subTest(mutation=mutation):
                def transform(task,payload,result):
                    if mutation=='number':result['text']='실제 광고주 1,000명이 동의했고 ROAS가 997.3% 개선됐다.'
                    if mutation=='quote':result['observations'][0]['quote']='근거에 존재하지 않는 실제 발언'
                    if mutation=='inline':result['text']='출처 [11111111-1111-1111-1111-111111111111]'
                    return result
                self.s.model=TransformModel(transform);conv=self.f.conversation()
                with self.assertRaises(AppError):self.chat(conv)
                self.assertEqual(self.s.conversation(self.po,conv['id'])['messages'],[])

    def test_persona_requires_both_evidence_kinds_when_available(self):
        self.voc()
        def transform(task,payload,result):
            if task=='persona':result['evidence_ids']=[self.f.insight['id']]
            return result
        self.s.model=TransformModel(transform)
        with self.assertRaises(AppError):self.s.post(self.po,'/api/personas/generate',{'segment':'소규모 광고주 소재 리포트'})
        self.s.model=RecordingModel();p=self.s.post(self.po,'/api/personas/generate',{'segment':'소규모 광고주 소재 리포트'})
        self.assertGreater(p['grounding_counts']['synthetic_voc'],0)
        self.assertEqual(p['grounding_status'],'synthetic_or_partial_evidence')

    def test_persona_creation_inside_chat_is_real_record_creation(self):
        self.voc();conv=self.f.conversation()
        response=self.chat(conv,'소재 리포트에 관심 있는 소규모 광고주 페르소나를 만들어 줘',action='create_persona',request_id='create-once')
        self.assertEqual(response['messages'][-1]['status'],'persona_created')
        self.assertEqual(len(self.store.list(self.p,'persona')),1)
        self.chat(conv,'소재 리포트에 관심 있는 소규모 광고주 페르소나를 만들어 줘',action='create_persona',request_id='create-once')
        self.assertEqual(len(self.store.list(self.p,'persona')),1)

    def test_persona_edit_pins_previous_version_for_followup(self):
        p=self.f.persona();conv=self.f.conversation([p['id']]);self.chat(conv)
        self.s.post(self.po,'/api/personas/update',{**p,'persona_id':p['id'],'expected_version':p['version'],'goals':'새 목표'})
        self.chat(conv,'이유를 더 설명해 주세요')
        self.assertEqual(self.f.model.calls[-1][1]['persona']['version'],1)
        self.assertEqual(self.f.model.calls[-1][1]['persona']['goals'],p['goals'])
        self.chat(conv,'@'+p['alias']+' 소재 리포트 의견')
        self.assertEqual(self.f.model.calls[-1][1]['persona']['version'],2)

    def test_long_fgi_retains_early_moderator_message_and_structured_history(self):
        p=self.f.persona();conv=self.f.conversation([p['id']]);directive='초기 결정: 자동 교체를 제외하고 소재 분석만 제안'
        self.chat(conv,directive)
        for i in range(16):self.chat(conv,'소재 리포트 후속 질문 '+chr(65+i))
        payload=self.f.model.calls[-1][1]
        self.assertIn(directive,[m['text'] for m in payload['moderator_messages']])
        self.assertGreater(len(payload['history']),12)
        self.assertTrue(any(m['is_synthetic'] and m['evidence_ids'] and m['persona_version']==1 for m in payload['history']))

    def test_failed_later_persona_does_not_save_a_partial_turn(self):
        a,b=self.f.persona('소규모'),self.f.persona('대행사')
        def transform(task,payload,result):
            if payload.get('persona',{}).get('id')==b['id']:raise AppError('simulated timeout',502)
            return result
        self.s.model=TransformModel(transform);conv=self.f.conversation()
        with self.assertRaises(AppError):self.chat(conv,'@소규모 @대행사 소재 리포트')
        self.assertEqual(self.store.list(self.p,'message'),[]);self.assertEqual(self.store.list(self.p,'turn'),[])

    def test_database_failure_rolls_back_the_entire_turn(self):
        self.f.persona('소규모');self.f.persona('대행사');conv=self.f.conversation()
        with self.store.db() as db:db.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON records WHEN NEW.kind='message' AND json_extract(NEW.body,'$.speaker')='대행사' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(AppError):self.chat(conv,'@소규모 @대행사 소재 리포트')
        self.assertEqual(self.store.list(self.p,'message'),[]);self.assertEqual(self.store.list(self.p,'turn'),[])

    def test_duplicate_and_concurrent_request_ids_generate_once(self):
        conv=self.f.conversation()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _: self.chat(conv,request_id='same-request'),range(12)))
        self.assertEqual(len(self.f.model.calls),1)
        self.assertTrue(all(len(r['messages'])==2 for r in results))
        with self.assertRaises(AppError):self.chat(conv,'다른 소재 리포트 질문',request_id='same-request')

    def test_moderator_change_during_generation_rejects_stale_turn(self):
        conv=self.f.conversation()
        self.f.model.hook=lambda:self.s.post(self.po,'/api/conversations/state',{'conversation_id':conv['id'],'objective':'수정된 범위'})
        with self.assertRaises(AppError):self.chat(conv)
        self.assertEqual(self.store.list(self.p,'message'),[])

    def test_structured_debrief_and_markdown_preserve_evidence_and_messages(self):
        p=self.f.persona();conv=self.f.conversation([p['id']]);self.chat(conv)
        debrief=self.s.post(self.po,'/api/debriefs',{'conversation_id':conv['id']})
        self.assertTrue(debrief['disagreements'][0]['message_ids'])
        self.s.post(self.po,'/api/proposals',{'conversation_id':conv['id']})
        markdown=self.s.get(self.po,'/api/export/'+conv['id']+'?format=markdown')['text']
        self.assertIn('['+self.f.insight['id']+']',markdown)
        self.assertIn('## FGI 디브리프',markdown);self.assertNotIn(CANARY,markdown)

    def test_acceptance_writes_prd_once_and_stale_competing_proposal_is_rejected(self):
        conv=self.f.conversation();self.chat(conv)
        a=self.s.post(self.po,'/api/proposals',{'conversation_id':conv['id']})
        b=self.s.post(self.po,'/api/proposals',{'conversation_id':conv['id']})
        applied=self.s.post(self.po,'/api/proposals/decision',{'proposal_id':a['id'],'expected_version':a['version'],'state':'accepted'})
        prd=self.store.get(self.p,'prd',conv['prd_id'])
        self.assertEqual(prd['version'],2);self.assertIn('비교',prd['sections'][1]['text'])
        self.s.post(self.po,'/api/proposals/decision',{'proposal_id':a['id'],'expected_version':a['version'],'state':'accepted'})
        self.assertEqual(self.store.get(self.p,'prd',conv['prd_id'])['version'],2)
        with self.assertRaises(AppError):self.s.post(self.po,'/api/proposals/decision',{'proposal_id':b['id'],'expected_version':b['version'],'state':'accepted'})
        self.assertEqual(applied['applied_prd_version'],2)
        self.assertEqual(len(self.store.history(self.p,'prd',conv['prd_id'])),2)

    def test_legacy_generated_artifacts_are_not_treated_as_authorized(self):
        conv=self.f.conversation()
        self.store.put(self.p,'message',{'conversation_id':conv['id'],'speaker':'legacy','text':'OLD_UNTRACKED_CLAIM','evidence_ids':[self.f.insight['id']]})
        self.assertNotIn('OLD_UNTRACKED_CLAIM',json.dumps(self.s.conversation(self.po,conv['id'])))

    def test_prd_import_and_foreign_version_access(self):
        prd=self.s.post(self.po,'/api/prds/import',encoded('baseline.md','# 문제\n분석 어려움\n# 요구사항\n리포트 개선',title='기준 기획서'))
        self.assertEqual(len(prd['sections']),2)
        with self.assertRaises(AppError):self.s.get(self.f.other,'/api/prds/versions/'+prd['id'])


class ProviderContractTests(unittest.TestCase):
    def payload_result(self):
        return {'text':'구조화 응답','evidence_ids':['e1'],'assumptions':['가상 검증'],'observations':[]}

    def test_bedrock_json_schema_and_tool_envelopes(self):
        for mode in ('json_schema','tool'):
            with self.subTest(mode=mode):
                response=self.payload_result()
                class Client:
                    def converse(client,**request):
                        client.request=request
                        return {'stopReason':'end_turn' if mode=='json_schema' else 'tool_use','output':{'message':{'role':'assistant','content':
                            [{'text':json.dumps(response)}] if mode=='json_schema' else [{'toolUse':{'name':'npd_chat','input':response}}]}}}
                client=Client();model=BedrockModel(model='test-only-model',client=client,output_mode=mode)
                self.assertEqual(model.generate('chat',{'question':'리서치'}),response)
                self.assertEqual(client.request['modelId'],'test-only-model')
                self.assertNotIn('Authorization',json.dumps(client.request))
                self.assertIn('outputConfig' if mode=='json_schema' else 'toolConfig',client.request)

    def test_bedrock_incomplete_refusal_malformed_and_wrong_tool_fail(self):
        cases=[{'stopReason':'max_tokens','output':{'message':{'content':[{'text':'{}'}]}}},
               {'stopReason':'guardrail_intervened','output':{}},
               {'stopReason':'end_turn','output':{'message':{'content':[{'text':'not json'}]}}}]
        for result in cases:
            with self.subTest(result=result):
                class Client:
                    def converse(client,**request):return result
                with self.assertRaises(AppError):BedrockModel(model='test',client=Client()).generate('chat',{})
        class ToolClient:
            def converse(client,**request):return {'stopReason':'tool_use','output':{'message':{'content':[{'toolUse':{'name':'delete_everything','input':{}}}]}}}
        with self.assertRaises(AppError):BedrockModel(model='test',client=ToolClient(),output_mode='tool').generate('chat',{})

    def test_provider_error_does_not_echo_request_credentials_or_data(self):
        class Client:
            def converse(client,**request):raise RuntimeError('SECRET_TOKEN PRIVATE_DOCUMENT')
        with self.assertRaises(AppError) as caught:BedrockModel(model='test',client=Client()).generate('chat',{})
        self.assertNotIn('SECRET_TOKEN',str(caught.exception));self.assertNotIn('PRIVATE_DOCUMENT',str(caught.exception))

    def test_provider_selection_is_explicit_and_bedrock_is_default(self):
        with patch.dict(os.environ,{'NPD_MODEL_PROVIDER':'bedrock','BEDROCK_MODEL_ID':''}):
            model=create_model();self.assertEqual(model.provider,'bedrock');self.assertFalse(model.configured)
        with patch.dict(os.environ,{'NPD_MODEL_PROVIDER':'unsupported'}),self.assertRaises(AppError):create_model()

    def test_schema_extra_fields_and_wrong_types_are_rejected(self):
        for mutation in [{'role':'owner'},{'evidence_ids':'e1'},{'assumptions':None}]:
            with self.subTest(mutation=mutation),self.assertRaises(AppError):validate('chat',{**self.payload_result(),**mutation})
