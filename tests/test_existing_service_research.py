"""Synthetic acceptance cases; no attached business documents or customer data."""
import copy
import json
import unittest
from unittest.mock import patch
from app.research_contracts import TYPES, PRD_SECTIONS, clean_fields
from app.research_details import computed_details
from app.store import AppError
from tests.helpers import Fixture, encoded
from tests.test_stage2 import PlanningModel
from tests.research_factory import synthetic_fields, reference, item, pack, graph


def structured_fields():
    f=synthetic_fields('service_baseline',detail_level='STRUCTURED',service_version='demo-v1',document_as_of='2026-01-01',data_as_of='2026-01-02')
    for key,spec in TYPES['service_baseline'].items():
        if spec['type']=='rows' and spec['required']:
            row={k:(v['options'][0] if v['type']=='select' else 'Synthetic documented value or explicitly unknown') for k,v in spec['columns'].items()}
            row['id']=key+'-1';f[key]=[row]
    f['feature_matrix'][0].update(state='CURRENT',name='Manual submission')
    f['feature_matrix'].append({**f['feature_matrix'][0],'id':'feature-2','state':'UNSUPPORTED','name':'Automatic optimization'})
    f['open_topics']=[{'id':'Q'+str(i),'question':'Synthetic topic '+str(i),'owner':'Product owner','needed_stage':'DEVELOPMENT','next_action':'Ask PO; not answered'} for i in range(1,4)]
    return f


class ExistingServiceTests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.s=self.f.service;self.u=self.f.po;self.s.model=PlanningModel()
        self.brief=item(self.s,self.u,'brief',fields=synthetic_fields('brief',required_tasks=['RS-00']))
    def tearDown(self):self.f.close()
    def post(self,path,body):return self.s.post(self.u,path,body)
    def make(self,kind,fields=None,refs=(),review=True,**kw):return item(self.s,self.u,kind,self.brief['id'],refs=refs,fields=fields,review=review,**kw)
    def review(self,row):return self.post('/api/research-workspace/items/review',{'item_id':row['id'],'expected_version':row['version'],'reason':'Synthetic acceptance review'})
    def save(self,row,fields):return self.post('/api/research-workspace/items',{'item_id':row['id'],'expected_version':row['version'],'fields':fields,'change_reason':'Synthetic edit'})
    def baseline(self,refs=()):return self.make('service_baseline',structured_fields(),refs=refs)
    def start(self,baseline):return self.post('/api/research-workspace/interview/start',{'baseline_id':baseline['id'],'expected_version':baseline['version'],'title':'Synthetic PO interview'})
    def answered(self,row):
        fields=copy.deepcopy(row['fields']);fields.update(run_state='EXECUTED',nature='SYNTHETIC')
        for i,a in enumerate(fields['answers'][:2]):
            a.update(status='ANSWERED',answer='Synthetic response',respondent_role='Synthetic PO',confirmed_at='2026-01-03',effective_at='2026-01-04',scope='Demo workspace only',evidence_note='Synthetic interview; no actual evidence',classification='OPERATION_CHANGE' if not i else 'FUTURE_REQUEST',target_field='permissions' if not i else 'proposed',proposed_text='Second approval required' if not i else 'Future automation idea')
        return self.review(self.save(row,fields))
    def apply(self,baseline,interview,ids):return self.post('/api/research-workspace/interview/apply',{'baseline_id':baseline['id'],'baseline_version':baseline['version'],'interview_id':interview['id'],'expected_version':interview['version'],'answer_ids':ids,'title':'Synthetic revised service','reason':'Explicit selective application'})
    def calculation(self,terms,**fields):
        source=self.make('evidence')
        rows=[{'id':'n'+str(i),'numerator':n,'denominator':d,'source_id':source['id'],'source_locator':'Synthetic table row '+str(i)} for i,(n,d) in enumerate(terms)]
        return self.make('calculation',synthetic_fields('calculation',measurement_state='SYNTHETIC',terms=rows,**fields),refs=[source])

    def test_detailed_baseline_requires_all_tables_and_preserves_unsupported(self):
        f=structured_fields();f['role_matrix']=[]
        row=self.make('service_baseline',f,review=False)
        with self.assertRaisesRegex(AppError,'역할별 권한'):self.review(row)
        baseline=self.baseline()
        self.assertEqual(baseline['fields']['feature_matrix'][1]['state'],'UNSUPPORTED')
        self.assertIn('실제 성공을 확인할 증거',baseline['text'])
        self.assertIn('분모 0',baseline['text'])
        self.assertEqual(baseline['fields']['document_as_of'],'2026-01-01')

    def test_legacy_baseline_does_not_satisfy_required_detailed_service_work(self):
        brief=item(self.s,self.u,'brief',fields=synthetic_fields('brief',required_tasks=['RS-00','SV-02']))
        old=item(self.s,self.u,'service_baseline',brief['id'])
        value=pack(self.s,self.u,brief,[old]+graph(self.s,self.u,brief))
        gate=self.post('/api/research-workspace/gate',{'pack_id':value['id'],'expected_version':value['version']})
        self.assertFalse(gate['ready']);self.assertTrue(any('구조' in x for x in gate['errors']))

    def test_interview_plan_has_no_answers_and_stale_version_is_rejected(self):
        baseline=self.baseline();plan=self.start(baseline)
        self.assertEqual(plan['fields']['run_state'],'NOT_RUN')
        self.assertTrue(all(not a['answer'] and a['status']=='OPEN' for a in plan['fields']['answers']))
        self.review(plan)
        with self.assertRaises(AppError):self.start({**baseline,'version':1})
        with self.assertRaises(AppError):self.s.post(self.f.other,'/api/research-workspace/interview/start',{'baseline_id':baseline['id'],'expected_version':baseline['version'],'title':'Cross project'})

    def test_interview_answers_require_execution_role_dates_and_classification(self):
        plan=self.start(self.baseline());f=copy.deepcopy(plan['fields'])
        f['answers'][0].update(status='ANSWERED',answer='Invented without confirmation')
        with self.assertRaisesRegex(AppError,'미실행'):self.review(self.save(plan,f))
        plan=self.start(self.baseline());answered=self.answered(plan)
        for key,value in [('respondent_role',''),('confirmed_at','bad-date'),('classification','UNVERIFIED'),('evidence_note','')]:
            f=copy.deepcopy(answered['fields']);f['answers'][0][key]=value
            draft=self.make('po_interview',f,refs=[self.s.research_ref(self.u,answered['input_refs'][0],True)],review=False)
            with self.assertRaises(AppError,msg=key):self.review(draft)

    def test_selective_apply_preserves_original_future_state_and_unanswered_topics(self):
        baseline=self.baseline();interview=self.answered(self.start(baseline));new=self.apply(baseline,interview,['Q1','Q2'])
        self.assertNotEqual(new['id'],baseline['id']);self.assertEqual(new['state'],'draft')
        self.assertEqual(self.s.research_item(self.u,baseline['id']),baseline)
        self.assertIn('Second approval required',new['fields']['permissions'])
        self.assertIn('적용일: 2026-01-04',new['fields']['permissions'])
        self.assertEqual(new['fields']['document_as_of'],'')
        self.assertEqual(new['fields']['feature_matrix'][1]['state'],'UNSUPPORTED')
        self.assertIn('향후 희망사항',new['fields']['proposed'])
        self.assertEqual([q['id'] for q in new['fields']['open_topics']],['Q3'])
        self.assertEqual(new['fields']['role_matrix'],[])
        self.assertIn('role_matrix',new['interview_application']['tables_to_reconcile'])
        with self.assertRaisesRegex(AppError,'역할별 권한'):self.review(new)
        f=copy.deepcopy(new['fields']);f['role_matrix']=structured_fields()['role_matrix'];f['role_matrix'][0]['approval']='Second approval required';f['document_as_of']='2026-01-04'
        new=self.review(self.save(new,f));self.assertTrue(new['contains_synthetic'])
        history=self.s.get(self.u,'/api/research-workspace/history/'+new['id'])
        self.assertGreaterEqual(len(history),3)
        self.assertEqual(new['interview_application']['changes'][0]['before'],baseline['fields']['permissions'])

    def test_future_request_cannot_change_current_behavior_and_extra_questions_survive(self):
        baseline=self.baseline();interview=self.answered(self.start(baseline));f=copy.deepcopy(interview['fields'])
        f['answers'][1]['target_field']='as_is'
        interview=self.review(self.save(interview,f))
        with self.assertRaisesRegex(AppError,'향후 희망'):self.apply(baseline,interview,['Q2'])
        f=copy.deepcopy(interview['fields']);f['answers'].append({**f['answers'][2],'id':'Q4','question':'Additional unresolved topic'})
        interview=self.review(self.save(interview,f));new=self.apply(baseline,interview,['Q1'])
        self.assertIn('Q4',[q['id'] for q in new['fields']['open_topics']])
        self.assertIn('Q2',[q['id'] for q in new['fields']['open_topics']])

    def test_withdrawal_or_concurrent_change_prevents_interview_application(self):
        asset=self.post('/api/planning-assets',encoded('demo.md','Synthetic scope only',title='Synthetic source',purpose='existing_service',source_nature='SYNTHETIC'))
        baseline=self.baseline([asset]);interview=self.answered(self.start(baseline))
        self.post('/api/planning-assets/withdraw',{'asset_id':asset['id'],'expected_version':asset['version']})
        with self.assertRaises(AppError):self.apply(baseline,interview,['Q2'])
        history=self.s.get(self.u,'/api/research-workspace/history/'+interview['id'])
        self.assertTrue(all(r.get('redacted') for r in history))
        baseline=self.baseline();interview=self.answered(self.start(baseline));count=len(self.s.store.list(self.u['project_id'],'research_item'))
        original=self.s.planning_actor
        def changed(user):
            self.s.store.update(user['project_id'],'research_item',baseline['id'],{'change_reason':'Concurrent edit'})
            original(user)
        with patch.object(self.s,'planning_actor',changed):
            with self.assertRaises(AppError):self.apply(baseline,interview,['Q2'])
        self.assertEqual(len(self.s.store.list(self.u['project_id'],'research_item')),count)

    def test_ratio_of_sums_not_mean_ratios_and_percent_is_level(self):
        ratio=self.calculation([(200,100),(300,300)])
        self.assertEqual(ratio['computed']['value'],1.25)
        self.assertNotAlmostEqual(ratio['computed']['value'],1.5)
        pct=self.calculation([(200,100),(300,300)],formula='PERCENT_OF_SUMS')
        self.assertEqual(pct['computed']['value'],125)
        self.assertEqual(pct['computed']['causal_effect'],'NOT_ESTABLISHED')

    def test_zero_unknown_and_overflow_never_become_zero_or_infinity(self):
        zero=self.calculation([(100,0)])
        self.assertIsNone(zero['computed']['value']);self.assertEqual(zero['computed']['status'],'NOT_COMPUTABLE')
        unknown=synthetic_fields('calculation',measurement_state='UNKNOWN',unknown_reason='No measurement',terms=[{'id':'n','numerator':None,'denominator':None,'source_id':'synthetic','source_locator':'not measured'}])
        self.assertIsNone(computed_details('calculation',unknown)['value'])
        overflow=self.calculation([(1e308,1e-308)])
        json.dumps(overflow,allow_nan=False)
        self.assertEqual(overflow['computed']['status'],'NOT_COMPUTABLE')
        for bad in [float('inf'),float('nan')]:
            with self.assertRaises(AppError):clean_fields('funnel',{'stages':[{'count':bad}]})
        with self.assertRaises(AppError):self.calculation([(-1,2)])

    def test_calculation_sources_and_row_identity_must_be_reviewable(self):
        row=self.calculation([(100,10)]);f=copy.deepcopy(row['fields']);f['terms'][0]['source_id']='unselected-private-source'
        with self.assertRaisesRegex(AppError,'공유 근거'):self.review(self.save(row,f))
        for bad in ['not a list',[{'bogus':'column'}],[None]]:
            with self.assertRaises(AppError):clean_fields('calculation',{'terms':bad})
        row=self.calculation([(10,2),(20,3)]);f=copy.deepcopy(row['fields']);f['terms'][1]['id']=f['terms'][0]['id']
        with self.assertRaisesRegex(AppError,'고유'):self.review(self.save(row,f))

    def test_nested_funnel_preserves_denominators_and_zero_missing(self):
        fields=synthetic_fields('funnel',measurement_state='SYNTHETIC',nesting_confirmed=True,stages=[{'id':'a','label':'Started','count':100},{'id':'b','label':'Approved','count':60},{'id':'c','label':'Actually completed','count':30}])
        row=self.make('funnel',fields)
        self.assertEqual(row['computed']['steps'][2]['of_start_percent'],30)
        self.assertEqual(row['computed']['steps'][2]['of_previous_percent'],50)
        self.assertEqual(row['computed']['steps'][2]['dropoff_from_previous'],30)
        for counts in [(100,101),(100,0.5)]:
            f={**fields,'stages':[{'id':str(i),'label':'Synthetic stage','count':n} for i,n in enumerate(counts)]}
            draft=self.make('funnel',f,review=False);self.assertEqual(draft['computed']['status'],'INVALID')
            with self.assertRaises(AppError):self.review(draft)
        f={**fields,'stages':[{'id':str(i),'label':'Synthetic stage','count':0} for i in range(2)]}
        row=self.make('funnel',f);self.assertIsNone(row['computed']['steps'][1]['of_start_percent'])

    def test_coding_counts_independent_participants_and_excluded_duplicates(self):
        source=self.make('evidence');records=[]
        for i,disposition in enumerate(['INCLUDED','INCLUDED','EXCLUDED','DUPLICATE']):
            records.append({'id':str(i),'participant_key':'p1','disposition':disposition,'nature':'SYNTHETIC','code':'usability','reason':'Synthetic coding','source_id':source['id']})
        row=self.make('voc_coding',synthetic_fields('voc_coding',records=records),refs=[source]);c=row['computed']
        self.assertEqual((c['included_utterances'],c['independent_participants'],c['excluded'],c['duplicates'],c['synthetic_utterances']),(2,1,1,1,2))
        f=copy.deepcopy(row['fields']);f['records'][1]['participant_key']='';row=self.review(self.save(row,f))
        self.assertIsNone(row['computed']['independent_participants'])
        f=copy.deepcopy(row['fields']);f['records'][0]['nature']='REAL'
        with self.assertRaisesRegex(AppError,'실제 발언'):self.review(self.save(row,f))

    def test_synthetic_upload_stays_synthetic_through_analysis_prd_and_export(self):
        content='All values are synthetic. This is a fictional test service.'
        with self.assertRaises(AppError):self.post('/api/planning-assets',encoded('demo.md',content,title='Synthetic source',purpose='existing_service',source_nature='OBSERVED'))
        asset=self.post('/api/planning-assets',encoded('demo.md',content,title='Synthetic source',purpose='existing_service'))
        self.assertEqual(asset['source_nature'],'SYNTHETIC')
        row=self.post('/api/service-analysis',{'asset_refs':[reference(asset)],'prompt':'Analyze synthetic fixture'})
        self.assertTrue(row['contains_synthetic']);self.assertEqual(self.s.model.calls[-1][1]['documents'][0]['source_nature'],'SYNTHETIC')
        reviewed=self.post('/api/research-results/review',{'result_id':row['id'],'expected_version':row['version'],'text':row['text']})
        baseline=self.baseline([reviewed]);self.assertTrue(baseline['contains_synthetic'])
        value=pack(self.s,self.u,self.brief,[baseline]);out=self.post('/api/research-workspace/export',{'pack_id':value['id'],'expected_version':value['version']})
        self.assertIn('SYNTHETIC',out['markdown']);self.assertNotIn(asset['id'],json.dumps(out['package']))
        definition=self.post('/api/research-workspace/compose',{'pack_id':value['id'],'expected_version':value['version'],'title':'Synthetic PRD'})
        self.assertTrue(definition['contains_synthetic'])
        for kind,f in [('measurement',synthetic_fields('measurement',measurement_state='OBSERVED',value=1)),('numeric_claim',synthetic_fields('numeric_claim',provenance='OBSERVED'))]:
            draft=self.make(kind,f,refs=[baseline],review=False)
            with self.assertRaisesRegex(AppError,'합성 입력'):self.review(draft)

    def test_synthetic_actual_report_cannot_be_promoted_to_customer_validation(self):
        asset=self.post('/api/planning-assets',encoded('report.md','All responses are synthetic.',title='Synthetic report',purpose='actual_fgi'))
        row=self.post('/api/research-results',{'category':'fgi_actual','asset_id':asset['id'],'asset_version':asset['version'],'title':'Synthetic submitted report','text':'Test only'})
        with self.assertRaisesRegex(AppError,'합성 자료'):self.post('/api/research-results/review',{'result_id':row['id'],'expected_version':row['version'],'text':'Test only','actual_customer_data':True})

    def test_known_conflict_cannot_be_hidden_by_omitting_it_from_pack(self):
        rows=graph(self.s,self.u,self.brief);other=self.make('evidence')
        conflict=self.make('conflict',refs=[rows[0],other])
        value=pack(self.s,self.u,self.brief,rows)
        gate=self.post('/api/research-workspace/gate',{'pack_id':value['id'],'expected_version':value['version']})
        self.assertFalse(gate['ready']);self.assertTrue(any(conflict['id'] in x for x in gate['errors']))

    def test_development_readiness_blocks_unanswered_definition_questions(self):
        value=pack(self.s,self.u,self.brief,graph(self.s,self.u,self.brief))
        row=self.post('/api/definitions',{'stage':'prd','title':'Synthetic complete sections',
            'sections':[{'id':k,'title':v,'text':'Synthetic draft; not validated'} for k,v in PRD_SECTIONS.items()],
            'questions':[{'text':'Confirm policy','status':'unanswered','owner':'PO','next_action':'Interview','needed_stage':'DEVELOPMENT'}]})
        body={'document_id':row['id'],'expected_version':row['version']}
        self.assertTrue(self.post('/api/definitions/readiness',body)['ready'])
        dev=self.post('/api/definitions/readiness',{**body,'stage':'development'})
        self.assertFalse(dev['ready']);self.assertIn('개발 전 미결 질문: Confirm policy',dev['errors'])

    def test_ai_tables_parse_without_invented_interview_execution(self):
        baseline=self.baseline();plan=self.answered(self.start(baseline));fields=plan['fields']
        output={'title':'Synthetic AI interview','values':[{'key':k,'text':json.dumps(v) if isinstance(v,list) else v} for k,v in fields.items()]}
        with patch.object(self.s,'generate',return_value=output):
            draft=self.post('/api/research-workspace/generate',{'output_type':'po_interview','research_id':self.brief['id'],'input_refs':[reference(baseline)],'prompt':'Prepare questions'})
        self.assertEqual(draft['fields']['run_state'],'NOT_RUN')
        self.assertTrue(all(a['status']=='OPEN' and not a['answer'] for a in draft['fields']['answers']))
        with patch.object(self.s,'generate',return_value={'title':'Bad rows','values':[{'key':'answers','text':'{not json}'}]}):
            with self.assertRaisesRegex(AppError,'AI 표 형식'):self.post('/api/research-workspace/generate',{'output_type':'po_interview','research_id':self.brief['id'],'input_refs':[reference(baseline)],'prompt':'Prepare questions'})

    def test_declared_synthetic_source_cannot_lose_label_on_reupload_or_insight_release(self):
        body=encoded('source.md','Synthetic test content without a whole-document declaration',title='Synthetic source',source_nature='SYNTHETIC')
        source=self.s.post(self.f.owner,'/api/research/upload',body)
        updated=self.s.post(self.f.owner,'/api/research/upload',{**body,'source_id':source['id'],'expected_version':source['version'],'source_nature':'UNVERIFIED'})
        self.assertEqual(updated['source_nature'],'SYNTHETIC')
        draft=self.s.insight_save(self.f.owner,{'source_id':source['id'],'title':'Synthetic draft','text':'Synthetic assertion','feature':'reporting','evidence_category':'PUBLIC_FACT'})
        with self.assertRaisesRegex(AppError,'합성 원본'):self.s.insight_release(self.f.owner,{'insight_id':draft['id'],'expected_version':draft['version'],'published':True})
        edited=self.s.insight_save(self.f.owner,{'insight_id':draft['id'],'expected_version':draft['version'],'evidence_category':'ATTACHMENT_STATEMENT'})
        self.s.insight_release(self.f.owner,{'insight_id':edited['id'],'expected_version':edited['version'],'published':True})
        self.assertTrue(next(r for r in self.s.knowledge(self.u['project_id']) if r['id']==edited['id'])['contains_synthetic'])

    def test_exception_is_scoped_and_cannot_replace_general_policy(self):
        baseline=self.baseline();interview=self.answered(self.start(baseline));f=copy.deepcopy(interview['fields'])
        f['answers'][0]['classification']='EXCEPTION'
        interview=self.review(self.save(interview,f))
        with self.assertRaisesRegex(AppError,'예외 상황'):self.apply(baseline,interview,['Q1'])
        f=copy.deepcopy(interview['fields']);f['answers'][0]['target_field']='exceptions';interview=self.review(self.save(interview,f))
        revised=self.apply(baseline,interview,['Q1'])
        self.assertEqual(revised['fields']['permissions'],baseline['fields']['permissions'])
        self.assertTrue(revised['fields']['exceptions'].startswith(baseline['fields']['exceptions']))
        self.assertIn('적용 범위: Demo workspace only',revised['fields']['exceptions'])

    def test_synthetic_only_requirement_stays_pilot_proposal_and_non_po_cannot_confirm(self):
        rows=graph(self.s,self.u,self.brief);value=pack(self.s,self.u,self.brief,rows)
        body={'pack_id':value['id'],'expected_version':value['version']}
        self.assertTrue(self.post('/api/research-workspace/gate',body)['ready'])
        gate=self.post('/api/research-workspace/gate',{**body,'stage':'development'})
        self.assertFalse(gate['ready']);self.assertTrue(any('파일럿 제안' in x for x in gate['errors']))
        with self.assertRaises(AppError) as err:self.s.post({**self.u,'role':'viewer'},'/api/research-workspace/items/review',{'item_id':rows[0]['id'],'expected_version':rows[0]['version'],'reason':'Unauthorized'})
        self.assertEqual(err.exception.status,403)

    def test_five_product_scopes_use_same_contract_without_advertising_defaults(self):
        for scope in ['Ads portal','Creative review','Shipping service','Support assistant','Handwritten planning']:
            brief=item(self.s,self.u,'brief',fields=synthetic_fields('brief',product_scope=scope,required_tasks=['RS-00']))
            fields=structured_fields();fields['roles']='Synthetic '+scope+' user';fields['jobs']='Synthetic goal for '+scope
            row=item(self.s,self.u,'service_baseline',brief['id'],fields=fields)
            self.assertEqual(row['fields']['roles'],'Synthetic '+scope+' user')
            self.assertNotIn('ROAS',row['text'])


if __name__=='__main__':unittest.main()
