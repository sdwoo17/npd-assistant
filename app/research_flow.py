"""Versioned PO research contexts, selected PRD inputs and customer persona drafts."""
import copy
import uuid
from .contracts import text, optional, strings, revision, dependency_map, validate_citations
from .flow_contracts import SERVICE_FIELDS
from .ingest import redact
from .research_provenance import derived_nature, synthetic_record
from .stories import lineage
from .store import AppError, timestamp


class ResearchFlow:
    def flow_record(self, user, kind, rid, version=None):
        row=self.store.get(user['project_id'],kind,rid)
        if (version is not None and row['version']!=version) or not self.accessible(user['project_id'],row):
            raise AppError('선택한 자료가 변경됐습니다. 현재 버전을 다시 확인하세요.',409)
        return row

    def flow_documents(self,user,refs,purpose):
        if not isinstance(refs,list) or not 1<=len(refs)<=10:raise AppError('문서 1~10개를 선택하세요.')
        if any(not isinstance(r,dict) for r in refs):raise AppError('문서 ID와 버전을 확인하세요.')
        assets=[self.asset(user,text(r,'id',80),revision({'expected_version':r.get('version')})) for r in refs]
        if len({r['id'] for r in assets})!=len(assets):raise AppError('중복 문서를 제외하세요.')
        if any(r['media_type']!='document' or r['purpose']!=purpose for r in assets):raise AppError('이 작업에 맞는 문서만 선택하세요.')
        docs=[{'id':r['id'],'asset_id':r['id'],'kind':'planning_asset','version':r['version'],'title':r['title'],
            'text':self.store.decrypt(r['encrypted_text']),'dependencies':[{'kind':'planning_asset','id':r['id'],'version':r['version']}],'evidence_type':'synthetic' if synthetic_record(r) else 'planning_context',
            **derived_nature([r])} for r in assets]
        if sum(len(r['text']) for r in docs)>120000:raise AppError('문서를 나누어 분석하세요. 최대 120,000자입니다.')
        if any(not r['text'].strip() for r in docs):raise AppError('추출 가능한 텍스트가 없는 문서입니다. 전사본을 확인해 주세요.')
        return assets,docs

    def context_updates(self,updates,assets,extracted=False):
        if not isinstance(updates,list) or len(updates)>len(SERVICE_FIELDS):raise AppError('서비스 필드 응답을 확인하세요.',502)
        seen=set();out=[];allowed={r['id'] for r in assets}
        for row in updates:
            key=row.get('field')
            if key not in SERVICE_FIELDS or key in seen:raise AppError('서비스 필드가 중복되거나 지원되지 않습니다.',502)
            relationship=row.get('relationship')
            if relationship not in ('DOCUMENT','PO_CURRENT','FUTURE_REQUEST','CONFLICT','UNKNOWN') or (relationship=='FUTURE_REQUEST' and key!='proposed'):
                raise AppError('향후 희망사항은 현재 기능과 구분해서 다시 제안해야 합니다.',502)
            seen.add(key);value=redact(optional(row,'value',12000));ids=strings(row.get('asset_ids',[]),10,80)
            if not set(ids).issubset(allowed) or extracted and value and not ids:raise AppError('추출 내용의 문서 근거를 확인하지 못했습니다.',502)
            validate_citations(value,allowed)
            out.append({'field':key,'value':value,'reason':text(row,'reason',2000),'asset_ids':ids,
                'origin':'document' if ids else 'po_reported','relationship':relationship})
        return out

    def extract_service_context(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        assets,docs=self.flow_documents(user,body.get('asset_refs'),'existing_service')
        result=self.generate('service_context_extract',{'documents':docs,'fields':SERVICE_FIELDS,'prompt':text(body,'prompt',5000)})
        updates=self.context_updates(result['updates'],assets,True)
        fields={k:'' for k in SERVICE_FIELDS};fields.update({r['field']:r['value'] for r in updates})
        if not fields['summary'] or not fields['details']:raise AppError('서비스 요약·상세 설명을 추출하지 못했습니다. 문서와 분석 범위를 확인하세요.',502)
        self.planning_actor(user)
        return self.store.write(p,inserts=[('service_context',{'fields':fields,'origins':updates,
            'source_refs':[{'id':r['id'],'version':r['version']} for r in assets], 'dependencies':lineage(assets),
            'questions':strings(result['questions'],40,3000),'turns':[],'pending_updates':[],
            'state':'editing','created_by':user['id'],**derived_nature(assets)},None)],expected_epoch=epoch)[0]

    def save_service_context(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.flow_record(user,'service_context',text(body,'context_id',80),revision(body))
        if row['state']!='editing':raise AppError('저장된 보고서의 Edit에서 새 편집본을 여세요.',409)
        fields=body.get('fields')
        if not isinstance(fields,dict) or set(fields)!=set(SERVICE_FIELDS):raise AppError('서비스 필드를 모두 확인하세요.')
        fields={k:redact(optional(fields,k,12000)) for k in SERVICE_FIELDS}
        origins=copy.deepcopy(row['origins'])
        for key in SERVICE_FIELDS:
            if fields[key]!=row['fields'][key]:
                origins=[r for r in origins if r['field']!=key]+[{'field':key,'value':fields[key],'reason':'PO 직접 수정','asset_ids':[],'origin':'po_edited'}]
        self.planning_actor(user)
        return self.store.write(p,updates=[('service_context',row['id'],{'fields':fields,'origins':origins},row['version'])],expected_epoch=epoch)[0]

    def interview_service_context(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.flow_record(user,'service_context',text(body,'context_id',80),revision(body))
        if row['state']!='editing' or row['pending_updates']:raise AppError('이전 변경안을 적용 또는 제외한 뒤 다음 질문을 보내세요.',409)
        if len(row['turns'])>=50:raise AppError('보고서를 저장하고 새 편집본에서 인터뷰를 이어가세요.')
        assets,docs=self.flow_documents(user,row['source_refs'],'existing_service')
        message=redact(text(body,'message',5000))
        result=self.generate('service_context_interview',{'fields':row['fields'],'documents':docs,
            'questions':row['questions'],'history':row['turns'],'message':message,'field_labels':SERVICE_FIELDS})
        updates=self.context_updates(result['updates'],assets)
        turn={'question':message,'answer':redact(text(result,'answer',12000)),'proposals':updates,'at':timestamp()}
        turns=row['turns']+[turn]
        if sum(len(t['question'])+len(t['answer']) for t in turns)>100000:raise AppError('인터뷰가 길어졌습니다. 보고서 저장 후 범위를 나눠 주세요.')
        self.planning_actor(user)
        return self.store.write(p,updates=[('service_context',row['id'],{'turns':turns,'pending_updates':updates,
            'questions':strings(result['questions'],40,3000)},row['version'])],expected_epoch=epoch)[0]

    def apply_context_updates(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.flow_record(user,'service_context',text(body,'context_id',80),revision(body))
        if row['state']!='editing':raise AppError('편집 중인 서비스 분석을 선택하세요.',409)
        selected=strings(body.get('fields',[]),len(SERVICE_FIELDS),80)
        if not set(selected).issubset({r['field'] for r in row['pending_updates']}):raise AppError('현재 변경안에서 적용할 항목을 선택하세요.')
        fields=dict(row['fields']);origins=copy.deepcopy(row['origins']);changes=[]
        for update in row['pending_updates']:
            if update['field'] in selected:
                key=update['field'];changes.append({'field':key,'before':fields[key],'after':update['value']})
                fields[key]=update['value'];origins=[r for r in origins if r['field']!=key]+[update]
        turns=copy.deepcopy(row['turns'])
        if turns:turns[-1].update(applied=changes,decision_reason=text(body,'reason',2000))
        self.planning_actor(user)
        return self.store.write(p,updates=[('service_context',row['id'],{'fields':fields,'origins':origins,
            'turns':turns,'pending_updates':[]},row['version'])],expected_epoch=epoch)[0]

    def complete_service_context(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        row=self.flow_record(user,'service_context',text(body,'context_id',80),revision(body))
        if row['state']!='editing' or row['pending_updates']:raise AppError('편집 중인 내용을 확인하고 남은 인터뷰 변경안을 처리하세요.',409)
        if any(not row['fields'][k].strip() for k in ('product_name','summary','details')):raise AppError('제품 이름·서비스 요약·상세 설명을 확인하세요.')
        assets=[self.asset(user,r['id'],r['version']) for r in row['source_refs']]
        old=self.flow_record(user,'research_result',row['base_result']['id'],row['base_result']['version']) if row.get('base_result') else None
        content='\n\n'.join('## '+label+'\n'+(row['fields'][k] or '미확인') for k,label in SERVICE_FIELDS.items())
        content+='\n\n## 남은 확인 질문\n'+'\n'.join('- '+q for q in row['questions'])
        fields={'title':text(body,'title',200),'category':'existing_service','text':content,'state':'reviewed',
            'sections':[{'title':label,'text':row['fields'][k] or '미확인','asset_ids':[]} for k,label in SERVICE_FIELDS.items()],
            'questions':row['questions'],'source_refs':row['source_refs'],'dependencies':lineage(assets),'evidence_ids':[],
            'context_snapshot':{k:row[k] for k in ('fields','origins','source_refs','questions','turns')},
            'evidence_type':'planning_context','created_by':user['id'],'reviewed_by':user['id'],
            'review_reason':text(body,'reason',2000),'customer_validation':'unverified',**derived_nature([row]+assets)}
        rid=old['id'] if old else 'RSC-'+uuid.uuid4().hex
        updates=[('service_context',row['id'],{'state':'completed','result_id':rid},row['version'])]
        if old:updates.append(('research_result',rid,fields,old['version']))
        self.planning_actor(user)
        saved=self.store.write(p,inserts=[] if old else [('research_result',fields,rid)],updates=updates,expected_epoch=epoch)
        return next(r for r in saved if r['kind']=='research_result')

    def edit_service_report(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        result=self.flow_record(user,'research_result',text(body,'result_id',80),revision(body))
        if result['category']!='existing_service':raise AppError('기존 서비스 보고서를 선택하세요.')
        snapshot=result.get('context_snapshot')
        if not snapshot:
            fields={k:'' for k in SERVICE_FIELDS};fields.update(product_name=result['title'],summary=result['text'][:1000],details=result['text'][:12000])
            snapshot={'fields':fields,'origins':[],'source_refs':result['source_refs'],'questions':result.get('questions',[]),'turns':[]}
        self.planning_actor(user)
        return self.store.write(p,inserts=[('service_context',{**copy.deepcopy(snapshot),'pending_updates':[],
            'state':'editing','base_result':{'id':result['id'],'version':result['version']},
            'dependencies':result['dependencies'],'created_by':user['id'],**derived_nature([result])},None)],expected_epoch=epoch)[0]

    def saved_research(self,user):
        rows=self.research_results(user)+self.get(user,'/api/debriefs')
        return [r for r in rows if not r.get('redacted') and (r.get('state')=='reviewed' or r.get('review_status')=='po_reviewed')]

    def research_inputs(self,user):
        rows=self.store.list(user['project_id'],'research_inputs')
        return {'selection':rows[0] if rows else None,'results':self.saved_research(user)}

    def selected_flow_inputs(self,user):
        selected=self.store.list(user['project_id'],'research_inputs')
        if not selected:return None,[]
        selection=selected[0]
        return selection,[self.research_ref(user,r,True) for r in selection['refs']]

    def save_research_inputs(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        refs=body.get('refs')
        if not isinstance(refs,list) or len(refs)>40:raise AppError('저장 결과를 40개 이하로 선택하세요.')
        if any(not isinstance(r,dict) for r in refs):raise AppError('참고 결과 ID와 버전을 확인하세요.')
        if any(r.get('kind') not in ('research_result','debrief') for r in refs):raise AppError('검토한 분석 결과 또는 FGI 디브리프만 선택하세요.')
        rows=[self.research_ref(user,r,True) for r in refs]
        if len({r['id'] for r in rows})!=len(rows):raise AppError('중복 결과를 제외하세요.')
        previous=self.store.list(p,'research_inputs');old=previous[0] if previous else None
        fields={'refs':[{'kind':r['kind'],'id':r['id'],'version':r['version']} for r in rows],
            'selected_by':user['id'],'selected_at':timestamp(),'dependencies':lineage(rows)}
        self.planning_actor(user)
        if old:return self.store.write(p,updates=[('research_inputs',old['id'],fields,revision(body))],expected_epoch=epoch)[0]
        return self.store.write(p,inserts=[('research_inputs',fields,None)],expected_epoch=epoch)[0]

    def result_history(self,user,rid):
        return [r if self.accessible(user['project_id'],r) else self.redacted(r)
            for r in self.store.history(user['project_id'],'research_result',rid)]

    def followup_study(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        study=self.flow_record(user,'study',text(body,'study_id',80),revision(body))
        if study['status']!='completed':raise AppError('완료된 FGI에서 후속 분석을 여세요.')
        debrief=self.research_ref(user,{'kind':'debrief',**study['completed_debrief']},True)
        original=self.conversation(user,study['conversation_id'])
        cid=str(uuid.uuid4())
        context={'study':{k:study[k] for k in ('title','objective','research_questions','recruitment_criteria','participants','guide')},
            'messages':[{k:m.get(k) for k in ('speaker','text','evidence_ids','is_synthetic')} for m in original['messages'] if not m.get('redacted')],
            'debrief':debrief['text']}
        if len(str(context))>140000:raise AppError('후속 분석 맥락이 큽니다. 별도 대화에서 검토 디브리프를 참고하세요.',409)
        # No study_id: this is an analysis conversation, not a mutation of the completed session.
        conv={'title':text(body,'title',200),'mode':'research','persona_ids':[],'participant_persona_versions':[],
            'created_by':user['id'],'prd_id':original['prd_id'],'decisions':[],'objective':study['objective'],
            'filters':original.get('filters',{}),'round_type':'explore','followup_context':context,
            'followup_ref':{'kind':'debrief','id':debrief['id'],'version':debrief['version']},'dependencies':lineage([debrief])}
        self.planning_actor(user)
        return self.store.write(p,inserts=[('conversation',conv,cid)],expected_epoch=epoch)[0]

    def generate_persona_candidates(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        profile=text(body,'profile',5000);assets=[];docs=[]
        if body.get('asset_refs'):assets,docs=self.flow_documents(user,body['asset_refs'],'customer_research')
        ids=strings(body.get('evidence_ids',[]),100,80)
        shared={r['id']:r for r in self.base_knowledge(p) if r['kind']=='voc' and r['evidence_type']=='real'}
        if any(i not in shared for i in ids):raise AppError('현재 공유된 고객 조사 근거를 선택하세요.',409)
        evidence=docs+[shared[i] for i in ids]
        if not evidence:raise AppError('고객 조사 보고서·서베이·심층 인터뷰 또는 고객 근거를 선택하세요.')
        result=self.generate('persona_candidates',{'profile':profile,'evidence':evidence})
        candidates=[self.save_persona(user,c,evidence,persist=False) for c in result['candidates']]
        if len({c['alias'] for c in candidates})!=len(candidates):raise AppError('페르소나 초안의 이름이 중복됩니다.',502)
        self.planning_actor(user)
        return self.store.write(p,inserts=[('persona_batch',{'profile':profile,'candidates':candidates,
            'rationale':text(result,'rationale',5000),'dependencies':lineage(assets)+dependency_map(evidence),
            'state':'draft','selected_indices':[],'is_synthetic':True,'created_by':user['id']},None)],expected_epoch=epoch)[0]

    def adopt_persona_candidates(self,user,body):
        p=user['project_id'];epoch=self.store.epoch(p)
        batch=self.flow_record(user,'persona_batch',text(body,'batch_id',80),revision(body))
        indices=body.get('indices')
        if batch['state']!='draft':raise AppError('이미 선택한 초안입니다.',409)
        if not isinstance(indices,list) or not indices or any(type(i)is not int or not 0<=i<len(batch['candidates']) for i in indices) or len(set(indices))!=len(indices):
            raise AppError('등록할 페르소나 초안을 선택하세요.')
        inserts=[('persona',{**batch['candidates'][i],'candidate_batch_id':batch['id'],'review_status':'po_selected'},None) for i in indices]
        self.planning_actor(user)
        saved=self.store.write(p,inserts=inserts,updates=[('persona_batch',batch['id'],{'state':'selected','selected_indices':indices},batch['version'])],expected_epoch=epoch)
        return [r for r in saved if r['kind']=='persona']
