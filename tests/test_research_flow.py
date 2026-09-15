"""Synthetic contracts for the PDF-driven PO workflow; no live provider calls."""
import copy
import io
import json
import unittest
from unittest.mock import patch, MagicMock
from tests.helpers import Fixture, encoded, CANARY
from tests.test_stage2 import PlanningModel
from app.model import Model
from app.store import AppError


class FlowModel(PlanningModel):
    def generate(self,task,payload,images=None):
        if task not in ('service_context_extract','service_context_interview','persona_candidates'):
            return super().generate(task,payload,images)
        self.calls.append((task,copy.deepcopy(payload)))
        if self.hook:self.hook()
        if task=='service_context_extract':
            return {'updates':[{'field':k,'value':v,'reason':'문서 설명 요약','relationship':'DOCUMENT','asset_ids':[d['id'] for d in payload['documents']]}
                for k,v in {'product_name':'시연 서비스','summary':'광고주가 조건을 비교한다.','details':'조건 확인 후 직접 적용하며 자동 변경은 제공하지 않는다.','features':'조건 비교'}.items()],
                'questions':['현재 운영 중인 서비스인가?']}
        if task=='service_context_interview':
            return {'answer':'현재 설명을 보존하고 향후 희망사항으로 추가하는 변경안입니다.',
                'updates':[{'field':'proposed','value':'향후 조건 저장 기능을 검토한다.','reason':'PO의 향후 희망사항','relationship':'FUTURE_REQUEST','asset_ids':[]}],
                'questions':['조건 저장의 대상 사용자는 누구인가?']}
        e=payload['evidence'][0]
        return {'candidates':[{'name':'초안_'+name,'segment':name,'goals':'조건 비교','constraints':'검토 시간 부족',
            'assumptions':['고객이 아닌 합성 프로필'],'evidence_ids':[e['id']],'observations':[{'evidence_id':e['id'],'quote':e['text'][:100]}]}
            for name in ('판매자','운영자')],'rationale':'고객 자료의 역할을 구분한 검토용 초안'}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.s=self.f.service;self.u=self.f.po;self.p=self.u['project_id']
        self.model=FlowModel();self.s.model=self.model
    def tearDown(self):self.f.close()
    def post(self,path,body):return self.s.post(self.u,path,body)
    def asset(self,purpose='existing_service'):
        return self.post('/api/planning-assets',encoded('synthetic.md','광고주는 조건 비교 후 직접 적용한다. 운영자는 승인 조건을 확인한다.',title='합성 문서',purpose=purpose,source_nature='SYNTHETIC'))
    def context(self):
        asset=self.asset()
        return self.post('/api/service-contexts/extract',{'asset_refs':[{'id':asset['id'],'version':asset['version']}],'prompt':'현재 서비스 설명'})
    def complete(self,context):
        return self.post('/api/service-contexts/complete',{'context_id':context['id'],'expected_version':context['version'],'title':'서비스 보고서','reason':'PO가 설명과 한계를 검토'})
    def select(self,rows,old=None):
        return self.post('/api/research-inputs',{'refs':[{k:r[k] for k in ('kind','id','version')} for r in rows],**({'expected_version':old['version']} if old else {})})

    def test_extract_interview_selected_updates_preserve_current_behavior(self):
        context=self.context();before=context['fields'].copy()
        self.assertEqual(context['fields']['users'],'')
        self.assertEqual(context['origins'][0]['origin'],'document')
        context=self.post('/api/service-contexts/interview',{'context_id':context['id'],'expected_version':context['version'],'message':'향후 조건 저장을 원합니다.'})
        self.assertEqual(context['fields'],before)
        with self.assertRaises(AppError):self.complete(context)
        context=self.post('/api/service-contexts/apply',{'context_id':context['id'],'expected_version':context['version'],'fields':['proposed'],'reason':'향후 희망사항으로 분리'})
        self.assertEqual(context['fields']['details'],before['details'])
        self.assertIn('향후',context['fields']['proposed'])
        report=self.complete(context)
        self.assertEqual(report['state'],'reviewed');self.assertTrue(report['contains_synthetic'])
        self.assertEqual(report['context_snapshot']['turns'][-1]['applied'][0]['field'],'proposed')
        self.assertTrue(self.s.accessible(self.p,report))
        self.assertNotIn(CANARY,json.dumps(self.model.calls,ensure_ascii=False))

    def test_future_request_cannot_be_applied_as_current_function(self):
        context=self.context()
        result={'answer':'변경안','questions':[],'updates':[{'field':'features','value':'자동 저장','reason':'미래 기능 요청',
            'asset_ids':[],'relationship':'FUTURE_REQUEST'}]}
        with patch.object(self.model,'generate',return_value=result):
            with self.assertRaises(AppError):self.post('/api/service-contexts/interview',{'context_id':context['id'],'expected_version':context['version'],'message':'미래 요청'})
        self.assertEqual(self.f.store.get(self.p,'service_context',context['id'])['turns'],[])

    def test_report_versions_edit_context_selection_and_revocation(self):
        report=self.complete(self.context());choice=self.select([report])
        draft=self.post('/api/definitions/generate',{'stage':'product','prompt':'서비스 개선'})
        self.assertEqual(draft['research_input_ref']['version'],choice['version'])
        edited=self.post('/api/service-contexts/edit',{'result_id':report['id'],'expected_version':report['version']})
        self.assertEqual(edited['fields'],report['context_snapshot']['fields'])
        saved=self.post('/api/service-contexts/save',{'context_id':edited['id'],'expected_version':edited['version'],'fields':{**edited['fields'],'summary':'검토해서 보충한 내용'}})
        report2=self.complete(saved)
        self.assertEqual(report2['id'],report['id']);self.assertEqual(report2['version'],2)
        history=self.s.get(self.u,'/api/research-results/history/'+report['id'])
        self.assertEqual(len(history),2);self.assertIn('광고주가 조건',history[0]['text'])
        self.assertFalse(self.s.accessible(self.p,draft))
        with self.assertRaises(AppError):self.s.definition_context(self.u)
        self.select([report2],choice)
        self.assertEqual(self.s.definition_context(self.u)['research_results'][0]['version'],2)
        asset=self.s.asset(self.u,report['source_refs'][0]['id'])
        self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        self.assertNotIn(report['id'],[r['id'] for r in self.s.knowledge(self.p)])
        self.assertTrue(all(r['redacted'] for r in self.s.result_history(self.u,report['id'])))

    def test_selection_excludes_unselected_results_and_old_variants(self):
        a=self.complete(self.context())
        # Another result may contain unrelated context. It must not enter selected PRD generation.
        b=self.f.store.put(self.p,'research_result',{**a,'title':'UNSELECTED-CONTEXT','text':'UNSELECTED-CONTEXT','dependencies':[]})
        self.select([a]);self.post('/api/definitions/generate',{'stage':'product','prompt':'개선 초안'})
        payload=next(p for t,p in reversed(self.model.calls) if t=='definition_draft')
        self.assertNotIn('UNSELECTED-CONTEXT',json.dumps(payload))
        self.assertEqual({r['id'] for r in payload['evidence']},{a['id']})
        self.assertNotIn(CANARY,json.dumps(self.s.get(self.u,'/api/bootstrap'),ensure_ascii=False))
        with self.assertRaises(AppError):self.s.post(self.f.other,'/api/research-inputs',{'refs':[{k:a[k] for k in ('kind','id','version')}]})

    def test_saved_results_searchable_with_recursive_withdrawal(self):
        report=self.complete(self.context())
        results=self.s.search(self.p,'조건 비교')[0]
        self.assertIn(report['id'],[r['id'] for r in results])
        conv=self.f.conversation()
        result=self.post('/api/chat',{'conversation_id':conv['id'],'message':'서비스 요약 조건 비교'})
        self.assertTrue(result['messages'])
        self.assertNotIn(CANARY,json.dumps(self.model.calls,ensure_ascii=False))

    def test_generation_revocation_and_stale_manual_edit_are_atomic(self):
        context=self.context();asset=self.s.asset(self.u,context['source_refs'][0]['id'])
        self.model.hook=lambda:self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        with self.assertRaises(AppError):self.post('/api/service-contexts/interview',{'context_id':context['id'],'expected_version':context['version'],'message':'내용 보완'})
        self.assertEqual(self.f.store.get(self.p,'service_context',context['id'])['turns'],[])
        with self.assertRaises(AppError):self.s.get(self.f.other,'/api/research-results/history/not-found')

    def test_customer_documents_generate_many_candidates_only_selected_activated(self):
        asset=self.asset('customer_research')
        batch=self.post('/api/persona-candidates/generate',{'profile':'판매자와 운영자의 차이','asset_refs':[{'id':asset['id'],'version':asset['version']}]})
        self.assertEqual(len(batch['candidates']),2);self.assertEqual(self.f.store.list(self.p,'persona'),[])
        people=self.post('/api/persona-candidates/adopt',{'batch_id':batch['id'],'expected_version':batch['version'],'indices':[1]})
        self.assertEqual([r['name'] for r in people],['초안_운영자']);self.assertTrue(self.s.accessible(self.p,people[0]))
        with self.assertRaises(AppError):self.post('/api/persona-candidates/adopt',{'batch_id':batch['id'],'expected_version':batch['version'],'indices':[0]})
        self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        self.assertFalse(self.s.accessible(self.p,people[0]))

    def test_persona_generation_requires_customer_sources_and_no_canned_fallback(self):
        asset=self.asset()
        with self.assertRaises(AppError):self.post('/api/persona-candidates/generate',{'profile':'프로필','asset_refs':[{'id':asset['id'],'version':asset['version']}]})
        with self.assertRaises(AppError):self.post('/api/persona-candidates/generate',{'profile':'프로필','evidence_ids':[self.f.insight['id']]})
        self.s.model=Model()
        with self.assertRaises(AppError):self.context()
        self.assertEqual(self.f.store.list(self.p,'service_context'),[])

    def test_synthetic_voc_requires_explicit_test_mode_and_cannot_be_actual_result(self):
        with self.assertRaises(AppError):self.post('/api/voc/classify',{})
        person=self.f.persona();conv=self.f.conversation([person['id']])
        conv=self.post('/api/chat',{'conversation_id':conv['id'],'message':'@'+person['alias']+' 소재 분석 문제를 알려주세요.'})
        answer=conv['messages'][-1]
        self.assertTrue(answer['contains_synthetic'])
        with self.assertRaises(AppError):self.s.require_actual_voc_messages(self.u,[answer])
        self.assertEqual(self.f.store.list(self.p,'research_result'),[])

    def test_actual_voc_analysis_requires_cited_real_records_and_rejects_virtual_speaker(self):
        self.s.post(self.f.owner,'/api/voc/upload',encoded('support.csv','external_id,text,evidence_type\nreal-1,조건 비교 설명이 필요합니다,real\n',source_name='테스트 상담 로그'))
        real=self.s.voc_records(self.p)[0]
        self.s.require_actual_voc_messages(self.u,[{'evidence_ids':[real['id']],'is_synthetic':False}])
        with self.assertRaises(AppError):self.s.require_actual_voc_messages(self.u,[{'evidence_ids':[real['id']],'is_synthetic':True}])
        with self.assertRaises(AppError):self.s.require_actual_voc_messages(self.u,[{'evidence_ids':[]}])

    def test_debrief_reuses_same_input_and_explicit_regeneration_is_validated(self):
        person=self.f.persona();conv=self.f.conversation([person['id']])
        self.post('/api/chat',{'conversation_id':conv['id'],'message':'소재 분석 문제'})
        a=self.post('/api/debriefs',{'conversation_id':conv['id']});count=len(self.model.calls)
        b=self.post('/api/debriefs',{'conversation_id':conv['id']})
        self.assertEqual(a['id'],b['id']);self.assertEqual(len(self.model.calls),count)
        with self.assertRaises(AppError):self.post('/api/debriefs',{'conversation_id':conv['id'],'regenerate':True})
        c=self.post('/api/debriefs',{'conversation_id':conv['id'],'regenerate':True,'reason':'다른 요약 비교'})
        self.assertNotEqual(c['id'],a['id'])

    def test_completed_demo_followup_preserves_original_and_uses_reviewed_context(self):
        study=self.post('/api/studies/demo',{})
        self.assertEqual(self.post('/api/studies/demo',{})['id'],study['id'])
        self.assertEqual(self.model.calls,[])
        original=self.s.conversation(self.u,study['conversation_id']);self.assertEqual(len(original['messages']),8)
        self.assertTrue(self.s.accessible(self.p,study));self.assertEqual(study['status'],'completed')
        follow=self.post('/api/studies/followup',{'study_id':study['id'],'expected_version':study['version'],'title':'의견 차이의 이유'})
        self.assertNotIn('study_id',follow)
        self.post('/api/chat',{'conversation_id':follow['id'],'message':'이 FGI에서 타겟 설정 조건에 관한 의견 차이를 설명해 주세요.'})
        payload=next(p for t,p in reversed(self.model.calls) if t=='chat')
        self.assertEqual(len(payload['followup_context']['messages']),8)
        self.assertEqual(self.s.conversation(self.u,study['conversation_id']),original)
        with self.assertRaises(AppError):self.post('/api/chat',{'conversation_id':study['conversation_id'],'message':'완료본 수정'})
        debrief=self.s.get(self.u,'/api/debriefs')[0]
        self.select([debrief]);self.post('/api/definitions/generate',{'stage':'product','prompt':'타겟 설정 개선 가설'})
        payload=next(p for t,p in reversed(self.model.calls) if t=='definition_draft')
        self.assertEqual(payload['context']['reviewed_fgi'][0]['id'],debrief['id'])

    def test_legacy_synthetic_voc_is_not_grandfathered_into_knowledge_or_prd(self):
        person=self.f.persona();conv=self.f.conversation([person['id']])
        conv=self.post('/api/chat',{'conversation_id':conv['id'],'message':'소재 분석 문제'})
        message=conv['messages'][-1]
        legacy=self.f.store.put(self.p,'research_result',{'category':'voc','state':'reviewed','title':'기존 잘못된 VoC 분류',
            'text':'페르소나 발언','message_ids':[message['id']],'dependencies':message['dependencies'],'contains_synthetic':False})
        self.assertFalse(self.s.accessible(self.p,legacy))
        self.assertNotIn(legacy['id'],[r['id'] for r in self.s.knowledge(self.p)])
        with self.assertRaises(AppError):self.select([legacy])

    def test_legacy_chat_creates_visible_batch_without_active_persona(self):
        conv=self.f.conversation()
        answer=self.post('/api/chat',{'conversation_id':conv['id'],'message':'소재 분석 광고주 페르소나를 만들어 주세요.'})
        self.assertEqual(answer['messages'][-1]['status'],'persona_draft_created')
        self.assertEqual(self.f.store.list(self.p,'persona'),[])
        self.assertEqual(len(self.s.get(self.u,'/api/persona-candidates')),1)

    def test_final_prd_gate_requires_matching_research_scope(self):
        from tests.research_factory import scope_with_source
        report=self.complete(self.context());choice=self.select([report])
        draft=self.post('/api/definitions/generate',{'stage':'prd','prompt':'선택한 서비스 개선 PRD'})
        selected_pack=scope_with_source(self.s,self.u,report)
        linked=self.post('/api/definitions/link-research',{'document_id':draft['id'],'expected_version':draft['version']})
        self.assertEqual(linked['research_pack_ref']['id'],selected_pack['id'])
        readiness=self.post('/api/definitions/readiness',{'document_id':linked['id'],'expected_version':linked['version']})
        self.assertFalse(readiness['ready'])
        self.assertTrue(readiness['errors'])
        # Incomplete structured research is never certified by choosing a report.
        with self.assertRaises(AppError):self.post('/api/definitions/confirm',{'document_id':linked['id'],'expected_version':linked['version']})
        self.select([],choice)
        other=self.post('/api/definitions/generate',{'stage':'prd','prompt':'빈 입력에서 직접 검토할 초안'})
        with self.assertRaises(AppError):self.post('/api/definitions/link-research',{'document_id':other['id'],'expected_version':other['version']})

    def test_generated_prd_cannot_cite_an_unselected_record(self):
        report=self.complete(self.context());self.select([report])
        result={'title':'범위 밖 인용','sections':[{'id':'overview','title':'범위','text':'조건 비교','evidence_ids':[self.f.insight['id']]}],'assumptions':[],'questions':[]}
        with patch.object(self.model,'generate',return_value=result):
            with self.assertRaises(AppError):self.post('/api/definitions/generate',{'stage':'product','prompt':'선택한 보고서만 사용'})
        self.assertEqual(self.f.store.list(self.p,'definition'),[])

    def test_android_source_fixed_endpoint_pagination_dedup_and_roles(self):
        body={'package_name':'com.example.demo','max_pages':2}
        response={'reviews':[{'reviewId':'r1','comments':[{'userComment':{'text':'조건 비교가 어렵습니다.','starRating':3,'lastModified':{'seconds':'1726358400'}}}]}]}
        with patch.dict('os.environ',{'ANDROID_PUBLISHER_TOKEN':'test-token'}),patch.object(self.s,'fetch_review_page',side_effect=[{**response,'tokenPagination':{'nextPageToken':'next'}},response]) as fetch:
            result=self.s.collect_android_reviews(self.f.owner,body)
        self.assertEqual(result['imported'],1);self.assertEqual(result['duplicates'],1)
        self.assertTrue(fetch.call_args.args[0].startswith('https://androidpublisher.googleapis.com/'))
        self.assertIn('token=next',fetch.call_args.args[0])
        self.assertEqual(self.s.voc_records(self.p)[0]['evidence_type'],'real')
        with self.assertRaises(AppError):self.s.collect_android_reviews(self.u,body)

    def test_s3_project_prefix_size_and_no_real_synthetic_relabeling(self):
        data=b'external_id,text,evidence_type\nr1,synthetic example,synthetic\n'
        stream=io.BytesIO(data);client=MagicMock();client.get_object.return_value={'Body':stream,'ContentLength':len(data)}
        with patch.dict('os.environ',{'NPD_REVIEW_S3_BUCKET':'configured-test-bucket','NPD_REVIEW_S3_PREFIX':'reviews'}),patch('boto3.client',return_value=client):
            with self.assertRaises(AppError):self.s.import_s3_reviews(self.f.owner,{'object_key':'reviews/project-b/data.csv'})
            result=self.s.import_s3_reviews(self.f.owner,{'object_key':'reviews/project-a/data.csv'})
        self.assertEqual(result['imported'],1);self.assertTrue(stream.closed)
        self.assertEqual(self.s.voc_records(self.p)[0]['evidence_type'],'synthetic')
        client.get_object.assert_called_once_with(Bucket='configured-test-bucket',Key='reviews/project-a/data.csv')


if __name__=='__main__':unittest.main()
